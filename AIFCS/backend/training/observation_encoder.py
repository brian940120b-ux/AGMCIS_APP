"""Turning an agent's observation into a policy input (PHASE 11).

A neural network needs a fixed-size vector of bounded numbers. An
``Observation`` is a variable-length, unbounded, physical thing. This module is
the boundary between them, and it is deliberately the only place that knows how
to cross it.

Three rules hold throughout:

* **Every element is bounded and finite.** An unbounded input is how a policy
  learns to exploit one enormous number, and a NaN is how training dies twenty
  minutes in. Everything is normalised and clipped.
* **It encodes the observation, never the world.** What goes in is what the
  sensor model and datalink gave the agent — noisy, delayed, sometimes missing.
  Encoding truth here would train a policy that cannot fly once deployed
  against the real perception pipeline.
* **The layout is documented and asserted.** A silent change to the ordering
  would make every saved model quietly wrong rather than loudly broken, so the
  layout has a version and the length is checked.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from agents.base_agent import ContactView, Observation

# Bumped whenever the layout changes. A model trained on one layout cannot be
# used with another, and the version is what makes that detectable.
LAYOUT_VERSION = 1

EGO_FEATURES = 12
CONTACT_FEATURES = 9
GOAL_FEATURES = 7


@dataclass(frozen=True)
class EncoderConfig:
    """Scales that turn physical quantities into roughly unit-sized numbers.

    These are normalisation constants, not limits on the simulation: a value
    beyond its scale is clipped in the vector while the aircraft carries on.
    """

    max_contacts: int = 3
    max_altitude_m: float = 20000.0
    reference_speed_mps: float = 400.0
    max_range_m: float = 80000.0
    max_rate_radps: float = 3.0
    max_vertical_speed_mps: float = 100.0
    track_memory_s: float = 3.0
    goal_scale_m: float = 20000.0

    @property
    def size(self) -> int:
        return EGO_FEATURES + self.max_contacts * CONTACT_FEATURES + GOAL_FEATURES


@dataclass(frozen=True)
class Goal:
    """Where the policy is being asked to go, in world metres.

    ``None`` means the episode has no navigation goal, which the vector says
    explicitly rather than by encoding a zero that looks like "straight ahead".
    """

    position: np.ndarray


def _clip(value: float, limit: float = 1.0) -> float:
    """Bound a value and turn any non-finite result into zero.

    A NaN reaching the policy is worse than a wrong number: it propagates
    through the network and poisons the optimiser, and the run dies far from
    the cause.
    """
    if not np.isfinite(value):
        return 0.0
    return float(np.clip(value, -limit, limit))


def _encode_contact(contact: ContactView | None, config: EncoderConfig) -> list[float]:
    """Nine numbers per contact slot; all zeros when the slot is empty.

    The leading "present" flag is what lets the policy tell an empty slot from
    a contact that happens to be at zero relative position.
    """
    if contact is None:
        return [0.0] * CONTACT_FEATURES

    relative = contact.relative_position
    return [
        1.0,
        _clip(float(relative[0]) / config.max_range_m),
        _clip(float(relative[1]) / config.max_range_m),
        _clip(float(relative[2]) / config.max_range_m),
        _clip(contact.distance_m / config.max_range_m),
        _clip(float(np.linalg.norm(contact.relative_velocity)) / config.reference_speed_mps),
        1.0 if contact.is_friendly else 0.0,
        _clip(contact.age_s / config.track_memory_s),
        _clip(contact.confidence),
    ]


def encode(observation: Observation, goal: Goal | None, config: EncoderConfig) -> np.ndarray:
    """Encode one observation into the policy's input vector.

    Layout (``LAYOUT_VERSION`` 1)::

        [ 0]  altitude / max_altitude
        [ 1]  speed / reference_speed
        [ 2]  sin(heading)
        [ 3]  cos(heading)
        [ 4]  roll / (pi/2)
        [ 5]  pitch / (pi/2)
        [ 6]  sin(yaw)
        [ 7]  cos(yaw)
        [ 8]  roll rate / max_rate
        [ 9]  pitch rate / max_rate
        [10]  yaw rate / max_rate
        [11]  observation confidence
        then max_contacts x 9:
              present, rel x, rel y, rel z, distance, closing speed,
              friendly, track age, track confidence
        then 7 goal features:
              present, rel x, rel y, rel z, distance, sin(bearing error),
              cos(bearing error)
    """
    heading = np.radians(observation.heading_deg)
    roll, pitch, yaw = (float(v) for v in observation.orientation)
    p, q, r = (float(v) for v in observation.angular_velocity)

    vector: list[float] = [
        _clip(observation.altitude / config.max_altitude_m),
        _clip(observation.speed / config.reference_speed_mps),
        _clip(float(np.sin(heading))),
        _clip(float(np.cos(heading))),
        _clip(roll / (np.pi / 2)),
        _clip(pitch / (np.pi / 2)),
        _clip(float(np.sin(yaw))),
        _clip(float(np.cos(yaw))),
        _clip(p / config.max_rate_radps),
        _clip(q / config.max_rate_radps),
        _clip(r / config.max_rate_radps),
        _clip(observation.confidence),
    ]

    # Nearest first: a fixed number of slots has to drop something, and the
    # thing worth keeping is what is closest.
    contacts = sorted(observation.contacts, key=lambda c: c.distance_m)
    for index in range(config.max_contacts):
        contact = contacts[index] if index < len(contacts) else None
        vector.extend(_encode_contact(contact, config))

    if goal is None:
        vector.extend([0.0] * GOAL_FEATURES)
    else:
        offset = np.asarray(goal.position, dtype=np.float64) - observation.position
        distance = float(np.linalg.norm(offset))
        # Bearing error: how far the goal is off the nose, which is the thing a
        # turn has to null. Sin and cos so the wrap at +/-180 is not a cliff.
        bearing = np.arctan2(float(offset[0]), float(offset[1]))
        error = bearing - heading
        vector.extend(
            [
                1.0,
                _clip(float(offset[0]) / config.goal_scale_m),
                _clip(float(offset[1]) / config.goal_scale_m),
                _clip(float(offset[2]) / config.goal_scale_m),
                _clip(distance / config.goal_scale_m),
                _clip(float(np.sin(error))),
                _clip(float(np.cos(error))),
            ]
        )

    encoded = np.asarray(vector, dtype=np.float32)
    if encoded.size != config.size:
        raise ValueError(f"observation layout produced {encoded.size} features, expected {config.size}")
    return encoded
