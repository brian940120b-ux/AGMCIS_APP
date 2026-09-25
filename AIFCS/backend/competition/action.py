"""Stick shaping: what the policy asks for, turned into what the aircraft gets.

The organiser puts a simulated joystick between the policy and the control
surfaces — deadbands, an exponential curve, a rate limit per axis — so a policy
cannot snap an aileron from hard left to hard right in one frame of 1/60 s. That
shaping is part of the plant as far as the policy is concerned: train without it
and the policy learns to fly an aircraft that does not exist.

Two things differ between the organiser's two copies, and both are resolved here
in the direction that competition day forces:

* **The elevator limit.** The client halves-and-then-some the elevator above the
  speed threshold; the trainer's two branches are both 1.0, which is no limit at
  all. The limit is kept: the scoring subtracts 1000 per second spent above 9G,
  weighted within a factor of two of what a second of tracking is worth, so a
  high-speed elevator limit is a G limiter that the scoring is asking for.
* **What the threshold is measured against.** The trainer reads JSBSim's Mach,
  which uses the local speed of sound. The OBS packet carries no Mach and no
  temperature, so on the day there is nothing to compute it from. True airspeed
  over a fixed 340 m/s is what the client uses and what is used here — in both
  paths, which is the part that matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Below this, an axis command is treated as zero.
DEADBANDS = np.array([0.01, 0.03, 0.06], dtype=np.float64)
#: |x|**e * sign(x). Higher exponents give finer control near centre.
EXPONENTS = np.array([1.0, 3.0, 3.0], dtype=np.float64)
#: Most an axis may move in one 1/60 s frame.
MAX_CHANGE_PER_STEP = np.array([0.050, 0.026, 0.013], dtype=np.float64)
THROTTLE_RATE_LIMIT = 0.004

#: Rudder authority. The organiser's action space pins the rudder to
#: [0, 1e-17] — so the reference policy has never used it — while the stick
#: shaping is written for +/-0.2. The shaping value is the honest one, and the
#: pinned action space is what an opponent trained against that space cannot do.
RUDDER_LIMIT = 0.2
AILERON_LIMIT = 1.0
ELEVATOR_LIMIT = 1.0
#: Applied above `ELEVATOR_LIMIT_ABOVE_MACH`.
ELEVATOR_LIMIT_HIGH_SPEED = 0.4
ELEVATOR_LIMIT_ABOVE_MACH = 0.8

#: Throttle the aircraft starts a round holding, per the reference reset.
INITIAL_THROTTLE = 0.8


@dataclass
class JoystickState:
    """The stick's position, which the next command moves from rather than to.

    A rate limit only means something relative to where the stick already is, so
    this has to persist between frames — and be cleared between rounds. In the
    reference client it is a module global that is never cleared, so round two
    starts from wherever round one's last frame left the stick.
    """

    last_command: np.ndarray = field(
        default_factory=lambda: np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE], dtype=np.float64)
    )

    def reset(self) -> None:
        """Back to centre with the reference's starting throttle."""
        self.last_command = np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE], dtype=np.float64)


def elevator_limit_for(reference_mach: float, high_speed_limit: float = ELEVATOR_LIMIT_HIGH_SPEED) -> float:
    """The elevator authority available at this speed.

    `high_speed_limit` is an argument because the reference package cannot
    agree with itself about it — the client limits, the trainer does not — and
    a policy trained under one and flown under the other meets a control
    authority it has never seen. Passing 1.0 reproduces the trainer exactly,
    which is what it takes to run a reference policy on the terms it learned.
    """
    if reference_mach > ELEVATOR_LIMIT_ABOVE_MACH:
        return high_speed_limit
    return ELEVATOR_LIMIT


def shape_command(
    raw_action: np.ndarray,
    joystick: JoystickState,
    reference_mach: float,
    *,
    high_speed_elevator_limit: float = ELEVATOR_LIMIT_HIGH_SPEED,
) -> np.ndarray:
    """One frame of stick shaping. Mutates `joystick`, returns the new command.

    `raw_action` is the policy's four channels: aileron, elevator, rudder,
    throttle.
    """
    limits = np.array(
        [
            AILERON_LIMIT,
            elevator_limit_for(reference_mach, high_speed_elevator_limit),
            RUDDER_LIMIT,
        ],
        dtype=np.float64,
    )
    previous = joystick.last_command
    shaped = np.zeros(4, dtype=np.float64)

    for axis in range(3):
        value = float(raw_action[axis])
        if abs(value) < DEADBANDS[axis]:
            value = 0.0
        value = float(np.sign(value) * (abs(value) ** EXPONENTS[axis]))
        value = float(np.clip(value, -limits[axis], limits[axis]))
        change = float(
            np.clip(
                value - previous[axis],
                -MAX_CHANGE_PER_STEP[axis],
                MAX_CHANGE_PER_STEP[axis],
            )
        )
        shaped[axis] = previous[axis] + change

    target_throttle = float(np.clip(raw_action[3], 0.0, 1.0))
    throttle_change = float(np.clip(target_throttle - previous[3], -THROTTLE_RATE_LIMIT, THROTTLE_RATE_LIMIT))
    shaped[3] = previous[3] + throttle_change

    joystick.last_command = shaped
    return shaped
