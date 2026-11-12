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
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession


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

    # 3. New command line contains correct -ss, -start_number, and -output_ts_offset
    cmd = session.cmd
    assert "-ss" in cmd
    assert cmd[cmd.index("-ss") + 1] == "12"

    assert "-start_number" in cmd
    assert cmd[cmd.index("-start_number") + 1] == "3"

    assert "-output_ts_offset" in cmd
    assert cmd[cmd.index("-output_ts_offset") + 1] == "12"

    # 4. INV-8: seg_000.m4s was NOT unlinked or deleted
    assert seg0.exists()
    assert seg0.read_bytes() == b"initial_segment_0"


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
