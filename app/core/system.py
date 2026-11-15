"""Runtime diagnostics used by the health endpoint.

Phase 0 needs to prove that the container has a working ffmpeg and reports
VAAPI availability honestly. The deeper capability probe (profiles, entrypoints
and test encodes) belongs to Phase 7; this module only answers "is the tool
present and does it run".
"""

import asyncio
import re
import sqlite3
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel

ComponentState = Literal["ok", "unavailable", "not_initialized", "error"]
CommandState = Literal["ok", "timeout", "missing"]

_PROBE_TIMEOUT_SECONDS = 5.0
_FFMPEG_VERSION_RE = re.compile(r"ffmpeg version (\S+)")
_VAAPI_DRIVER_RE = re.compile(r"Driver version:\s*(.+)")

type CommandOutput = tuple[int, str, str]


class CommandOutcome(NamedTuple):
    """Result of a bounded subprocess run.

    ``state`` distinguishes a timeout from a missing binary from a completed
    run, because callers such as the hardware capability probe must not treat a
    slow probe as conclusive evidence that a feature is unsupported.
    """

    state: CommandState
    returncode: int
    stdout: str
    stderr: str


class ComponentHealth(BaseModel):
    """Health of a single runtime dependency."""

    status: ComponentState
    detail: str | None = None


class FFmpegHealth(ComponentHealth):
    """Health and version of the ffmpeg binary."""

    version: str | None = None


class VaapiHealth(ComponentHealth):
    """Health of the VAAPI render device and driver."""

    device: str
    driver: str | None = None


async def run_command(command: list[str], timeout: float) -> CommandOutcome:
    """Run an argument-list command with a timeout, reporting why it ended.

    Never raises for environmental failures: a missing binary, a timeout, or a
    non-zero exit are all returned as data so callers can decide whether the
    result is conclusive.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        return CommandOutcome("missing", -1, "", "")

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return CommandOutcome("timeout", -1, "", "")
    except asyncio.CancelledError:
        # The caller (for example an overall probe budget) gave up on us; do
        # not leave the child running.
        process.kill()
        raise

    return CommandOutcome(
        "ok",
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def _run(command: list[str], timeout: float) -> CommandOutput | None:
    """Run an argument-list command with a timeout.

    Returns ``None`` when the binary is missing or the call times out. Callers
    decide how to report that; this helper never raises for environmental
    failures.
    """
    outcome = await run_command(command, timeout)
    if outcome.state != "ok":
        return None
    return outcome.returncode, outcome.stdout, outcome.stderr


async def probe_ffmpeg(
    binary: str = "ffmpeg", timeout: float = _PROBE_TIMEOUT_SECONDS
) -> FFmpegHealth:
    """Return the installed ffmpeg version, or why it is unavailable."""
    result = await _run([binary, "-version"], timeout)
    if result is None:
        return FFmpegHealth(
            status="unavailable",
            detail=f"{binary} not found or timed out after {timeout:g}s",
        )

    returncode, stdout, stderr = result
    match = _FFMPEG_VERSION_RE.search(stdout)
    if returncode != 0 or match is None:
        detail = (stderr or stdout).strip().splitlines()
        return FFmpegHealth(
            status="error",
            detail=detail[0] if detail else f"{binary} exited with status {returncode}",
        )

    return FFmpegHealth(status="ok", version=match.group(1))


async def probe_vaapi(
    device: Path,
    binary: str = "vainfo",
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> VaapiHealth:
    """Return whether VAAPI works on ``device`` without inventing capability.

    A missing render device, missing ``vainfo`` or a failing probe is reported
    as ``unavailable``; this is a normal state on hosts without a GPU.
    """
    device_label = str(device)
    if not device.exists():
        return VaapiHealth(
            status="unavailable",
            device=device_label,
            detail="render device not present",
        )

    result = await _run(
        [binary, "--display", "drm", "--device", device_label],
        timeout,
    )
    if result is None:
        return VaapiHealth(
            status="unavailable",
            device=device_label,
            detail=f"{binary} not found or timed out after {timeout:g}s",
        )

    returncode, stdout, stderr = result
    driver_match = _VAAPI_DRIVER_RE.search(stdout)
    driver = driver_match.group(1).strip() if driver_match else None
    if returncode != 0 or "VAProfile" not in stdout:
        lines = (stderr or stdout).strip().splitlines()
        return VaapiHealth(
            status="unavailable",
            device=device_label,
            driver=driver,
            detail=lines[-1] if lines else f"{binary} exited with status {returncode}",
        )

    return VaapiHealth(status="ok", device=device_label, driver=driver)


def check_database(db_path: Path, timeout: float = 2.0) -> ComponentHealth:
    """Report database file state without creating or mutating it.

    Phase 0 has no schema yet, so a missing file is ``not_initialized`` rather
    than an error. When the file exists it is opened read-only and a trivial
    pragma is executed to prove it is a usable SQLite database.
    """
    if not db_path.exists():
        return ComponentHealth(
            status="not_initialized",
            detail=f"{db_path} does not exist yet",
        )

    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=timeout)
        try:
            connection.execute("PRAGMA user_version").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return ComponentHealth(status="error", detail=f"database check failed: {exc}")

    return ComponentHealth(status="ok")
