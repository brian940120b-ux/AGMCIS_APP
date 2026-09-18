"""Six-degree-of-freedom rigid-body flight physics (PHASE 2).

Replaces the PHASE 1 kinematic integrator with Newton-Euler dynamics: gravity,
thrust, lift, drag, side force, control moments and rotary damping.

Frames
------
The world frame is ENU (+X east, +Y north, +Z **up**), as established in
PHASE 1 and used by the API and the dashboard.

The physics runs in the standard aerospace convention instead:

* body frame FRD — +X forward, +Y right, +Z down,
* navigation frame NED — +X north, +Y east, +Z down.

That is not gratuitous: every published aerodynamic coefficient (``Cm_alpha``,
``Cn_beta``, ``Clp`` …) carries signs that assume FRD/NED. Running them in an
up-is-positive frame silently inverts the pitch and yaw moments, which turns
static stability into divergence. Converting at the boundary keeps the
coefficients meaning what they say.

``ENU_NED`` converts between the two and is its own inverse.

Attitude is integrated as a **quaternion** (body → NED), so the model stays
valid through vertical manoeuvres where Euler angles hit gimbal lock. Roll,
pitch and yaw are derived each step for display, in the usual aviation sense:
pitch positive = nose up, roll positive = right wing down, yaw = heading from
north.

Integration is fixed-step RK4 over the 13-element state
``[position_enu(3), velocity_enu(3), quaternion(4), omega_body(3)]``. RK4 rather
than Euler because at 60 Hz it keeps a steady turn from spiralling out through
integration error alone, and it is fully deterministic.
"""

from __future__ import annotations

import math

import numpy as np

from core.world_state import EntityState, EntityStatus, WorldState
from simulation.aircraft import AircraftParameters, ControlInputs, get_aircraft

# Below this airspeed the aerodynamic model is meaningless (and the velocity
# direction is numerically ill-conditioned), so aerodynamic terms are dropped.
MIN_AIRSPEED_MPS = 1.0

# ENU <-> NED: swap east/north and flip the vertical axis. Self-inverse.
ENU_NED = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]])


# NumPy's generic implementations of cross, clip and norm carry enough
# per-call overhead that on 3-element vectors they dominate the tick. These
# helpers do the same arithmetic explicitly; the physics is unchanged.


def _norm3(v: np.ndarray) -> float:
    return math.sqrt(float(v[0]) * float(v[0]) + float(v[1]) * float(v[1]) + float(v[2]) * float(v[2]))


