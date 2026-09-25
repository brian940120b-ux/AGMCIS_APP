"""The competition environment: an F-16, an opponent, and the judge's ruler.

Built on `competition.state` and `competition.action`, so what a policy sees
here is what the UDP client will hand it on the day — that is the whole reason
the encoder lives in one place.

Three things differ from the reference environment, all deliberate:

**The round setup is the published one.** 競賽規則 1 gives separations of
3,000 / 6,000 / 9,000 ft, altitudes of 10,000-20,000 ft and 340 knots. The
reference randomises separation from 0 to 12,000 ft and altitude from 1,000 ft,
which trains for a merge that cannot happen and a deck that is never that low.
Both distributions are available; the competition one is the default.

**The score is computed alongside the reward.** `SideScore` runs every frame,
so "how am I doing" can be asked in the units the judges use rather than in the
units the reward happens to be in. They are not the same question: the reference
reward pays for a nose on target at any range, and the competition pays only
between 500 and 3,000 ft.

**Which F-16 is a parameter, because it is not obvious.** The package ships an
`aircraft/f16` whose engine differs from the one pip installs — bypass ratio
0.360 against 0.4, bleed 0.03 against none, idle N1/N2 30/60 against 40/53 —
and `FGFDMExec(None)`, which the reference training uses, loads the pip one. So
the reference trains against an engine it does not ship. Flying both through
the same 30 seconds of stick puts them 2.4 ft and 2.2 fps apart, which is small
and is not zero. Which one the competition host runs is not knowable from here;
it is the first thing to check against the real host, and until then the choice
is recorded rather than assumed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from competition.action import INITIAL_THROTTLE, JoystickState, shape_command
from competition.scoring import (
    COLLISION_DISTANCE_M,
    CRASH_ALTITUDE_M,
    ROUND_SECONDS,
    AttackEnvelope,
    ScoringWeights,
    SideScore,
)
from competition.state import STATE_SIZE, Geometry, StateEncoder, Telemetry
from core.logging_config import get_logger

log = get_logger("competition.env")

SIM_HZ = 60
FT_TO_M = 0.3048
#: The reference's observation bounds. Kept so its policies load here unchanged.
OBSERVATION_BOUND = 500.0


@dataclass(frozen=True)
class RoundSetup:
    """Initial conditions. 競賽規則 1 of NCSIST-AIPilot-01."""

    separations_ft: tuple[float, ...] = (3000.0, 6000.0, 9000.0)
    altitude_range_ft: tuple[float, float] = (10_000.0, 20_000.0)
    speed_kcas: float = 340.0
    #: Where the engagement happens. Only the relative geometry matters, but
    #: latitude enters the tangent-plane conversion, so it is not arbitrary.
    centre_lat_deg: float = 23.060552
    centre_lon_deg: float = 121.948555

    @classmethod
    def reference(cls) -> RoundSetup:
        """What the reference environment randomises over, for comparison."""
        return cls(
            separations_ft=tuple(float(ft) for ft in range(0, 12_001, 500)),
            altitude_range_ft=(1_000.0, 22_000.0),
            speed_kcas=340.0,
        )


class Aircraft:
    """One JSBSim F-16, set up the way the reference sets one up.

    Same property names, same order, same `run_ic` / starter / refuel dance.
    Anything different here is a different aeroplane.
    """

    def __init__(
        self,
        *,
        root: str | None,
        lat_deg: float,
        lon_deg: float,
        altitude_ft: float,
        heading_deg: float,
        speed_kcas: float,
    ) -> None:
        import jsbsim

        self.fdm = jsbsim.FGFDMExec(root)
        self.fdm.set_debug_level(0)
        self.fdm.load_model("f16")
        self.fdm.set_dt(1.0 / SIM_HZ)

        self.fdm["ic/vc-kts"] = speed_kcas
        self.fdm["ic/lat-gc-deg"] = lat_deg
        self.fdm["ic/long-gc-deg"] = lon_deg
        self.fdm["ic/h-sl-ft"] = altitude_ft
        self.fdm["ic/psi-true-deg"] = heading_deg
        self.fdm["ic/theta-deg"] = 0.0
        self.fdm["ic/phi-deg"] = 0.0
        self.fdm.run_ic()

        self.fdm["propulsion/starter_cmd"] = 1
        self.fdm["propulsion/refuel"] = 1
        self.fdm.run()
        self.fdm["propulsion/active_engine"] = True
        self.fdm["propulsion/set-running"] = -1

    def apply(self, command: np.ndarray) -> None:
        self.fdm["fcs/aileron-cmd-norm"] = float(command[0])
        self.fdm["fcs/elevator-cmd-norm"] = float(command[1])
        self.fdm["fcs/rudder-cmd-norm"] = float(command[2])
        self.fdm["fcs/throttle-cmd-norm"] = float(command[3])

    def step(self) -> None:
        self.fdm.run()

    @property
    def altitude_m(self) -> float:
        return float(self.fdm["position/h-sl-ft"]) * FT_TO_M

    @property
    def g_load(self) -> float:
        return float(self.fdm["accelerations/n-pilot-z-norm"])

    def telemetry_against(self, other: Aircraft) -> Telemetry:
        """The 26 values the host would send, from this aircraft's point of view."""
        f, g = self.fdm, other.fdm
        return Telemetry(
            own_lat_deg=float(f["position/lat-gc-deg"]),
            own_lon_deg=float(f["position/long-gc-deg"]),
            own_alt_ft=float(f["position/h-sl-ft"]),
            own_roll_deg=float(f["attitude/phi-deg"]),
            own_pitch_deg=float(f["attitude/theta-deg"]),
            own_yaw_deg=float(f["attitude/psi-deg"]),
            own_vn_fps=float(f["velocities/v-north-fps"]),
            own_ve_fps=float(f["velocities/v-east-fps"]),
            own_vd_fps=float(f["velocities/v-down-fps"]),
            own_p_radps=float(f["velocities/p-rad_sec"]),
            own_q_radps=float(f["velocities/q-rad_sec"]),
            own_r_radps=float(f["velocities/r-rad_sec"]),
            own_vc_fps=float(f["velocities/vc-fps"]),
            own_vt_fps=float(f["velocities/vt-fps"]),
            own_g_acc=float(f["accelerations/n-pilot-z-norm"]),
            own_u_fps=float(f["velocities/u-fps"]),
            own_v_fps=float(f["velocities/v-fps"]),
            own_w_fps=float(f["velocities/w-fps"]),
            own_alpha_deg=float(f["aero/alpha-deg"]),
            own_beta_deg=float(f["aero/beta-deg"]),
            enemy_lat_deg=float(g["position/lat-gc-deg"]),
            enemy_lon_deg=float(g["position/long-gc-deg"]),
            enemy_alt_ft=float(g["position/h-sl-ft"]),
            enemy_vn_fps=float(g["velocities/v-north-fps"]),
            enemy_ve_fps=float(g["velocities/v-east-fps"]),
            enemy_vd_fps=float(g["velocities/v-down-fps"]),
        )


