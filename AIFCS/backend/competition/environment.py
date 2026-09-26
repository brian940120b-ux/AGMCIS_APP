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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from competition.action import (
    ELEVATOR_LIMIT_HIGH_SPEED,
    INITIAL_THROTTLE,
    RUDDER_LIMIT,
    JoystickState,
    shape_command,
)
from competition.safety import GroundAvoidance
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

    @classmethod
    def measured(cls) -> RoundSetup:
        """What the public test host actually did, 2026-09-25, over two rounds.

        Not the published setup. Measured with WGS84 curvature:

            round 1   19,116 ft   3,295.0 ft apart   heading 340 deg
            round 2   14,659 ft   4,850.2 ft apart   heading  55 deg

        Both separations are whole feet (3,295.013 is 4 mm off an integer) and
        both headings are whole degrees, which is what
        `random.randint(0, 12000) * 0.3048` and `random.randint(0, 359)`
        produce — the reference environment's generator, not the rules'
        3,000 / 6,000 / 9,000. Both aircraft shared an altitude and both flew at
        340 KCAS, which the reference environment does not do.

        The readme calls this build 民眾公告版, a public release for testing a
        connection, so a test host randomising where the competition does not
        is unremarkable. What it means is that the test host cannot be used to
        check the round setup, and the published figures stay the default:
        they are the only statement about competition day that exists.

        Two samples. Not a distribution.
        """
        return cls(
            separations_ft=tuple(float(ft) for ft in range(0, 12_001, 1)),
            altitude_range_ft=(10_000.0, 20_000.0),
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
        speed_before_altitude: bool = False,
    ) -> None:
        import jsbsim

        self.fdm = jsbsim.FGFDMExec(root)
        self.fdm.set_debug_level(0)
        self.fdm.load_model("f16")
        self.fdm.set_dt(1.0 / SIM_HZ)

        # Order matters, and the reference has it the wrong way round. A
        # calibrated airspeed set before the altitude is resolved at sea level,
        # and the later altitude change preserves the true airspeed instead, so
        # the aircraft ends up slow. Asking for 340 knots at 15,000 ft:
        #
        #     speed first       234 KCAS   292 KTAS   Mach 0.466
        #     altitude first    340 KCAS   419 KTAS   Mach 0.669
        #
        # 106 knots, and it is why the Mach 0.8 elevator limit never fires in
        # the reference environment: nothing there ever gets close to Mach 0.8.
        #
        # MEASURED AGAINST THE REAL HOST, 2026-09-25: it starts a round at
        # 339.9 KCAS, 444.5 KTAS, Mach 0.673 at 19,116 ft. The host has the
        # correct ordering, so the published 340 knots is what a round actually
        # begins at and the default here is right. The reference package
        # therefore trained its policy at Mach 0.47 for a competition that runs
        # at Mach 0.67. `speed_before_altitude` reproduces that for comparison,
        # and is not what to train with.
        if speed_before_altitude:
            self.fdm["ic/vc-kts"] = speed_kcas
        self.fdm["ic/lat-gc-deg"] = lat_deg
        self.fdm["ic/long-gc-deg"] = lon_deg
        self.fdm["ic/h-sl-ft"] = altitude_ft
        if not speed_before_altitude:
            self.fdm["ic/vc-kts"] = speed_kcas
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


