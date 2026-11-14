"""HLS fMP4 streaming and session management endpoints."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from app.core.deps import SettingsDep, Viewer, ViewerDep
from app.core.errors import (
    BadGatewayError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.services.subtitles import extract_embedded_subtitle
from app.services.transcode.codecs import compute_master_codecs
from app.services.transcode.session import SessionManager, TranscodeSession, get_session_manager

router = APIRouter(prefix="/hls", tags=["hls"])

_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$|^[a-zA-Z0-9_-]{1,64}$"
)
_SEGMENT_RE = re.compile(r"^(init\.mp4|seg_\d{1,6}\.(m4s|ts))$")
HLS_MIME = "application/vnd.apple.mpegurl"


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id):
        raise BadRequestError("Invalid session ID format", code="invalid_session_id")


def _resolve_session_path(session_dir: Path, filename: str) -> Path:
    resolved_dir = session_dir.resolve()
    resolved_path = (session_dir / filename).resolve()
    if not resolved_path.is_relative_to(resolved_dir):
        raise ForbiddenError("Path traversal detected", code="forbidden_path")
    return resolved_path


def _get_validated_session(
    session_id: str,
    manager: SessionManager,
    viewer: Viewer,
) -> TranscodeSession:
    _validate_session_id(session_id)
    session = manager.get_session(session_id)
    if session is None:
        raise NotFoundError("Transcoding session not found", code="session_not_found")
    if viewer.user.id != session.user_id and viewer.user.role != "admin":
        raise ForbiddenError("Session access denied", code="session_access_denied")
    if viewer.session_id and viewer.session_id != session.id:
        raise ForbiddenError("Media token not valid for this session", code="invalid_media_token")
    if viewer.item_id and viewer.item_id != session.item_id:
        raise ForbiddenError("Media token not valid for this item", code="invalid_media_token")
    return session


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
    session = _get_validated_session(session_id, manager, viewer)

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

    if session.plan.subtitle_action == "sidecar_vtt":
        stream_inf_attrs.append('SUBTITLES="subs"')

    attrs_line = ",".join(stream_inf_attrs)

    master_lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
    if session.plan.subtitle_action == "sidecar_vtt":
        master_lines.append(
            f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="subtitles",DEFAULT=YES,AUTOSELECT=YES,FORCED=NO,URI="subtitles.m3u8{token_suffix}"'
        )
    master_lines.append(f"#EXT-X-STREAM-INF:{attrs_line}")
    master_lines.append(f"media.m3u8{token_suffix}")
    master_content = "\n".join(master_lines) + "\n"

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
    session = _get_validated_session(session_id, manager, viewer)

    session.touch()
    m3u8_path = _resolve_session_path(session.output_dir, "media.m3u8")
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
    "/{session_id}/subtitles.m3u8",
    response_class=Response,
    responses={
        200: {
            "description": "Subtitle HLS playlist",
            "content": {"application/vnd.apple.mpegurl": {"schema": {"type": "string"}}},
        }
    },
)
async def get_subtitle_playlist(
    session_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
    token: str | None = Query(default=None),
) -> Response:
    """Return the WebVTT subtitle playlist for an HLS session."""
    manager = get_session_manager(settings)
    session = _get_validated_session(session_id, manager, viewer)
    session.touch()

    token_suffix = f"?token={quote(token, safe='')}" if token else ""
    seg_dur = max(1.0, session.duration_seconds or 30.0)
    target_dur = math.ceil(seg_dur)

    content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f"#EXT-X-TARGETDURATION:{target_dur}\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXT-X-PLAYLIST-TYPE:VOD\n"
        f"#EXTINF:{seg_dur:.3f},\n"
        f"subtitles.vtt{token_suffix}\n"
        "#EXT-X-ENDLIST\n"
    )
    return Response(
        content=content,
        media_type=HLS_MIME,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get(
    "/{session_id}/subtitles.vtt",
    response_class=Response,
    responses={
        200: {
            "description": "WebVTT subtitle track for HLS session",
            "content": {"text/vtt": {"schema": {"type": "string"}}},
        }
    },
)
async def get_subtitle_vtt(
    session_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
) -> Response:
    """Return the WebVTT subtitle document for an HLS session."""
    manager = get_session_manager(settings)
    session = _get_validated_session(session_id, manager, viewer)
    session.touch()

    vtt_path = _resolve_session_path(session.output_dir, "subtitles.vtt")
    if vtt_path.exists():
        vtt_content = vtt_path.read_text(encoding="utf-8", errors="replace")
    elif session.plan.subtitle_stream_index is not None:
        vtt_content = await extract_embedded_subtitle(
            session.input_path,
            session.plan.subtitle_stream_index,
            fmt="webvtt",
            output_path=vtt_path,
        )
    else:
        vtt_content = "WEBVTT\n\n"

    return Response(
        content=vtt_content,
        media_type="text/vtt",
        headers={"Cache-Control": "public, max-age=3600"},
    )


class TrackedFileResponse(FileResponse):
    """FileResponse that decrements session active_request_count
    when streaming completes or disconnects.
    """

    def __init__(self, session: TranscodeSession, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.session = session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.session.decrement_request()


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
    session = _get_validated_session(session_id, manager, viewer)

    session.increment_request()
    try:
        init_path = _resolve_session_path(session.output_dir, "init.mp4")
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

        return TrackedFileResponse(
            session=session,
            path=init_path,
            media_type="video/mp4",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception:
        session.decrement_request()
        raise


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
        if (
            ".." in segment_file
            or "%" in segment_file
            or "/" in segment_file
            or "\\" in segment_file
            or len(segment_file) > 64
        ):
            raise BadRequestError("Invalid segment filename", code="invalid_segment")
        raise NotFoundError("Invalid segment filename", code="invalid_segment")

    manager = get_session_manager(settings)
    session = _get_validated_session(session_id, manager, viewer)

    session.increment_request()
    try:
        segment_index = int(segment_file.split(".")[0].split("_")[1]) if "_" in segment_file else 0

        if session.duration_seconds > 0:
            total_segments = max(
                1, math.ceil(session.duration_seconds / settings.transcode_segment_duration_seconds)
            )
            if segment_index < 0 or segment_index >= total_segments:
                raise NotFoundError("Segment out of range", code="segment_out_of_range")

        seg_path = _resolve_session_path(session.output_dir, segment_file)
        if seg_path.exists() and seg_path.stat().st_size > 0:
            session.record_success()
            session.record_served_segment(segment_index)
            return TrackedFileResponse(
                session=session,
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

        session.record_served_segment(segment_index)
        return TrackedFileResponse(
            session=session,
            path=seg_path,
            media_type="video/iso.segment",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception:
        session.decrement_request()
        raise


@router.post("/{session_id}/stop")
async def stop_hls_session(
    session_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
) -> SessionStopResponse:
    """Explicitly stop an active playback session."""
    _validate_session_id(session_id)
    manager = get_session_manager(settings)
    session = manager.get_session(session_id)
    if session is None:
        return SessionStopResponse(stopped=True)
    if viewer.user.id != session.user_id and viewer.user.role != "admin":
        raise ForbiddenError("Session access denied", code="session_access_denied")
    if viewer.session_id and viewer.session_id != session.id:
        raise ForbiddenError("Media token not valid for this session", code="invalid_media_token")
    if viewer.item_id and viewer.item_id != session.item_id:
        raise ForbiddenError("Media token not valid for this item", code="invalid_media_token")

    await manager.stop_session(session_id)
    return SessionStopResponse(stopped=True)


__all__ = ["router"]