#: An opponent: given its own telemetry, return four raw control channels.
Opponent = Callable[[Telemetry], np.ndarray]


def level_opponent(target_altitude_ft: float) -> Opponent:
    """Holds height and wings level. What the reference trains against.

    Deliberately weak, and named so. A policy that only ever meets this learns
    to catch something that does not fight back, which is most of why the
    reference policy takes 46 to 180 seconds to reach three seconds of tracking.

    Its output goes to the control surfaces directly, not through the stick
    shaping. That is how the reference flies its own opponent, and it matters:
    the elevator curve is a cube, so the small corrections a hold-altitude loop
    makes would come out at a thousandth of their size and the aircraft would
    fly into the ground. It did, at 21 seconds, before this was noticed.
    """
    state = {"previous_pitch_error": 0.0}

    def fly(telemetry: Telemetry) -> np.ndarray:
        altitude_error_ft = target_altitude_ft - telemetry.own_alt_ft
        pitch_target_deg = float(np.clip(altitude_error_ft * 0.02, -8.0, 8.0))
        pitch_error = pitch_target_deg - telemetry.own_pitch_deg
        pitch_rate = pitch_error - state["previous_pitch_error"]
        state["previous_pitch_error"] = pitch_error
        # Negative elevator is nose-up in the reference's sign convention.
        elevator = float(np.clip(-(pitch_error * 0.05 + pitch_rate * 0.5), -0.6, 0.6))
        aileron = float(np.clip(-(telemetry.own_roll_deg * 0.03 + telemetry.own_p_radps * 2.0), -0.6, 0.6))
        return np.array([aileron, elevator, 0.0, INITIAL_THROTTLE])

    return fly


