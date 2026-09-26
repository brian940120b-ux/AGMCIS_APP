"""Shared pytest fixtures for the AIFCS backend."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_CONFIGS = BACKEND_ROOT.parent / "configs"
PROJECT_SCENARIOS = BACKEND_ROOT.parent / "scenarios"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.config import load_settings, reset_settings_cache
from core.runtime import reset_runtime
from main import create_app


@pytest.fixture(scope="session", autouse=True)
def _isolated_data(tmp_path_factory):
    """Point recordings and the database at a temporary directory.

    PHASE 9 writes files. Without this, running the suite would drop replay
    files and rows into the project's own ``data/`` directory, and a test would
    see runs left behind by the last one.

    The real ``configs/`` are copied and only the two paths are rewritten, so
    tests still exercise the shipped configuration rather than a stub.
    """
    root = tmp_path_factory.mktemp("aifcs-data")
    config_dir = root / "configs"
    shutil.copytree(PROJECT_CONFIGS, config_dir)

    analysis = config_dir / "analysis.yaml"
    text = analysis.read_text(encoding="utf-8")
    text = text.replace("directory: data/replay", f"directory: {root / 'replay'}")
    text = text.replace("database_path: data/aifcs.db", f"database_path: {root / 'aifcs.db'}")
    analysis.write_text(text, encoding="utf-8")

    # PHASE 16 writes a generated JSBSim data root. Same reasoning: a test run
    # must not leave airframe files in the project's own data directory.
    simulation_config = config_dir / "simulation.yaml"
    simulation_config.write_text(
        simulation_config.read_text(encoding="utf-8").replace(
            "data_root: data/jsbsim", f"data_root: {root / 'jsbsim'}"
        ),
        encoding="utf-8",
    )

    # PHASE 10 writes scenario files. Point the directory at a copy so a test
    # that creates or deletes one cannot touch the scenarios in the repository.
    scenario_dir = root / "scenarios"
    shutil.copytree(PROJECT_SCENARIOS, scenario_dir)
    scenarios_config = config_dir / "scenarios.yaml"
    scenarios_config.write_text(
        scenarios_config.read_text(encoding="utf-8").replace(
            "directory: scenarios", f"directory: {scenario_dir}"
        ),
        encoding="utf-8",
    )

    previous = os.environ.get("AIFCS_CONFIG_DIR")
    os.environ["AIFCS_CONFIG_DIR"] = str(config_dir)

    # COMP PHASE 10. The backend serves frontend/dist when it is there, which
    # would make what the application returns for an unknown path depend on
    # whether anyone had run a build — 404 on a clean checkout, the dashboard's
    # own page on a developer's machine. The suite pins it off and the tests
    # that are about the dashboard mount it themselves.
    previous_dashboard = os.environ.get("AIFCS_SERVE_DASHBOARD")
    os.environ["AIFCS_SERVE_DASHBOARD"] = "0"

    yield root

    if previous is None:
        os.environ.pop("AIFCS_CONFIG_DIR", None)
    else:
        os.environ["AIFCS_CONFIG_DIR"] = previous
    if previous_dashboard is None:
        os.environ.pop("AIFCS_SERVE_DASHBOARD", None)
    else:
        os.environ["AIFCS_SERVE_DASHBOARD"] = previous_dashboard


@pytest.fixture(autouse=True)
def _clean_runtime():
    """Each test gets fresh settings and a fresh simulation engine.

    Without this the engine singleton would carry a running clock and a
    half-finished world from one test into the next.
    """
    reset_settings_cache()
    reset_runtime()
    yield
    reset_settings_cache()
    reset_runtime()


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def client():
    """TestClient with lifespan executed, so logging is configured as in prod."""
    with TestClient(create_app()) as test_client:
        yield test_client
