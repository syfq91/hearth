"""Integration tests for TV show API endpoints."""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Episode,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Movie,
    Season,
    Series,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
NOW = datetime.now(UTC)


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _add_user(
    client: httpx.AsyncClient, admin: dict[str, str], username: str, library_ids: list[str]
) -> dict[str, str]:
    created = await client.post(
        "/api/v1/users",
        json={
            "username": username,
            "password": "correct horse",
            "role": "user",
            "library_ids": library_ids,
        },
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_tv_library(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], name: str = "TV Shows"
) -> str:
    root = settings.media_dir / f"tv_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    async with sessionmaker() as session:
        library = Library(name=name, type="tv")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


async def _seed_show_hierarchy(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    *,
    title: str = "Breaking Bad",
    season_counts: dict[int, int] | None = None,
) -> dict[str, str]:
    """Seed a show with seasons and episodes. Returns mapping of name to item_id."""
    if season_counts is None:
        season_counts = {1: 3, 2: 2}
    ids: dict[str, str] = {}
    async with sessionmaker() as session:
        series_item = MediaItem(
            library_id=library_id,
            type="series",
            title=title,
            sort_title=sort_title(title),
            year=2008,
            match_state="matched",
            overview=f"Overview of {title}",
            runtime_seconds=2700,
        )
        session.add(series_item)
        await session.flush()
        session.add(
            Series(
                item_id=series_item.id,
                original_title=title,
                status="Ended",
                network="AMC",
            )
        )
        ids["series"] = series_item.id

        for s_num, ep_count in season_counts.items():
            s_title = "Specials" if s_num == 0 else f"Season {s_num}"
            s_item = MediaItem(
                library_id=library_id,
                type="season",
                title=s_title,
                sort_title=sort_title(s_title),
                match_state="matched",
            )
            session.add(s_item)
            await session.flush()
            session.add(
                Season(
                    item_id=s_item.id,
                    series_id=series_item.id,
                    season_number=s_num,
                    air_date="2008-01-20",
                )
            )
            ids[f"season_{s_num}"] = s_item.id

            for ep_num in range(1, ep_count + 1):
                ep_title = f"{title} S{s_num:02d}E{ep_num:02d}"
                ep_item = MediaItem(
                    library_id=library_id,
                    type="episode",
                    title=ep_title,
                    sort_title=sort_title(ep_title),
                    year=2008,
                    match_state="matched",
                    overview=f"Episode {ep_num} overview",
                    runtime_seconds=2700,
                )
                session.add(ep_item)
                await session.flush()

                media_file = MediaFile(
                    item_id=ep_item.id,
                    path=f"/media/{title}/S{s_num:02d}E{ep_num:02d}_{uuid.uuid4().hex[:8]}.mkv",
                    size_bytes=100_000,
                    mtime=1.0,
                    partial_hash=uuid.uuid4().hex * 2,
                    container="matroska",
                    duration_seconds=2700.0,
                )
                session.add(media_file)
                await session.flush()

                session.add(
                    Episode(
                        item_id=ep_item.id,
                        series_id=series_item.id,
                        season_id=s_item.id,
                        season_number=s_num,
                        episode_number=ep_num,
                        air_date="2008-01-20",
                        file_id=media_file.id,
                    )
                )
                ids[f"s{s_num}e{ep_num}"] = ep_item.id

        await session.commit()
    return ids


