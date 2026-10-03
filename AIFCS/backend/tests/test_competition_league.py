"""Choosing who to fight next (COMP PHASE 15).

The thresholds are not invented. They are PHANG-MAN's, from the AlphaDogfight
Trials paper: sampling stays uniform until every opponent has been met a
hundred times and the agent is winning overall, then goes proportional to how
often each one beats it, clipped to between 0.2% and 11.7%.

These are arithmetic tests. Whether a league trains a better policy is a
question for a GPU; whether the distribution does what the paper describes is
a question with an answer here.
"""

from __future__ import annotations

import pytest

from competition.league import GATE_WIN_RATE, MAX_SHARE, MIN_SHARE, WINDOW, League


def played(league: League, name: str, wins: int, losses: int) -> None:
    for _ in range(wins):
        league.record(name, True)
    for _ in range(losses):
        league.record(name, False)


def test_an_empty_league_is_refused():
    with pytest.raises(ValueError):
        League(names=[])


def test_sampling_is_uniform_before_anyone_has_been_met():
    league = League(names=["drone", "pursuit", "old"])
    assert not league.weighting
    assert list(league.shares().values()) == pytest.approx([1 / 3] * 3)


def test_it_stays_uniform_while_the_agent_is_losing():
    """Weighting towards whoever beats you, while everyone beats you, is just
    uniform with extra steps — and the paper gates on winning for that reason."""
    league = League(names=["a", "b"])
    for name in ("a", "b"):
        played(league, name, wins=10, losses=WINDOW - 10)

    assert league.overall_win_rate == pytest.approx(0.1)
    assert not league.weighting
    assert list(league.shares().values()) == pytest.approx([0.5, 0.5])


def test_it_stays_uniform_until_everyone_has_been_met_enough():
    """The other half of the gate: a hundred matchups each."""
    league = League(names=["a", "b"])
    played(league, "a", wins=90, losses=10)
    played(league, "b", wins=9, losses=1)  # only ten so far

    assert league.overall_win_rate > GATE_WIN_RATE
    assert not league.weighting, "b has not been played the full window"


def test_once_it_is_winning_the_hard_opponents_come_up_more():
    league = League(names=["easy", "hard"])
    played(league, "easy", wins=95, losses=5)
    played(league, "hard", wins=55, losses=45)

    assert league.weighting
    shares = league.shares()
    assert shares["hard"] > shares["easy"]
    assert sum(shares.values()) == pytest.approx(1.0)


def test_nobody_is_ever_dropped_entirely():
    """An opponent beaten every single time still comes up, because a policy
    that stops meeting something forgets how to beat it."""
    league = League(names=["beaten", "hard", "harder"])
    played(league, "beaten", wins=WINDOW, losses=0)
    played(league, "hard", wins=60, losses=40)
    played(league, "harder", wins=51, losses=49)

    shares = league.shares()
    assert shares["beaten"] > 0.0
    assert sum(shares.values()) == pytest.approx(1.0)


def test_beating_everyone_every_time_falls_back_to_uniform():
    """Otherwise the weights are all zero and the distribution is undefined."""
    league = League(names=["a", "b"])
    for name in ("a", "b"):
        played(league, name, wins=WINDOW, losses=0)

    assert list(league.shares().values()) == pytest.approx([0.5, 0.5])


def test_only_the_last_hundred_matchups_count():
    """A window, not a history: an opponent that used to be hard and is not any
    more should stop being oversampled."""
    league = League(names=["a"])
    played(league, "a", wins=0, losses=WINDOW)
    assert league.records["a"].win_rate == 0.0

    played(league, "a", wins=WINDOW, losses=0)
    assert league.records["a"].played == WINDOW, "the old results fell out"
    assert league.records["a"].win_rate == 1.0


def test_an_unplayed_opponent_is_neither_feared_nor_dismissed():
    assert League(names=["new"]).records["new"].win_rate == 0.5


def test_the_clips_are_the_papers():
    assert MIN_SHARE == 0.002
    assert MAX_SHARE == 0.117
    assert WINDOW == 100
    assert GATE_WIN_RATE == 0.5


