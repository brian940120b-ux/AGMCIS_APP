"""Container definition tests (PHASE 20).

There is no Docker daemon in the environment these run in, so the image cannot
be built here and this does not pretend to. What it does check is the class of
mistake that a build *would* catch and that nobody notices until deployment
day: a COPY of a path that is not in the repository, a compose file pointing at
a Dockerfile that does not exist, an image that cannot run without the bind
mounts its compose file happens to provide.

That last one is not hypothetical. The backend image copied `backend/` and
`configs/` but not `scenarios/`, and compose mounted the scenarios in, so the
stack worked and `docker run` of the image alone failed to load a scenario.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = sorted((PROJECT_ROOT / "docker").glob("Dockerfile.*"))


def _copy_sources(dockerfile: Path) -> list[str]:
    """Every local path a COPY brings into the image.

    Skips `--from=` copies: those come from an earlier build stage, not the
    repository, so there is nothing on disk to check them against.
    """
    sources: list[str] = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        parts = stripped.split()[1:]
        if any(part.startswith("--from=") for part in parts):
            continue
        parts = [p for p in parts if not p.startswith("--")]
        # The last argument is the destination.
        sources.extend(parts[:-1])
    return sources


def test_there_are_dockerfiles_to_check():
    assert DOCKERFILES, "expected docker/Dockerfile.* to exist"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_every_copied_path_exists_in_the_repository(dockerfile):
    """A COPY of a missing path fails the build — on deployment day."""
    for source in _copy_sources(dockerfile):
        # A trailing * is a glob for an optional file (a lockfile that may not
        # be committed); only the directory it sits in has to exist.
        target = PROJECT_ROOT / source.rstrip("*")
        if source.endswith("*"):
            assert target.parent.is_dir(), f"{dockerfile.name} copies from {source}, which is nowhere"
            continue
        assert target.exists(), f"{dockerfile.name} copies {source}, which is not in the repository"


def test_the_backend_image_carries_everything_it_needs_to_run():
    """It must run on its own, not only under the compose file's bind mounts."""
    sources = _copy_sources(PROJECT_ROOT / "docker" / "Dockerfile.backend")
    for needed in ("backend/", "configs/", "scenarios/", "requirements.txt"):
        assert any(s.rstrip("./") == needed.rstrip("./") for s in sources), (
            f"the backend image never copies {needed}, so it cannot start without a bind mount"
        )


def test_the_compose_file_points_at_real_build_files():
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        build = service.get("build")
        assert build, f"service {name} has no build section"
        context = PROJECT_ROOT / build["context"]
        assert context.is_dir(), f"service {name} builds from {context}, which is nowhere"
        assert (context / build["dockerfile"]).is_file(), (
            f"service {name} names {build['dockerfile']}, which does not exist"
        )


def test_the_compose_file_only_mounts_paths_that_exist():
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    named_volumes = set(compose.get("volumes") or {})
    for name, service in compose["services"].items():
        for mount in service.get("volumes", []):
            source = mount.split(":")[0]
            if not source.startswith("."):
                assert source in named_volumes, (
                    f"service {name} mounts {source}, which is not a declared volume"
                )
                continue
            assert (PROJECT_ROOT / source).exists(), (
                f"service {name} mounts {source}, which is not in the repository"
            )


def test_the_frontend_image_serves_the_api_through_nginx():
    """The built frontend is static: without these the dashboard cannot reach the backend."""
    nginx = (PROJECT_ROOT / "docker" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass" in nginx
    assert re.search(r"location\s+/api/", nginx), "nginx must proxy /api/ to the backend"
    assert re.search(r"location\s+/ws/", nginx), "nginx must proxy /ws/ for live telemetry"


def test_the_backend_image_healthcheck_probes_the_real_endpoint():
    dockerfile = (PROJECT_ROOT / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile, "the healthcheck must probe an endpoint that exists"
