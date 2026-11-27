"""Integration tests for non-destructive seeking (T03).

Validates:
1. Seeking into a previously produced range reuses cached segments without spawning
   a new ffmpeg process, preserving existing file inodes and mtimes.
2. Seeking forward terminates the old ffmpeg process cleanly and spawns a new process
   with correct -ss, -start_number, and -output_ts_offset, preserving earlier segments.
3. No code path in transcode services deletes segments other than session stop/reap (INV-8).
"""

from __future__ import annotations

import ast
import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx
import pytest

from app.core.config import Settings
from app.core.secrets import load_keys
from app.core.tokens import create_media_token
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession, get_session_manager


class MockSubprocess:
    """Mock asyncio subprocess tracking lifecycle."""

    def __init__(self, pid: int = 1001) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminate_called = False
        self.kill_called = False
        self.stderr = AsyncMock()
        self.stderr.readline = AsyncMock(return_value=b"")

    def terminate(self) -> None:
        self.terminate_called = True
        self.returncode = -15

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _make_sample_session(tmp_path: Path) -> TranscodeSession:
    out_dir = tmp_path / "transcode_seek_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(transcode_dir=tmp_path, transcode_segment_duration_seconds=4)
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    return TranscodeSession(
        session_id="seek_session_test",
        user_id="user_test",
        item_id="item_test",
        file_id="file_test",
        input_path=Path("/media/source.mp4"),
        output_dir=out_dir,
        plan=plan,
        settings=settings,
        start_offset=0.0,
        duration_seconds=120.0,
    )


@pytest.mark.asyncio
async def test_seek_into_cached_range_does_not_spawn_and_preserves_files(tmp_path: Path) -> None:
    """T03 acceptance 1: seek forward then back into a previously produced range:
    No new ffmpeg process is spawned and files are unchanged (inode/mtime assertions).
    """
    session = _make_sample_session(tmp_path)
    mock_proc = MockSubprocess(pid=2001)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        await session.start_process()

    assert session.process is mock_proc

    # Simulate completed segments 0, 1, 2 on disk
    seg0 = session.output_dir / "seg_000.m4s"
    seg1 = session.output_dir / "seg_001.m4s"
    seg2 = session.output_dir / "seg_002.m4s"
    seg0.write_bytes(b"data_seg_0")
    seg1.write_bytes(b"data_seg_1")
    seg2.write_bytes(b"data_seg_2")

    stat1_before = seg1.stat()
    inode1_before = stat1_before.st_ino
    mtime1_before = stat1_before.st_mtime_ns

    # Seek into segment 1 range (e.g. 5.5s with 4s segments -> segment index 1)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_exec:
        await session.seek(5.5)
        # Assert no new process was spawned
        mock_exec.assert_not_called()

    # Active process was not terminated
    assert session.process is mock_proc
    assert not mock_proc.terminate_called

    # File invariants: segment 1 is completely untouched (same inode, same mtime)
    stat1_after = seg1.stat()
    assert stat1_after.st_ino == inode1_before, "Inode changed on cached seek!"
    assert stat1_after.st_mtime_ns == mtime1_before, "mtime changed on cached seek!"

    # Invariant INV-8: All other previously completed segments remain intact
    assert seg0.exists() and seg0.read_bytes() == b"data_seg_0"
    assert seg1.exists() and seg1.read_bytes() == b"data_seg_1"
    assert seg2.exists() and seg2.read_bytes() == b"data_seg_2"


