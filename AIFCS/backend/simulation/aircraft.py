"""Fictional aircraft definitions (PHASE 2).

Every parameter here is **invented for simulation research**. The numbers are
chosen so the resulting dynamics are stable and illustrative, not to represent
any real aircraft's mass, aerodynamics or performance.

Coefficients follow standard flight-dynamics naming so the model is readable to
anyone who has seen a textbook treatment, but the values are arbitrary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class ControlInputs:
    """Abstract control-surface demands.

    Deflections are normalised to [-1, 1] and throttle to [0, 1]; they are not
    angles. Mapping a demand to a deflection is the flight controller's job
    (PHASE 4).

    The channels are **demands in the intuitive sense**, so that an agent does
    not have to know the sign convention of a control surface:

    * ``aileron``  > 0 rolls right (right wing down)
    * ``elevator`` > 0 pitches nose up
    * ``rudder``   > 0 yaws nose right
    * ``throttle`` 0 = idle, 1 = maximum thrust
    """

    aileron: float = 0.0
    elevator: float = 0.0
    rudder: float = 0.0
    throttle: float = 0.0

    def clamped(self) -> ControlInputs:
        """Return a copy with every channel inside its valid range.

        Non-finite input collapses to neutral rather than poisoning the physics
        with NaN — the safety layer in PHASE 4 also rejects it upstream.
        """

        def surface(value: float) -> float:
            return float(np.clip(value, -1.0, 1.0)) if np.isfinite(value) else 0.0

        def throttle(value: float) -> float:
            return float(np.clip(value, 0.0, 1.0)) if np.isfinite(value) else 0.0

        return ControlInputs(
            aileron=surface(self.aileron),
            elevator=surface(self.elevator),
            rudder=surface(self.rudder),
            throttle=throttle(self.throttle),
        )

    def as_array(self) -> np.ndarray:
        return np.array([self.aileron, self.elevator, self.rudder, self.throttle], dtype=np.float64)

    def to_dict(self) -> dict[str, float]:
        return {
            "aileron": self.aileron,
            "elevator": self.elevator,
            "rudder": self.rudder,
            "throttle": self.throttle,
        }


@dataclass(frozen=True)
class AircraftParameters:
    """Mass, geometry and aerodynamic coefficients of a fictional platform."""

    name: str = "fictional_aircraft"

    # Mass and inertia. Inertia is diagonal: the platform is assumed symmetric.
    mass_kg: float = 9000.0
    inertia_xx: float = 12000.0
    inertia_yy: float = 75000.0
    inertia_zz: float = 85000.0

    # Reference geometry.
    wing_area_m2: float = 28.0
    wing_span_m: float = 10.0
    mean_chord_m: float = 3.2

    # Propulsion.
    max_thrust_n: float = 75000.0

    # Longitudinal aerodynamics. cl_0 is zero and the airframe trims on a small
    # positive angle of attack instead, which is what makes hands-off flight at
    # the demo cruise speed come out level.
    cl_0: float = 0.0  # lift at zero angle of attack
    cl_alpha: float = 4.8  # lift-curve slope, per radian
    cl_max: float = 1.6  # stall ceiling on the lift coefficient
    cd_0: float = 0.022  # parasite drag
    induced_drag_k: float = 0.09  # induced-drag factor: Cd = Cd0 + k * Cl^2

    # Lateral aerodynamics.
    cy_beta: float = -0.9  # side force from sideslip (restoring)

    # Static stability. Negative Cm_alpha and positive Cn_beta make the airframe
    # naturally return towards trimmed flight. cm_0 sets the trim angle of
    # attack: alpha_trim = cm_0 / -cm_alpha, chosen so that lift balances weight
    # at roughly 220 m/s, the cruise speed used by the demo scenario.
    cm_0: float = 0.0122
    cm_alpha: float = -0.55
    cn_beta: float = 0.12
    cl_beta: float = -0.08  # dihedral effect: sideslip induces roll

    # Control power, per unit of normalised demand. Signs follow the demand
    # convention documented on ControlInputs, not physical surface deflection.
    #
    # Sized from the authority each channel should have at full demand, so the
    # airframe is firm but flyable by a controller running at 10 Hz:
    #   elevator -> alpha_max   = cm_elevator / -cm_alpha   ~ 20 deg
    #   aileron  -> roll rate   = cl_aileron / -clp * 2V/b  ~ 200 deg/s
    #   rudder   -> sideslip    = cn_rudder / cn_beta       ~ 10 deg
    cl_aileron: float = 0.035
    cm_elevator: float = 0.18
    cn_rudder: float = 0.022

    # Rotary damping, per unit of normalised rate.
    clp: float = -0.42  # roll damping
    cmq: float = -9.0  # pitch damping
    cnr: float = -0.65  # yaw damping

    @property
    def inertia(self) -> np.ndarray:
        return np.array([self.inertia_xx, self.inertia_yy, self.inertia_zz], dtype=np.float64)

    def to_dict(self) -> dict[str, float | str]:
        return {
            "name": self.name,
            "mass_kg": self.mass_kg,
            "wing_area_m2": self.wing_area_m2,
            "wing_span_m": self.wing_span_m,
            "mean_chord_m": self.mean_chord_m,
            "max_thrust_n": self.max_thrust_n,
        }


# Catalogue of fictional platforms. Scenarios reference these by name.
FICTIONAL_AIRCRAFT: dict[str, AircraftParameters] = {
    "fictional_aircraft": AircraftParameters(),
    # A lighter, more agile variant used to give BLUE and RED different handling.
    "fictional_interceptor": AircraftParameters(
        name="fictional_interceptor",
        mass_kg=7200.0,
        inertia_xx=9000.0,
        inertia_yy=58000.0,
        inertia_zz=66000.0,
        wing_area_m2=24.0,
        max_thrust_n=68000.0,
        cl_aileron=0.045,
        cm_elevator=0.21,
    ),
}


def get_aircraft(name: str | None) -> AircraftParameters:
    """Look up a fictional platform, falling back to the default airframe."""
    if not name:
        return FICTIONAL_AIRCRAFT["fictional_aircraft"]
    return FICTIONAL_AIRCRAFT.get(name, FICTIONAL_AIRCRAFT["fictional_aircraft"])


@dataclass
class AircraftState:
    """Per-entity physics context carried alongside the truth state."""

    parameters: AircraftParameters = field(default_factory=AircraftParameters)
    controls: ControlInputs = field(default_factory=ControlInputs)
