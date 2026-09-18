"""Shared pytest fixtures for the AIFCS backend."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.config import load_settings, reset_settings_cache
from core.runtime import reset_runtime
from main import create_app


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
