"""API tests for health, status, compute and config endpoints (PHASE 0)."""

from __future__ import annotations


def test_the_api_describes_the_platform(client):
    """At /api, because / belongs to the dashboard once one has been built.

    This description used to live at the root. It moved rather than doubled up,
    so that what the root returns depends on the machine while what /api
    returns does not.
    """
    body = client.get("/api").json()
    assert body["app"] == "AIFCS"
    assert body["health"] == "/api/health"
    assert "fictional" in body["scope"].lower()


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "AIFCS"
    assert body["uptime_s"] >= 0
    assert len(body["config_hash"]) == 16


def test_system_status_lists_subsystems(client):
    body = client.get("/api/system/status").json()
    assert body["operational"] is True

    states = {s["key"]: s["state"] for s in body["subsystems"]}
    # These genuinely run as of PHASE 1.
    assert states["api"] == "ONLINE"
    assert states["config"] == "ONLINE"
    assert states["logging"] == "ONLINE"
    assert states["simulation"] == "ONLINE"
    assert states["physics"] == "ONLINE"
    assert states["agents"] == "ONLINE"
    assert states["controllers"] == "ONLINE"
    assert states["sensors"] == "ONLINE"
    assert states["communications"] == "ONLINE"
    assert states["websocket"] == "ONLINE"
    assert states["replay"] == "ONLINE"
    assert states["scoring"] == "ONLINE"
    assert states["storage"] == "ONLINE"
    # PHASE 11-13 built the training stack, but it is an optional dependency:
    # the state must follow what is actually installed, never assume it.
    from training.pipeline import rl_available

    assert states["training"] == ("ONLINE" if rl_available() else "OFFLINE")


def test_every_subsystem_has_a_valid_state(client):
    valid = {"ONLINE", "READY", "WARNING", "ERROR", "OFFLINE", "NOT_IMPLEMENTED"}
    for sub in client.get("/api/system/status").json()["subsystems"]:
        assert sub["state"] in valid
        assert sub["label"] and sub["phase"]


def test_compute_reports_cpu_fallback_without_gpu(client):
    body = client.get("/api/system/compute").json()
    assert body["device"] in {"cpu", "cuda"}
    # Without CUDA the platform must still be usable.
    if not body["cuda_available"]:
        assert body["training_device"] == "cpu"
        assert body["gpu_name"] is None


def test_config_endpoint_exposes_real_values(client):
    body = client.get("/api/config").json()
    assert body["simulation"]["tick_rate_hz"] == 60
    assert body["simulation"]["dt"] > 0
    assert 1.0 in body["simulation"]["allowed_speeds"]
    assert body["world"]["bounds"]["altitude_max"] > body["world"]["bounds"]["altitude_min"]


def test_openapi_schema_is_generated(client):
    schema = client.get("/openapi.json").json()
    assert "/api/health" in schema["paths"]
    assert "/api/system/status" in schema["paths"]


def test_reported_phase_matches_the_last_completed_phase_in_the_docs():
    """The header's phase label must not drift behind what has been built.

    It was a literal in `health.py` and went stale twice — the dashboard still
    said PHASE 9 after PHASE 10 and PHASE 11-13 had shipped. The docs are the
    record of what is done, so the label is checked against them.
    """
    import re
    from pathlib import Path

    from api.health import BUILD_PHASE

    phases_md = Path(__file__).resolve().parents[2] / "docs" / "PHASES.md"
    completed = re.findall(r"^## (PHASE [^—]+?) — .*\*\*Complete\*\*", phases_md.read_text(), re.M)
    assert completed, "docs/PHASES.md lists no completed phase"
    # The headings use an en dash, the constant a plain hyphen.
    assert completed[-1].replace("\u2013", "-") == BUILD_PHASE
