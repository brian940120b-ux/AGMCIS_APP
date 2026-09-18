"""Action validation (PHASE 4).

The first gate between an agent and the aircraft. It answers one question: is
this command something the simulation can safely execute?

Policy, in order:

1. **Reject** when the aircraft state is not usable (non-finite position,
   velocity or attitude). Nothing sensible can be commanded into that.
2. **Clamp** a command that is merely out of range or non-finite in one channel.
   Clamping keeps the aircraft flying; rejecting would leave it with stale
   controls, which is usually worse.
3. **Log** every violation, so a policy that constantly saturates its controls
   is visible rather than silently corrected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from controllers.limits import SafetyLimits, ViolationAction, ViolationType
from core.world_state import EntityState
from simulation.aircraft import ControlInputs


@dataclass(frozen=True)
class Violation:
    """One thing the safety layer had to correct."""

    type: ViolationType
    action: ViolationAction
    channel: str
    detail: str
    original: float | None = None
    applied: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "action": self.action.value,
            "channel": self.channel,
            "detail": self.detail,
            "original": self.original,
            "applied": self.applied,
        }


@dataclass
class ValidationResult:
    """The command that may be applied, plus why it differs from the request."""

    controls: ControlInputs
    accepted: bool = True
    violations: list[Violation] = field(default_factory=list)

    @property
    def modified(self) -> bool:
        return bool(self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "controls": self.controls.to_dict(),
            "violations": [v.to_dict() for v in self.violations],
        }


def entity_state_is_valid(entity: EntityState) -> bool:
    """True when the aircraft state can support a control command at all."""
    return bool(
        np.all(np.isfinite(entity.position))
        and np.all(np.isfinite(entity.velocity))
        and np.all(np.isfinite(entity.attitude_quaternion))
        and np.all(np.isfinite(entity.angular_velocity))
    )


class ActionValidator:
    """Checks a commanded action against the safety limits."""

    # (channel name, lower bound, upper bound)
    CHANNELS: tuple[tuple[str, float, float], ...] = (
        ("aileron", -1.0, 1.0),
        ("elevator", -1.0, 1.0),
        ("rudder", -1.0, 1.0),
        ("throttle", 0.0, 1.0),
    )

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        self.limits = limits or SafetyLimits()

    def validate(self, controls: ControlInputs, entity: EntityState) -> ValidationResult:
        violations: list[Violation] = []

        if self.limits.reject_on_invalid_state and not entity_state_is_valid(entity):
            # Hold the last command rather than layering a new one on a broken state.
            return ValidationResult(
                controls=entity.controls,
                accepted=False,
                violations=[
                    Violation(
                        type=ViolationType.INVALID_STATE,
                        action=ViolationAction.REJECTED,
                        channel="*",
                        detail=f"{entity.id} has a non-finite state; command not applied",
                    )
                ],
            )

        values: dict[str, float] = {}
        for channel, low, high in self.CHANNELS:
            requested = float(getattr(controls, channel))

            if not np.isfinite(requested):
                # Neutral for every channel, including throttle. The rate
                # limiter downstream makes the transition gradual, so this does
                # not slam the controls shut in a single tick.
                values[channel] = 0.0
                violations.append(
                    Violation(
                        type=ViolationType.NON_FINITE,
                        action=ViolationAction.CLAMPED,
                        channel=channel,
                        detail="non-finite demand replaced with neutral",
                        original=None,
                        applied=0.0,
                    )
                )
                continue

            clamped = min(max(requested, low), high)
            values[channel] = clamped
            if clamped != requested:
                violations.append(
                    Violation(
                        type=ViolationType.OUT_OF_RANGE,
                        action=ViolationAction.CLAMPED,
                        channel=channel,
                        detail=f"demand outside [{low}, {high}]",
                        original=requested,
                        applied=clamped,
                    )
                )

        return ValidationResult(controls=ControlInputs(**values), violations=violations)