def _cross3(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.array(
        [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]
    )


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def enu_to_ned(vector: np.ndarray) -> np.ndarray:
    return ENU_NED @ vector


def ned_to_enu(vector: np.ndarray) -> np.ndarray:
    return ENU_NED @ vector


# --------------------------------------------------------------- quaternions


def quat_normalize(q: np.ndarray) -> np.ndarray:
    norm = math.sqrt(float(q[0]) ** 2 + float(q[1]) ** 2 + float(q[2]) ** 2 + float(q[3]) ** 2)
    if norm < 1e-12:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return q / norm


def quat_from_euler(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Quaternion (w, x, y, z) for the ZYX sequence yaw → pitch → roll."""
    cr, sr = np.cos(roll / 2), np.sin(roll / 2)
    cp, sp = np.cos(pitch / 2), np.sin(pitch / 2)
    cy, sy = np.cos(yaw / 2), np.sin(yaw / 2)
    return quat_normalize(
        np.array(
            [
                cr * cp * cy + sr * sp * sy,
                sr * cp * cy - cr * sp * sy,
                cr * sp * cy + sr * cp * sy,
                cr * cp * sy - sr * sp * cy,
            ]
        )
    )


def euler_from_quat(q: np.ndarray) -> np.ndarray:
    """Roll, pitch, yaw (radians) from a quaternion (w, x, y, z)."""
    w, x, y, z = q

    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    # Clamp guards against a domain error when the argument drifts past 1.
    pitch = math.asin(_clamp(2.0 * (w * y - z * x), -1.0, 1.0))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))

    return np.array([roll, pitch, yaw])


def rotation_body_to_ned(q: np.ndarray) -> np.ndarray:
    """3x3 rotation matrix taking a body-frame (FRD) vector into NED axes."""
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def quat_derivative(q: np.ndarray, omega_body: np.ndarray) -> np.ndarray:
    """dq/dt = 0.5 * q ⊗ (0, ω_body)."""
    w, x, y, z = q
    p, qq, r = omega_body
    return 0.5 * np.array(
        [
            -x * p - y * qq - z * r,
            w * p + y * r - z * qq,
            w * qq - x * r + z * p,
            w * r + x * qq - y * p,
        ]
    )


# ------------------------------------------------------------- aerodynamics


def aerodynamic_angles(velocity_body: np.ndarray) -> tuple[float, float, float]:
    """Airspeed, angle of attack and sideslip from body-frame (FRD) velocity.

    Standard definitions: ``alpha = atan2(w, u)`` (relative wind below the nose
    is positive) and ``beta = asin(v / V)`` (relative wind from the right is
    positive).
    """
    u, v, w = float(velocity_body[0]), float(velocity_body[1]), float(velocity_body[2])
    airspeed = _norm3(velocity_body)
    if airspeed < MIN_AIRSPEED_MPS:
        return airspeed, 0.0, 0.0

    alpha = math.atan2(w, u)
    beta = math.asin(_clamp(v / airspeed, -1.0, 1.0))
    return airspeed, alpha, beta


def _forces_and_moments(
    velocity_ned: np.ndarray,
    q: np.ndarray,
    omega_body: np.ndarray,
    params: AircraftParameters,
    controls: ControlInputs,
    gravity: float,
    air_density: float,
    wind_ned: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Net force in **NED** axes and net moment in **body** axes."""
    rotation = rotation_body_to_ned(q)
    velocity_body = rotation.T @ (velocity_ned - wind_ned)
    airspeed, alpha, beta = aerodynamic_angles(velocity_body)

    # Thrust acts along the body X axis (forward).
    force_body = np.array([params.max_thrust_n * controls.throttle, 0.0, 0.0])
    moment_body = np.zeros(3)

    if airspeed >= MIN_AIRSPEED_MPS:
        q_s = 0.5 * air_density * airspeed * airspeed * params.wing_area_m2

        # Lift coefficient saturates, which models stall instead of letting
        # lift grow without bound at extreme angles of attack.
        cl = _clamp(params.cl_0 + params.cl_alpha * alpha, -params.cl_max, params.cl_max)
        cd = params.cd_0 + params.induced_drag_k * cl * cl

        lift = q_s * cl
        drag = q_s * cd
        side = q_s * params.cy_beta * beta

        # Stability-axis lift and drag resolved into body axes.
        ca, sa = math.cos(alpha), math.sin(alpha)
        force_body += np.array(
            [
                -drag * ca + lift * sa,
                side,
                -drag * sa - lift * ca,
            ]
        )

        # Non-dimensional rates; the 2V normalisation is the standard form.
        span, chord = params.wing_span_m, params.mean_chord_m
        p, qq, r = float(omega_body[0]), float(omega_body[1]), float(omega_body[2])
        p_hat = p * span / (2.0 * airspeed)
        q_hat = qq * chord / (2.0 * airspeed)
        r_hat = r * span / (2.0 * airspeed)

        moment_body = np.array(
            [
                q_s
                * span
                * (params.cl_aileron * controls.aileron + params.cl_beta * beta + params.clp * p_hat),
                q_s
                * chord
                * (
                    params.cm_0
                    + params.cm_alpha * alpha
                    + params.cm_elevator * controls.elevator
                    + params.cmq * q_hat
                ),
                q_s
                * span
                * (params.cn_beta * beta + params.cn_rudder * controls.rudder + params.cnr * r_hat),
            ]
        )

    # Gravity is +Z in NED (down is positive).
    force_ned = rotation @ force_body + np.array([0.0, 0.0, params.mass_kg * gravity])
    return force_ned, moment_body


# --------------------------------------------------------------- integration


def state_derivative(
    state: np.ndarray,
    params: AircraftParameters,
    controls: ControlInputs,
    gravity: float,
    air_density: float,
    wind_enu: np.ndarray,
) -> np.ndarray:
    """Derivative of [position_enu, velocity_enu, quaternion, omega_body]."""
    velocity_enu = state[3:6]
    q = quat_normalize(state[6:10])
    omega_body = state[10:13]

    force_ned, moment_body = _forces_and_moments(
        enu_to_ned(velocity_enu),
        q,
        omega_body,
        params,
        controls,
        gravity,
        air_density,
        enu_to_ned(wind_enu),
    )

    acceleration_enu = ned_to_enu(force_ned / params.mass_kg)

    # Euler's rotational equation with a diagonal inertia tensor:
    # I * omega_dot = M - omega x (I * omega)
    inertia = params.inertia
    omega_dot = (moment_body - _cross3(omega_body, inertia * omega_body)) / inertia

    derivative = np.empty(13)
    derivative[0:3] = velocity_enu
    derivative[3:6] = acceleration_enu
    derivative[6:10] = quat_derivative(q, omega_body)
    derivative[10:13] = omega_dot
    return derivative


def rk4_step(
    state: np.ndarray,
    dt: float,
    params: AircraftParameters,
    controls: ControlInputs,
    gravity: float,
    air_density: float,
    wind_enu: np.ndarray,
) -> np.ndarray:
    """One classical Runge-Kutta 4 step."""
    args = (params, controls, gravity, air_density, wind_enu)
    k1 = state_derivative(state, *args)
    k2 = state_derivative(state + 0.5 * dt * k1, *args)
    k3 = state_derivative(state + 0.5 * dt * k2, *args)
    k4 = state_derivative(state + dt * k3, *args)

    advanced = state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
    advanced[6:10] = quat_normalize(advanced[6:10])
    return advanced


class Simple6DOFModel:
    """Newton-Euler 6DOF integrator (the PHASE 2 default physics backend)."""

    name = "simple_6dof"

    def integrate(self, world: WorldState, dt: float) -> None:
        gravity = world.environment.gravity_mps2
        air_density = world.environment.air_density_kgpm3
        wind = world.environment.wind

        for entity in world.entities.values():
            if entity.status is not EntityStatus.ACTIVE:
                continue
            self._advance_entity(entity, dt, gravity, air_density, wind)

    def _advance_entity(
        self,
        entity: EntityState,
        dt: float,
        gravity: float,
        air_density: float,
        wind: np.ndarray,
    ) -> None:
        params = get_aircraft(str(entity.metadata.get("type", "")))
        controls = entity.controls.clamped()

        state = np.empty(13)
        state[0:3] = entity.position
        state[3:6] = entity.velocity
        state[6:10] = entity.attitude_quaternion
        state[10:13] = entity.angular_velocity

        advanced = rk4_step(state, dt, params, controls, gravity, air_density, wind)

        # A non-finite result means the integration diverged. Freeze the entity
        # and flag it rather than letting NaN spread through the world state.
        if not np.all(np.isfinite(advanced)):
            entity.status = EntityStatus.DISABLED
            entity.metadata["physics_error"] = "non-finite state from integration"
            entity.velocity[:] = 0.0
            entity.angular_velocity[:] = 0.0
            return

        entity.position[:] = advanced[0:3]
        entity.velocity[:] = advanced[3:6]
        entity.attitude_quaternion[:] = advanced[6:10]
        entity.angular_velocity[:] = advanced[10:13]
        entity.orientation[:] = euler_from_quat(entity.attitude_quaternion)