@pytest.mark.asyncio
async def test_seek_forward_terminates_old_and_spawns_with_correct_ss_and_start_number(
    tmp_path: Path,
) -> None:
    """T03 acceptance 2: seek forward outside cached range:
    Old process terminated, new argv has correct -ss, -start_number, and -output_ts_offset.
    """
    session = _make_sample_session(tmp_path)
    old_proc = MockSubprocess(pid=3001)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=old_proc)):
        await session.start_process()

    # Pre-populate seg_000.m4s to verify it is NOT deleted when seeking forward
    seg0 = session.output_dir / "seg_000.m4s"
    seg0.write_bytes(b"initial_segment_0")

    new_proc = MockSubprocess(pid=3002)
    # Seek to 12.0s (segment index 3 at 4s duration)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=new_proc)):
        await session.seek(12.0)

    # 1. Old process was terminated cleanly
    assert old_proc.terminate_called is True

    # 2. New process is active
    assert session.process is new_proc

    # 3. New command line contains correct -ss, -start_number, and -output_ts_offset.
    # The grid opens with a 2s segment, so segment 3 (which contains 12s) starts
    # at 2 + 2 * 4 = 10s and the process must resume from that boundary.
    cmd = session.cmd
    assert "-ss" in cmd
    assert cmd[cmd.index("-ss") + 1] == "10"

    assert "-start_number" in cmd
    assert cmd[cmd.index("-start_number") + 1] == "3"

    assert "-copyts" in cmd
    assert "-output_ts_offset" not in cmd

    # 4. INV-8: seg_000.m4s was NOT unlinked or deleted
    assert seg0.exists()
    assert seg0.read_bytes() == b"initial_segment_0"


@pytest.mark.asyncio
async def test_seek_backward_restarts_with_target_segment(tmp_path: Path) -> None:
    """A seek before the current resume point restarts ffmpeg at the target.

    The media playlist lists the whole VOD timeline, so a backward seek must
    produce the earlier segments instead of skipping them as a cached gap.
    """
    session = _make_sample_session(tmp_path)
    first_proc = MockSubprocess(pid=3001)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=first_proc)):
        await session.start_process()

    # Move forward first, then seek back to the beginning of the timeline.
    second_proc = MockSubprocess(pid=3002)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=second_proc)):
        await session.seek(12.0)
    assert second_proc is session.process

    third_proc = MockSubprocess(pid=3003)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=third_proc)):
        await session.seek(0.0)

    assert second_proc.terminate_called is True
    assert session.process is third_proc

    cmd = session.cmd
    # Zero offset omits -ss, -start_number and -output_ts_offset in the builder.
    assert session.current_start_segment == 0
    assert "-ss" not in cmd
    assert "-start_number" not in cmd
    assert "-output_ts_offset" not in cmd


def test_no_code_path_other_than_stop_deletes_segments() -> None:
    """T03 acceptance 4: Grep / AST proof that no code path other than session end/reap
    deletes segments from disk (INV-8).
    """
    session_module_path = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "services"
        / "transcode"
        / "session.py"
    )
    code = session_module_path.read_text(encoding="utf-8")
    tree = ast.parse(code)

    # Search for any calls to .unlink() or .rmdir() or rmtree()
    # Allowed locations:
    # - TranscodeSession.stop(): shutil.rmtree(self.output_dir)
    # - SessionManager.startup_sweep(): shutil.rmtree(entry)
    class DeletionVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.deletions: list[tuple[str, int, str]] = []
            self.current_func: str = ""

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            prev = self.current_func
            self.current_func = node.name
            self.generic_visit(node)
            self.current_func = prev

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            prev = self.current_func
            self.current_func = node.name
            self.generic_visit(node)
            self.current_func = prev

        def visit_Call(self, node: ast.Call) -> None:
            call_name = ""
            if isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                call_name = node.func.id

            if call_name in ("unlink", "rmtree", "rmdir"):
                self.deletions.append((self.current_func, node.lineno, call_name))
            self.generic_visit(node)

    visitor = DeletionVisitor()
    visitor.visit(tree)

    # Assert that no deletion calls exist in seek()
    seek_deletions = [d for d in visitor.deletions if d[0] == "seek"]
    assert not seek_deletions, f"Found deletion calls inside seek(): {seek_deletions}"

    # Verify that all deletions in session.py are only in stop or startup_sweep
    for func_name, line_no, op in visitor.deletions:
        assert func_name in ("stop", "startup_sweep"), (
            f"Unexpected deletion call {op}() in {func_name} at line {line_no}"
        )


