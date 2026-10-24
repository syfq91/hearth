"""Integration tests for the movie browse, detail and artwork endpoints."""

import io
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Genre,
    Image,
    ItemGenre,
    ItemPerson,
    Library,
    MediaFile,
    MediaItem,
    Movie,
    Person,
    UserItemState,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
VIEWER = {"username": "viewer", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _login(client: httpx.AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct horse"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _make_library(
    sessionmaker: async_sessionmaker[AsyncSession], name: str = "Movies"
) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"{name} {uuid.uuid4().hex[:8]}", type="movie")
        session.add(library)
        await session.commit()
        return library.id


def _png(path: Path, size: tuple[int, int] = (800, 600)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    PILImage.new("RGB", size, (30, 80, 140)).save(path, "PNG")
    return path


async def _add_item(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    *,
    title: str,
    year: int | None = None,
    rating: float | None = None,
    added_at: datetime | None = None,
    genres: tuple[str, ...] = (),
    poster: Path | None = None,
    backdrop: Path | None = None,
    people: tuple[tuple[str, str], ...] = (),
    state_user_id: str | None = None,
    watched: bool = False,
) -> str:
    async with sessionmaker() as session:
        item = MediaItem(
            library_id=library_id,
            type="movie",
            title=title,
            sort_title=sort_title(title),
            year=year,
            rating=rating,
            added_at=added_at or datetime.now(UTC),
            match_state="matched",
            overview=f"Overview of {title}",
            runtime_seconds=7200,
        )
        session.add(item)
        await session.flush()

        session.add(
            Movie(
                item_id=item.id,
                original_title=title,
                tagline=f"Tagline for {title}",
                release_date=f"{year}-01-01" if year else None,
                edition="Director's Cut",
                part=1,
            )
        )
        session.add(
            MediaFile(
                item_id=item.id,
                path=f"/media/{uuid.uuid4().hex}.mkv",
                size_bytes=1024,
                mtime=1.0,
                partial_hash=uuid.uuid4().hex * 2,
                container="matroska",
                duration_seconds=7200,
                bit_rate=8_000_000,
            )
        )
        for kind, path, primary in (
            ("poster", poster, True),
            ("backdrop", backdrop, False),
        ):
            if path is not None:
                session.add(
                    Image(
                        item_id=item.id,
                        kind=kind,
                        provider="local",
                        source_url=str(path),
                        local_path=str(path),
                        width=800,
                        height=600,
                        blurhash="LEHV6nWB2yk8pyo0adR*.7kCMdnj",
                        is_primary=primary,
                    )
                )
        for name in genres:
            genre = await session.scalar(select(Genre).where(Genre.name == name))
            if genre is None:
                genre = Genre(name=name)
                session.add(genre)
                await session.flush()
            session.add(ItemGenre(item_id=item.id, genre_id=genre.id))
        for name, role in people:
            person = Person(name=name)
            session.add(person)
            await session.flush()
            session.add(ItemPerson(item_id=item.id, person_id=person.id, role=role, ordering=0))
        if state_user_id is not None:
            session.add(
                UserItemState(
                    user_id=state_user_id,
                    item_id=item.id,
                    played=watched,
                    position_ms=1234,
                    favorite=True,
                    rating=8,
                    play_count=2,
                    last_played_at=datetime.now(UTC),
                )
            )
        await session.commit()
        return item.id


async def _admin_id(client: httpx.AsyncClient, admin: dict[str, str]) -> str:
    response = await client.get("/api/v1/auth/me", headers=_bearer(admin))
    assert response.status_code == 200
    return str(response.json()["id"])


async def test_list_sorts_by_title_and_paginates(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_id = await _make_library(db_sessionmaker)
    for index, title in enumerate(["Zulu", "The Matrix", "Alpha", "Heat", "Arrival"]):
        await _add_item(
            db_sessionmaker,
            library_id,
            title=title,
            added_at=datetime.now(UTC) + timedelta(minutes=index),
        )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = await api_client.get(
            f"/api/v1/libraries/{library_id}/items",
            params=params,
            headers=_bearer(admin),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(item["title"] for item in body["items"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        assert body["next_cursor"]
        cursor = body["next_cursor"]

    assert seen == ["Alpha", "Arrival", "Heat", "The Matrix", "Zulu"]


async def test_list_supports_added_year_and_rating_sorts(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_id = await _make_library(db_sessionmaker)
    base = datetime(2020, 1, 1, tzinfo=UTC)
    await _add_item(
        db_sessionmaker,
        library_id,
        title="Old",
        year=1990,
        rating=5.0,
        added_at=base,
    )
    await _add_item(
        db_sessionmaker,
        library_id,
        title="New",
        year=2020,
        rating=9.0,
        added_at=base + timedelta(days=2),
    )
    await _add_item(
        db_sessionmaker,
        library_id,
        title="Unknown",
        year=None,
        rating=None,
        added_at=base + timedelta(days=1),
    )

    async def titles(**params: object) -> list[str]:
        response = await api_client.get(
            f"/api/v1/libraries/{library_id}/items",
            params=params,
            headers=_bearer(admin),
        )
        assert response.status_code == 200, response.text
        return [item["title"] for item in response.json()["items"]]

    assert await titles(sort="year") == ["New", "Old", "Unknown"]
    assert await titles(sort="year", order="asc") == ["Old", "New", "Unknown"]
    assert await titles(sort="rating") == ["New", "Old", "Unknown"]
    assert await titles(sort="added", order="desc") == ["New", "Unknown", "Old"]
    assert await titles(sort="added", order="asc") == ["Old", "Unknown", "New"]


async def test_list_filters_by_genre_year_and_watched(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    user_id = await _admin_id(api_client, admin)
    library_id = await _make_library(db_sessionmaker)
    await _add_item(
        db_sessionmaker,
        library_id,
        title="Action 1999",
        year=1999,
        genres=("Action",),
        state_user_id=user_id,
        watched=False,
    )
    await _add_item(
        db_sessionmaker,
        library_id,
        title="Drama 2004",
        year=2004,
        genres=("Drama",),
    )
    await _add_item(
        db_sessionmaker,
        library_id,
        title="Action 2004",
        year=2004,
        genres=("Action", "Thriller"),
        state_user_id=user_id,
        watched=True,
    )

    async def titles(**params: object) -> list[str]:
        response = await api_client.get(
            f"/api/v1/libraries/{library_id}/items",
            params=params,
            headers=_bearer(admin),
        )
        assert response.status_code == 200, response.text
        return [item["title"] for item in response.json()["items"]]

    assert await titles(genre="action") == ["Action 1999", "Action 2004"]
    assert await titles(year=2004) == ["Action 2004", "Drama 2004"]
    assert await titles(watched="true") == ["Action 2004"]
    assert await titles(watched="false") == ["Action 1999", "Drama 2004"]
    assert await titles(genre="Action", year=2004) == ["Action 2004"]


async def test_list_rejects_a_mismatched_cursor(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_id = await _make_library(db_sessionmaker)
    for title in ("Alpha", "Beta"):
        await _add_item(db_sessionmaker, library_id, title=title)

    first = await api_client.get(
        f"/api/v1/libraries/{library_id}/items",
        params={"limit": 1},
        headers=_bearer(admin),
    )
    cursor = first.json()["next_cursor"]

    response = await api_client.get(
        f"/api/v1/libraries/{library_id}/items",
        params={"limit": 1, "sort": "year", "cursor": cursor},
        headers=_bearer(admin),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        ("title", "asc"),
        ("added", "asc"),
        ("added", "desc"),
        ("year", "asc"),
        ("year", "desc"),
        ("rating", "asc"),
        ("rating", "desc"),
    ],
)
async def test_cursor_pagination_matches_a_single_page_for_every_sort(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    sort: str,
    order: str,
) -> None:
    admin = await _setup(api_client)
    library_id = await _make_library(db_sessionmaker)
    base = datetime(2021, 5, 1, tzinfo=UTC)
    fixtures = [
        ("Zulu", 1999, 7.0),
        ("Alpha", 2020, None),
        ("Heat", None, 9.5),
        ("Arrival", 2016, 8.1),
        ("Brazil", 1985, 8.0),
    ]
    for index, (title, year, rating) in enumerate(fixtures):
        await _add_item(
            db_sessionmaker,
            library_id,
            title=title,
            year=year,
            rating=rating,
            added_at=base + timedelta(days=index),
        )

    async def page(params: dict[str, object]) -> dict[str, object]:
        response = await api_client.get(
            f"/api/v1/libraries/{library_id}/items",
            params=params,
            headers=_bearer(admin),
        )
        assert response.status_code == 200, response.text
        return response.json()

    whole = await page({"sort": sort, "order": order, "limit": 50})
    assert whole["has_more"] is False
    expected = [item["title"] for item in whole["items"]]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, object] = {"sort": sort, "order": order, "limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = await page(params)
        seen.extend(item["title"] for item in body["items"])
        pages += 1
        if not body["has_more"]:
            break
        next_cursor = body["next_cursor"]
        assert isinstance(next_cursor, str)
        cursor = next_cursor

    assert seen == expected
    assert pages == 3


async def test_read_endpoints_require_auth_and_reject_media_tokens(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    admin = await _setup(api_client)
    library_id = await _make_library(db_sessionmaker)
    item_id = await _add_item(
        db_sessionmaker, library_id, title="The Matrix", poster=_png(tmp_path / "poster.png")
    )
    image_id = await _first_image_id(db_sessionmaker, item_id)

    for url in (
        f"/api/v1/libraries/{library_id}/items",
        f"/api/v1/items/{item_id}",
        f"/api/v1/images/{image_id}",
    ):
        assert (await api_client.get(url)).status_code == 401

    minted = await api_client.post(
        "/api/v1/auth/media-tokens",
        json={"item_id": item_id},
        headers=_bearer(admin),
    )
    assert minted.status_code == 201
    token = minted.json()["token"]

    # A media token is for media-element URLs only, never a bearer credential.
    assert (
        await api_client.get(f"/api/v1/images/{image_id}", params={"token": token})
    ).status_code == 200
    assert (
        await api_client.get(f"/api/v1/items/{item_id}", params={"token": token})
    ).status_code == 401
    assert (
        await api_client.get(f"/api/v1/libraries/{library_id}/items", params={"token": token})
    ).status_code == 401


async def test_item_detail_includes_metadata_people_files_and_artwork(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    admin = await _setup(api_client)
    user_id = await _admin_id(api_client, admin)
    library_id = await _make_library(db_sessionmaker)
    poster = _png(tmp_path / "poster.png")
    backdrop = _png(tmp_path / "backdrop.png", (1280, 720))
    item_id = await _add_item(
        db_sessionmaker,
        library_id,
        title="The Matrix",
        year=1999,
        rating=8.2,
        genres=("Action", "Science Fiction"),
        poster=poster,
        backdrop=backdrop,
        people=(("Keanu Reeves", "cast"), ("Lana Wachowski", "director")),
        state_user_id=user_id,
        watched=True,
    )

    response = await api_client.get(f"/api/v1/items/{item_id}", headers=_bearer(admin))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "The Matrix"
    assert body["original_title"] == "The Matrix"
    assert body["release_date"] == "1999-01-01"
    assert body["tagline"] == "Tagline for The Matrix"
    assert body["edition"] == "Director's Cut"
    assert body["part"] == 1
    assert body["genres"] == ["Action", "Science Fiction"]
    assert body["files"][0]["missing"] is False
    assert body["files"][0]["container"] == "matroska"
    assert {(person["name"], person["role"]) for person in body["people"]} == {
        ("Keanu Reeves", "cast"),
        ("Lana Wachowski", "director"),
    }
    assert body["watch_state"]["watched"] is True
    assert body["watch_state"]["position_ms"] == 1234
    assert body["watch_state"]["favorite"] is True
    assert body["poster"]["kind"] == "poster"
    assert body["poster"]["url"] == f"/api/v1/images/{body['poster']['id']}"
    assert body["backdrop"]["kind"] == "backdrop"
    assert {image["kind"] for image in body["images"]} == {"poster", "backdrop"}

    missing = await api_client.get("/api/v1/items/does-not-exist", headers=_bearer(admin))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "item_not_found"


async def test_library_access_is_enforced(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    admin = await _setup(api_client)
    granted_library = await _make_library(db_sessionmaker, "Granted")
    secret_library = await _make_library(db_sessionmaker, "Secret")
    granted_item = await _add_item(
        db_sessionmaker,
        granted_library,
        title="Visible",
        poster=_png(tmp_path / "visible.png"),
    )
    secret_item = await _add_item(
        db_sessionmaker,
        secret_library,
        title="Hidden",
        poster=_png(tmp_path / "hidden.png"),
    )

    created = await api_client.post(
        "/api/v1/users",
        json={**VIEWER, "role": "user", "library_ids": [granted_library]},
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    viewer = await _login(api_client, VIEWER["username"])

    listed = await api_client.get(
        f"/api/v1/libraries/{granted_library}/items", headers=_bearer(viewer)
    )
    assert listed.status_code == 200
    assert [item["title"] for item in listed.json()["items"]] == ["Visible"]

    denied = await api_client.get(
        f"/api/v1/libraries/{secret_library}/items", headers=_bearer(viewer)
    )
    assert denied.status_code == 404

    assert (
        await api_client.get(f"/api/v1/items/{granted_item}", headers=_bearer(viewer))
    ).status_code == 200
    assert (
        await api_client.get(f"/api/v1/items/{secret_item}", headers=_bearer(viewer))
    ).status_code == 404

    granted_image = await _first_image_id(db_sessionmaker, granted_item)
    secret_image = await _first_image_id(db_sessionmaker, secret_item)
    assert (
        await api_client.get(f"/api/v1/images/{granted_image}", headers=_bearer(viewer))
    ).status_code == 200
    assert (
        await api_client.get(f"/api/v1/images/{secret_image}", headers=_bearer(viewer))
    ).status_code == 404


async def _first_image_id(sessionmaker: async_sessionmaker[AsyncSession], item_id: str) -> str:
    async with sessionmaker() as session:
        image_id = await session.scalar(select(Image.id).where(Image.item_id == item_id).limit(1))
        assert image_id is not None
        return image_id


async def test_image_endpoint_renders_variants_and_accepts_media_tokens(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    admin = await _setup(api_client)
    library_id = await _make_library(db_sessionmaker)
    poster = _png(tmp_path / "poster.png")
    item_id = await _add_item(db_sessionmaker, library_id, title="The Matrix", poster=poster)
    image_id = await _first_image_id(db_sessionmaker, item_id)
    other_item = await _add_item(
        db_sessionmaker,
        library_id,
        title="Heat",
        poster=_png(tmp_path / "heat.png"),
    )

    default = await api_client.get(f"/api/v1/images/{image_id}", headers=_bearer(admin))
    assert default.status_code == 200, default.text
    assert default.headers["content-type"] == "image/webp"
    with PILImage.open(io.BytesIO(default.content)) as image:
        assert image.format == "WEBP"
        assert image.width == 400
        assert image.height == 300

    jpeg = await api_client.get(
        f"/api/v1/images/{image_id}",
        params={"width": 100, "format": "jpeg"},
        headers=_bearer(admin),
    )
    assert jpeg.status_code == 200
    assert jpeg.headers["content-type"] == "image/jpeg"
    with PILImage.open(io.BytesIO(jpeg.content)) as image:
        assert image.format == "JPEG"
        assert image.width == 100

    cached = await api_client.get(
        f"/api/v1/images/{image_id}",
        params={"width": 100, "format": "jpeg"},
        headers=_bearer(admin),
    )
    assert cached.content == jpeg.content

    assert default.headers["cache-control"].startswith("private")

    # A media token in the query string is enough for an <img> element.
    token_response = await api_client.post(
        "/api/v1/auth/media-tokens",
        json={"item_id": item_id},
        headers=_bearer(admin),
    )
    assert token_response.status_code == 201
    token = token_response.json()["token"]
    with_token = await api_client.get(f"/api/v1/images/{image_id}", params={"token": token})
    assert with_token.status_code == 200

    other_token_response = await api_client.post(
        "/api/v1/auth/media-tokens",
        json={"item_id": other_item},
        headers=_bearer(admin),
    )
    other_token = other_token_response.json()["token"]
    scoped = await api_client.get(f"/api/v1/images/{image_id}", params={"token": other_token})
    assert scoped.status_code == 404

    assert (await api_client.get(f"/api/v1/images/{image_id}")).status_code == 401
    assert (
        await api_client.get("/api/v1/images/does-not-exist", headers=_bearer(admin))
    ).status_code == 404
    assert (
        await api_client.get(
            f"/api/v1/images/{image_id}", params={"width": 5}, headers=_bearer(admin)
        )
    ).status_code == 422
    assert (
        await api_client.get(
            f"/api/v1/images/{image_id}",
            params={"format": "gif"},
            headers=_bearer(admin),
        )
    ).status_code == 422
