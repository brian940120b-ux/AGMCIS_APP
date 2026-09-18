"""Autopilot: guidance and stabilisation loops (PHASE 3, moved here in PHASE 4).

Turns a target (heading, altitude, speed) into control demands. Deliberately
simple and transparent: cascaded proportional loops with explicit limits, so
every command traces back to a measured error.

    heading error   -> bank command  -> aileron
    altitude error  -> pitch command -> elevator
    speed error     -> throttle

It produces *demands*. Whether those demands reach the aircraft is the flight
controller's decision, after the safety layer has checked them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from simulation.aircraft import ControlInputs

# Trim throttle for level flight at the demo cruise speed. The speed loop works
# as a correction around this rather than from zero, so a small speed error does
# not command idle or full power.
TRIM_THROTTLE = 0.26


@dataclass
class GuidanceState:
    """Integrator memory. Owned by the agent and cleared on reset, so a replay
    of the same seed reproduces the same commands."""

    altitude_integral: float = 0.0

    def reset(self) -> None:
        self.altitude_integral = 0.0


@dataclass(frozen=True)
class GuidanceGains:
    """Tuning for the guidance loops. Held in config, never hard-coded inline."""

    max_bank_deg: float = 60.0
    max_pitch_deg: float = 25.0

    # Outer loops: error -> attitude command.
    heading_to_bank: float = 1.1  # bank degrees per degree of heading error
    # Damping on the turn rate. Without it the heading loop has no derivative
    # term and hunts: a large heading error commands a steep bank, the aircraft
    # overshoots, and the cycle repeats with the sign flipped.
    turn_rate_damping: float = 4.0  # bank degrees per degree/second of yaw rate
    altitude_to_pitch: float = 0.02  # pitch degrees per metre of altitude error

    # Inner loops: attitude error -> surface demand.
    bank_to_aileron: float = 0.035
    roll_rate_damping: float = 0.22
    pitch_to_elevator: float = 0.055
    pitch_rate_damping: float = 0.35

    # Integral action on altitude. A purely proportional loop must hold a
    # standing error to command the trim pitch attitude, so it settles about
    # 60 m low. The integral term removes that droop; the limit bounds windup.
    altitude_integral_to_pitch: float = 0.0022
    altitude_integral_limit: float = 2500.0  # metre-seconds

    # Speed loop.
    speed_to_throttle: float = 0.006

    # Extra back pressure in a turn, where the vertical lift component falls as
    # 1/cos(bank). Without it the aircraft descends through every turn.
    turn_compensation: float = 0.9


def heading_error_deg(target_deg: float, current_deg: float) -> float:
    """Signed smallest angle from current to target, in [-180, 180]."""
    return float((target_deg - current_deg + 180.0) % 360.0 - 180.0)


def bearing_deg(from_position: np.ndarray, to_position: np.ndarray) -> float:
    """Compass bearing between two ENU positions: 0 = north, 90 = east."""
    delta = to_position - from_position
    return float(np.degrees(np.arctan2(delta[0], delta[1])) % 360.0)


def compute_controls(
    *,
    orientation: np.ndarray,
    angular_velocity: np.ndarray,
    altitude_m: float,
    speed_mps: float,
    heading_deg: float,
    target_heading_deg: float,
    target_altitude_m: float,
    target_speed_mps: float,
    gains: GuidanceGains,
    state: GuidanceState | None = None,
    dt: float = 0.1,
) -> ControlInputs:
    """Control demands that steer the aircraft towards the given targets."""
    roll, pitch, _ = orientation
    roll_rate, pitch_rate, _ = angular_velocity

    # --- Lateral: heading error -> bank command -> aileron.
    heading_err = heading_error_deg(target_heading_deg, heading_deg)
    yaw_rate_deg = float(np.degrees(angular_velocity[2]))
    bank_cmd_deg = float(
        np.clip(
            gains.heading_to_bank * heading_err - gains.turn_rate_damping * yaw_rate_deg,
            -gains.max_bank_deg,
            gains.max_bank_deg,
        )
    )
    bank_err_deg = bank_cmd_deg - np.degrees(roll)
    aileron = gains.bank_to_aileron * bank_err_deg - gains.roll_rate_damping * roll_rate

    # --- Longitudinal: altitude error -> pitch command -> elevator.
    altitude_err = target_altitude_m - altitude_m

    integral_term = 0.0
    if state is not None:
        state.altitude_integral = float(
            np.clip(
                state.altitude_integral + altitude_err * dt,
                -gains.altitude_integral_limit,
                gains.altitude_integral_limit,
            )
        )
        integral_term = gains.altitude_integral_to_pitch * state.altitude_integral

    pitch_cmd_deg = float(
        np.clip(
            gains.altitude_to_pitch * altitude_err + integral_term,
            -gains.max_pitch_deg,
            gains.max_pitch_deg,
        )
    )
    pitch_err_deg = pitch_cmd_deg - np.degrees(pitch)
    elevator = gains.pitch_to_elevator * pitch_err_deg - gains.pitch_rate_damping * pitch_rate

    # Lift loss in a bank goes as 1/cos(bank); add matching back pressure.
    bank_rad = float(np.clip(roll, -np.pi / 2 + 0.05, np.pi / 2 - 0.05))
    elevator += gains.turn_compensation * (1.0 / np.cos(bank_rad) - 1.0)

    # --- Speed loop around the trim setting.
    throttle = TRIM_THROTTLE + gains.speed_to_throttle * (target_speed_mps - speed_mps)

    return ControlInputs(
        aileron=aileron,
        elevator=elevator,
        rudder=0.0,  # Turns are flown with bank; the rudder is unused until PHASE 4.
        throttle=throttle,
    ).clamped()