def reference_opponent(target_altitude_ft: float, target_speed_kcas: float) -> Opponent:
    """A faithful port of the reference package's `auto_run`.

    Worth having exactly rather than approximately, because it is the only
    opponent a reference policy has ever met, and any comparison against that
    policy is a comparison against this.

    Three of its details are not obvious and all three matter:

    * Its heading logic picks from `["Straight"]` — the turn options are
      commented out in the source — so it never manoeuvres. It is a target drone.
    * Its elevator carries a constant -0.05 bias plus a bank compensation term,
      because an untrimmed F-16 needs back pressure to hold level.
    * Its throttle is a PID onto a target calibrated airspeed, around a base of
      0.5. A fixed throttle instead lets the opponent accelerate away from the
      pursuer, which is what a first attempt at this did.
    """
    state = {"previous_roll_error": 0.0, "previous_pitch_error": 0.0, "previous_speed_error": 0.0}
    dt = 1.0 / SIM_HZ

    def fly(telemetry: Telemetry) -> np.ndarray:
        altitude_error_ft = target_altitude_ft - telemetry.own_alt_ft
        target_pitch_deg = max(min(altitude_error_ft * 0.01, 10.0), -5.0)

        roll_error = 0.0 - telemetry.own_roll_deg
        roll_rate = (roll_error - state["previous_roll_error"]) / dt
        aileron = 0.02 * roll_error + 0.01 * roll_rate
        state["previous_roll_error"] = roll_error

        pitch_error = target_pitch_deg - telemetry.own_pitch_deg
        pitch_rate = (pitch_error - state["previous_pitch_error"]) / dt
        elevator = -(0.05 * pitch_error + 0.02 * pitch_rate)
        bank_compensation = abs(math.sin(math.radians(telemetry.own_roll_deg))) * 0.2
        elevator -= 0.05 + bank_compensation
        state["previous_pitch_error"] = pitch_error

        speed_error = target_speed_kcas - telemetry.own_vc_fps / 1.68781
        speed_rate = (speed_error - state["previous_speed_error"]) / dt
        throttle = 0.5 + 0.1 * speed_error + 0.01 * speed_rate
        state["previous_speed_error"] = speed_error

        return np.array(
            [
                max(min(aileron, 1.0), -1.0),
                max(min(elevator, 1.0), -1.0),
                0.0,
                max(min(throttle, 1.0), 0.0),
            ]
        )

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
    #: How far the rudder may actually deflect once unlocked. 表 2 of the
    #: 公告說明 allows -1~+1; the sample client clips itself to 0.2 and its
    #: training action space pins it shut. The default is the sample's, because
    #: that is what has a trained policy behind it.
    rudder_limit: float = RUDDER_LIMIT
    round_seconds: float = ROUND_SECONDS
    #: 0.4 is what the competition client applies above Mach 0.8. 1.0 is what
    #: the reference *trainer* applies, which is no limit at all — and is what
    #: a reference policy learned to fly under.
    high_speed_elevator_limit: float = ELEVATOR_LIMIT_HIGH_SPEED
    #: True reproduces the reference's initial-condition ordering, which
    #: leaves the aircraft 106 knots slower than asked for.
    speed_before_altitude: bool = False
    #: Who we fly against.
    #:
    #: "reference" ports the package's own auto_run, which is a target drone:
    #: its turn branches are commented out. "level" is a simpler straight hold.
    #: "pursuit" turns towards us, which neither of the others does and the
    #: opponent on the day certainly will.
    opponent: str = "reference"
    #: How hard "pursuit" pulls. A ladder of these is a curriculum.
    opponent_aggression: float = 1.0
    #: A saved policy to fly the other aircraft, in place of a script. Built by
    #: the caller and passed in, because loading a checkpoint is not something
    #: a config should do — and because a pool hands a different one per round.
    opponent_policy: Any | None = None
    #: A named pool for the environment to draw from, one per round. When this
    #: is set the environment picks; `opponent_policy` is what it picked.
    opponent_pool: dict[str, Any] = field(default_factory=dict)
    #: "reference" is the package's twenty inputs, which its own policies
    #: expect. "extended" adds ten more derived from the same packet — among
    #: them our own G, which the scoring penalises and the reference state does
    #: not contain. Choosing it ends compatibility with their models.
    observation: str = "reference"
    #: A rule-based pull-up under the policy. None is the reference's
    #: behaviour: nothing catches the aircraft. See `safety.py`.
    ground_avoidance: GroundAvoidance | None = None
    #: How many 60 Hz frames one decision is held for.
    #:
    #: 1 reproduces the reference, which decides sixty times a second — 18,000
    #: decisions in a round, which is a great many to assign credit across. It
    #: is also more often than the aircraft can answer: the stick's own rate
    #: limit moves the elevator 0.026 per frame, so full deflection takes 38
    #: frames and most of that decision bandwidth goes nowhere. 6 gives 10 Hz,
    #: which is the rate the PHANG-MAN agent's high-level policy ran at.
    #:
    #: The rules are unaffected: a command still goes back every frame (注意
    #: 事項 10), it is simply the same command. What changes is how often the
    #: policy is asked, and that must change identically in training and on the
    #: day — which is why it lives in the config both sides read.
    action_repeat: int = 1

    def __post_init__(self) -> None:
        if self.action_repeat < 1:
            raise ValueError(f"action_repeat is a number of frames, not {self.action_repeat}")

    def describe(self) -> dict[str, Any]:
        """What a model card needs to say this policy is comparable."""
        return {
            "separations_ft": list(self.setup.separations_ft),
            "altitude_range_ft": list(self.setup.altitude_range_ft),
            "speed_kcas": self.setup.speed_kcas,
            "jsbsim_root": self.jsbsim_root or "installed package",
            "rudder_enabled": self.rudder_enabled,
            "rudder_limit": self.rudder_limit,
            "high_speed_elevator_limit": self.high_speed_elevator_limit,
            "speed_before_altitude": self.speed_before_altitude,
            "opponent": self.opponent,
            "opponent_aggression": self.opponent_aggression,
            "opponent_policy": type(self.opponent_policy).__name__
            if self.opponent_policy is not None
            else None,
            "opponent_pool": sorted(self.opponent_pool),
            "action_repeat": self.action_repeat,
            "observation": self.observation,
            "ground_avoidance": None if self.ground_avoidance is None else asdict(self.ground_avoidance),
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
        self.encoder = build_encoder(self.config)
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
        # Measured against the real host, 2026-09-25: both aircraft started at
        # 19,116.00001 and 19,116.00002 ft, matching to a hundred-thousandth of
        # a foot. Two independent draws from a 10,000 ft range do not do that,
        # so the altitude is shared by construction and is shared here.
        foe_altitude_ft = altitude_ft
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
            speed_before_altitude=self.config.speed_before_altitude,
        )
        self.foe = Aircraft(
            root=root,
            lat_deg=foe_lat,
            lon_deg=foe_lon,
            altitude_ft=foe_altitude_ft,
            heading_deg=foe_heading,
            speed_kcas=setup.speed_kcas,
            speed_before_altitude=self.config.speed_before_altitude,
        )
        self.opponent = _build_opponent(self.config, foe_altitude_ft, setup.speed_kcas)

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

    def foe_geometry(self) -> Geometry:
        """The same frame from the opponent's side.

        Already computed each step to score them; exposed because a reward that
        is the score *margin* needs both halves, and recomputing it somewhere
        else is how two copies of a thing start to disagree.
        """
        return self.encoder.geometry(self.foe.telemetry_against(self.own))

    def observe(self) -> np.ndarray:
        return self.encoder.encode(self.telemetry())

    # ----------------------------------------------------------------- step

    def step(self, raw_action: np.ndarray) -> tuple[np.ndarray, Geometry, bool, str]:
        """Advance one frame. Returns the new state, the geometry, and why it ended."""
        telemetry = self.telemetry()
        if self.config.ground_avoidance is not None:
            raw_action = self.config.ground_avoidance(raw_action, telemetry)
        command = shape_command(
            raw_action,
            self.joystick,
            telemetry.reference_mach,
            high_speed_elevator_limit=self.config.high_speed_elevator_limit,
            rudder_limit=self.config.rudder_limit,
        )
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


