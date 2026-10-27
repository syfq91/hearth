"""HLS fMP4 streaming and session management endpoints."""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.core.deps import SettingsDep, ViewerDep
from app.core.errors import BadGatewayError, ForbiddenError, NotFoundError
from app.services.transcode.session import get_session_manager

router = APIRouter(prefix="/hls", tags=["hls"])

_SEGMENT_RE = re.compile(r"^seg_\d+\.m4s$")
HLS_MIME = "application/vnd.apple.mpegurl"


class SessionStopResponse(BaseModel):
    stopped: bool = True


@router.get(
    "/{session_id}/master.m3u8",
    response_class=Response,
    responses={
        200: {
            "description": "Master HLS playlist",
            "content": {"application/vnd.apple.mpegurl": {"schema": {"type": "string"}}},
        }
    },
)
async def get_master_playlist(
    session_id: str,
    request: Request,
    viewer: ViewerDep,
    settings: SettingsDep,
    token: str | None = Query(default=None),
) -> Response:
    """Return the master playlist for an active HLS session.

    Delays the response until ffmpeg produces the first segment, avoiding 404
    races in client players.
    """
    manager = get_session_manager(settings)
    session = manager.get_session(session_id)
    if session is None:
        raise NotFoundError("Transcoding session not found", code="session_not_found")
    if viewer.user.id != session.user_id and viewer.user.role != "admin":
        raise ForbiddenError("Session access denied", code="session_access_denied")

    session.touch()
    ready = await session.wait_for_first_segment(
        timeout=settings.transcode_first_segment_timeout_seconds
    )
    if not ready:
        raise BadGatewayError(
            "Transcoding failed to produce the initial segment",
            code="transcode_failed",
        )

    # Carry forward media token if present in query string
    token_suffix = f"?token={quote(token, safe='')}" if token else ""
    width = session.plan.target_width or 1920
    height = session.plan.target_height or 1080
    bandwidth = session.plan.target_bitrate or 6_000_000

    master_content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height},CODECS="avc1.640028,mp4a.40.2"\n'
        f"media.m3u8{token_suffix}\n"
    )

    return Response(
        content=master_content,
        media_type=HLS_MIME,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get(
    "/{session_id}/media.m3u8",
    response_class=Response,
    responses={
        200: {
            "description": "Media playlist",
            "content": {"application/vnd.apple.mpegurl": {"schema": {"type": "string"}}},
        }
    },
)
async def get_media_playlist(
    session_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
    token: str | None = Query(default=None),
) -> Response:
    """Return the active media playlist with token-appended segment references."""
    manager = get_session_manager(settings)
    session = manager.get_session(session_id)
    if session is None:
        raise NotFoundError("Transcoding session not found", code="session_not_found")
    if viewer.user.id != session.user_id and viewer.user.role != "admin":
        raise ForbiddenError("Session access denied", code="session_access_denied")

    session.touch()
    m3u8_path = session.output_dir / "media.m3u8"
    if not m3u8_path.exists():
        ready = await session.wait_for_first_segment(
            timeout=settings.transcode_first_segment_timeout_seconds
        )
        if not ready or not m3u8_path.exists():
            raise NotFoundError("Media playlist not available", code="playlist_not_found")

    raw_content = m3u8_path.read_text(encoding="utf-8")
    token_suffix = f"?token={quote(token, safe='')}" if token else ""

    if token_suffix:
        # Append token query param to segment and init references
        lines = []
        for line in raw_content.splitlines():
            if line.startswith("#EXT-X-MAP:URI="):
                # e.g. #EXT-X-MAP:URI="init.mp4"
                line = line.replace('URI="init.mp4"', f'URI="init.mp4{token_suffix}"')
            elif line.endswith(".m4s"):
                line = f"{line}{token_suffix}"
            lines.append(line)
        content = "\n".join(lines) + "\n"
    else:
        content = raw_content

    return Response(
        content=content,
        media_type=HLS_MIME,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get(
    "/{session_id}/init.mp4",
    response_class=FileResponse,
    responses={
        200: {
            "description": "Initialization fMP4 segment",
            "content": {"video/mp4": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def get_init_segment(
    session_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
) -> FileResponse:
    """Return the fMP4 initialization segment."""
    manager = get_session_manager(settings)
    session = manager.get_session(session_id)
    if session is None:
        raise NotFoundError("Transcoding session not found", code="session_not_found")
    if viewer.user.id != session.user_id and viewer.user.role != "admin":
        raise ForbiddenError("Session access denied", code="session_access_denied")

    session.touch()
    init_path = session.output_dir / "init.mp4"
    if not init_path.exists():
        raise NotFoundError("Initialization segment not found", code="segment_not_found")

    return FileResponse(
        path=init_path,
        media_type="video/mp4",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get(
    "/{session_id}/{segment_file}",
    response_class=FileResponse,
    responses={
        200: {
            "description": "Media segment fMP4",
            "content": {"video/iso.segment": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def get_media_segment(
    session_id: str,
    segment_file: str,
    viewer: ViewerDep,
    settings: SettingsDep,
) -> FileResponse:
    """Return a media segment (.m4s)."""
    if not _SEGMENT_RE.match(segment_file):
        raise NotFoundError("Invalid segment filename", code="invalid_segment")

    manager = get_session_manager(settings)
    session = manager.get_session(session_id)
    if session is None:
        raise NotFoundError("Transcoding session not found", code="session_not_found")
    if viewer.user.id != session.user_id and viewer.user.role != "admin":
        raise ForbiddenError("Session access denied", code="session_access_denied")

    session.touch()
    seg_path = session.output_dir / segment_file
    if not seg_path.exists():
        raise NotFoundError("Segment file not found", code="segment_not_found")

    return FileResponse(
        path=seg_path,
        media_type="video/iso.segment",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/{session_id}/stop")
async def stop_hls_session(
    session_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
) -> SessionStopResponse:
    """Explicitly stop an active playback session."""
    manager = get_session_manager(settings)
    session = manager.get_session(session_id)
    if session is None:
        return SessionStopResponse(stopped=True)
    if viewer.user.id != session.user_id and viewer.user.role != "admin":
        raise ForbiddenError("Session access denied", code="session_access_denied")

    await manager.stop_session(session_id)
    return SessionStopResponse(stopped=True)


__all__ = ["router"]