@dataclass
class EnvConfig:
    setup: RoundSetup = field(default_factory=RoundSetup)
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    envelope: AttackEnvelope = field(default_factory=AttackEnvelope)
    #: None means whatever `jsbsim` installed. A path selects a data root, such
    #: as the competition package, whose engine differs.
    jsbsim_root: str | None = None
    #: The reference pins the rudder to zero through its action space, so its
    #: policy has never used one. False reproduces that exactly.
    rudder_enabled: bool = False
    round_seconds: float = ROUND_SECONDS

    def describe(self) -> dict[str, Any]:
        """What a model card needs to say this policy is comparable."""
        return {
            "separations_ft": list(self.setup.separations_ft),
            "altitude_range_ft": list(self.setup.altitude_range_ft),
            "speed_kcas": self.setup.speed_kcas,
            "jsbsim_root": self.jsbsim_root or "installed package",
            "rudder_enabled": self.rudder_enabled,
            "round_seconds": self.round_seconds,
            "attack_half_angle_deg": self.envelope.half_angle_deg,
            "attack_range_ft": [self.envelope.min_range_ft, self.envelope.max_range_ft],
        }


def resolve_jsbsim_root(candidate: str | None) -> str | None:
    """Check a data root before JSBSim fails on it three layers down."""
    if candidate is None:
        return None
    root = Path(candidate)
    if not (root / "aircraft" / "f16" / "f16.xml").is_file():
        raise FileNotFoundError(
            f"no aircraft/f16/f16.xml under {root} — "
            "point jsbsim_root at the competition package, or leave it unset "
            "to use the installed JSBSim data"
        )
    return str(root)


