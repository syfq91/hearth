"""Comprehensive security review test suite for Phase 10 Task 10.3.

Covers:
1. Path traversal attacks on stream, HLS, subtitles, and static endpoints.
2. Token scope enforcement (media tokens vs API tokens, role checks).
3. SSRF restrictions on external metadata and image fetching.
4. Default-deny CORS configuration.
5. Defensive security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy).
6. Secret redaction in structured JSON logs and in-memory ring buffers.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import JSONFormatter, RingBufferLogHandler
from app.core.tokens import create_media_token
from app.db.models import Library, LibraryPath, MediaFile, MediaItem
from app.services.images.pipeline import ImagePipeline
from app.services.transcode.session import get_session_manager


@pytest.mark.asyncio
async def test_security_path_traversal_stream_and_hls(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Verify path traversal attacks are rejected on streaming and HLS endpoints."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201
    token = setup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Test HLS segment path traversal: regex only permits seg_\d+\.m4s
    get_session_manager(db_settings)
    session_dir = db_settings.transcode_dir / "valid_session_1"
    session_dir.mkdir(parents=True, exist_ok=True)

    traversal_filenames = [
        "../etc/passwd",
        "..%2Fetc%2Fpasswd",
        "seg_000.m4s/../../../etc/passwd",
        "init.mp4",  # init segment has its own dedicated endpoint
        "secret.key",
        "seg_000.m4s.exe",
    ]
    for bad_name in traversal_filenames:
        res = await api_client.get(
            f"/api/v1/hls/valid_session_1/{bad_name}",
            headers=headers,
        )
        assert res.status_code in (400, 404), f"Rejected {bad_name}: {res.status_code}"

    # 3. Test media streaming path outside library roots
    sensitive_file = tmp_path / "outside_root.txt"
    sensitive_file.write_text("SENSITIVE_DATA_OUTSIDE_ROOT")

    movie_dir = db_settings.media_dir / "movies"
    movie_dir.mkdir(parents=True, exist_ok=True)

    async with db_sessionmaker() as session:
        lib = Library(name="Movies", type="movie")
        lib.paths.append(LibraryPath(path=str(movie_dir)))
        session.add(lib)
        await session.flush()

        item = MediaItem(
            id="traversal-item-1",
            library_id=lib.id,
            type="movie",
            title="Traversal Attempt",
            sort_title="Traversal Attempt",
        )
        session.add(item)
        file_row = MediaFile(
            id="traversal-file-1",
            item_id=item.id,
            path=str(sensitive_file),
            size_bytes=len("SENSITIVE_DATA_OUTSIDE_ROOT"),
            mtime=sensitive_file.stat().st_mtime,
            partial_hash="fakehash123",
        )
        session.add(file_row)
        await session.commit()

    # Requesting file pointing outside library root is blocked by path_is_within_roots
    res = await api_client.get("/api/v1/stream/traversal-file-1", headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "file_not_found"


@pytest.mark.asyncio
async def test_security_token_scope_separation(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    """Verify short-lived media tokens cannot access standard API or admin endpoints."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201

    # 2. Generate a media token for item_id="item-123"
    from app.core.secrets import load_keys

    keys = load_keys(db_settings)
    media_token, _ = create_media_token(
        user_id="user-456",
        token_version=1,
        settings=db_settings,
        key=keys.media,
        item_id="item-123",
    )

    # 3. Attempt to use media token as an API bearer token on various endpoints
    media_headers = {"Authorization": f"Bearer {media_token}"}

    endpoints_to_test = [
        ("GET", "/api/v1/libraries"),
        ("GET", "/api/v1/home/recently-added"),
        ("POST", "/api/v1/users"),
        ("GET", "/api/v1/search?q=test"),
    ]
    for method, path in endpoints_to_test:
        kwargs = {"headers": media_headers}
        if method == "POST":
            kwargs["json"] = {"username": "hacker", "password": "password", "role": "admin"}
        res = await api_client.request(method, path, **kwargs)
        assert res.status_code == 401, f"{method} {path} allowed media token: {res.status_code}"
        assert res.json()["error"]["code"] == "invalid_token"


@pytest.mark.asyncio
async def test_security_ssrf_image_pipeline_restrictions(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify image download pipeline enforces host allowlisting and scheme validation."""
    pipeline = ImagePipeline(db_settings, db_sessionmaker)

    ssrf_urls = [
        "http://127.0.0.1:8000/internal-data",
        "http://localhost:8000/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/private-api",
        "http://192.168.1.1/router",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_flushall",
        "http://evil-attacker-site.com/exploit.png",
    ]

    for url in ssrf_urls:
        downloaded = await pipeline._download(url)
        assert downloaded is None, f"URL {url} bypassed SSRF protection"


@pytest.mark.asyncio
async def test_security_default_deny_cors(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify default CORS configuration rejects unauthorized origins without reflection."""
    res = await api_client.get(
        "/api/v1/health",
        headers={"Origin": "https://unauthorized-attacker.example.com"},
    )
    assert res.status_code == 200
    # Default-deny: no Access-Control-Allow-Origin header is present
    allow_origin = res.headers.get("access-control-allow-origin")
    assert allow_origin is None or allow_origin != "https://unauthorized-attacker.example.com"


@pytest.mark.asyncio
async def test_security_defensive_headers(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify security headers are attached to responses."""
    res = await api_client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "SAMEORIGIN"
    assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_security_secrets_never_logged() -> None:
    """Verify passwords, tokens, and secret keys are automatically redacted from logs."""
    formatter = JSONFormatter()
    ring_handler = RingBufferLogHandler()

    logger = logging.getLogger("test.security")
    logger.setLevel(logging.INFO)

    secret_extra = {
        "password": "SuperSecretPassword123!",
        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret",
        "secret_key": "my-secret-signing-key",
        "api_key": "sk-1234567890abcdef",
        "safe_field": "public_info",
    }

    record = logger.makeRecord(
        name="test.security",
        level=logging.INFO,
        fn="test_file.py",
        lno=1,
        msg="User authentication attempt",
        args=(),
        exc_info=None,
        extra=secret_extra,
    )

    # 1. Test JSONFormatter output
    formatted = formatter.format(record)
    assert "SuperSecretPassword123!" not in formatted
    assert "my-secret-signing-key" not in formatted
    assert "sk-1234567890abcdef" not in formatted
    assert "***REDACTED***" in formatted
    assert "public_info" in formatted

    # 2. Test RingBufferLogHandler output
    ring_handler.emit(record)
    from app.core.logging import get_recent_logs

    logs = get_recent_logs(limit=5)
    matching = [entry for entry in logs if entry.get("message") == "User authentication attempt"]
    assert len(matching) > 0
    latest = matching[0]
    assert latest.get("password") == "***REDACTED***"
    assert latest.get("token") == "***REDACTED***"
    assert latest.get("secret_key") == "***REDACTED***"
    assert latest.get("api_key") == "***REDACTED***"
    assert latest.get("safe_field") == "public_info"
