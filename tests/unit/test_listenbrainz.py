"""Unit tests for ListenBrainz scrobbling service and job handler."""

from __future__ import annotations

import httpx
import pytest

from app.services.scrobbling.listenbrainz import submit_listen


@pytest.mark.asyncio
async def test_submit_listen_payload_and_headers() -> None:
    sent_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent_request
        sent_request = request
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    track_metadata = {
        "track_name": "Paranoid Android",
        "artist_name": "Radiohead",
        "release_name": "OK Computer",
        "musicbrainz_track_id": "rec-123",
        "musicbrainz_album_id": "rel-456",
        "musicbrainz_artist_id": "art-789",
    }

    ok = await submit_listen(
        token="my-lb-token",
        track_metadata=track_metadata,
        listened_at=1700000000,
        base_url="https://api.listenbrainz.org",
        client=client,
    )
    assert ok is True
    assert sent_request is not None
    assert sent_request.headers["Authorization"] == "Token my-lb-token"
    assert "HearthMediaServer" in sent_request.headers["User-Agent"]

    import json

    body = json.loads(sent_request.content)
    assert body["listen_type"] == "single"
    payload = body["payload"][0]
    assert payload["listened_at"] == 1700000000
    assert payload["track_metadata"]["track_name"] == "Paranoid Android"
    assert payload["track_metadata"]["artist_name"] == "Radiohead"
    assert payload["track_metadata"]["release_name"] == "OK Computer"
    additional = payload["track_metadata"]["additional_info"]
    assert additional["recording_mbid"] == "rec-123"
    assert additional["release_mbid"] == "rel-456"
    assert additional["artist_mbids"] == ["art-789"]


@pytest.mark.asyncio
async def test_submit_listen_retry_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    with pytest.raises(httpx.HTTPStatusError):
        await submit_listen(
            token="token",
            track_metadata={"track_name": "Song", "artist_name": "Band"},
            listened_at=1700000000,
            client=client,
        )


@pytest.mark.asyncio
async def test_submit_listen_rejects_on_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid token"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    ok = await submit_listen(
        token="invalid-token",
        track_metadata={"track_name": "Song", "artist_name": "Band"},
        listened_at=1700000000,
        client=client,
    )
    assert ok is False
