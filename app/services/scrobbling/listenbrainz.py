"""ListenBrainz scrobbling service and background job handler."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, MediaItem, User
from app.services.jobs.types import JobContext

logger = logging.getLogger("hearth.scrobbling.listenbrainz")

LISTENBRAINZ_SCROBBLE_JOB_TYPE = "scrobble.listenbrainz"


async def submit_listen(
    token: str,
    track_metadata: dict[str, Any],
    listened_at: int,
    base_url: str = "https://api.listenbrainz.org",
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Submit a single listen to ListenBrainz.

    Raises HTTPStatusError or RequestError on transient errors (5xx, 429, timeouts)
    so durable background job workers retry. Returns False on permanent auth errors (401).
    """
    url = f"{base_url.rstrip('/')}/1/submit-listens"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "User-Agent": "HearthMediaServer/0.1.0 ( https://github.com/hearth-media/hearth )",
    }

    additional_info: dict[str, Any] = {
        "submission_client": "Hearth",
        "submission_client_version": "0.1.0",
    }
    if track_metadata.get("musicbrainz_track_id"):
        additional_info["recording_mbid"] = track_metadata["musicbrainz_track_id"]
        additional_info["track_mbid"] = track_metadata["musicbrainz_track_id"]
    if track_metadata.get("musicbrainz_album_id"):
        additional_info["release_mbid"] = track_metadata["musicbrainz_album_id"]
    if track_metadata.get("musicbrainz_artist_id"):
        additional_info["artist_mbids"] = [track_metadata["musicbrainz_artist_id"]]

    payload = {
        "listen_type": "single",
        "payload": [
            {
                "listened_at": listened_at,
                "track_metadata": {
                    "artist_name": track_metadata.get("artist_name") or "Unknown Artist",
                    "track_name": track_metadata.get("track_name") or "Unknown Track",
                    "release_name": track_metadata.get("release_name"),
                    "additional_info": additional_info,
                },
            }
        ],
    }

    async def _send(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(url, json=payload, headers=headers, timeout=10.0)

    if client is not None:
        res = await _send(client)
    else:
        async with httpx.AsyncClient() as c:
            res = await _send(c)

    if res.status_code == 200:
        return True
    if res.status_code in (400, 401, 403):
        logger.warning("ListenBrainz rejected listen (HTTP %s): %s", res.status_code, res.text)
        return False
    # 429, 5xx, or other status: raise to let job worker retry
    res.raise_for_status()
    return True


async def enqueue_scrobble(
    session: AsyncSession,
    user_id: str,
    track_item_id: str,
    listened_at: int | None = None,
) -> Job | None:
    """Enqueue a durable background scrobble job if the user has configured a token."""
    user = await session.get(User, user_id)
    if user is None or not user.listenbrainz_token:
        return None

    now_ts = listened_at if listened_at is not None else int(datetime.now(UTC).timestamp())
    job = Job(
        type=LISTENBRAINZ_SCROBBLE_JOB_TYPE,
        payload={
            "user_id": user_id,
            "track_id": track_item_id,
            "listened_at": now_ts,
        },
        max_attempts=3,
    )
    session.add(job)
    return job


async def scrobble_listenbrainz_job(context: JobContext) -> dict[str, Any]:
    """Job handler for sending a listen to ListenBrainz."""
    if not context.settings.listenbrainz_enabled:
        return {"status": "skipped", "reason": "disabled"}

    user_id = context.payload.get("user_id")
    track_id = context.payload.get("track_id")
    listened_at = context.payload.get("listened_at") or int(datetime.now(UTC).timestamp())

    if not user_id or not track_id:
        raise ValueError("Missing user_id or track_id in scrobble payload")

    async with context.sessionmaker() as session:
        user = await session.get(User, user_id)
        if user is None or not user.listenbrainz_token:
            return {"status": "skipped", "reason": "no_token"}
        token = user.listenbrainz_token

        track_item = await session.get(MediaItem, track_id)
        if track_item is None or not track_item.track:
            return {"status": "skipped", "reason": "track_not_found"}

        tr = track_item.track
        track_name = track_item.title
        artist_name = (
            tr.artist.item.title
            if tr.artist and tr.artist.item
            else (tr.album.album_artist if tr.album and tr.album.album_artist else "Unknown Artist")
        )
        release_name = tr.album.item.title if tr.album and tr.album.item else None

        track_metadata = {
            "track_name": track_name,
            "artist_name": artist_name,
            "release_name": release_name,
            "musicbrainz_track_id": tr.musicbrainz_track_id,
            "musicbrainz_album_id": tr.album.musicbrainz_id if tr.album else None,
            "musicbrainz_artist_id": tr.artist.musicbrainz_id if tr.artist else None,
        }

    success = await submit_listen(
        token=token,
        track_metadata=track_metadata,
        listened_at=int(listened_at),
        base_url=context.settings.listenbrainz_base_url,
    )
    return {"status": "ok" if success else "rejected", "track_id": track_id}
