"""HLS fMP4 streaming and session management endpoints."""

from __future__ import annotations

import math
import re
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.core.deps import SettingsDep, ViewerDep
from app.core.errors import (
    BadGatewayError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.services.transcode.codecs import compute_master_codecs
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
    if session.is_circuit_open():
        raise ServiceUnavailableError(
            "Transcoding circuit breaker open; retry shortly",
            code="transcode_circuit_open",
            retry_after=session.circuit_retry_after(),
        )
    ready = await session.wait_for_first_segment(
        timeout=settings.transcode_first_segment_timeout_seconds
    )
    if not ready:
        if session.is_circuit_open():
            raise ServiceUnavailableError(
                "Transcoding circuit breaker open; retry shortly",
                code="transcode_circuit_open",
                retry_after=session.circuit_retry_after(),
            )
        raise BadGatewayError(
            "Transcoding failed to produce the initial segment",
            code="transcode_failed",
        )

    # Carry forward media token if present in query string
    token_suffix = f"?token={quote(token, safe='')}" if token else ""
    width = session.plan.target_width
    height = session.plan.target_height
    if not width and session.source_video:
        width = session.source_video.width
    if not height and session.source_video:
        height = session.source_video.height
    width = width or 1920
    height = height or 1080

    bandwidth = session.plan.target_bitrate or 6_000_000

    stream_inf_attrs: list[str] = [
        f"BANDWIDTH={bandwidth}",
        f"AVERAGE-BANDWIDTH={bandwidth}",
        f"RESOLUTION={width}x{height}",
    ]

    fps = session.fps or session.source_fps
    if fps and fps > 0:
        stream_inf_attrs.append(f"FRAME-RATE={fps:.3f}")

    codecs_str = compute_master_codecs(session)
    if codecs_str:
        stream_inf_attrs.append(f'CODECS="{codecs_str}"')

    attrs_line = ",".join(stream_inf_attrs)

    master_content = (
        f"#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-STREAM-INF:{attrs_line}\nmedia.m3u8{token_suffix}\n"
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
        if session.is_circuit_open():
            raise ServiceUnavailableError(
                "Transcoding circuit breaker open; retry shortly",
                code="transcode_circuit_open",
                retry_after=session.circuit_retry_after(),
            )
        ready = await session.wait_for_first_segment(
            timeout=settings.transcode_first_segment_timeout_seconds
        )
        if not ready or not m3u8_path.exists():
            if session.is_circuit_open():
                raise ServiceUnavailableError(
                    "Transcoding circuit breaker open; retry shortly",
                    code="transcode_circuit_open",
                    retry_after=session.circuit_retry_after(),
                )
            raise ServiceUnavailableError(
                "Media playlist generation in progress; retry shortly",
                code="transcode_timeout",
                retry_after=1,
            )

    raw_content = m3u8_path.read_text(encoding="utf-8")
    token_suffix = f"?token={quote(token, safe='')}" if token else ""

    if session.duration_seconds > 0:
        duration = session.duration_seconds
        seg_dur = settings.transcode_segment_duration_seconds
        total_segments = max(1, math.ceil(duration / seg_dur))
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:7",
            f"#EXT-X-TARGETDURATION:{seg_dur}",
            "#EXT-X-MEDIA-SEQUENCE:0",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            f'#EXT-X-MAP:URI="init.mp4{token_suffix}"',
        ]
        for i in range(total_segments):
            if i == total_segments - 1:
                d = duration - i * seg_dur
                if d <= 0:
                    d = float(seg_dur)
            else:
                d = float(seg_dur)
            lines.append(f"#EXTINF:{d:.3f},")
            lines.append(f"seg_{i:03d}.m4s{token_suffix}")
        lines.append("#EXT-X-ENDLIST")
        content = "\n".join(lines) + "\n"
    elif token_suffix:
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
    if not (init_path.exists() and init_path.stat().st_size > 0):
        if session.is_circuit_open():
            raise ServiceUnavailableError(
                "Transcoding circuit breaker open; retry shortly",
                code="transcode_circuit_open",
                retry_after=session.circuit_retry_after(),
            )
        ready = await session.wait_for_first_segment(
            timeout=settings.transcode_first_segment_timeout_seconds
        )
        if not ready or not init_path.exists() or init_path.stat().st_size == 0:
            if session.is_circuit_open():
                raise ServiceUnavailableError(
                    "Transcoding circuit breaker open; retry shortly",
                    code="transcode_circuit_open",
                    retry_after=session.circuit_retry_after(),
                )
            raise ServiceUnavailableError(
                "Initialization segment generation in progress; retry shortly",
                code="transcode_timeout",
                retry_after=1,
            )

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
    segment_index = int(segment_file.split(".")[0].split("_")[1])

    if session.duration_seconds > 0:
        total_segments = max(
            1, math.ceil(session.duration_seconds / settings.transcode_segment_duration_seconds)
        )
        if segment_index < 0 or segment_index >= total_segments:
            raise NotFoundError("Segment out of range", code="segment_out_of_range")

    seg_path = session.output_dir / segment_file
    if seg_path.exists() and seg_path.stat().st_size > 0:
        session.record_success()
        return FileResponse(
            path=seg_path,
            media_type="video/iso.segment",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    if session.is_circuit_open():
        raise ServiceUnavailableError(
            "Transcoding circuit breaker open; retry shortly",
            code="transcode_circuit_open",
            retry_after=session.circuit_retry_after(),
        )

    ready = await session.ensure_segment(
        segment_index,
        timeout=settings.transcode_segment_wait_timeout_seconds,
    )
    if not ready or not seg_path.exists() or seg_path.stat().st_size == 0:
        if session.is_circuit_open():
            raise ServiceUnavailableError(
                "Transcoding circuit breaker open; retry shortly",
                code="transcode_circuit_open",
                retry_after=session.circuit_retry_after(),
            )
        raise ServiceUnavailableError(
            "Segment generation in progress; retry shortly",
            code="transcode_timeout",
            retry_after=1,
        )

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