def test_choosing_is_reproducible_from_the_seed():
    """A training run has to be repeatable, and the opponent order is part of
    what a run was."""
    first = League(names=["a", "b", "c"], seed=7)
    second = League(names=["a", "b", "c"], seed=7)
    assert [first.next_opponent() for _ in range(20)] == [second.next_opponent() for _ in range(20)]


# ----------------------------------------------------- the pool, end to end

jsbsim = pytest.importorskip("jsbsim")

import numpy as np

from competition.action import INITIAL_THROTTLE
from competition.environment import EnvConfig
from competition.gym_env import CompetitionEnv
from competition.league import PolicyOpponent, collect_checkpoints
from competition.safety import GroundAvoidance
from competition.scoring import Verdict, decide_round


def constant(elevator: float) -> PolicyOpponent:
    """A stand-in for a trained policy: enough to fly, enough to differ."""
    return PolicyOpponent(lambda state: np.array([0.0, elevator, 0.0, 1.0]))


def test_the_environment_draws_an_opponent_per_round_and_records_the_result():
    pool = {"gentle": constant(-0.05), "hard": constant(-0.4), "diving": constant(0.2)}
    env = CompetitionEnv(
        EnvConfig(opponent_pool=pool, ground_avoidance=GroundAvoidance()),
        reward_mode="margin",
        seed=3,
    )
    hold = np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE])

    faced = []
    for episode in range(4):
        env.reset(seed=100 + episode)
        while True:
            _, _, terminated, truncated, info = env.step(hold)
            if terminated or truncated:
                break
        faced.append(info["opponent_name"])

    assert set(faced) <= set(pool), "only opponents from the pool"
    assert len(set(faced)) > 1, "and not the same one every time"
    league = env._league
    assert league is not None
    assert sum(r.played for r in league.records.values()) == 4, "every round was recorded"


def test_the_result_recorded_is_the_organisers_verdict():
    """Not "did the reward go up" — 表 3, the same table that decides the day.

    Checked against `decide_round` recomputed from the finished round rather
    than against a predicted outcome: a first version assumed a steeply diving
    opponent would hit the ground inside five minutes, and it did not. What
    matters is that the league and the rules agree, whatever happened.
    """
    pool = {"diving": constant(0.9)}
    env = CompetitionEnv(
        EnvConfig(opponent_pool=pool, ground_avoidance=GroundAvoidance()),
        reward_mode="margin",
        seed=5,
    )
    env.reset(seed=100)
    hold = np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE])
    while True:
        _, _, terminated, truncated, info = env.step(hold)
        if terminated or truncated:
            break

    reason = info["reason"]
    expected = decide_round(
        env.round.score,
        env.round.opponent_score,
        blue_crashed=reason == "CRASH",
        red_crashed=reason == "FOE_CRASH",
        collided=reason == "COLLISION",
    )
    league = env._league
    assert league is not None
    won = expected.verdict is Verdict.BLUE
    assert league.records["diving"].wins == (1 if won else 0)
    assert league.records["diving"].played == 1


def test_no_pool_means_no_league_and_the_scripted_opponent_stands():
    env = CompetitionEnv(EnvConfig(), reward_mode="margin", seed=1)
    assert env._league is None


def test_a_directory_of_checkpoints_becomes_a_pool(tmp_path):
    """So self-play is snapshotting into a folder, not editing a command line."""
    for name in ("run1", "run2"):
        (tmp_path / f"{name}.zip").write_bytes(b"not really a model")

    found = collect_checkpoints([str(tmp_path)])
    assert sorted(found) == ["run1", "run2"]


def test_a_session_directorys_checkpoint_is_named_after_the_session(tmp_path):
    """`checkpoint.zip` tells a reader nothing; the session name tells them
    which run they are fighting."""
    session = tmp_path / "v2"
    session.mkdir()
    (session / "checkpoint.zip").write_bytes(b"not really a model")

    assert sorted(collect_checkpoints([str(session / "checkpoint.zip")])) == ["v2"]


def test_an_empty_or_missing_pool_says_so(tmp_path):
    with pytest.raises(FileNotFoundError):
        collect_checkpoints([str(tmp_path)])
    with pytest.raises(FileNotFoundError):
        collect_checkpoints([str(tmp_path / "nothing.zip")])