def build_encoder(config: EnvConfig) -> Any:
    """The encoder both paths use, chosen once so they cannot disagree."""
    if config.observation == "extended":
        from competition.features import ExtendedEncoder

        return ExtendedEncoder(round_seconds=config.round_seconds)
    return StateEncoder()


def observation_space(observation: str = "reference") -> Any:
    """The reference's bounds, so its policies load into this env unchanged."""
    from gymnasium.spaces import Box

    from competition.features import EXTENDED_STATE_SIZE

    size = EXTENDED_STATE_SIZE if observation == "extended" else STATE_SIZE
    return Box(
        low=-OBSERVATION_BOUND,
        high=OBSERVATION_BOUND,
        shape=(size,),
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


def pursuit_opponent(
    target_speed_kcas: float,
    *,
    aggression: float = 1.0,
    floor_ft: float = 3000.0,
) -> Opponent:
    """An opponent that turns towards us, which the reference one never does.

    The reference opponent is a target drone: its heading logic chooses from
    `["Straight"]` because the turn branches are commented out in the source.
    Training against it teaches a policy to beat something that flies straight,
    and the measurement says as much — a policy that does *nothing at all*,
    with only a ground-avoidance floor under it, wins 67% of rounds against it.

    A win rate against a drone stops being informative long before it stops
    going up, and the opponent on the day is another team's agent. So this one
    flies lag pursuit: roll to put us on its nose, pull, and hold energy.

    Deliberately scripted rather than learned. It is the bottom rung of the
    ladder PHANG-MAN used — train against scripted opponents first, add
    learned ones once the win rate passes half — and a rung has to be fixed
    for the rungs above it to mean anything.

    `aggression` scales the pull, which is the whole difference between an
    opponent that tracks and one that overshoots, so a range of them makes a
    curriculum out of one function.
    """
    state = {"previous_roll_error": 0.0, "previous_pitch_error": 0.0, "previous_speed_error": 0.0}
    dt = 1.0 / SIM_HZ
    encoder = StateEncoder()

    def fly(telemetry: Telemetry) -> np.ndarray:
        geometry = encoder.geometry(telemetry)

        # Bank towards the bearing. A 90-degree bank is the most a turn asks
        # for; beyond that the lift vector goes back down the other side.
        wanted_roll_deg = max(min(geometry.azimuth_deg * 1.5, 80.0), -80.0)
        # Except near the ground, where rolling into a turn is how an aircraft
        # arrives at it. Wings level takes priority over the fight.
        if telemetry.own_alt_ft < floor_ft:
            wanted_roll_deg = 0.0

        roll_error = wanted_roll_deg - telemetry.own_roll_deg
        roll_rate = (roll_error - state["previous_roll_error"]) / dt
        aileron = 0.02 * roll_error + 0.01 * roll_rate
        state["previous_roll_error"] = roll_error

        # Pull towards the target once banked, and pull harder the closer the
        # nose already is — that is what turns a bank into a tracking turn
        # rather than a drift. Positive elevator is nose down here, so the
        # commanded pitch is subtracted in the same shape the reference uses.
        bank = abs(math.sin(math.radians(telemetry.own_roll_deg)))
        # `aggression` scales the whole vertical command, not just the extra
        # pull in the turn. Gating it on bank alone made it a knob that did
        # nothing whenever the wings were level, which is most of the time.
        wanted_pitch_deg = max(min((geometry.elevation_deg + 10.0 * bank) * aggression, 20.0), -10.0)
        if telemetry.own_alt_ft < floor_ft:
            wanted_pitch_deg = max(wanted_pitch_deg, 5.0)

        pitch_error = wanted_pitch_deg - telemetry.own_pitch_deg
        pitch_rate = (pitch_error - state["previous_pitch_error"]) / dt
        elevator = -(0.05 * pitch_error + 0.02 * pitch_rate)
        elevator -= 0.05 + bank * 0.2  # the untrimmed aircraft's back pressure
        state["previous_pitch_error"] = pitch_error

        # Energy: full power when it is chasing, the reference's PID otherwise.
        if abs(geometry.azimuth_deg) < 60.0 and geometry.distance_m > 500.0:
            throttle = 1.0
            state["previous_speed_error"] = 0.0
        else:
            speed_error = target_speed_kcas - telemetry.own_vc_fps / 1.68781
            speed_rate = (speed_error - state["previous_speed_error"]) / dt
            throttle = 0.5 + 0.1 * speed_error + 0.01 * speed_rate
            state["previous_speed_error"] = speed_error

        return np.array(
            [
                max(min(aileron, 1.0), -1.0),
                max(min(elevator, 1.0), -1.0),
                0.0,
                max(min(throttle, 1.0), 0.0),
            ]
        )

    return fly


def _build_opponent(config: EnvConfig, altitude_ft: float, speed_kcas: float) -> Opponent:
    """One place that knows the names, so adding one cannot miss a call site."""
    if config.opponent_policy is not None:
        # A policy outlives a round, so its per-round state is cleared here
        # rather than rebuilt — the weights are the expensive part.
        config.opponent_policy.reset()
        return config.opponent_policy
    if config.opponent == "level":
        return level_opponent(altitude_ft)
    if config.opponent == "pursuit":
        return pursuit_opponent(speed_kcas, aggression=config.opponent_aggression)
    return reference_opponent(altitude_ft, speed_kcas)
