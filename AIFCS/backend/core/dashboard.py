"""Serving the built dashboard from the backend.

A laptop that cannot run Vite's dev server can still run the platform: the
dashboard is a pile of static files once it has been built, and the backend is
already an HTTP server. Serving them from the same origin also removes the
proxy the dev server provides, because the frontend addresses the API with
relative paths (`/api`, `/ws`) in every environment.

Building still needs Node. Serving does not, and serving is what happens every
time the platform is started.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

# Paths the dashboard must never be allowed to swallow. The catch-all below is
# registered after every router, so ordering already protects these; listing
# them as well means a route added later in the wrong place fails loudly here
# rather than quietly returning HTML to something expecting JSON.
RESERVED_PREFIXES = ("api/", "ws/", "docs", "redoc", "openapi.json")


@dataclass(frozen=True)
class DashboardStatus:
    """What the backend can actually serve, as opposed to what it would like to."""

    available: bool
    directory: Path
    detail: str


def dashboard_directory(project_root: Path) -> Path:
    return project_root / "frontend" / "dist"


def mount_dashboard(app: FastAPI, project_root: Path) -> DashboardStatus:
    """Serve `frontend/dist` at the root, if it has been built.

    Returns what happened rather than raising: a backend with no dashboard is a
    working backend, and the API is the half that matters to the competition.
    """
    directory = dashboard_directory(project_root)
    index = directory / "index.html"
    if not index.is_file():
        return DashboardStatus(
            available=False,
            directory=directory,
            detail="not built - run: npm run build (in frontend/)",
        )

    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="dashboard-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(request: Request, path: str) -> Response:
        # A single-page application owns its own routing, so any path that is
        # not a file on disk returns the shell and lets the browser sort it out.
        if path.startswith(RESERVED_PREFIXES):
            # Raised rather than returned, so the application's own error
            # handler answers: a missing endpoint under /api is a JSON body
            # with a request id in it, whether or not a dashboard is mounted.
            raise HTTPException(status_code=404, detail="Not Found")
        if path:
            candidate = (directory / path).resolve()
            # resolve() before the containment check, so "../../etc/passwd"
            # is compared as the path it actually denotes.
            if candidate.is_file() and candidate.is_relative_to(directory.resolve()):
                return FileResponse(candidate)
        return FileResponse(index)

    return DashboardStatus(available=True, directory=directory, detail=f"built bundle from {directory}")
