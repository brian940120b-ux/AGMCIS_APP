"""Safety limits for the control path (PHASE 4).

These are simulation guard-rails, not a model of any real flight-control system.
They exist so that a bad command — from a rule agent, an untrained RL policy or
a malformed API call — cannot corrupt the world state or drive the aircraft into
a physically meaningless condition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ViolationType(StrEnum):
    """What was wrong with a command."""

    NON_FINITE = "NON_FINITE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    RATE_LIMITED = "RATE_LIMITED"
    LOAD_FACTOR = "LOAD_FACTOR"
    ALTITUDE_FLOOR = "ALTITUDE_FLOOR"
    ALTITUDE_CEILING = "ALTITUDE_CEILING"
    INVALID_STATE = "INVALID_STATE"


class ViolationAction(StrEnum):
    """What the safety layer did about it."""

    CLAMPED = "CLAMPED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class SafetyLimits:
    """Bounds enforced between an agent's action and the physics."""

    # Maximum change in a normalised control channel per second. Real surfaces
    # cannot slew instantly, and an unbounded step lets a policy chatter the
    # aircraft into nonsense between two ticks.
    max_control_rate_per_s: float = 4.0

    # Structural load limit. PHASE 2 showed full elevator commanding ~15 g,
    # which is physically consistent but not something a platform should be
    # allowed to sustain.
    max_load_factor: float = 9.0

    # Soft altitude band. Inside the buffer the controller biases the elevator
    # rather than blocking it outright, so recovery is gradual.
    min_altitude_m: float = 100.0
    max_altitude_m: float = 19_000.0
    altitude_buffer_m: float = 500.0

    # A command is rejected outright when the aircraft state itself is invalid.
    reject_on_invalid_state: bool = True
