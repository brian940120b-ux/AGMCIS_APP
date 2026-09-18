"""Command mapping and actuator rate limiting (PHASE 4).

Between a validated demand and the aircraft sits an actuator. Real surfaces move
at a finite rate, and letting a command jump from -1 to +1 in a single tick lets
a noisy policy chatter the aircraft into behaviour the physics would never
produce. This module enforces that rate.

The limiter keeps one small piece of state per entity — the surface position it
last commanded — so it is cleared on reset to keep runs reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

from controllers.action_validator import Violation
from controllers.limits import SafetyLimits, ViolationAction, ViolationType
from simulation.aircraft import ControlInputs

CHANNELS = ("aileron", "elevator", "rudder", "throttle")


@dataclass
class ActuatorState:
    """Where each surface currently is."""

    aileron: float = 0.0
    elevator: float = 0.0
    rudder: float = 0.0
    throttle: float = 0.0

    def as_controls(self) -> ControlInputs:
        return ControlInputs(
            aileron=self.aileron,
            elevator=self.elevator,
            rudder=self.rudder,
            throttle=self.throttle,
        )


class CommandMapper:
    """Applies actuator rate limits to validated demands."""

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        self.limits = limits or SafetyLimits()
        self._state: dict[str, ActuatorState] = {}

    def reset(self) -> None:
        """Clear actuator memory so a replayed run starts identically."""
        self._state.clear()

    def seed(self, entity_id: str, controls: ControlInputs) -> None:
        """Initialise actuator position, e.g. from a scenario's trim setting."""
        self._state[entity_id] = ActuatorState(
            aileron=controls.aileron,
            elevator=controls.elevator,
            rudder=controls.rudder,
            throttle=controls.throttle,
        )

    def map(self, entity_id: str, demand: ControlInputs, dt: float) -> tuple[ControlInputs, list[Violation]]:
        """Move the actuators towards the demand, no faster than the rate limit."""
        state = self._state.setdefault(entity_id, ActuatorState())
        max_step = self.limits.max_control_rate_per_s * dt

        violations: list[Violation] = []
        for channel in CHANNELS:
            current = getattr(state, channel)
            wanted = float(getattr(demand, channel))
            delta = wanted - current

            if abs(delta) > max_step:
                applied = current + max_step * (1.0 if delta > 0 else -1.0)
                violations.append(
                    Violation(
                        type=ViolationType.RATE_LIMITED,
                        action=ViolationAction.CLAMPED,
                        channel=channel,
                        detail=f"demand slewed at the {self.limits.max_control_rate_per_s}/s limit",
                        original=wanted,
                        applied=applied,
                    )
                )
            else:
                applied = wanted

            setattr(state, channel, applied)

        return state.as_controls(), violations
