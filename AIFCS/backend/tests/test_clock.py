"""Simulation clock tests (PHASE 1)."""

from __future__ import annotations

import time

import pytest

from core.clock import ClockState, SimulationClock


def test_dt_is_derived_from_tick_rate():
    assert SimulationClock(tick_rate_hz=60).dt == pytest.approx(1 / 60)
    assert SimulationClock(tick_rate_hz=120).dt == pytest.approx(1 / 120)


def test_simulation_time_is_a_function_of_ticks():
    clock = SimulationClock(tick_rate_hz=60)
    for _ in range(600):
        clock.advance()
    assert clock.tick_count == 600
    assert clock.simulation_time == pytest.approx(10.0)


def test_speed_does_not_change_the_timestep():
    """The core determinism guarantee: speed is pacing only."""
    clock = SimulationClock(tick_rate_hz=60)
    baseline_dt = clock.dt

    for speed in (0.25, 1.0, 50.0):
        clock.set_speed(speed)
        assert clock.dt == baseline_dt
        assert clock.target_interval_s == pytest.approx(baseline_dt / speed)


def test_pause_stops_simulation_time():
    clock = SimulationClock()
    clock.start()
    clock.advance()
    clock.pause()

    frozen = clock.simulation_time
    time.sleep(0.05)
    assert clock.simulation_time == frozen
    assert clock.state is ClockState.PAUSED


def test_pause_stops_real_time_accumulating():
    clock = SimulationClock()
    clock.start()
    time.sleep(0.03)
    clock.pause()

    banked = clock.real_time_s
    time.sleep(0.05)
    assert clock.real_time_s == pytest.approx(banked, abs=1e-6)
    assert banked >= 0.02


def test_resume_continues_from_the_same_tick():
    clock = SimulationClock()
    clock.start()
    for _ in range(10):
        clock.advance()
    clock.pause()
    clock.resume()

    assert clock.state is ClockState.RUNNING
    assert clock.tick_count == 10


def test_reset_returns_to_zero_but_keeps_configuration():
    clock = SimulationClock(tick_rate_hz=120, speed=5.0)
    clock.start()
    clock.advance()
    clock.reset()

    assert clock.tick_count == 0
    assert clock.simulation_time == 0.0
    assert clock.real_time_s == 0.0
    assert clock.state is ClockState.STOPPED
    # Tick rate and speed are configuration, not run state.
    assert clock.tick_rate_hz == 120
    assert clock.speed == 5.0


def test_step_while_paused_is_allowed():
    """Single-step debugging must work without resuming the clock."""
    clock = SimulationClock()
    clock.start()
    clock.pause()
    clock.advance()
    assert clock.tick_count == 1
    assert clock.state is ClockState.PAUSED


def test_lifecycle_transitions_are_idempotent():
    clock = SimulationClock()
    clock.start()
    clock.start()
    assert clock.state is ClockState.RUNNING

    clock.resume()  # not paused — no effect
    assert clock.state is ClockState.RUNNING

    clock.pause()
    clock.pause()
    assert clock.state is ClockState.PAUSED


def test_realtime_factor_reports_zero_before_running():
    assert SimulationClock().realtime_factor == 0.0


def test_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        SimulationClock(tick_rate_hz=0)
    with pytest.raises(ValueError):
        SimulationClock(speed=0)
    with pytest.raises(ValueError):
        SimulationClock().set_speed(-1.0)


def test_snapshot_exposes_the_api_contract():
    snapshot = SimulationClock().snapshot()
    assert {
        "state",
        "tick",
        "tick_rate_hz",
        "dt",
        "simulation_time",
        "real_time_s",
        "speed",
        "realtime_factor",
    } <= set(snapshot)
