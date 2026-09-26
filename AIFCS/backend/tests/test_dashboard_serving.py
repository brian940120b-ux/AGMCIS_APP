"""Serving the built dashboard from the backend (COMP PHASE 10).

A laptop that cannot run Vite's dev server can still run the platform, because
the dashboard is static files once built and the backend is already an HTTP
server. What these guard is the part that is easy to get wrong: a catch-all
route added to serve a single-page application is one wildcard away from
swallowing the API.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.dashboard import DashboardStatus, mount_dashboard

INDEX = "<!doctype html><title>dashboard</title>"


@pytest.fixture
def built(tmp_path: Path) -> Path:
    """A project root with a dashboard bundle in it."""
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    # Bait one level above the bundle and two, because "how far up" decides
    # whether a traversal probe reaches anything: the first version of this put
    # the file two levels up and tested a path that only climbed one, so the
    # test passed whether or not the containment check was there at all.
    (tmp_path / "frontend" / "secret.txt").write_text("not for the web", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for the web", encoding="utf-8")
    # A symlink is the case the path arithmetic cannot catch on its own: the
    # name stays inside the bundle and the file it names does not.
    (dist / "escape.txt").symlink_to(tmp_path / "secret.txt")
    return tmp_path


def app_with(root: Path) -> tuple[FastAPI, DashboardStatus]:
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    status = mount_dashboard(app, root)
    return app, status


def test_no_bundle_means_no_routes_and_a_reason_why(tmp_path: Path):
    """An unbuilt dashboard is a fact to report, not an error to raise.

    The backend is the half that matters to the competition, and it has to come
    up on a machine where the frontend was never built.
    """
    app, status = app_with(tmp_path)
    assert not status.available
    assert "npm run build" in status.detail

    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 404


def test_the_bundle_is_served_at_the_root(built: Path):
    app, status = app_with(built)
    assert status.available

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert response.text == INDEX


def test_the_api_is_not_swallowed_by_the_catch_all(built: Path):
    """The whole risk of a catch-all, in one test.

    A route matching "/{path:path}" will answer for /api/health too if it is
    registered first — and it answers with HTML, so a caller expecting JSON
    fails somewhere far away from the cause.
    """
    client = TestClient(app_with(built)[0])
    assert client.get("/api/health").json() == {"status": "ok"}
    # A path under /api that does not exist is a missing endpoint, not a page.
    assert client.get("/api/nothing-here").status_code == 404


def test_unknown_paths_get_the_shell_because_the_app_routes_itself(built: Path):
    """/models is a page in the browser, not a file on disk."""
    client = TestClient(app_with(built)[0])
    response = client.get("/models")
    assert response.status_code == 200
    assert response.text == INDEX


def test_real_files_are_served_as_themselves(built: Path):
    client = TestClient(app_with(built)[0])
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/favicon.svg").text == "<svg/>"


@pytest.mark.parametrize(
    "path",
    [
        # Starlette normalises these away before routing, so they never reach
        # the handler as "..". Kept because that is worth knowing and worth
        # noticing if it ever stops being true.
        "/../secret.txt",
        "/../../etc/passwd",
        "/assets/../../secret.txt",
        # These do arrive with ".." intact, and are what the containment check
        # is actually for.
        "/%2e%2e%2fsecret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        # Inside the bundle by name, outside it on disk.
        "/escape.txt",
    ],
)
def test_nothing_outside_the_bundle_can_be_read(built: Path, path: str):
    """A file server pointed at a directory must not serve the one above it.

    Whatever the path normalises to, the only two answers available are a file
    inside the bundle or the bundle's own index.
    """
    response = TestClient(app_with(built)[0]).get(path)
    assert "not for the web" not in response.text
    assert "root:" not in response.text


# ------------------------------------------- the real application, with a bundle


def test_the_real_app_keeps_its_error_shape_behind_the_dashboard(built: Path, monkeypatch):
    """The catch-all must not turn a missing endpoint into a page.

    A 404 from the API carries a JSON body and a request id that joins the
    response to its traceback in the log. Returning a bare status from the
    catch-all took both away, and only a test using the real application
    noticed — the isolated one asserted a status code and was happy.

    The bundle is a temporary one, pointed at through the application's own
    wiring rather than mounted afterwards, so this also covers the ordering:
    the catch-all is registered last and every router above it still wins.
    """
    import main

    monkeypatch.setenv("AIFCS_SERVE_DASHBOARD", "1")
    monkeypatch.setattr(main, "mount_dashboard", lambda app, _root: mount_dashboard(app, built))

    with TestClient(main.create_app()) as client:
        missing = client.get("/api/definitely-not-a-route")
        assert missing.status_code == 404
        assert missing.json()["request_id"]

        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/docs").status_code == 200
        assert client.get("/").text == INDEX