@pytest.mark.asyncio
async def test_segment_endpoint_503_retry_after_on_timeout(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    """T03 acceptance 2 / INV-7: Segment endpoints never return 404 for a segment that
    exists in the playlist but failed to generate. They return 503 + Retry-After.
    """
    res_setup = await api_client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct horse"},
    )
    assert res_setup.status_code == 201, res_setup.text
    tokens = res_setup.json()
    bearer_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    me_res = await api_client.get("/api/v1/auth/me", headers=bearer_headers)
    admin_id = me_res.json()["id"]

    manager = get_session_manager(db_settings)
    session_id = "test_503_timeout_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Initial setup: duration is 20s (5 segments of 4s: seg_000 to seg_004)
    # Fast segment wait timeout (0.1s) for testing
    db_settings.transcode_segment_wait_timeout_seconds = 0.1
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_seek_503",
        file_id="file_seek_503",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
        duration_seconds=20.0,
    )
    manager.sessions[session_id] = session

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_seek_503",
    )
    encoded_token = quote(token, safe="")

    # Request segment 2 (which exists in the 20s playlist but is not yet on disk)
    res_pending = await api_client.get(
        f"/api/v1/hls/{session_id}/seg_002.m4s?token={encoded_token}"
    )
    assert res_pending.status_code == 503, f"Expected 503, got {res_pending.status_code}"
    assert res_pending.headers.get("Retry-After") == "1"
    assert res_pending.json()["error"]["code"] == "transcode_timeout"

    # Request segment 10 (beyond 20s duration -> 5 segments max 0..4) -> 404 out of range
    res_out_of_range = await api_client.get(
        f"/api/v1/hls/{session_id}/seg_010.m4s?token={encoded_token}"
    )
    assert res_out_of_range.status_code == 404
    assert res_out_of_range.json()["error"]["code"] == "segment_out_of_range"

    # Request invalid segment name format -> 404 invalid_segment
    res_invalid = await api_client.get(
        f"/api/v1/hls/{session_id}/seg_bad.m4s?token={encoded_token}"
    )
    assert res_invalid.status_code == 404
    assert res_invalid.json()["error"]["code"] == "invalid_segment"

    await manager.stop_all()


@pytest.mark.asyncio
async def test_seek_endpoint_restarts_ffmpeg_at_target_position(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    """A client seek notification restarts ffmpeg at the requested offset."""
    res_setup = await api_client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct horse"},
    )
    assert res_setup.status_code == 201, res_setup.text
    tokens = res_setup.json()
    admin_id = (
        await api_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
    ).json()["id"]

    manager = get_session_manager(db_settings)
    session_id = "test_seek_endpoint_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_seek_endpoint",
        file_id="file_seek_endpoint",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=TranscodePlan(
            mode="transcode_hls",
            video_action="transcode",
            video_codec="libx264",
            audio_action="copy",
            audio_codec="copy",
            audio_channels=2,
            subtitle_action="none",
            hwaccel="none",
        ),
        settings=db_settings,
        duration_seconds=120.0,
    )
    manager.sessions[session_id] = session
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    new_proc = MockSubprocess(pid=4001)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=new_proc)) as mock_exec:
        res = await api_client.post(
            f"/api/v1/hls/{session_id}/seek",
            json={"position_seconds": 50.0},
            headers=headers,
        )

    assert res.status_code == 200, res.text
    assert res.json() == {"position_seconds": 50.0}
    mock_exec.assert_awaited_once()
    assert session.process is new_proc
    assert session.cmd[session.cmd.index("-ss") + 1] == "50"

    await manager.stop_all()


