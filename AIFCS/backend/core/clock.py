"""Simulation clock (PHASE 1).

AIFCS keeps three different notions of time apart, because conflating them is
what makes a simulator irreproducible:

* **Simulation time** — ``tick_count * dt``. Advances only when the engine ticks,
  never when paused, and is completely independent of how fast the host runs.
* **Real time** — wall-clock seconds spent running, excluding paused periods.
  Used to measure whether the simulation is keeping up, never to drive physics.
* **Render time** — owned by the browser. The backend never uses it.

The timestep is fixed. Speed multipliers change how often a tick is *requested*
in real time; they never change ``dt``. That is what allows a run at 50x to
produce exactly the same trajectory as a run at 1x.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class ClockState(StrEnum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


@dataclass
class SimulationClock:
    """Fixed-timestep clock driving the simulation engine."""

    tick_rate_hz: int = 60
    speed: float = 1.0

    state: ClockState = ClockState.STOPPED
    tick_count: int = 0

    # Wall-clock bookkeeping. ``_running_since`` is None whenever not RUNNING.
    _running_since: float | None = field(default=None, repr=False)
    _accumulated_real_s: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        if self.tick_rate_hz <= 0:
            raise ValueError("tick_rate_hz must be positive")
        if self.speed <= 0:
            raise ValueError("speed must be positive")

    # ------------------------------------------------------------------ time

    @property
    def dt(self) -> float:
        """Fixed timestep in simulation seconds. Never affected by speed."""
        return 1.0 / float(self.tick_rate_hz)

    @property
    def simulation_time(self) -> float:
        """Simulation seconds elapsed — a pure function of the tick count."""
        return self.tick_count * self.dt

    @property
    def real_time_s(self) -> float:
        """Wall-clock seconds spent running, excluding paused periods."""
        elapsed = self._accumulated_real_s
        if self.state is ClockState.RUNNING and self._running_since is not None:
            elapsed += time.monotonic() - self._running_since
        return elapsed

    @property
    def target_interval_s(self) -> float:
        """Real seconds that *should* pass between ticks at the current speed."""
        return self.dt / self.speed

    @property
    def realtime_factor(self) -> float:
        """Simulation seconds produced per real second (0.0 before any run).

        Compare against ``speed`` to see whether the host is keeping up.
        """
        real = self.real_time_s
        return self.simulation_time / real if real > 0 else 0.0

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Begin running from the current tick. Idempotent while RUNNING."""
        if self.state is ClockState.RUNNING:
            return
        self.state = ClockState.RUNNING
        self._running_since = time.monotonic()

    def pause(self) -> None:
        """Stop advancing simulation time; banks the real time spent so far."""
        if self.state is not ClockState.RUNNING:
            return
        if self._running_since is not None:
            self._accumulated_real_s += time.monotonic() - self._running_since
        self._running_since = None
        self.state = ClockState.PAUSED

    def resume(self) -> None:
        """Resume from PAUSED. Has no effect in any other state."""
        if self.state is not ClockState.PAUSED:
            return
        self.state = ClockState.RUNNING
        self._running_since = time.monotonic()

    def stop(self) -> None:
        """Halt the clock, keeping the tick count for inspection."""
        if self.state is ClockState.RUNNING and self._running_since is not None:
            self._accumulated_real_s += time.monotonic() - self._running_since
        self._running_since = None
        self.state = ClockState.STOPPED

    def reset(self) -> None:
        """Return to tick zero. Speed and tick rate are configuration, so kept."""
        self.state = ClockState.STOPPED
        self.tick_count = 0
        self._running_since = None
        self._accumulated_real_s = 0.0

    # ------------------------------------------------------------------ tick

    def advance(self) -> float:
        """Advance exactly one fixed timestep and return the new simulation time.

        Deliberately allowed while PAUSED or STOPPED: that is what single-step
        debugging (``POST /api/simulation/step``) needs.
        """
        self.tick_count += 1
        return self.simulation_time

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.speed = float(speed)

    def snapshot(self) -> dict[str, float | int | str]:
        """Serialisable view for the API and telemetry."""
        return {
            "state": self.state.value,
            "tick": self.tick_count,
            "tick_rate_hz": self.tick_rate_hz,
            "dt": self.dt,
            "simulation_time": self.simulation_time,
            "real_time_s": round(self.real_time_s, 4),
            "speed": self.speed,
            "realtime_factor": round(self.realtime_factor, 4),
        }