def _telemetry():
    """Only the fields an encoder reads; the rest are placeholders."""
    from competition.state import Telemetry

    values = dict.fromkeys(Telemetry.__dataclass_fields__, 0.0)
    values["own_alt_ft"] = 18_000.0
    values["own_vc_fps"] = 574.0
    values["own_vt_fps"] = 740.0
    values["enemy_alt_ft"] = 18_000.0
    values["enemy_lat_deg"] = 0.01
    return Telemetry(**values)


# ------------------------------------- a pool opponent flies its own aircraft


def test_a_pool_opponent_is_built_from_its_own_card(tmp_path, monkeypatch):
    """The bug that would have stopped self-play on its first round.

    PolicyOpponent hardcoded StateEncoder(), the 20-dimensional reference
    encoding, and train.py handed it the *new* run's action repeat and rudder
    cap. v4 trains on `extended`, 30 numbers, at 6 frames a decision — so the
    first pool built from it would have fed a 30-dimensional policy 20 inputs.

    An opponent flies the aeroplane it learned on, the same rule the evaluator
    already follows for the policy under test.
    """
    import json

    from competition.features import EXTENDED_STATE_SIZE
    from competition.league import opponent_from_checkpoint

    session = tmp_path / "v4"
    session.mkdir()
    (session / "card.json").write_text(
        json.dumps(
            {
                "environment": {
                    "observation": "extended",
                    "action_repeat": 6,
                    "rudder_limit": 0.5,
                    "high_speed_elevator_limit": 0.9,
                }
            }
        ),
        encoding="utf-8",
    )
    checkpoint = session / "checkpoint.zip"
    checkpoint.write_bytes(b"")

    seen: list[int] = []

    def fake_load_actor(path, algorithm="sac", device="cpu"):
        def predict(state):
            seen.append(len(state))
            return np.zeros(4)

        return predict

    monkeypatch.setattr("competition.league.load_actor", fake_load_actor)
    opponent = opponent_from_checkpoint(checkpoint)

    assert opponent.action_repeat == 6, "its decision rate, not the new run's"
    assert opponent.rudder_limit == 0.5
    assert opponent.high_speed_elevator_limit == 0.9

    opponent(_telemetry())
    assert seen == [EXTENDED_STATE_SIZE], "and its observation width"


def test_a_checkpoint_with_no_card_falls_back_to_the_reference_aircraft():
    """What a session recorded before any of those fields existed looks like."""
    from pathlib import Path

    from competition.league import opponent_from_checkpoint
    from competition.state import STATE_SIZE

    seen: list[int] = []

    def fake_load_actor(path, algorithm="sac", device="cpu"):
        def predict(state):
            seen.append(len(state))
            return np.zeros(4)

        return predict

    import competition.league as league

    original, league.load_actor = league.load_actor, fake_load_actor
    try:
        opponent = opponent_from_checkpoint(Path("nowhere/checkpoint.zip"))
        assert opponent.action_repeat == 1
        opponent(_telemetry())
    finally:
        league.load_actor = original

    assert seen == [STATE_SIZE]


def test_a_pool_entry_crosses_a_process_boundary_without_torch():
    """What killed v5 twice.

    First a loaded SAC model in the pool was serialised once per worker and
    unpickled by all eight at once (MemoryError). Then, loading lazily inside
    each worker, every worker imported torch and every torch import brought an
    OpenMP thread pool sized to the machine:

        OMP: Error #137: Cannot create thread.
        OMP: System error #1450

    So the weights are extracted to numpy in the parent — the one process that
    already has torch, because it is the one training — and what reaches a
    worker is arrays. The assertion is on the pickle itself, because "does a
    worker import torch" is not a thing a test can ask after the fact.
    """
    import pickle

    from competition.league import NumpyActor
    from competition.state import STATE_SIZE

    weights = {
        "layers": [
            {"kind": "linear", "weight": np.zeros((4, STATE_SIZE)), "bias": np.zeros(4)},
            {"kind": "relu"},
        ],
        "low": -np.ones(4),
        "high": np.ones(4),
        "squash": True,
    }
    opponent = PolicyOpponent(NumpyActor(weights))
    payload = pickle.dumps(opponent)

    assert b"torch" not in payload, "a worker unpickling this must not pull in torch"
    revived = pickle.loads(payload)
    assert revived(_telemetry()).shape == (4,)


