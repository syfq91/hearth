"""Static serving for the Phase 9 web UI (a SvelteKit single-page app).

The SPA lives in ``web/dist`` (built by ``npm run build`` inside ``web/``).
FastAPI mounts it at ``/`` so the UI and the API share one origin in
production; client-side routes (``/login``, ``/watch/<id>`` …) fall back to
``index.html`` while unknown API paths keep the unified JSON error envelope.
"""

from collections.abc import Sequence
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_WEB_DIST = Path("web") / "dist"
_INDEX = "index.html"


def find_web_dist(roots: Sequence[Path] | None = None) -> Path | None:
    """Return the built SPA directory when present, else ``None``.

    Without explicit ``roots`` the lookup checks the current working
    directory (the container runs from ``/app``) and the repository root
    (source checkouts), mirroring the developer test-player lookup in
    ``app/main.py``.
    """
    if roots is None:
        roots = (Path.cwd(), Path(__file__).resolve().parents[2])
    for root in roots:
        candidate = root / _WEB_DIST
        if (candidate / _INDEX).is_file():
            return candidate
    return None


def _is_client_route(path: str) -> bool:
    """Return ``True`` for extensionless paths the SPA router should handle.

    API prefixes never fall back to the SPA (their 404s must stay JSON), and
    paths with a file extension are treated as asset requests so a missing
    ``/_app/*.js`` fails loudly instead of silently returning HTML.
    """
    if path == "api" or path.startswith("api/"):
        return False
    return "." not in path.rsplit("/", 1)[-1]


class SpaStaticFiles(StaticFiles):
    """``StaticFiles`` with an ``index.html`` fallback for client routes.

    Unknown ``/api/...`` paths are re-raised as HTTP 404 so the application's
    unified error handler produces the standard JSON envelope instead of the
    SPA shell.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # StaticFiles raises HTTPException(404) for unknown paths; hand
            # extensionless ones to the SPA router instead.
            if (
                exc.status_code == 404
                and scope["method"] in ("GET", "HEAD")
                and _is_client_route(path)
            ):
                return await super().get_response(_INDEX, scope)
            raise
