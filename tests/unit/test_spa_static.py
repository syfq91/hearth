"""Static serving of the built web UI (Phase 9 scaffold)."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.spa import SpaStaticFiles, find_web_dist
from app.main import create_app

INDEX_HTML = "<!doctype html><html><body>Hearth SPA shell</body></html>"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A minimal built SPA directory."""
    root = tmp_path / "dist"
    (root / "_app").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "_app" / "main.js").write_text("export {};\n", encoding="utf-8")
    return root


@pytest.fixture
def client(dist: Path) -> TestClient:
    """Bare FastAPI app with only the SPA mounted at `/`."""
    application = FastAPI()
    application.mount("/", SpaStaticFiles(directory=dist, html=True), name="web")
    return TestClient(application)


def test_root_serves_the_spa_shell(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_client_route_falls_back_to_index(client: TestClient) -> None:
    """Deep links (e.g. after a page reload) return the SPA shell."""
    response = client.get("/watch/018d3b50-0000-7000-8000-000000000001")

    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_nested_client_route_falls_back_to_index(client: TestClient) -> None:
    response = client.get("/library/lib-1/items")

    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_missing_asset_returns_404_not_html(client: TestClient) -> None:
    """A missing bundle asset must fail loudly, not serve the shell."""
    response = client.get("/_app/missing.js")

    assert response.status_code == 404
    assert response.text != INDEX_HTML


def test_existing_asset_is_served(client: TestClient) -> None:
    response = client.get("/_app/main.js")

    assert response.status_code == 200
    assert response.text == "export {};\n"


def test_api_paths_are_not_shadowed_by_the_spa(client: TestClient) -> None:
    """Unknown API paths must not return HTML (JSON envelope in the real app)."""
    response = client.get("/api/v1/definitely-not-a-route")

    assert response.status_code == 404
    assert response.text != INDEX_HTML


def test_find_web_dist_accepts_explicit_roots(tmp_path: Path) -> None:
    assert find_web_dist(roots=[tmp_path]) is None

    built = tmp_path / "web" / "dist"
    built.mkdir(parents=True)
    (built / "index.html").write_text(INDEX_HTML, encoding="utf-8")

    assert find_web_dist(roots=[tmp_path]) == built


def test_create_app_mounts_the_spa_when_built(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app factory serves `/` from web/dist when the SPA has been built."""
    built = tmp_path / "web" / "dist"
    built.mkdir(parents=True)
    (built / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    application = create_app(settings)

    with TestClient(application) as spa_client:
        root = spa_client.get("/")
        api_404 = spa_client.get("/api/v1/definitely-not-a-route")

    assert root.status_code == 200
    assert root.text == INDEX_HTML
    # The unified error envelope, not the SPA shell.
    assert api_404.status_code == 404
    assert api_404.json()["error"]["code"] == "not_found"


def test_routes_registered_after_create_app_still_win(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SPA mount must never shadow a route added after the factory call.

    The mount runs in the lifespan (after all application routes exist), so a
    route registered by a test — or by any future module — keeps priority.
    """
    built = tmp_path / "web" / "dist"
    built.mkdir(parents=True)
    (built / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    application = create_app(settings)

    @application.get("/_test/later-route")
    def later_route() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(application) as spa_client:
        response = spa_client.get("/_test/later-route")
        root = spa_client.get("/")

    assert response.status_code == 200
    assert response.json() == {"ok": "yes"}
    assert root.status_code == 200
    assert root.text == INDEX_HTML


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings isolated to a temporary directory (no DB file)."""
    return Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        transcode_dir=tmp_path / "transcode",
        media_dir=tmp_path / "media",
        render_device=tmp_path / "renderD128",
    )
