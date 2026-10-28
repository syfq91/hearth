"""Container-level backup -> restore round-trip acceptance test (Phase 8).

Seeds a catalog in one container, downloads a backup archive through the admin
API, then restores that archive into a freshly started container with an empty
volume and asserts the catalog is identical.

Opt-in: requires ``HEARTH_CONTAINER_E2E=1`` plus a built image
(``HEARTH_E2E_IMAGE``, default ``hearth:e2e``). The CI container job runs this
against the image it just built.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request
import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.container_e2e

IMAGE = os.environ.get("HEARTH_E2E_IMAGE", "hearth:e2e")
SECRET_KEY = "hearth-e2e-secret-key"
ADMIN = {"username": "admin", "password": "correct horse"}
TEMP_ADMIN = {"username": "temp-admin", "password": "temporary pass"}


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


@pytest.fixture
def docker_ready() -> None:
    """Skip unless the environment can build and run the container test."""
    if os.environ.get("HEARTH_CONTAINER_E2E") != "1":
        pytest.skip("container e2e is opt-in: set HEARTH_CONTAINER_E2E=1")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not available")
    if _docker("info", check=False).returncode != 0:
        pytest.skip("docker daemon is not reachable")
    if _docker("image", "inspect", IMAGE, check=False).returncode != 0:
        pytest.skip(f"image {IMAGE} is not built")


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _start_container(name: str, volume: str, media_root: str) -> str:
    """Run a Hearth container on an ephemeral port and return its base URL."""
    _docker(
        "run",
        "-d",
        "--name",
        name,
        "--read-only",
        "--tmpfs",
        "/transcode:size=64m,mode=1777",
        "--tmpfs",
        "/tmp:size=16m",
        "-v",
        f"{volume}:/config",
        "-v",
        f"{media_root}:/media:ro",
        "-e",
        "HEARTH_ENV=production",
        "-e",
        f"HEARTH_SECRET_KEY={SECRET_KEY}",
        "-e",
        "HEARTH_DATA_DIR=/config",
        "-e",
        "HEARTH_DB_PATH=/config/hearth.db",
        "-e",
        "HEARTH_MEDIA_DIR=/media",
        "-e",
        "HEARTH_TRANSCODE_DIR=/transcode",
        "-e",
        "HEARTH_LOG_LEVEL=WARNING",
        "-p",
        "127.0.0.1::8000",
        IMAGE,
    )
    published = _docker("port", name, "8000/tcp").stdout.strip()
    host, _, port = published.rpartition(":")
    return f"http://{host}:{port}"


def _wait_healthy(name: str, base_url: str, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/health", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1.5)
    logs = _docker("logs", "--tail", "40", name, check=False).stdout
    raise AssertionError(f"container never became healthy:\n{logs}")


def _dump_catalog(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    """Capture the catalog state: libraries, collections, and accounts."""
    catalog: dict[str, Any] = {}
    for section in ("libraries", "collections", "users"):
        response = client.get(f"/api/v1/{section}", headers=headers)
        assert response.status_code == 200, response.text
        catalog[section] = response.json()
    return catalog


def _stable_users(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop fields a post-restore login legitimately mutates.

    Logging into the restored container bumps the account's ``updated_at``
    (``last_login_at`` is set on every login), so it cannot participate in an
    equality comparison of the restored snapshot.
    """
    return [{k: v for k, v in user.items() if k != "updated_at"} for user in users]


def test_backup_restore_into_fresh_container_yields_identical_catalog(
    docker_ready: None, tmp_path: Any
) -> None:
    suffix = uuid.uuid4().hex[:8]
    name_a, name_b = f"hearth-e2e-a-{suffix}", f"hearth-e2e-b-{suffix}"
    volume_a, volume_b = f"hearth-e2e-a-{suffix}", f"hearth-e2e-b-{suffix}"
    archive = tmp_path / "hearth_e2e_backup.tar.gz"

    media_root = tmp_path / "media"
    (media_root / "movies").mkdir(parents=True)
    (media_root / "shows").mkdir()

    try:
        # --- Container A: seed the catalog and download a backup -------------
        base_a = _start_container(name_a, volume_a, str(media_root))
        _wait_healthy(name_a, base_a)

        with httpx.Client(base_url=base_a, timeout=30) as client:
            setup = client.post("/api/v1/auth/setup", json=ADMIN)
            assert setup.status_code == 201, setup.text
            admin_headers = _bearer(setup.json())

            for name, library_type, path in (
                ("E2E Movies", "movie", "/media/movies"),
                ("E2E Shows", "tv", "/media/shows"),
            ):
                created = client.post(
                    "/api/v1/libraries",
                    json={"name": name, "type": library_type, "paths": [path]},
                    headers=admin_headers,
                )
                assert created.status_code == 201, created.text

            user = client.post(
                "/api/v1/users",
                json={"username": "e2e-viewer", "password": "viewer pass", "role": "user"},
                headers=admin_headers,
            )
            assert user.status_code == 201, user.text

            collection = client.post(
                "/api/v1/collections",
                json={"name": "E2E Collection"},
                headers=admin_headers,
            )
            assert collection.status_code == 201, collection.text

            before = _dump_catalog(client, admin_headers)

            backup = client.post("/api/v1/admin/backup", headers=admin_headers)
            assert backup.status_code == 200, backup.text
            archive.write_bytes(backup.content)
            assert archive.stat().st_size > 0

        # --- Retire container A entirely ------------------------------------
        _docker("stop", "--time", "10", name_a, check=False)
        _docker("rm", "-f", name_a, check=False)
        _docker("volume", "rm", volume_a, check=False)

        # --- Container B: fresh volume, restore the archive -----------------
        base_b = _start_container(name_b, volume_b, str(media_root))
        _wait_healthy(name_b, base_b)

        with httpx.Client(base_url=base_b, timeout=30) as client:
            setup = client.post("/api/v1/auth/setup", json=TEMP_ADMIN)
            assert setup.status_code == 201, setup.text
            temp_headers = _bearer(setup.json())

            restore = client.post(
                "/api/v1/admin/restore",
                content=archive.read_bytes(),
                headers={**temp_headers, "Content-Type": "application/gzip"},
            )
            assert restore.status_code == 200, restore.text

            # The restore replaced the database; the original admin owns the
            # restored catalog again.
            login = client.post("/api/v1/auth/login", json=ADMIN)
            assert login.status_code == 200, login.text
            restored_headers = _bearer(login.json())

            after = _dump_catalog(client, restored_headers)

        # --- The restored catalog must be identical -------------------------
        assert after["libraries"] == before["libraries"]
        assert after["collections"] == before["collections"]
        assert _stable_users(after["users"]) == _stable_users(before["users"])
        assert len(after["libraries"]) == 2
        assert len(after["users"]) == 2
    finally:
        for name in (name_a, name_b):
            _docker("rm", "-f", name, check=False)
        for volume in (volume_a, volume_b):
            _docker("volume", "rm", volume, check=False)