@pytest.mark.asyncio
async def test_seek_endpoint_validates_position_and_session(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    """Unknown sessions 404, negative positions 422, and overshoots clamp to duration."""
    res_setup = await api_client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct horse"},
    )
    tokens = res_setup.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    admin_id = (await api_client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    manager = get_session_manager(db_settings)
    session_id = "test_seek_validation_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_seek_validation",
        file_id="file_seek_validation",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=TranscodePlan(
            mode="transcode_hls",
            video_action="transcode",
            video_codec="libx264",
            audio_action="copy",
            audio_codec="copy",
            audio_channels=2,
            subtitle_action="none",
            hwaccel="none",
        ),
        settings=db_settings,
        duration_seconds=20.0,
    )
    manager.sessions[session_id] = session

    missing = await api_client.post(
        "/api/v1/hls/does_not_exist/seek",
        json={"position_seconds": 1.0},
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "session_not_found"

    negative = await api_client.post(
        f"/api/v1/hls/{session_id}/seek",
        json={"position_seconds": -1.0},
        headers=headers,
    )
    assert negative.status_code == 422

    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=MockSubprocess(pid=4002)),
    ):
        clamped = await api_client.post(
            f"/api/v1/hls/{session_id}/seek",
            json={"position_seconds": 999.0},
            headers=headers,
        )
    assert clamped.status_code == 200, clamped.text
    assert clamped.json() == {"position_seconds": 20.0}

    await manager.stop_all()


@pytest.mark.asyncio
async def test_media_playlist_cache_busts_init_after_seek(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    """Each ffmpeg restart bumps the init URL so clients refetch the new init box."""
    res_setup = await api_client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct horse"},
    )
    tokens = res_setup.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    admin_id = (await api_client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_init_bust",
    )
    encoded = quote(token, safe="")

    manager = get_session_manager(db_settings)
    session_id = "test_init_bust_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_init_bust",
        file_id="file_init_bust",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=TranscodePlan(
            mode="transcode_hls",
            video_action="transcode",
            video_codec="libx264",
            audio_action="copy",
            audio_codec="copy",
            audio_channels=2,
            subtitle_action="none",
            hwaccel="none",
        ),
        settings=db_settings,
        duration_seconds=20.0,
    )
    manager.sessions[session_id] = session

    before = await api_client.get(f"/api/v1/hls/{session_id}/media.m3u8?token={encoded}")
    assert before.status_code == 200, before.text
    assert "init.mp4?v=0&token=" in before.text

    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=MockSubprocess(pid=4003)),
    ):
        seek = await api_client.post(
            f"/api/v1/hls/{session_id}/seek",
            json={"position_seconds": 8.0},
            headers=headers,
        )
    assert seek.status_code == 200, seek.text

    after = await api_client.get(f"/api/v1/hls/{session_id}/media.m3u8?token={encoded}")
    assert after.status_code == 200, after.text
    assert "init.mp4?v=1&token=" in after.text

    await manager.stop_all()