def test_the_numpy_actor_reproduces_what_the_model_would_have_said():
    """Measured against SB3's own predict over 200 random observations and
    three architectures, relu and tanh: worst difference 1.8e-07, which is
    float32 against float64 rather than an approximation.

    What is pinned here is the unscaling, because it is the step that a range
    check cannot see. A zero pre-tanh output is the midpoint of each channel's
    range once unscaled, and zero if the unscaling is skipped — the first
    version of this test asserted bounds and passed either way.
    """
    from competition.league import NumpyActor

    weights = {
        "layers": [{"kind": "linear", "weight": np.zeros((2, 3)), "bias": np.zeros(2)}],
        "low": np.array([-1.0, 0.0]),
        "high": np.array([1.0, 1.0]),
        "squash": True,
    }
    action = NumpyActor(weights)(np.array([7.0, -3.0, 0.5]))

    assert action[0] == pytest.approx(0.0), "midpoint of [-1, 1]"
    assert action[1] == pytest.approx(0.5), "midpoint of [0, 1], not its floor"


def test_an_unsquashed_actor_is_left_alone():
    """`squash_output` is read off the policy rather than assumed, so an actor
    that does not tanh its output is not given one."""
    from competition.league import NumpyActor

    weights = {
        "layers": [{"kind": "linear", "weight": np.eye(2), "bias": np.array([0.3, -0.4])}],
        "low": np.array([-1.0, -1.0]),
        "high": np.array([1.0, 1.0]),
        "squash": False,
    }
    action = NumpyActor(weights)(np.zeros(2))

    assert action == pytest.approx(np.array([0.3, -0.4]))


def test_a_pool_opponent_keeps_the_floor_it_learned_under(tmp_path, monkeypatch):
    """The omission that probably cost v5 its two million steps.

    v4 trained with a ground-avoidance floor. Flown as a pool opponent without
    one it is a different aircraft, and the one it becomes flies into the
    ground — so v5's whole curriculum was an opponent that kills itself.
    Measured afterwards: a centred stick beats v5 65% of the time and v4 only
    10%.

    The encoder, decision rate and rudder cap were already rebuilt from the
    card. The floor was the one left out, which is why it was worth a test
    rather than a second reading of the same function.
    """
    import json

    from competition.league import opponent_from_checkpoint

    session = tmp_path / "v4"
    session.mkdir()
    (session / "card.json").write_text(
        json.dumps({"environment": {"ground_avoidance": {"elevator": 1.0, "ceiling_ft": 15000.0}}}),
        encoding="utf-8",
    )
    (session / "checkpoint.zip").write_bytes(b"")

    monkeypatch.setattr("competition.league.load_actor", lambda *a, **k: lambda state: np.zeros(4))
    opponent = opponent_from_checkpoint(session / "checkpoint.zip")

    assert opponent.ground_avoidance is not None
    assert opponent.ground_avoidance.elevator == 1.0
    assert opponent.ground_avoidance.ceiling_ft == 15000.0


def test_an_opponent_that_trained_without_a_floor_is_not_given_one(tmp_path, monkeypatch):
    """Symmetry: the point is the aircraft it learned on, not a floor for
    everybody."""
    import json

    from competition.league import opponent_from_checkpoint

    session = tmp_path / "run1"
    session.mkdir()
    (session / "card.json").write_text(
        json.dumps({"environment": {"ground_avoidance": None}}), encoding="utf-8"
    )
    (session / "checkpoint.zip").write_bytes(b"")

    monkeypatch.setattr("competition.league.load_actor", lambda *a, **k: lambda state: np.zeros(4))
    assert opponent_from_checkpoint(session / "checkpoint.zip").ground_avoidance is None


def test_the_floor_reaches_the_command_an_opponent_sends():
    """Not just held on the object. A floor that is stored and never applied is
    the same as no floor, and looks correct in a debugger."""
    from competition.league import PolicyOpponent
    from competition.safety import GroundAvoidance
    from competition.state import Telemetry

    values = dict.fromkeys(Telemetry.__dataclass_fields__, 0.0)
    values["own_alt_ft"] = 300.0  # below the floor's hard altitude
    values["own_vd_fps"] = 500.0  # and descending fast
    values["enemy_alt_ft"] = 300.0
    diving = Telemetry(**values)

    def nose_down(state):
        return np.array([0.0, 1.0, 0.0, 0.8])  # positive elevator is nose down

    unprotected = PolicyOpponent(nose_down)
    protected = PolicyOpponent(nose_down, ground_avoidance=GroundAvoidance())

    for _ in range(120):
        loose = unprotected(diving)
        held = protected(diving)

    assert loose[1] > 0.0, "the policy asked to keep diving and got it"
    assert held[1] < 0.0, "the floor pulled up instead"


