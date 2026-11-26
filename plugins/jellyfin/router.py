"""Jellyfin-compatible FastAPI router exposing Hearth libraries and media."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import __version__
from app.api.v1.stream import stream_media_file
from app.core.deps import (
    ImagePipelineDep,
    KeysDep,
    LoginLimiterDep,
    SettingsDep,
    Viewer,
    client_ip,
    user_can_access_library,
)
from app.core.errors import NotFoundError, RateLimitError, UnauthorizedError
from app.core.locks import LoopAwareLock
from app.core.passwords import verify_password_async
from app.core.tokens import create_media_token
from app.db.base import utcnow
from app.db.models import (
    Album,
    Artist,
    Collection,
    CollectionItem,
    Episode,
    Genre,
    ItemGenre,
    ItemPerson,
    Library,
    LibraryAccess,
    LibraryPath,
    MediaFile,
    MediaItem,
    Person,
    Season,
    Series,
    Track,
    User,
    UserItemState,
)
from app.db.models import (
    Image as ImageModel,
)
from app.db.session import SessionDep
from app.services.libraries import path_is_within_roots
from app.services.media.probe import ProbeResult
from app.services.playback_state import chapters_from_streams, resolve_watch_state
from app.services.ratings import is_rating_permitted
from app.services.subtitles import (
    convert_to_webvtt,
    decode_subtitle_bytes,
    decode_subtitle_id,
    embedded_subtitle_cache_dir,
    ensure_embedded_subtitles,
    extract_embedded_subtitle,
    read_embedded_subtitle,
    text_subtitle_tracks,
)
from app.services.transcode.capabilities import probe_transcode_capabilities
from app.services.transcode.decision import PlaybackOptions, UserLimits, evaluate_playback_plan
from app.services.transcode.profile import ClientCapabilities
from app.services.transcode.session import get_session_manager
from plugins.jellyfin.auth import (
    JellyfinUser,
    OptionalJellyfinUser,
    create_jellyfin_token,
    parse_jellyfin_auth_header,
    server_id_for_keys,
)
from plugins.jellyfin.mapper import (
    build_media_sources,
    library_to_view_dto,
    media_item_to_dto,
    ticks_to_ms,
    user_data_dto,
    user_to_dto,
)

if TYPE_CHECKING:
    from plugins.jellyfin.plugin import JellyfinConfig

logger = logging.getLogger("hearth.plugins.jellyfin")
_STATE_LOCK = LoopAwareLock()

_JF_TO_HEARTH_TYPES: dict[str, str] = {
    "movie": "movie",
    "series": "series",
    "season": "season",
    "episode": "episode",
    "musicartist": "artist",
    "musicalbum": "album",
    "audio": "track",
    "book": "book",
}

_IMAGE_TYPE_TO_KIND: dict[str, str] = {
    "primary": "poster",
    "backdrop": "backdrop",
    "logo": "logo",
    "thumb": "thumb",
    "banner": "banner",
}


def _get_config(request: Request) -> JellyfinConfig:
    from plugins.jellyfin.plugin import JellyfinConfig

    manager = getattr(request.app.state, "plugin_manager", None)
    if manager is not None:
        plugin = manager.get_plugin("org.hearth.plugins.jellyfin")
        if plugin is not None and isinstance(plugin.config, JellyfinConfig):
            return plugin.config
    return JellyfinConfig()


async def _accessible_library_ids(session: AsyncSession, user: User) -> list[str]:
    if user.role == "admin":
        return list(await session.scalars(select(Library.id)))
    return list(
        await session.scalars(
            select(LibraryAccess.library_id).where(LibraryAccess.user_id == user.id)
        )
    )


def _item_load_options() -> list[Any]:
    return [
        selectinload(MediaItem.movie),
        selectinload(MediaItem.series),
        selectinload(MediaItem.season).selectinload(Season.series).selectinload(Series.item),
        selectinload(MediaItem.episode).selectinload(Episode.series).selectinload(Series.item),
        selectinload(MediaItem.episode).selectinload(Episode.season).selectinload(Season.item),
        selectinload(MediaItem.episode).selectinload(Episode.file),
        selectinload(MediaItem.artist),
        selectinload(MediaItem.album).selectinload(Album.artist).selectinload(Artist.item),
        selectinload(MediaItem.track).selectinload(Track.album).selectinload(Album.item),
        selectinload(MediaItem.track).selectinload(Track.artist).selectinload(Artist.item),
        selectinload(MediaItem.track).selectinload(Track.file),
        selectinload(MediaItem.files),
        selectinload(MediaItem.images),
        selectinload(MediaItem.genres),
    ]


async def _load_authorized_item(
    session: AsyncSession,
    user: User,
    item_id: str,
) -> MediaItem:
    item = await session.scalar(
        select(MediaItem).options(*_item_load_options()).where(MediaItem.id == item_id)
    )
    if item is None or not await user_can_access_library(session, user, item.library_id):
        raise NotFoundError("Item not found", code="item_not_found")
    if not is_rating_permitted(item.content_rating, user.max_rating):
        raise NotFoundError("Item not found", code="item_not_found")
    return item


async def _load_states_map(
    session: AsyncSession,
    user_id: str,
    item_ids: list[str],
) -> dict[str, UserItemState]:
    if not item_ids:
        return {}
    rows = await session.scalars(
        select(UserItemState).where(
            UserItemState.user_id == user_id,
            UserItemState.item_id.in_(item_ids),
        )
    )
    return {row.item_id: row for row in rows}


async def _load_or_create_state(
    session: AsyncSession,
    user_id: str,
    item_id: str,
) -> UserItemState:
    state = await session.get(UserItemState, (user_id, item_id))
    if state is None:
        state = UserItemState(user_id=user_id, item_id=item_id)
        session.add(state)
    return state


async def _load_people_dtos(session: AsyncSession, item_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(ItemPerson, Person)
            .join(Person, Person.id == ItemPerson.person_id)
            .where(ItemPerson.item_id == item_id)
            .order_by(ItemPerson.ordering.asc())
        )
    ).all()
    people: list[dict[str, Any]] = []
    for credit, person in rows:
        jf_role = {
            "cast": "Actor",
            "director": "Director",
            "writer": "Writer",
            "producer": "Producer",
        }.get(credit.role.lower(), "Actor")
        people.append(
            {
                "Name": person.name,
                "Id": person.id,
                "Role": credit.character,
                "Type": jf_role,
            }
        )
    return people


def _resolve_target_user_id(requested_user_id: str, current_user: User) -> str:
    if requested_user_id == current_user.id or current_user.role == "admin":
        return requested_user_id
    raise NotFoundError("User not found", code="user_not_found")


def _parse_item_types(raw: str | None) -> list[str]:
    if not raw:
        return []
    result: list[str] = []
    for part in raw.split(","):
        mapped = _JF_TO_HEARTH_TYPES.get(part.strip().lower())
        if mapped:
            result.append(mapped)
    return result


def _item_files_for_playback(item: MediaItem) -> list[MediaFile]:
    if item.type == "episode" and item.episode and item.episode.file is not None:
        files = [item.episode.file]
    elif item.type == "track" and item.track and item.track.file is not None:
        files = [item.track.file]
    else:
        files = list(item.files)
    return [f for f in files if f.missing_since is None]


def create_jellyfin_router() -> APIRouter:
    """Construct the Jellyfin-compatible APIRouter."""
    router = APIRouter(tags=["jellyfin"])

    # ------------------------------------------------------------------
    # System & Branding
    # ------------------------------------------------------------------

    @router.get("/System/Info/Public")
    async def system_info_public(request: Request, keys: KeysDep) -> dict[str, Any]:
        cfg = _get_config(request)
        return {
            "LocalAddress": str(request.base_url).rstrip("/"),
            "ServerName": cfg.server_name,
            "Version": cfg.advertised_version,
            "ProductName": "Jellyfin Server",
            "OperatingSystem": "Linux",
            "Id": server_id_for_keys(keys),
            "StartupWizardCompleted": True,
        }

    @router.get("/System/Info")
    async def system_info(
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
    ) -> dict[str, Any]:
        cfg = _get_config(request)
        return {
            "LocalAddress": str(request.base_url).rstrip("/"),
            "ServerName": cfg.server_name,
            "Version": cfg.advertised_version,
            "ProductName": "Jellyfin Server",
            "OperatingSystem": "Linux",
            "Id": server_id_for_keys(keys),
            "StartupWizardCompleted": True,
            "CanSelfRestart": False,
            "CanLaunchWebBrowser": False,
            "SupportsLibraryMonitor": True,
            "HasUpdateAvailable": False,
            "HasPendingRestart": False,
            "HearthVersion": __version__,
        }

    @router.get("/System/Ping")
    @router.post("/System/Ping")
    async def system_ping() -> str:
        return "Jellyfin Server"

    @router.get("/Branding/Configuration")
    async def branding_configuration() -> dict[str, Any]:
        return {
            "LoginDisclaimer": "",
            "CustomCss": "",
            "SplashscreenEnabled": False,
        }

    @router.get("/Branding/Css")
    @router.get("/Branding/Css.css")
    async def branding_css() -> Response:
        return Response(content="", media_type="text/css")

    # ------------------------------------------------------------------
    # Users & Authentication
    # ------------------------------------------------------------------

    @router.get("/Users/Public")
    async def users_public(
        request: Request,
        keys: KeysDep,
        session: SessionDep,
    ) -> list[dict[str, Any]]:
        cfg = _get_config(request)
        if not cfg.expose_public_users:
            return []
        server_id = server_id_for_keys(keys)
        users = list(await session.scalars(select(User).where(User.disabled.is_(False))))
        return [user_to_dto(u, server_id) for u in users]

    @router.post("/Users/AuthenticateByName")
    async def authenticate_by_name(
        request: Request,
        settings: SettingsDep,
        keys: KeysDep,
        limiter: LoginLimiterDep,
        session: SessionDep,
        body: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        username = str(body.get("Username") or "").strip().lower()
        password = str(body.get("Pw") or body.get("Password") or "")

        ip = client_ip(request, settings)
        ip_retry = limiter.retry_after(f"jf:ip:{ip}")
        user_retry = limiter.retry_after(f"jf:user:{username}") if username else 0.0
        if ip_retry > 0 or user_retry > 0:
            raise RateLimitError(
                max(ip_retry, user_retry),
                "Too many failed login attempts",
            )

        user = await session.scalar(select(User).where(User.username == username))
        if (
            user is None
            or user.disabled
            or not await verify_password_async(user.password_hash, password)
        ):
            limiter.hit(f"jf:ip:{ip}")
            if username:
                limiter.hit(f"jf:user:{username}")
            raise UnauthorizedError("Invalid username or password", code="invalid_credentials")

        limiter.reset(f"jf:ip:{ip}")
        limiter.reset(f"jf:user:{username}")

        cfg = _get_config(request)
        auth_header = parse_jellyfin_auth_header(request)
        token, _expires_at = create_jellyfin_token(
            user_id=user.id,
            token_version=user.token_version,
            auth_header=auth_header,
            settings=settings,
            key=keys.jwt,
            ttl_days=cfg.token_ttl_days,
        )

        user.last_login_at = utcnow()
        await session.commit()

        server_id = server_id_for_keys(keys)
        user_dto = user_to_dto(user, server_id)
        return {
            "User": user_dto,
            "SessionInfo": {
                "Id": auth_header.device_id,
                "UserId": user.id,
                "UserName": user.username,
                "Client": auth_header.client,
                "DeviceName": auth_header.device,
                "DeviceId": auth_header.device_id,
                "ApplicationVersion": auth_header.version,
                "IsActive": True,
                "SupportsRemoteControl": False,
                "PlayableMediaTypes": ["Audio", "Video"],
                "SupportedCommands": [],
            },
            "AccessToken": token,
            "ServerId": server_id,
        }

    @router.post("/Sessions/Logout", status_code=status.HTTP_204_NO_CONTENT)
    async def sessions_logout(_user: JellyfinUser) -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/Users/Me")
    async def users_me(user: JellyfinUser, keys: KeysDep) -> dict[str, Any]:
        return user_to_dto(user, server_id_for_keys(keys))

    @router.get("/Users/{user_id}")
    async def get_user_by_id(
        user_id: str,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
    ) -> dict[str, Any]:
        target_id = _resolve_target_user_id(user_id, user)
        target = user if target_id == user.id else await session.get(User, target_id)
        if target is None:
            raise NotFoundError("User not found", code="user_not_found")
        return user_to_dto(target, server_id_for_keys(keys))

    # ------------------------------------------------------------------
    # Views / Libraries
    # ------------------------------------------------------------------

    @router.get("/UserViews")
    @router.get("/Users/{user_id}/Views")
    async def get_user_views(
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if user_id is not None:
            _resolve_target_user_id(user_id, user)
        lib_ids = await _accessible_library_ids(session, user)
        libs = (
            list(
                await session.scalars(
                    select(Library).where(Library.id.in_(lib_ids)).order_by(Library.name.asc())
                )
            )
            if lib_ids
            else []
        )
        server_id = server_id_for_keys(keys)
        items = [library_to_view_dto(lib, server_id) for lib in libs]
        return {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0}

    # ------------------------------------------------------------------
    # Items Query & Detail
    # ------------------------------------------------------------------

    async def _query_items(
        request: Request,
        user: User,
        keys: KeysDep,
        session: SessionDep,
        *,
        parent_id: str | None = None,
        include_item_types: str | None = None,
        exclude_item_types: str | None = None,
        recursive: bool = False,
        sort_by: str | None = None,
        sort_order: str | None = None,
        start_index: int = 0,
        limit: int = 100,
        search_term: str | None = None,
        filters: str | None = None,
        genres: str | None = None,
        years: str | None = None,
        ids: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        cfg = _get_config(request)
        server_id = server_id_for_keys(keys)
        header = parse_jellyfin_auth_header(request)
        lib_ids = await _accessible_library_ids(session, user)
        if not lib_ids:
            return {"Items": [], "TotalRecordCount": 0, "StartIndex": start_index}

        stmt = select(MediaItem).where(MediaItem.library_id.in_(lib_ids))

        if ids:
            id_list = [x.strip() for x in ids.split(",") if x.strip()]
            if not id_list:
                return {"Items": [], "TotalRecordCount": 0, "StartIndex": start_index}
            stmt = stmt.where(MediaItem.id.in_(id_list))

        if parent_id:
            if parent_id in lib_ids:
                stmt = stmt.where(MediaItem.library_id == parent_id)
                if not recursive and not include_item_types:
                    # Top-level items inside a library
                    stmt = stmt.where(MediaItem.type.in_(["movie", "series", "artist", "book"]))
            else:
                parent_item = await session.get(MediaItem, parent_id)
                if parent_item is not None:
                    if parent_item.type == "series":
                        if recursive:
                            stmt = stmt.join(Episode, Episode.item_id == MediaItem.id).where(
                                Episode.series_id == parent_id
                            )
                        else:
                            stmt = stmt.join(Season, Season.item_id == MediaItem.id).where(
                                Season.series_id == parent_id
                            )
                    elif parent_item.type == "season":
                        stmt = stmt.join(Episode, Episode.item_id == MediaItem.id).where(
                            Episode.season_id == parent_id
                        )
                    elif parent_item.type == "artist":
                        if recursive:
                            stmt = stmt.join(Track, Track.item_id == MediaItem.id).where(
                                Track.artist_id == parent_id
                            )
                        else:
                            stmt = stmt.join(Album, Album.item_id == MediaItem.id).where(
                                Album.artist_id == parent_id
                            )
                    elif parent_item.type == "album":
                        stmt = stmt.join(Track, Track.item_id == MediaItem.id).where(
                            Track.album_id == parent_id
                        )
                    else:
                        return {"Items": [], "TotalRecordCount": 0, "StartIndex": start_index}
                else:
                    # Could be a Collection
                    coll = await session.get(Collection, parent_id)
                    if coll is not None:
                        stmt = stmt.join(
                            CollectionItem, CollectionItem.media_item_id == MediaItem.id
                        ).where(CollectionItem.collection_id == parent_id)
                    else:
                        return {"Items": [], "TotalRecordCount": 0, "StartIndex": start_index}

        included = _parse_item_types(include_item_types)
        if included:
            stmt = stmt.where(MediaItem.type.in_(included))
        excluded = _parse_item_types(exclude_item_types)
        if excluded:
            stmt = stmt.where(MediaItem.type.notin_(excluded))

        if search_term:
            term = f"%{search_term.strip()}%"
            stmt = stmt.where(MediaItem.title.ilike(term))

        if years:
            year_vals = [int(y.strip()) for y in years.split(",") if y.strip().isdigit()]
            if year_vals:
                stmt = stmt.where(MediaItem.year.in_(year_vals))

        if genres:
            genre_names = [
                g.strip().lower() for g in genres.replace("|", ",").split(",") if g.strip()
            ]
            if genre_names:
                stmt = stmt.where(
                    MediaItem.id.in_(
                        select(ItemGenre.item_id)
                        .join(Genre, Genre.id == ItemGenre.genre_id)
                        .where(func.lower(Genre.name).in_(genre_names))
                    )
                )

        filter_set = {f.strip().lower() for f in (filters or "").split(",") if f.strip()}
        if "isplayed" in filter_set:
            stmt = stmt.join(
                UserItemState,
                (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
            ).where(UserItemState.played.is_(True))
        elif "isunplayed" in filter_set:
            stmt = stmt.outerjoin(
                UserItemState,
                (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
            ).where(or_(UserItemState.played.is_(None), UserItemState.played.is_(False)))
        elif "isresumable" in filter_set:
            stmt = stmt.join(
                UserItemState,
                (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
            ).where(UserItemState.played.is_(False), UserItemState.position_ms > 0)
        elif "isfavorite" in filter_set:
            stmt = stmt.join(
                UserItemState,
                (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
            ).where(UserItemState.favorite.is_(True))

        # Sorting
        descending = (sort_order or "").lower() == "descending"
        primary_sort = (sort_by or "SortName").split(",")[0].strip().lower()
        order_col: Any = MediaItem.sort_title
        if primary_sort in {"datecreated", "dateadded"}:
            order_col = MediaItem.added_at
        elif primary_sort in {"productionyear", "premieredate"}:
            order_col = MediaItem.year
        elif primary_sort == "communityrating":
            order_col = MediaItem.rating
        elif primary_sort == "random":
            order_col = func.random()

        stmt = stmt.order_by(
            order_col.desc() if descending else order_col.asc(), MediaItem.id.asc()
        )

        # Execute with rating filter in Python (rating ceiling is string comparison)
        all_rows = list(await session.scalars(stmt.options(*_item_load_options())))
        permitted = [
            it for it in all_rows if is_rating_permitted(it.content_rating, user.max_rating)
        ]
        total = len(permitted)
        clamped_limit = max(1, min(limit, 500))
        page = permitted[max(0, start_index) : max(0, start_index) + clamped_limit]

        states = await _load_states_map(session, user.id, [it.id for it in page])
        include_sources = "mediasources" in (fields or "").lower()
        dtos = [
            media_item_to_dto(
                it,
                server_id,
                user_state=states.get(it.id),
                include_media_sources=include_sources,
                token=header.token,
                allow_transcoding=cfg.allow_transcoding,
            )
            for it in page
        ]
        return {"Items": dtos, "TotalRecordCount": total, "StartIndex": start_index}

    @router.get("/Items")
    @router.get("/Users/{user_id}/Items")
    async def list_items(
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        user_id: str | None = None,
        ParentId: str | None = None,
        IncludeItemTypes: str | None = None,
        ExcludeItemTypes: str | None = None,
        Recursive: bool = False,
        SortBy: str | None = None,
        SortOrder: str | None = None,
        StartIndex: int = 0,
        Limit: int = 100,
        SearchTerm: str | None = None,
        Filters: str | None = None,
        Genres: str | None = None,
        Years: str | None = None,
        Ids: str | None = None,
        Fields: str | None = None,
    ) -> dict[str, Any]:
        if user_id is not None:
            _resolve_target_user_id(user_id, user)
        return await _query_items(
            request,
            user,
            keys,
            session,
            parent_id=ParentId,
            include_item_types=IncludeItemTypes,
            exclude_item_types=ExcludeItemTypes,
            recursive=Recursive,
            sort_by=SortBy,
            sort_order=SortOrder,
            start_index=StartIndex,
            limit=Limit,
            search_term=SearchTerm,
            filters=Filters,
            genres=Genres,
            years=Years,
            ids=Ids,
            fields=Fields,
        )

    @router.get("/Items/Latest")
    @router.get("/Users/{user_id}/Items/Latest")
    async def list_latest_items(
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        user_id: str | None = None,
        ParentId: str | None = None,
        IncludeItemTypes: str | None = None,
        Limit: int = 20,
    ) -> list[dict[str, Any]]:
        if user_id is not None:
            _resolve_target_user_id(user_id, user)
        result = await _query_items(
            request,
            user,
            keys,
            session,
            parent_id=ParentId,
            include_item_types=IncludeItemTypes or "Movie,Series,Episode,MusicAlbum,Book",
            recursive=True,
            sort_by="DateCreated",
            sort_order="Descending",
            start_index=0,
            limit=Limit,
        )
        return list(result["Items"])

    @router.get("/Items/Resume")
    @router.get("/Users/{user_id}/Items/Resume")
    async def list_resume_items(
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        user_id: str | None = None,
        ParentId: str | None = None,
        Limit: int = 12,
    ) -> dict[str, Any]:
        if user_id is not None:
            _resolve_target_user_id(user_id, user)
        return await _query_items(
            request,
            user,
            keys,
            session,
            parent_id=ParentId,
            recursive=True,
            filters="IsResumable",
            sort_by="DateCreated",
            sort_order="Descending",
            start_index=0,
            limit=Limit,
            fields="MediaSources",
        )

    @router.get("/Items/{item_id}")
    @router.get("/Users/{user_id}/Items/{item_id}")
    async def get_item_detail(
        item_id: str,
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if user_id is not None:
            _resolve_target_user_id(user_id, user)
        cfg = _get_config(request)
        server_id = server_id_for_keys(keys)
        header = parse_jellyfin_auth_header(request)

        lib = await session.get(Library, item_id)
        if lib is not None and await user_can_access_library(session, user, lib.id):
            return library_to_view_dto(lib, server_id)

        item = await _load_authorized_item(session, user, item_id)
        state = await session.get(UserItemState, (user.id, item.id))
        people = await _load_people_dtos(session, item.id)
        return media_item_to_dto(
            item,
            server_id,
            user_state=state,
            people=people,
            include_media_sources=True,
            token=header.token,
            allow_transcoding=cfg.allow_transcoding,
        )

    @router.get("/Users/{user_id}/Items/{item_id}/SpecialFeatures")
    @router.get("/Users/{user_id}/Items/{item_id}/LocalTrailers")
    async def item_extras_empty(
        user_id: str,
        item_id: str,
        user: JellyfinUser,
        session: SessionDep,
    ) -> list[dict[str, Any]]:
        _resolve_target_user_id(user_id, user)
        await _load_authorized_item(session, user, item_id)
        return []

    @router.get("/Items/{item_id}/Similar")
    @router.get("/Movies/{item_id}/Similar")
    @router.get("/Shows/{item_id}/Similar")
    async def similar_items(
        item_id: str,
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        Limit: int = 12,
    ) -> dict[str, Any]:
        item = await _load_authorized_item(session, user, item_id)
        jf_type = {"movie": "Movie", "series": "Series", "album": "MusicAlbum"}.get(item.type)
        res = await _query_items(
            request,
            user,
            keys,
            session,
            parent_id=item.library_id,
            include_item_types=jf_type,
            limit=Limit + 1,
        )
        filtered = [it for it in res["Items"] if it["Id"] != item.id][:Limit]
        return {"Items": filtered, "TotalRecordCount": len(filtered), "StartIndex": 0}

    # ------------------------------------------------------------------
    # Shows (Seasons / Episodes / NextUp)
    # ------------------------------------------------------------------

    @router.get("/Shows/{series_id}/Seasons")
    async def show_seasons(
        series_id: str,
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
    ) -> dict[str, Any]:
        await _load_authorized_item(session, user, series_id)
        return await _query_items(
            request,
            user,
            keys,
            session,
            parent_id=series_id,
            include_item_types="Season",
            sort_by="SortName",
        )

    @router.get("/Shows/{series_id}/Episodes")
    async def show_episodes(
        series_id: str,
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        SeasonId: str | None = None,
    ) -> dict[str, Any]:
        await _load_authorized_item(session, user, series_id)
        parent = SeasonId or series_id
        return await _query_items(
            request,
            user,
            keys,
            session,
            parent_id=parent,
            include_item_types="Episode",
            recursive=True,
            fields="MediaSources",
        )

    @router.get("/Shows/NextUp")
    async def shows_next_up(
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        Limit: int = 12,
        ParentId: str | None = None,
    ) -> dict[str, Any]:
        cfg = _get_config(request)
        server_id = server_id_for_keys(keys)
        header = parse_jellyfin_auth_header(request)
        lib_ids = await _accessible_library_ids(session, user)
        if ParentId:
            lib_ids = [lid for lid in lib_ids if lid == ParentId]
        if not lib_ids:
            return {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}

        # Find episodes not yet played in accessible libraries, ordered by series + season + episode
        episodes = list(
            await session.scalars(
                select(MediaItem)
                .join(Episode, Episode.item_id == MediaItem.id)
                .outerjoin(
                    UserItemState,
                    (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
                )
                .where(
                    MediaItem.library_id.in_(lib_ids),
                    or_(UserItemState.played.is_(None), UserItemState.played.is_(False)),
                )
                .options(*_item_load_options())
                .order_by(
                    Episode.series_id.asc(),
                    Episode.season_number.asc(),
                    Episode.episode_number.asc(),
                )
            )
        )

        seen_series: set[str] = set()
        next_episodes: list[MediaItem] = []
        for ep in episodes:
            if not ep.episode or ep.episode.series_id in seen_series:
                continue
            if not is_rating_permitted(ep.content_rating, user.max_rating):
                continue
            seen_series.add(ep.episode.series_id)
            next_episodes.append(ep)
            if len(next_episodes) >= max(1, min(Limit, 100)):
                break

        states = await _load_states_map(session, user.id, [it.id for it in next_episodes])
        dtos = [
            media_item_to_dto(
                it,
                server_id,
                user_state=states.get(it.id),
                include_media_sources=True,
                token=header.token,
                allow_transcoding=cfg.allow_transcoding,
            )
            for it in next_episodes
        ]
        return {"Items": dtos, "TotalRecordCount": len(dtos), "StartIndex": 0}

    # ------------------------------------------------------------------
    # Artists, Genres, Search Hints
    # ------------------------------------------------------------------

    @router.get("/Artists")
    @router.get("/Artists/AlbumArtists")
    async def list_artists(
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        ParentId: str | None = None,
        StartIndex: int = 0,
        Limit: int = 100,
        SearchTerm: str | None = None,
    ) -> dict[str, Any]:
        return await _query_items(
            request,
            user,
            keys,
            session,
            parent_id=ParentId,
            include_item_types="MusicArtist",
            recursive=True,
            start_index=StartIndex,
            limit=Limit,
            search_term=SearchTerm,
        )

    @router.get("/Genres")
    async def list_genres(
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        ParentId: str | None = None,
    ) -> dict[str, Any]:
        lib_ids = await _accessible_library_ids(session, user)
        if ParentId:
            lib_ids = [lid for lid in lib_ids if lid == ParentId]
        if not lib_ids:
            return {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
        rows = list(
            await session.scalars(
                select(Genre)
                .join(ItemGenre, ItemGenre.genre_id == Genre.id)
                .join(MediaItem, MediaItem.id == ItemGenre.item_id)
                .where(MediaItem.library_id.in_(lib_ids))
                .distinct()
                .order_by(Genre.name.asc())
            )
        )
        server_id = server_id_for_keys(keys)
        items = [
            {
                "Name": g.name,
                "Id": g.id,
                "ServerId": server_id,
                "Type": "Genre",
                "IsFolder": True,
            }
            for g in rows
        ]
        return {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0}

    @router.get("/Search/Hints")
    async def search_hints(
        request: Request,
        user: JellyfinUser,
        keys: KeysDep,
        session: SessionDep,
        SearchTerm: str = "",
        Limit: int = 20,
    ) -> dict[str, Any]:
        res = await _query_items(
            request,
            user,
            keys,
            session,
            search_term=SearchTerm,
            recursive=True,
            limit=Limit,
        )
        hints = [
            {
                "Id": it["Id"],
                "ItemId": it["Id"],
                "Name": it["Name"],
                "Type": it["Type"],
                "MediaType": it.get("MediaType"),
                "ProductionYear": it.get("ProductionYear"),
                "PrimaryImageTag": it.get("ImageTags", {}).get("Primary"),
                "RunTimeTicks": it.get("RunTimeTicks"),
            }
            for it in res["Items"]
        ]
        return {"SearchHints": hints, "TotalRecordCount": len(hints)}

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    @router.get("/Items/{item_id}/Images/{image_type}")
    @router.get("/Items/{item_id}/Images/{image_type}/{image_index}")
    async def get_item_image(
        item_id: str,
        image_type: str,
        request: Request,
        user: OptionalJellyfinUser,
        pipeline: ImagePipelineDep,
        session: SessionDep,
        image_index: int = 0,
        maxWidth: int = Query(default=400, ge=16, le=4096),
        width: int | None = Query(default=None, ge=16, le=4096),
        format: str = Query(default="jpeg"),
    ) -> FileResponse:
        cfg = _get_config(request)
        if user is None and not cfg.allow_unauthenticated_images:
            raise UnauthorizedError("Authentication required", code="unauthorized")

        item = await session.get(MediaItem, item_id)
        if item is None:
            raise NotFoundError("Image not found", code="image_not_found")
        if user is not None:
            if not await user_can_access_library(session, user, item.library_id):
                raise NotFoundError("Image not found", code="image_not_found")
            if not is_rating_permitted(item.content_rating, user.max_rating):
                raise NotFoundError("Image not found", code="image_not_found")

        kind = _IMAGE_TYPE_TO_KIND.get(image_type.lower())
        if kind is None:
            raise NotFoundError("Image not found", code="image_not_found")

        images = list(
            await session.scalars(
                select(ImageModel)
                .where(ImageModel.item_id == item_id, ImageModel.kind == kind)
                .order_by(ImageModel.is_primary.desc(), ImageModel.created_at.asc())
            )
        )
        if not images or image_index < 0 or image_index >= len(images):
            raise NotFoundError("Image not found", code="image_not_found")

        image = images[image_index]
        if not Path(image.local_path).is_file():
            raise NotFoundError("Image not found", code="image_not_found")

        target_width = width or maxWidth
        fmt = format.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in {"webp", "jpeg", "png"}:
            fmt = "jpeg"

        variant = await pipeline.ensure_variant(
            image.id, image.local_path, width=target_width, image_format=fmt
        )
        media_type = {"webp": "image/webp", "jpeg": "image/jpeg", "png": "image/png"}[fmt]
        return FileResponse(
            variant,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=86400, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # ------------------------------------------------------------------
    # PlaybackInfo, Direct Stream, HLS Transcode, Subtitles
    # ------------------------------------------------------------------

    @router.get("/Items/{item_id}/PlaybackInfo")
    @router.post("/Items/{item_id}/PlaybackInfo")
    async def get_playback_info(
        item_id: str,
        request: Request,
        user: JellyfinUser,
        session: SessionDep,
    ) -> dict[str, Any]:
        cfg = _get_config(request)
        header = parse_jellyfin_auth_header(request)
        item = await _load_authorized_item(session, user, item_id)
        sources = build_media_sources(
            item,
            token=header.token,
            allow_transcoding=cfg.allow_transcoding and user.allow_transcode,
        )
        return {
            "MediaSources": sources,
            "PlaySessionId": f"jf-{item.id}",
        }

    @router.get("/Videos/{item_id}/stream")
    @router.get("/Videos/{item_id}/stream.{container}")
    @router.head("/Videos/{item_id}/stream")
    @router.head("/Videos/{item_id}/stream.{container}")
    @router.get("/Audio/{item_id}/stream")
    @router.get("/Audio/{item_id}/stream.{container}")
    @router.get("/Audio/{item_id}/universal")
    @router.head("/Audio/{item_id}/stream")
    async def stream_jellyfin_media(
        item_id: str,
        request: Request,
        user: JellyfinUser,
        settings: SettingsDep,
        session: SessionDep,
        MediaSourceId: str | None = None,
        container: str | None = None,
    ) -> Response:
        item = await _load_authorized_item(session, user, item_id)
        files = _item_files_for_playback(item)
        if not files:
            raise NotFoundError("Media file not found", code="file_not_found")
        selected = next((f for f in files if f.id == MediaSourceId), files[0])
        viewer = Viewer(user=user, item_id=item.id)
        return await stream_media_file(
            selected.id,
            request.method,
            request.headers,
            viewer,
            settings.stream_chunk_bytes,
            session,
        )

    @router.get("/Videos/{item_id}/master.m3u8")
    async def stream_jellyfin_hls(
        item_id: str,
        request: Request,
        user: JellyfinUser,
        settings: SettingsDep,
        keys: KeysDep,
        session: SessionDep,
        MediaSourceId: str | None = None,
        StartTimeTicks: int = 0,
    ) -> RedirectResponse:
        cfg = _get_config(request)
        if not cfg.allow_transcoding or not user.allow_transcode:
            raise NotFoundError("Transcoding disabled", code="transcode_disabled")

        item = await _load_authorized_item(session, user, item_id)
        files = _item_files_for_playback(item)
        if not files:
            raise NotFoundError("Media file not found", code="file_not_found")
        media_file = next((f for f in files if f.id == MediaSourceId), files[0])

        roots = list(
            await session.scalars(
                select(LibraryPath.path).where(LibraryPath.library_id == item.library_id)
            )
        )
        if not path_is_within_roots(Path(media_file.path), roots):
            raise NotFoundError("Media file not found", code="file_not_found")

        probe_res = ProbeResult.model_validate(
            {"container": media_file.container or "mp4", **(media_file.streams_json or {})}
        )
        server_caps = await probe_transcode_capabilities(settings)
        client_caps = ClientCapabilities(
            video_codecs=["h264"],
            audio_codecs=["aac"],
            containers=["mp4"],
        )
        limits = UserLimits(
            max_bitrate=user.max_bitrate,
            allow_transcode=user.allow_transcode,
        )
        start_seconds = max(0.0, ticks_to_ms(StartTimeTicks) / 1000.0)
        playback_options = PlaybackOptions(quality="auto")
        plan = evaluate_playback_plan(
            probe_res,
            client_caps,
            limits,
            server_caps,
            playback_options,
            user_agent=request.headers.get("user-agent"),
            file_extension=Path(media_file.path).suffix,
        )
        manager = get_session_manager(settings)
        transcode_session = await manager.create_session(
            user_id=user.id,
            item_id=item.id,
            file_id=media_file.id,
            input_path=Path(media_file.path),
            plan=plan,
            start_offset=start_seconds,
            duration_seconds=media_file.duration_seconds or probe_res.duration_seconds or 0.0,
            source_video=probe_res.primary_video,
            source_audio=probe_res.audio_streams[0] if probe_res.audio_streams else None,
            font_attachments=probe_res.font_attachments,
        )
        media_token, _ = create_media_token(
            user_id=user.id,
            token_version=user.token_version,
            settings=settings,
            key=keys.media,
            item_id=item.id,
            session_id=transcode_session.id,
        )
        return RedirectResponse(
            url=f"/api/v1/hls/{transcode_session.id}/master.m3u8?token={quote(media_token)}",
            status_code=status.HTTP_302_FOUND,
        )

    @router.get("/Videos/{item_id}/{media_source_id}/Subtitles/{index}/{start_ticks}/Stream.vtt")
    @router.get("/Videos/{item_id}/{media_source_id}/Subtitles/{index}/{start_ticks}/Stream.srt")
    async def get_jellyfin_subtitle(
        item_id: str,
        media_source_id: str,
        index: int,
        start_ticks: int,
        user: JellyfinUser,
        settings: SettingsDep,
        session: SessionDep,
        subId: str | None = None,
    ) -> Response:
        item = await _load_authorized_item(session, user, item_id)
        files = _item_files_for_playback(item)
        media_file = next((f for f in files if f.id == media_source_id), None)
        if media_file is None:
            raise NotFoundError("Subtitle not found", code="subtitle_not_found")

        roots = list(
            await session.scalars(
                select(LibraryPath.path).where(LibraryPath.library_id == item.library_id)
            )
        )
        if not path_is_within_roots(Path(media_file.path), roots):
            raise NotFoundError("Subtitle not found", code="subtitle_not_found")

        if subId:
            sub_ref = decode_subtitle_id(subId)
            if sub_ref is None or sub_ref.file_id != media_file.id:
                raise NotFoundError("Subtitle not found", code="subtitle_not_found")
            sub_path = Path(media_file.path).parent / sub_ref.name
            if not path_is_within_roots(sub_path, roots) or not sub_path.is_file():
                raise NotFoundError("Subtitle not found", code="subtitle_not_found")
            raw_bytes = sub_path.read_bytes()
            text = decode_subtitle_bytes(raw_bytes)
            fmt = sub_path.suffix.lstrip(".").lower() or "srt"
            vtt = text if fmt == "vtt" else convert_to_webvtt(text, fmt)
            return Response(content=vtt, media_type="text/vtt; charset=utf-8")

        probe_res = ProbeResult.model_validate(
            {"container": media_file.container or "mp4", **(media_file.streams_json or {})}
        )
        subtitle_streams = [s for s in probe_res.streams if s.codec_type == "subtitle"]
        ordinal = next(
            (ord_idx for ord_idx, s in enumerate(subtitle_streams) if s.index == index),
            None,
        )
        tracks = text_subtitle_tracks(probe_res.streams)
        track = next((t for t in tracks if t.ordinal == ordinal), None)
        if track is None or ordinal is None:
            raise NotFoundError("Subtitle not found", code="subtitle_not_found")

        cache_dir = embedded_subtitle_cache_dir(
            settings.data_dir / "cache" / "subtitles",
            media_file.id,
            media_file.mtime,
            media_file.size_bytes,
        )
        await ensure_embedded_subtitles(
            media_file.path,
            [track],
            cache_dir,
            ffmpeg_binary=settings.ffmpeg_binary,
        )
        cached = await read_embedded_subtitle(cache_dir, track.ordinal, "vtt")
        if cached is None:
            cached = await extract_embedded_subtitle(
                media_file.path,
                track.ordinal,
                "vtt",
            )
        if cached is None:
            raise NotFoundError("Subtitle not found", code="subtitle_not_found")
        return Response(content=cached, media_type="text/vtt; charset=utf-8")

    # ------------------------------------------------------------------
    # Sessions & Playback Progress / UserData
    # ------------------------------------------------------------------

    @router.post("/Sessions/Capabilities", status_code=status.HTTP_204_NO_CONTENT)
    @router.post("/Sessions/Capabilities/Full", status_code=status.HTTP_204_NO_CONTENT)
    async def sessions_capabilities(_user: JellyfinUser) -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/Sessions")
    async def list_sessions(
        request: Request,
        user: JellyfinUser,
    ) -> list[dict[str, Any]]:
        header = parse_jellyfin_auth_header(request)
        return [
            {
                "Id": header.device_id,
                "UserId": user.id,
                "UserName": user.username,
                "Client": header.client,
                "DeviceName": header.device,
                "DeviceId": header.device_id,
                "ApplicationVersion": header.version,
                "IsActive": True,
                "PlayableMediaTypes": ["Audio", "Video"],
                "SupportedCommands": [],
            }
        ]

    async def _update_playback_state(
        *,
        session: AsyncSession,
        user: User,
        item_id: str,
        position_ticks: int | None,
        watched_threshold: float,
        playback_session_gap_seconds: int,
        is_start: bool = False,
        is_stop: bool = False,
    ) -> None:
        async with _STATE_LOCK:
            await session.rollback()
            await session.refresh(user)
            item = await _load_authorized_item(session, user, item_id)
            state = await _load_or_create_state(session, user.id, item.id)
            now = utcnow()

            if is_start:
                gap_exceeded = (
                    state.last_played_at is None
                    or (now - state.last_played_at).total_seconds() >= playback_session_gap_seconds
                )
                if gap_exceeded:
                    state.play_count += 1

            files = _item_files_for_playback(item)
            primary_file = files[0] if files else None
            duration_s = item.runtime_seconds or (
                primary_file.duration_seconds if primary_file else None
            )
            duration_ms = int(duration_s * 1000) if duration_s else None
            chapters = chapters_from_streams(primary_file.streams_json if primary_file else None)

            pos_ms = ticks_to_ms(position_ticks)
            decision = resolve_watch_state(
                position_ms=pos_ms,
                duration_ms=duration_ms,
                chapters=chapters,
                threshold=watched_threshold,
            )
            state.position_ms = 0 if (is_stop and decision.watched) else decision.position_ms
            if decision.watched:
                state.played = True
            state.last_played_at = now
            await session.commit()

    @router.post("/Sessions/Playing", status_code=status.HTTP_204_NO_CONTENT)
    async def report_playing_start(
        user: JellyfinUser,
        settings: SettingsDep,
        session: SessionDep,
        body: Annotated[dict[str, Any], Body()],
    ) -> Response:
        item_id = str(body.get("ItemId") or "").strip()
        if item_id:
            await _update_playback_state(
                session=session,
                user=user,
                item_id=item_id,
                position_ticks=body.get("PositionTicks"),
                watched_threshold=settings.watched_threshold,
                playback_session_gap_seconds=settings.playback_session_gap_seconds,
                is_start=True,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/Sessions/Playing/Progress", status_code=status.HTTP_204_NO_CONTENT)
    async def report_playing_progress(
        user: JellyfinUser,
        settings: SettingsDep,
        session: SessionDep,
        body: Annotated[dict[str, Any], Body()],
    ) -> Response:
        item_id = str(body.get("ItemId") or "").strip()
        if item_id:
            await _update_playback_state(
                session=session,
                user=user,
                item_id=item_id,
                position_ticks=body.get("PositionTicks"),
                watched_threshold=settings.watched_threshold,
                playback_session_gap_seconds=settings.playback_session_gap_seconds,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/Sessions/Playing/Stopped", status_code=status.HTTP_204_NO_CONTENT)
    async def report_playing_stopped(
        user: JellyfinUser,
        settings: SettingsDep,
        session: SessionDep,
        body: Annotated[dict[str, Any], Body()],
    ) -> Response:
        item_id = str(body.get("ItemId") or "").strip()
        if item_id:
            await _update_playback_state(
                session=session,
                user=user,
                item_id=item_id,
                position_ticks=body.get("PositionTicks"),
                watched_threshold=settings.watched_threshold,
                playback_session_gap_seconds=settings.playback_session_gap_seconds,
                is_stop=True,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/Users/{user_id}/PlayedItems/{item_id}")
    async def mark_item_played(
        user_id: str,
        item_id: str,
        user: JellyfinUser,
        session: SessionDep,
    ) -> dict[str, Any]:
        _resolve_target_user_id(user_id, user)
        async with _STATE_LOCK:
            await session.rollback()
            await session.refresh(user)
            item = await _load_authorized_item(session, user, item_id)
            state = await _load_or_create_state(session, user.id, item.id)
            state.played = True
            state.position_ms = 0
            state.play_count = max(1, state.play_count)
            state.last_played_at = utcnow()
            await session.commit()
            return user_data_dto(item.id, state)

    @router.delete("/Users/{user_id}/PlayedItems/{item_id}")
    async def mark_item_unplayed(
        user_id: str,
        item_id: str,
        user: JellyfinUser,
        session: SessionDep,
    ) -> dict[str, Any]:
        _resolve_target_user_id(user_id, user)
        async with _STATE_LOCK:
            await session.rollback()
            await session.refresh(user)
            item = await _load_authorized_item(session, user, item_id)
            state = await _load_or_create_state(session, user.id, item.id)
            state.played = False
            state.position_ms = 0
            await session.commit()
            return user_data_dto(item.id, state)

    @router.post("/Users/{user_id}/FavoriteItems/{item_id}")
    async def mark_item_favorite(
        user_id: str,
        item_id: str,
        user: JellyfinUser,
        session: SessionDep,
    ) -> dict[str, Any]:
        _resolve_target_user_id(user_id, user)
        async with _STATE_LOCK:
            await session.rollback()
            await session.refresh(user)
            item = await _load_authorized_item(session, user, item_id)
            state = await _load_or_create_state(session, user.id, item.id)
            state.favorite = True
            await session.commit()
            return user_data_dto(item.id, state)

    @router.delete("/Users/{user_id}/FavoriteItems/{item_id}")
    async def unmark_item_favorite(
        user_id: str,
        item_id: str,
        user: JellyfinUser,
        session: SessionDep,
    ) -> dict[str, Any]:
        _resolve_target_user_id(user_id, user)
        async with _STATE_LOCK:
            await session.rollback()
            await session.refresh(user)
            item = await _load_authorized_item(session, user, item_id)
            state = await _load_or_create_state(session, user.id, item.id)
            state.favorite = False
            await session.commit()
            return user_data_dto(item.id, state)

    return router
