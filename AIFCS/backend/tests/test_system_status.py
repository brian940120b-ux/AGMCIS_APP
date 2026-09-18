"""Subsystem registry behaviour (PHASE 0).

These tests encode the project rule that unbuilt features must never claim to be
operational.
"""

from __future__ import annotations

import pytest

from core.system_status import (
    Subsystem,
    SubsystemState,
    SystemStatusRegistry,
    build_default_registry,
)


def test_default_registry_covers_every_planned_subsystem():
    keys = {s.key for s in build_default_registry().all()}
    expected = {
        "api",
        "config",
        "logging",
        "simulation",
        "physics",
        "agents",
        "controllers",
        "sensors",
        "communications",
        "websocket",
        "replay",
        "scoring",
        "storage",
        "training",
    }
    assert expected <= keys


def test_only_phase_0_subsystems_are_online():
    registry = build_default_registry()
    online = {s.key for s in registry.all() if s.state is SubsystemState.ONLINE}
    assert online == {"api", "config", "logging"}


def test_unbuilt_subsystems_report_not_implemented():
    registry = build_default_registry()
    for key in ("simulation", "physics", "agents", "training", "replay"):
        subsystem = registry.get(key)
        assert subsystem is not None
        assert subsystem.state is SubsystemState.NOT_IMPLEMENTED
        assert subsystem.phase, "an unbuilt subsystem must name its target phase"


def test_set_state_updates_registry():
    registry = build_default_registry()
    registry.set_state("simulation", SubsystemState.ONLINE, "engine ticking")

    subsystem = registry.get("simulation")
    assert subsystem is not None
    assert subsystem.state is SubsystemState.ONLINE
    assert subsystem.detail == "engine ticking"


def test_set_state_rejects_unknown_subsystem():
    with pytest.raises(KeyError):
        build_default_registry().set_state("warp_drive", SubsystemState.ONLINE)


def test_operational_flips_on_error():
    registry = build_default_registry()
    assert registry.operational is True

    registry.set_state("api", SubsystemState.ERROR, "router failure")
    assert registry.operational is False


def test_registry_serialises_for_the_api():
    registry = SystemStatusRegistry()
    registry.register(Subsystem("x", "Example", SubsystemState.READY, "detail", "PHASE 1"))

    payload = registry.to_list()
    assert payload == [
        {
            "key": "x",
            "label": "Example",
            "state": "READY",
            "detail": "detail",
            "phase": "PHASE 1",
            "metadata": {},
        }
    ]