# --------------------------- every setting that makes it a different aircraft


#: Fields of EnvConfig that change how an aircraft flies, and therefore have to
#: reach a pool opponent too, against how each one gets there. Written out
#: rather than inferred so that adding a field to EnvConfig fails this test and
#: forces the question — which is the only thing that would have caught the
#: five separate omissions this has already cost: the encoder, the decision
#: rate, the rudder cap, the ground floor and the load-factor limit, each found
#: one at a time and each after a training run that did not mean what it said.
PLANT_FIELDS = {
    "observation": "encoder",
    "action_repeat": "action_repeat",
    "rudder_limit": "rudder_limit",
    "high_speed_elevator_limit": "high_speed_elevator_limit",
    "ground_avoidance": "ground_avoidance",
    "g_limit": "g_limit",
}

#: Fields that describe the engagement rather than the aeroplane. An opponent
#: does not carry these: they belong to the round both sides are flying.
NOT_THE_AIRCRAFT = {
    "setup",
    "weights",
    "envelope",
    "opponent",
    "opponent_aggression",
    "opponent_policy",
    "opponent_pool",
    "league_scheme",
    "jsbsim_root",
    "round_seconds",
    "rudder_enabled",
    "speed_before_altitude",
    "seed",
}


def test_every_plant_setting_is_classified():
    """A new EnvConfig field is a decision, not a default.

    Each of the five omissions so far looked like this one: a field added to
    the player's plant and not to the opponent's, discovered after a run.
    """
    from competition.environment import EnvConfig

    known = set(PLANT_FIELDS) | NOT_THE_AIRCRAFT
    unclassified = sorted(set(EnvConfig.__dataclass_fields__) - known)

    assert not unclassified, (
        f"EnvConfig gained {unclassified}: does a pool opponent need it? "
        "Add it to PLANT_FIELDS and to opponent_from_checkpoint, or to "
        "NOT_THE_AIRCRAFT if it describes the round rather than the aeroplane."
    )


def test_a_pool_opponent_carries_every_plant_setting(tmp_path, monkeypatch):
    """And carries them from its own card, not from whatever is training."""
    import json

    from competition.league import opponent_from_checkpoint

    session = tmp_path / "v4"
    session.mkdir()
    (session / "card.json").write_text(
        json.dumps(
            {
                "environment": {
                    "observation": "extended",
                    "action_repeat": 6,
                    "rudder_limit": 0.6,
                    "high_speed_elevator_limit": 0.4,
                    "ground_avoidance": {"elevator": 1.0},
                    "g_limit": 9.0,
                }
            }
        ),
        encoding="utf-8",
    )
    (session / "checkpoint.zip").write_bytes(b"")

    monkeypatch.setattr("competition.league.load_actor", lambda *a, **k: lambda state: np.zeros(4))
    opponent = opponent_from_checkpoint(session / "checkpoint.zip")

    for field, attribute in PLANT_FIELDS.items():
        assert getattr(opponent, attribute, None) is not None, f"{field} did not reach the opponent"
    assert opponent.g_limit == 9.0
    assert opponent.action_repeat == 6


# ------------------------------------------------------- the "ema" scheme


def test_the_ema_scheme_weights_from_the_first_round():
    """The paper's gate needs a hundred matchups per opponent per worker; a
    2,000,000-step run over eight workers never gets there. SRC-012's moving
    average starts pulling towards whoever beats us immediately."""
    from competition.league import EMA_ALPHA, EMA_TEMPERATURE, EMA_UNIFORM_FLOOR, League

    league = League(["easy", "hard"], scheme="ema")
    before = league.shares()
    assert before["easy"] == pytest.approx(before["hard"])
    league.record("easy", True)
    league.record("hard", False)
    after = league.shares()
    assert after["hard"] > after["easy"]
    assert league.ema_alpha == EMA_ALPHA and league.temperature == EMA_TEMPERATURE
    assert league.uniform_floor == EMA_UNIFORM_FLOOR