class CompetitionRound:
    """One five-minute round, steppable a frame at a time.

    Gymnasium-free so it can be used by a test, a replay, or an env wrapper
    without any of them needing the others.
    """

    def __init__(self, config: EnvConfig | None = None, seed: int | None = None) -> None:
        self.config = config or EnvConfig()
        self.random = random.Random(seed)
        self.encoder = StateEncoder()
        self.joystick = JoystickState()
        self.opponent_joystick = JoystickState()
        self.score = SideScore(weights=self.config.weights, envelope=self.config.envelope)
        self.opponent_score = SideScore(weights=self.config.weights, envelope=self.config.envelope)
        self.own: Aircraft
        self.foe: Aircraft
        self.opponent: Opponent
        self.frame = 0
        self.reset()

    # ------------------------------------------------------------ lifecycle

    def reset(self, seed: int | None = None) -> np.ndarray:
        if seed is not None:
            self.random.seed(seed)
        setup = self.config.setup
        root = resolve_jsbsim_root(self.config.jsbsim_root)

        separation_ft = self.random.choice(setup.separations_ft)
        altitude_ft = self.random.uniform(*setup.altitude_range_ft)
        foe_altitude_ft = self.random.uniform(*setup.altitude_range_ft)
        bearing_deg = self.random.uniform(0.0, 360.0)
        own_heading = self.random.uniform(0.0, 360.0)
        foe_heading = self.random.uniform(0.0, 360.0)

        foe_lat, foe_lon = _offset(
            setup.centre_lat_deg, setup.centre_lon_deg, bearing_deg, separation_ft * FT_TO_M
        )

        self.own = Aircraft(
            root=root,
            lat_deg=setup.centre_lat_deg,
            lon_deg=setup.centre_lon_deg,
            altitude_ft=altitude_ft,
            heading_deg=own_heading,
            speed_kcas=setup.speed_kcas,
        )
        self.foe = Aircraft(
            root=root,
            lat_deg=foe_lat,
            lon_deg=foe_lon,
            altitude_ft=foe_altitude_ft,
            heading_deg=foe_heading,
            speed_kcas=setup.speed_kcas,
        )
        self.opponent = level_opponent(foe_altitude_ft)

        self.encoder.reset()
        self.joystick.reset()
        self.opponent_joystick.reset()
        self.score = SideScore(weights=self.config.weights, envelope=self.config.envelope)
        self.opponent_score = SideScore(weights=self.config.weights, envelope=self.config.envelope)
        self.frame = 0
        return self.observe()

    # ---------------------------------------------------------------- state

    def telemetry(self) -> Telemetry:
        return self.own.telemetry_against(self.foe)

    def geometry(self) -> Geometry:
        return self.encoder.geometry(self.telemetry())

    def observe(self) -> np.ndarray:
        return self.encoder.encode(self.telemetry())

    # ----------------------------------------------------------------- step

    def step(self, raw_action: np.ndarray) -> tuple[np.ndarray, Geometry, bool, str]:
        """Advance one frame. Returns the new state, the geometry, and why it ended."""
        telemetry = self.telemetry()
        command = shape_command(raw_action, self.joystick, telemetry.reference_mach)
        self.own.apply(command)

        # Straight to the surfaces: an opponent is part of the world, not a
        # policy wearing the same stick.
        self.foe.apply(self.opponent(self.foe.telemetry_against(self.own)))

        self.own.step()
        self.foe.step()
        self.frame += 1

        geometry = self.geometry()
        self.score.observe(geometry, self.own.g_load)
        self.opponent_score.observe(
            self.encoder.geometry(self.foe.telemetry_against(self.own)), self.foe.g_load
        )

        reason = self._end_reason(geometry)
        return self.observe(), geometry, bool(reason), reason

    def _end_reason(self, geometry: Geometry) -> str:
        if self.score.killed:
            return "KILL"
        if self.opponent_score.killed:
            return "KILLED"
        if geometry.distance_m < COLLISION_DISTANCE_M:
            return "COLLISION"
        if self.own.altitude_m <= CRASH_ALTITUDE_M:
            return "CRASH"
        if self.foe.altitude_m <= CRASH_ALTITUDE_M:
            return "FOE_CRASH"
        if self.frame >= int(self.config.round_seconds * SIM_HZ):
            return "TIME"
        return ""


def _offset(lat_deg: float, lon_deg: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """A point `distance_m` away on a bearing, on the local tangent plane."""
    earth_radius_m = 6378137.0
    bearing = math.radians(bearing_deg)
    north = distance_m * math.cos(bearing)
    east = distance_m * math.sin(bearing)
    new_lat = lat_deg + math.degrees(north / earth_radius_m)
    new_lon = lon_deg + math.degrees(east / (earth_radius_m * math.cos(math.radians(lat_deg))))
    return new_lat, new_lon


def observation_space() -> Any:
    """The reference's bounds, so its policies load into this env unchanged."""
    from gymnasium.spaces import Box

    return Box(
        low=-OBSERVATION_BOUND,
        high=OBSERVATION_BOUND,
        shape=(STATE_SIZE,),
        dtype=np.float64,
    )


def action_space(rudder_enabled: bool) -> Any:
    """Four channels. The reference pins the rudder shut; that is reproducible."""
    from gymnasium.spaces import Box

    rudder_high = 1.0 if rudder_enabled else 1e-17
    rudder_low = -1.0 if rudder_enabled else 0.0
    # Built as float32 already: Gymnasium warns when it has to lower the
    # precision of bounds it was handed, and 1e-17 is exactly the kind of
    # bound that makes such a warning worth listening to.
    return Box(
        low=np.array([-1.0, -1.0, rudder_low, 0.0], dtype=np.float32),
        high=np.array([1.0, 1.0, rudder_high, 1.0], dtype=np.float32),
        dtype=np.float32,
    )