async def test_tv_library_browse_and_series_seasons_episodes_detail(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    tokens = await _setup(api_client)
    headers = _bearer(tokens)
    lib_id = await _create_tv_library(db_settings, db_sessionmaker)
    ids = await _seed_show_hierarchy(db_sessionmaker, lib_id, title="The Wire")

    # 1. Browse TV library -> returns series item
    browse_resp = await api_client.get(f"/api/v1/libraries/{lib_id}/items", headers=headers)
    assert browse_resp.status_code == 200, browse_resp.text
    items = browse_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == ids["series"]
    assert items[0]["type"] == "series"
    assert items[0]["title"] == "The Wire"

    # 2. Series detail
    series_resp = await api_client.get(f"/api/v1/series/{ids['series']}", headers=headers)
    assert series_resp.status_code == 200, series_resp.text
    series_data = series_resp.json()
    assert series_data["id"] == ids["series"]
    assert series_data["title"] == "The Wire"
    assert series_data["network"] == "AMC"
    assert series_data["status"] == "Ended"
    assert series_data["total_seasons"] == 2
    assert series_data["total_episodes"] == 5
    assert len(series_data["seasons"]) == 2

    # 3. List seasons for series
    seasons_resp = await api_client.get(f"/api/v1/series/{ids['series']}/seasons", headers=headers)
    assert seasons_resp.status_code == 200, seasons_resp.text
    seasons_list = seasons_resp.json()
    assert len(seasons_list) == 2
    assert seasons_list[0]["season_number"] == 1
    assert seasons_list[0]["episode_count"] == 3
    assert seasons_list[1]["season_number"] == 2
    assert seasons_list[1]["episode_count"] == 2

    # 4. Season detail with constituent episodes
    s1_resp = await api_client.get(f"/api/v1/seasons/{ids['season_1']}", headers=headers)
    assert s1_resp.status_code == 200, s1_resp.text
    s1_data = s1_resp.json()
    assert s1_data["season_number"] == 1
    assert len(s1_data["episodes"]) == 3
    assert s1_data["episodes"][0]["episode_number"] == 1
    assert s1_data["episodes"][0]["id"] == ids["s1e1"]

    # 5. List episodes for season
    ep_list_resp = await api_client.get(
        f"/api/v1/seasons/{ids['season_1']}/episodes", headers=headers
    )
    assert ep_list_resp.status_code == 200, ep_list_resp.text
    assert len(ep_list_resp.json()) == 3

    # 6. Episode detail
    ep_resp = await api_client.get(f"/api/v1/episodes/{ids['s1e1']}", headers=headers)
    assert ep_resp.status_code == 200, ep_resp.text
    ep_data = ep_resp.json()
    assert ep_data["id"] == ids["s1e1"]
    assert ep_data["series_title"] == "The Wire"
    assert ep_data["season_number"] == 1
    assert ep_data["episode_number"] == 1
    assert len(ep_data["files"]) == 1


async def test_next_up_progression_across_seasons_and_out_of_order(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    tokens = await _setup(api_client)
    headers = _bearer(tokens)
    lib_id = await _create_tv_library(db_settings, db_sessionmaker)
    # 2 seasons: season 1 (2 episodes), season 2 (2 episodes)
    ids = await _seed_show_hierarchy(
        db_sessionmaker, lib_id, title="Succession", season_counts={1: 2, 2: 2}
    )

    # Initially, next up is S01E01
    next_resp = await api_client.get(f"/api/v1/series/{ids['series']}/next-up", headers=headers)
    assert next_resp.status_code == 200, next_resp.text
    assert next_resp.json()["id"] == ids["s1e1"]

    # Watch S01E01
    mark_resp = await api_client.post(
        f"/api/v1/items/{ids['s1e1']}/watched",
        json={"watched": True},
        headers=headers,
    )
    assert mark_resp.status_code == 200

    # Next up is S01E02
    next_resp = await api_client.get(f"/api/v1/series/{ids['series']}/next-up", headers=headers)
    assert next_resp.status_code == 200
    assert next_resp.json()["id"] == ids["s1e2"]

    # Watch S01E02 (Season 1 finale) -> Should cross season boundary to S02E01!
    mark_resp = await api_client.post(
        f"/api/v1/items/{ids['s1e2']}/watched",
        json={"watched": True},
        headers=headers,
    )
    assert mark_resp.status_code == 200

    next_resp = await api_client.get(f"/api/v1/series/{ids['series']}/next-up", headers=headers)
    assert next_resp.status_code == 200
    assert next_resp.json()["id"] == ids["s2e1"]
    assert next_resp.json()["season_number"] == 2
    assert next_resp.json()["episode_number"] == 1

    # Out-of-order test: User skips S02E01 and watches S02E02 (series finale)
    mark_resp = await api_client.post(
        f"/api/v1/items/{ids['s2e2']}/watched",
        json={"watched": True},
        headers=headers,
    )
    assert mark_resp.status_code == 200

    # Because S02E02 was finale and S02E01 was skipped, next up should be the unwatched S02E01!
    next_resp = await api_client.get(f"/api/v1/series/{ids['series']}/next-up", headers=headers)
    assert next_resp.status_code == 200
    assert next_resp.json()["id"] == ids["s2e1"]

    # Now watch S02E01 -> All episodes watched
    mark_resp = await api_client.post(
        f"/api/v1/items/{ids['s2e1']}/watched",
        json={"watched": True},
        headers=headers,
    )
    assert mark_resp.status_code == 200

    # Next up returns 404 (all watched)
    next_resp = await api_client.get(f"/api/v1/series/{ids['series']}/next-up", headers=headers)
    assert next_resp.status_code == 404


async def test_mark_season_and_series_watched_endpoints(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    tokens = await _setup(api_client)
    headers = _bearer(tokens)
    lib_id = await _create_tv_library(db_settings, db_sessionmaker)
    ids = await _seed_show_hierarchy(
        db_sessionmaker, lib_id, title="Severance", season_counts={1: 3, 2: 2}
    )

    # Mark Season 1 watched
    resp = await api_client.post(
        f"/api/v1/seasons/{ids['season_1']}/watched",
        json={"watched": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["watched"] is True

    # Episodes in Season 1 should now be watched
    for ep_id in (ids["s1e1"], ids["s1e2"], ids["s1e3"]):
        ep_resp = await api_client.get(f"/api/v1/episodes/{ep_id}", headers=headers)
        assert ep_resp.json()["watch_state"]["watched"] is True

    # Season 2 episodes remain unwatched
    for ep_id in (ids["s2e1"], ids["s2e2"]):
        ep_resp = await api_client.get(f"/api/v1/episodes/{ep_id}", headers=headers)
        assert ep_resp.json()["watch_state"]["watched"] is False

    # Next up should be S02E01
    next_resp = await api_client.get(f"/api/v1/series/{ids['series']}/next-up", headers=headers)
    assert next_resp.status_code == 200
    assert next_resp.json()["id"] == ids["s2e1"]

    # Now mark the entire series watched
    series_watched_resp = await api_client.post(
        f"/api/v1/series/{ids['series']}/watched",
        json={"watched": True},
        headers=headers,
    )
    assert series_watched_resp.status_code == 200
    assert series_watched_resp.json()["watched"] is True

    # Now all Season 2 episodes are watched as well
    for ep_id in (ids["s2e1"], ids["s2e2"]):
        ep_resp = await api_client.get(f"/api/v1/episodes/{ep_id}", headers=headers)
        assert ep_resp.json()["watch_state"]["watched"] is True

    # Mark Season 1 unwatched
    unwatch_resp = await api_client.post(
        f"/api/v1/seasons/{ids['season_1']}/watched",
        json={"watched": False},
        headers=headers,
    )
    assert unwatch_resp.status_code == 200
    assert unwatch_resp.json()["watched"] is False

    # Season 1 episodes should now be unwatched
    for ep_id in (ids["s1e1"], ids["s1e2"], ids["s1e3"]):
        ep_resp = await api_client.get(f"/api/v1/episodes/{ep_id}", headers=headers)
        assert ep_resp.json()["watch_state"]["watched"] is False


async def test_continue_watching_merges_movies_and_episodes(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    tokens = await _setup(api_client)
    headers = _bearer(tokens)

    # 1. Movie library with movie
    movie_root = db_settings.media_dir / f"movies_{uuid.uuid4().hex[:8]}"
    movie_root.mkdir(parents=True, exist_ok=True)
    async with db_sessionmaker() as session:
        movie_lib = Library(name="Movies", type="movie")
        movie_lib.paths.append(LibraryPath(path=str(movie_root)))
        session.add(movie_lib)
        await session.flush()
        movie_item = MediaItem(
            library_id=movie_lib.id,
            type="movie",
            title="Inception",
            sort_title=sort_title("Inception"),
            year=2010,
            runtime_seconds=8880,
            match_state="matched",
        )
        session.add(movie_item)
        await session.flush()
        session.add(Movie(item_id=movie_item.id))
        movie_file = MediaFile(
            item_id=movie_item.id,
            path=f"/media/inception_{uuid.uuid4().hex[:8]}.mkv",
            size_bytes=1000,
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
        )
        session.add(movie_file)
        await session.commit()
        movie_id = movie_item.id

    # 2. TV library with show and episode
    tv_lib_id = await _create_tv_library(db_settings, db_sessionmaker)
    tv_ids = await _seed_show_hierarchy(
        db_sessionmaker, tv_lib_id, title="Dark", season_counts={1: 2}
    )
    episode_id = tv_ids["s1e1"]

    # Partially watch movie (position: 30000 ms, duration: 8880000 ms)
    movie_prog = await api_client.post(
        f"/api/v1/items/{movie_id}/progress",
        json={"position_ms": 30000, "duration_ms": 8880000, "state": "paused"},
        headers=headers,
    )
    assert movie_prog.status_code == 200

    # Partially watch episode (position: 60000 ms, duration: 2700000 ms) later
    ep_prog = await api_client.post(
        f"/api/v1/items/{episode_id}/progress",
        json={"position_ms": 60000, "duration_ms": 2700000, "state": "paused"},
        headers=headers,
    )
    assert ep_prog.status_code == 200

    # Continue watching shelf should contain both, with most recently watched (episode) first
    cw_resp = await api_client.get("/api/v1/home/continue-watching", headers=headers)
    assert cw_resp.status_code == 200, cw_resp.text
    cw_items = cw_resp.json()["items"]
    assert len(cw_items) >= 2
    cw_ids = [item["id"] for item in cw_items[:2]]
    assert cw_ids == [episode_id, movie_id]

    # Shows Next-Up endpoint should list the active series
    shows_next_resp = await api_client.get("/api/v1/shows/next-up", headers=headers)
    assert shows_next_resp.status_code == 200, shows_next_resp.text
    next_shelf = shows_next_resp.json()["items"]
    assert len(next_shelf) >= 1
    assert next_shelf[0]["series_title"] == "Dark"


async def test_tv_access_control_and_user_isolation(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    tokens = await _setup(api_client)
    admin_headers = _bearer(tokens)

    # Create TV library
    tv_lib_id = await _create_tv_library(db_settings, db_sessionmaker, name="Restricted TV")
    ids = await _seed_show_hierarchy(db_sessionmaker, tv_lib_id, title="Lost", season_counts={1: 2})

    # Create user with access to TV library
    alice_tokens = await _add_user(api_client, tokens, "alice_tv", library_ids=[tv_lib_id])
    alice_headers = _bearer(alice_tokens)

    # Create user WITHOUT access to TV library
    bob_tokens = await _add_user(api_client, tokens, "bob_no_tv", library_ids=[])
    bob_headers = _bearer(bob_tokens)

    # Bob cannot access series, season, episode, or next-up (all return 404)
    assert (
        await api_client.get(f"/api/v1/series/{ids['series']}", headers=bob_headers)
    ).status_code == 404
    assert (
        await api_client.get(f"/api/v1/seasons/{ids['season_1']}", headers=bob_headers)
    ).status_code == 404
    assert (
        await api_client.get(f"/api/v1/episodes/{ids['s1e1']}", headers=bob_headers)
    ).status_code == 404
    assert (
        await api_client.get(f"/api/v1/series/{ids['series']}/next-up", headers=bob_headers)
    ).status_code == 404
    assert (
        await api_client.get(f"/api/v1/libraries/{tv_lib_id}/items", headers=bob_headers)
    ).status_code == 404

    # Alice CAN access
    assert (
        await api_client.get(f"/api/v1/series/{ids['series']}", headers=alice_headers)
    ).status_code == 200

    # User isolation: Alice watches S1E1
    await api_client.post(
        f"/api/v1/items/{ids['s1e1']}/watched",
        json={"watched": True},
        headers=alice_headers,
    )

    # Alice's next up is S1E2
    alice_next = await api_client.get(
        f"/api/v1/series/{ids['series']}/next-up", headers=alice_headers
    )
    assert alice_next.status_code == 200
    assert alice_next.json()["id"] == ids["s1e2"]

    # Admin's next up remains S1E1 (unaffected by Alice)
    admin_next = await api_client.get(
        f"/api/v1/series/{ids['series']}/next-up", headers=admin_headers
    )
    assert admin_next.status_code == 200
    assert admin_next.json()["id"] == ids["s1e1"]
