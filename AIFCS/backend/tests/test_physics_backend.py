"""Physics backend selection tests (PHASE 16).

The point of these is less that JSBSim works and more that the *platform* does
not depend on it. Every test here runs whether or not JSBSim is installed:
absence is simulated, so the behaviour without it is covered even on a machine
that has it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.config import load_settings
from core.integrator import KinematicIntegrator, NullIntegrator
from core.physics_backend import available_backends, backend_status, build_integrator
from core.simulation_engine import SimulationEngine
from main import create_app
from simulation.jsbsim_adapter import JSBSimUnavailableError
from simulation.physics import Simple6DOFModel


@pytest.fixture
def absent_jsbsim(monkeypatch):
    """Make the platform behave as if JSBSim were not installed."""
    monkeypatch.setattr("core.physics_backend.jsbsim_available", lambda: False)
    monkeypatch.setattr("core.physics_backend.jsbsim_version", lambda: None)
    monkeypatch.setattr("simulation.jsbsim_adapter.jsbsim_available", lambda: False)


def test_the_default_backend_is_the_built_in_model(settings):
    assert settings.physics.backend == "simple_6dof"
    assert isinstance(build_integrator(settings), Simple6DOFModel)


def test_the_engine_uses_the_configured_backend(settings):
    settings.physics.backend = "kinematic"
    engine = SimulationEngine(settings)
    assert engine.integrator.name == "kinematic"


def test_an_explicit_integrator_still_wins(settings):
    """A test pins a model without having to write configuration."""
    settings.physics.backend = "kinematic"
    engine = SimulationEngine(settings, integrator=NullIntegrator())
    assert engine.integrator.name == "null"


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("simple_6dof", Simple6DOFModel), ("kinematic", KinematicIntegrator), ("null", NullIntegrator)],
)
def test_every_built_in_backend_builds(settings, backend, expected):
    settings.physics.backend = backend
    assert isinstance(build_integrator(settings), expected)


def test_an_unknown_backend_is_rejected_by_configuration(settings):
    with pytest.raises(ValueError, match=r"physics\.backend must be one of"):
        settings.physics.__class__(backend="teleportation")


def test_the_catalogue_lists_every_backend():
    keys = {b.key for b in available_backends()}
    assert keys == {"simple_6dof", "jsbsim", "kinematic", "null"}
    built_in = {b.key: b.available for b in available_backends() if b.key != "jsbsim"}
    assert all(built_in.values()), "the built-in backends are always available"


# --------------------------------------------------------- JSBSim absent


def test_requesting_jsbsim_without_it_raises_with_an_install_hint(settings, absent_jsbsim):
    settings.physics.backend = "jsbsim"
    with pytest.raises(JSBSimUnavailableError, match="pip install jsbsim"):
        build_integrator(settings)


def test_a_missing_backend_never_falls_back_to_a_different_model(settings, absent_jsbsim):
    """A run under the wrong model is worse than a run that refused to start."""
    settings.physics.backend = "jsbsim"
    with pytest.raises(JSBSimUnavailableError):
        SimulationEngine(settings)


def test_the_platform_still_flies_without_jsbsim(settings, absent_jsbsim):
    engine = SimulationEngine(settings)
    engine.load_scenario("demo_alpha")
    engine.step(120)
    assert engine.integrator.name == "simple_6dof"
    assert engine.clock.tick_count == 120
    assert all(e.status.value == "ACTIVE" for e in engine.world.entities.values())


def test_the_status_says_unavailable_and_how_to_fix_it(settings, absent_jsbsim):
    settings.physics.backend = "jsbsim"
    status = backend_status(settings)
    assert status["available"] is False
    assert "pip install jsbsim" in status["install_hint"]
    jsbsim_row = next(b for b in status["backends"] if b["key"] == "jsbsim")
    assert jsbsim_row["available"] is False


def test_the_app_starts_and_reports_honestly_without_jsbsim(monkeypatch):
    """The backend being absent must not stop the platform coming up."""
    monkeypatch.setattr("core.physics_backend.jsbsim_available", lambda: False)
    monkeypatch.setattr("core.physics_backend.jsbsim_version", lambda: None)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        body = client.get("/api/physics").json()
        jsbsim_row = next(b for b in body["backends"] if b["key"] == "jsbsim")
        assert jsbsim_row["available"] is False
        assert "Not installed" in jsbsim_row["detail"]
        # The configured backend is the built-in one, so physics is still fine.
        states = {s["key"]: s["state"] for s in client.get("/api/system/status").json()["subsystems"]}
        assert states["physics"] == "ONLINE"


# ------------------------------------------------------------------- API


def test_the_physics_endpoint_reports_what_is_flying(client):
    body = client.get("/api/physics").json()
    assert body["requested"] == "simple_6dof"
    assert body["active"] == "simple_6dof"
    assert body["available"] is True
    assert len(body["backends"]) == 4
    assert "fictional" in body["notice"].lower()


def test_the_airframes_are_one_list_for_both_backends(client):
    body = client.get("/api/physics/airframes").json()
    names = [a["name"] for a in body["airframes"]]
    assert names == ["fictional_aircraft", "fictional_interceptor"]
    assert body["count"] == 2
    assert "fictional" in body["notice"].lower()
    for airframe in body["airframes"]:
        assert airframe["mass_kg"] > 0
        assert len(airframe["inertia"]) == 3


def test_the_subsystem_registry_names_the_active_backend(client):
    subsystems = {s["key"]: s for s in client.get("/api/system/status").json()["subsystems"]}
    assert subsystems["physics"]["state"] == "ONLINE"
    assert "simple_6dof" in subsystems["physics"]["detail"]


def test_changing_the_backend_changes_the_config_hash():
    """A run recorded under one model must not look like a run under another."""
    a = load_settings()
    b = load_settings()
    b.physics.backend = "kinematic"
    assert a.config_hash != b.config_hash