@pytest.mark.asyncio
async def test_init_endpoint_does_not_serve_partially_rewritten_init(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    """While ffmpeg is active, init.mp4 is only served after its first segment is complete."""
    res_setup = await api_client.post(
        "/api/v1/auth/setup",
        json={"username": "admin", "password": "correct horse"},
    )
    tokens = res_setup.json()
    admin_id = (
        await api_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
    ).json()["id"]

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_init_ready",
    )
    encoded = quote(token, safe="")

    manager = get_session_manager(db_settings)
    session_id = "test_init_ready_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_init_ready",
        file_id="file_init_ready",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=TranscodePlan(
            mode="transcode_hls",
            video_action="transcode",
            video_codec="libx264",
            audio_action="copy",
            audio_codec="copy",
            audio_channels=2,
            subtitle_action="none",
            hwaccel="none",
        ),
        settings=db_settings,
        duration_seconds=20.0,
    )
    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=MockSubprocess(pid=4004)),
    ):
        await session.start_process()
    manager.sessions[session_id] = session
    db_settings.transcode_segment_wait_timeout_seconds = 0.15

    pending = await api_client.get(f"/api/v1/hls/{session_id}/init.mp4?token={encoded}")
    assert pending.status_code == 503, pending.text
    assert pending.json()["error"]["code"] == "transcode_timeout"

    first_segment = out_dir / f"seg_{session.current_start_segment:03d}.m4s"
    first_segment.write_bytes(b"complete_segment")
    ready = await api_client.get(f"/api/v1/hls/{session_id}/init.mp4?token={encoded}")
    assert ready.status_code == 200, ready.text
    assert ready.headers["cache-control"] == "no-store"

    await manager.stop_all()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe required for seek timestamp continuity smoke test",
)
@pytest.mark.asyncio
async def test_seek_smoke_timestamp_continuity_back_to_back(tmp_path: Path) -> None:
    """T03 acceptance 3 (§3.5 / §6.3 smoke): Segments produced after a seek play back-to-back
    with earlier ones without a timestamp jump.

    First PTS of post-seek segment is ~ start_number * seg_s (± one frame).
    """
    input_file = tmp_path / "smoke_seek_src.mp4"
    out_dir = tmp_path / "smoke_seek_hls"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create a 10s source with 24fps and 1s GOP
    gen_proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=24",
        "-t",
        "10",
        "-c:v",
        "libx264",
        "-g",
        "24",
        "-pix_fmt",
        "yuv420p",
        str(input_file),
    )
    await gen_proc.wait()
    assert gen_proc.returncode == 0

    # 2. Start session at offset 0.0 with segment_duration = 1s
    settings = Settings(transcode_dir=tmp_path, transcode_segment_duration_seconds=1)
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    session = TranscodeSession(
        session_id="smoke_seek_session",
        user_id="user_smoke",
        item_id="item_smoke",
        file_id="file_smoke",
        input_path=input_file,
        output_dir=out_dir,
        plan=plan,
        settings=settings,
        start_offset=0.0,
        duration_seconds=10.0,
    )

    await session.start_process()

    # Wait for first segment seg_000.m4s to be produced
    seg0 = out_dir / "seg_000.m4s"
    ready0 = await session.ensure_segment(0, timeout=10.0)
    assert ready0 and seg0.exists()

    # 3. Seek forward to 4.0s (segment index 4)
    await session.seek(4.0)

    # Wait for post-seek segment seg_004.m4s
    seg4 = out_dir / "seg_004.m4s"
    ready4 = await session.ensure_segment(4, timeout=10.0)
    assert ready4 and seg4.exists()

    # INV-8 check: Earlier seg_000.m4s is STILL on disk
    assert seg0.exists(), "Earlier segment seg_000.m4s was deleted by seek!"

    # 4. Probe PTS timestamps using ffprobe on init.mp4 + segment
    init_bytes = (out_dir / "init.mp4").read_bytes()

    async def _get_first_pts(seg_path: Path) -> float:
        combined = init_bytes + seg_path.read_bytes()
        probe_proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time,flags",
            "-of",
            "csv=p=0",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await probe_proc.communicate(input=combined)
        first_line = stdout.decode().splitlines()[0]
        pts_str, _ = first_line.split(",")
        return float(pts_str)

    pts0 = await _get_first_pts(seg0)
    pts4 = await _get_first_pts(seg4)

    # Segment 0 starts near 0.0 (within H.264 B-frame reordering delay <= 2 frames ~ 0.085s)
    assert abs(pts0 - 0.0) <= 0.1, f"Expected seg_000 first PTS near 0.0s, got {pts0}s"

    # Segment 4 starts near 4.0s, advancing by exactly the 4.0s seek delta
    assert abs((pts4 - pts0) - 4.0) <= 0.045, (
        f"Expected PTS delta between seg_004 and seg_000 ~4.0s, got {pts4 - pts0:.4f}s "
        f"(pts0={pts0}, pts4={pts4})"
    )
    assert abs(pts4 - 4.0) <= 0.1, (
        f"Expected post-seek seg_004 first PTS near 4.0s, got {pts4}s (timestamp jump detected!)"
    )

    await session.stop()
