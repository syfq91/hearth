"""The developer test player is a static single-file page."""

import httpx


async def test_testplayer_is_served_and_labelled(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/testplayer/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    assert "developer test player" in body
    assert "not the Hearth frontend" in body
    assert "<video" in body
    assert "/api/v1/auth/login" in body
    assert "/api/v1/items/${item.id}/playback-info" in body
    assert "/api/v1/items/" in body
    assert "hls.js" in body
    assert "preset-select" in body
    assert "debug-panel" in body
    assert "audio-select" in body
    assert "subtitle-select" in body
