"""API tests for health, status, compute and config endpoints (PHASE 0)."""

from __future__ import annotations


def test_root_describes_platform(client):
    body = client.get("/").json()
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
    # Not built yet — must be reported honestly, never as ONLINE.
    assert states["agents"] == "NOT_IMPLEMENTED"
    assert states["training"] == "NOT_IMPLEMENTED"


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