def test_the_ema_is_the_stated_recurrence():
    from competition.league import League

    league = League(["a"], scheme="ema", ema_alpha=0.1)
    assert league.records["a"].ema == 0.5
    league.record("a", True)
    assert league.records["a"].ema == pytest.approx(0.55)
    league.record("a", False)
    assert league.records["a"].ema == pytest.approx(0.495)


def test_the_uniform_floor_keeps_every_opponent_in_play():
    """Beat one opponent forty times running: it still gets at least half of
    its uniform share, which is the paper's MIN_SHARE by another route."""
    from competition.league import League

    league = League(["beaten", "other", "third"], scheme="ema", uniform_floor=0.5)
    played(league, "beaten", wins=40, losses=0)
    played(league, "other", wins=0, losses=40)
    shares = league.shares()
    assert shares["beaten"] >= 0.5 / 3 - 1e-9
    assert shares["other"] > shares["third"] > shares["beaten"]
    assert sum(shares.values()) == pytest.approx(1.0)


def test_the_ema_shares_are_the_stated_formula():
    import math

    from competition.league import League

    league = League(["a", "b"], scheme="ema", ema_alpha=0.5, temperature=0.3, uniform_floor=0.5)
    league.record("a", True)  # ema 0.75
    league.record("b", False)  # ema 0.25
    ea, eb = math.exp(-0.75 / 0.3), math.exp(-0.25 / 0.3)
    expected_a = 0.25 + 0.5 * ea / (ea + eb)
    assert league.shares()["a"] == pytest.approx(expected_a)


def test_an_unknown_scheme_is_refused():
    from competition.league import League

    with pytest.raises(ValueError, match="league scheme"):
        League(["a"], scheme="elo")


def test_the_paper_scheme_is_untouched_by_the_ema_fields():
    """Default construction is the paper's rule exactly, as before."""
    from competition.league import League

    league = League(["a", "b"])
    assert league.scheme == "paper"
    league.record("a", False)
    assert league.shares()["a"] == pytest.approx(0.5), "uniform: the gate has not opened"


def test_a_pool_opponent_encodes_every_frame_and_decides_every_action_repeat():
    """The extended observation's rates are per-frame differences times 60,
    and its clock counts encode calls. An opponent that encoded only when it
    decided saw rates six times too large and a round ageing at a sixth of
    the speed; the seat under test, encoded every frame, won 85% against the
    same checkpoint. Both seats have to read the same numbers.
    """
    from competition.features import EXTRA_FIELDS, ExtendedEncoder
    from competition.state import STATE_SIZE, Telemetry

    seen: list[np.ndarray] = []

    def predict(observation):
        seen.append(np.array(observation, dtype=np.float64))
        return np.zeros(4)

    opponent = PolicyOpponent(predict, action_repeat=6, encoder=ExtendedEncoder())
    reference = ExtendedEncoder()
    frames = []
    for frame in range(13):
        values = dict.fromkeys(Telemetry.__dataclass_fields__, 0.0)
        values["own_alt_ft"] = values["enemy_alt_ft"] = 18_000.0
        values["own_vc_fps"], values["own_vt_fps"] = 574.0, 740.0
        values["own_vn_fps"] = values["enemy_vn_fps"] = 700.0
        values["enemy_lat_deg"] = 0.01
        values["enemy_lon_deg"] = 0.0005 * frame  # drifting across the nose
        frames.append(Telemetry(**values))

    per_frame = [reference.encode(t) for t in frames]
    for t in frames:
        opponent(t)

    assert len(seen) == 3, "decisions at frames 0, 6 and 12"
    rate = STATE_SIZE + EXTRA_FIELDS.index("azimuth_rate")
    clock = STATE_SIZE + EXTRA_FIELDS.index("round_elapsed")
    assert seen[2][rate] == pytest.approx(per_frame[12][rate])
    assert seen[2][clock] == pytest.approx(per_frame[12][clock])
    assert seen[2][rate] != 0.0, "the drift has to show up, or the test checks nothing"
