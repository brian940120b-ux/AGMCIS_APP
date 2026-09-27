"""Measuring a policy the way the day measures it (COMP PHASE 13).

Until this existed there was no repeatable number to tune against: training
reports a reward, the competition reports a verdict, and `rewards.py` exists
precisely because those are different functions. These tests are about the
measuring instrument, so they check the properties that make a measurement
worth trusting — same seeds give the same answer, the rates count what they
say they count — rather than any particular policy being good.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("jsbsim")

from competition.action import INITIAL_THROTTLE
from competition.environment import EnvConfig
from competition.evaluate import SWEET_SPOT_M, evaluate, neutral_policy
from competition.scoring import AttackEnvelope, distance_factor
from competition.state import Geometry


def _at(distance_m: float) -> Geometry:
    """Geometry with the nose exactly on, at a chosen range."""
    return Geometry(
        distance_m=distance_m,
        track_angle_deg=0.0,
        azimuth_deg=0.0,
        elevation_deg=0.0,
        aspect_angle_deg=0.0,
        own_alt_m=5000.0,
        enemy_alt_m=5000.0,
    )


def test_the_sweet_spot_is_where_the_two_rules_overlap():
    """Arithmetic, not opinion, and the one place the scoring rewards a choice.

    The attack envelope opens at 500 ft (152 m) and the distance factor is at
    its highest, 1.2, from 150 m to 500 m. Between 152 m and 500 m a policy is
    therefore both able to score a kill and earning position points at the best
    rate available. Outside it, one of the two is always giving something up.
    """
    low, high = SWEET_SPOT_M
    envelope = AttackEnvelope()

    # The lower edge is the envelope's, not the band's. The two do not line up:
    # the 1.2 band opens at 150 m and the envelope at 152.4 m, leaving 2.4 m
    # where the position score is at its best and a shot is still too close to
    # count. Worth knowing rather than rounding away — the rules use metres for
    # one and feet for the other, and that is where the sliver comes from.
    assert low == pytest.approx(envelope.min_range_ft * 0.3048, abs=0.01)
    assert distance_factor(149.0) == 0.1, "below the band, position is worth a twelfth"
    assert distance_factor(151.0) == 1.2, "the band opens before the envelope does"
    assert not envelope.contains(_at(151.0)), "and a shot there is too close to count"

    assert distance_factor((low + high) / 2) == 1.2, "inside, the band pays the most"
    assert distance_factor(high + 1) == 1.0, "above it, position is worth less"
    # And every metre of the sweet spot is a firing solution.
    assert envelope.min_range_ft <= low / 0.3048 + 0.001
    assert high / 0.3048 <= envelope.max_range_ft


def test_the_same_seed_gives_the_same_round(tmp_path):
    """A measurement that moves on its own cannot settle an argument."""
    config = EnvConfig()
    first = evaluate(neutral_policy(), config, rounds=2, seed=7, label="a")
    second = evaluate(neutral_policy(), config, rounds=2, seed=7, label="b")

    assert [r.frames for r in first.rounds] == [r.frames for r in second.rounds]
    assert first.mean_margin == second.mean_margin
    assert [r.outcome.verdict for r in first.rounds] == [r.outcome.verdict for r in second.rounds]


def test_different_seeds_give_different_rounds():
    """The counterpart: identical results across seeds would mean the seed is
    not reaching the initial conditions, and every round would be one round."""
    config = EnvConfig()
    report = evaluate(neutral_policy(), config, rounds=3, seed=11)
    assert len({r.frames for r in report.rounds}) > 1


def test_doing_nothing_flies_into_the_ground():
    """Worth pinning, because it is the environment's most important property.

    A centred stick is not level flight here: the aircraft is initialised
    without trim, exactly as the reference package initialises it, and it
    pitches down and accelerates into the ground inside a minute. Every policy
    has to spend some of its learning on staying airborne before any of it can
    go on fighting, which is the likeliest reason an undertrained policy scores
    zero kills and crashes in every round.
    """
    report = evaluate(neutral_policy(), EnvConfig(), rounds=3, seed=100, label="neutral")

    assert report.crash_rate == 1.0
    assert report.win_rate == 0.0
    assert all(r.frames < 300 * 60 for r in report.rounds), "it never reaches five minutes"


def test_the_neutral_baseline_holds_the_throttle_it_started_with():
    """The first version of this sent four zeros, which closes the throttle.

    That is not "do nothing" — it is an order — and the crash it produced would
    have been read as the environment being harsher than it is.
    """
    action = neutral_policy()(np.zeros(20))
    assert action[3] == pytest.approx(INITIAL_THROTTLE)
    assert list(action[:3]) == [0.0, 0.0, 0.0]


def test_a_report_counts_what_it_says_it_counts():
    report = evaluate(neutral_policy(), EnvConfig(), rounds=4, seed=100)
    won = sum(1 for r in report.rounds if r.won)
    assert report.win_rate == pytest.approx(won / 4)
    assert "rounds" in report.summary()
    assert report.as_dict()["rounds"] == 4
    assert len(report.as_dict()["detail"]) == 4


# --------------------------------------------- rebuilding a session's aircraft


def test_a_policy_is_flown_in_the_plant_it_learned(tmp_path):
    """Two sessions trained under different settings cannot share one config —
    a thirty-input policy will not even accept a twenty-input observation.

    So each is rebuilt from its own card, and only the exam is held common.
    Comparing two policies means comparing two systems, because a system is
    what goes to the competition.
    """
    from competition.evaluate import config_from_card

    modern = config_from_card(
        {
            "environment": {
                "observation": "extended",
                "action_repeat": 6,
                "rudder_limit": 1.0,
                "ground_avoidance": {"seconds_to_impact": 8.0, "elevator": 0.6},
            }
        },
        opponent="pursuit",
        aggression=1.5,
    )
    assert modern.observation == "extended"
    assert modern.action_repeat == 6
    assert modern.rudder_limit == 1.0
    assert modern.ground_avoidance is not None
    assert modern.ground_avoidance.elevator == 0.6

    # The opponent is the exam, not the aircraft: it comes from the caller.
    assert modern.opponent == "pursuit"
    assert modern.opponent_aggression == 1.5


def test_a_session_from_before_these_settings_rebuilds_as_the_reference():
    """run1 was trained before most of them existed and still has to be
    scoreable, or there is nothing to compare anything against."""
    from competition.evaluate import config_from_card

    old = config_from_card({"environment": {"opponent": "reference"}}, "reference", 1.0)
    assert old.observation == "reference"
    assert old.action_repeat == 1
    assert old.ground_avoidance is None


def test_a_card_that_is_missing_entirely_still_scores():
    from competition.evaluate import config_from_card

    assert config_from_card({}, "reference", 1.0).observation == "reference"


# ---------------------------------------------- asking a what-if of a policy


def test_a_floor_can_be_bolted_onto_a_policy_that_never_trained_with_one():
    """run1 reached five million steps, scores the best margin of anything
    measured, and crashes in nineteen rounds out of twenty — so it wins none,
    because losing the aircraft loses the round whatever the score.

    The floor is a separate layer that needs no retraining, so "what would this
    policy do with one under it" is a fair question to measure.
    """
    from competition.evaluate import config_from_card

    trained_without = {"environment": {"opponent": "reference"}}
    assert config_from_card(trained_without, "reference", 1.0).ground_avoidance is None

    with_one = config_from_card(trained_without, "reference", 1.0, ground_avoidance=True)
    assert with_one.ground_avoidance is not None


def test_a_floor_can_be_taken_away_to_see_what_the_policy_did_alone():
    from competition.evaluate import config_from_card

    trained_with = {"environment": {"ground_avoidance": {"elevator": 0.6}}}
    assert config_from_card(trained_with, "reference", 1.0).ground_avoidance is not None
    assert config_from_card(trained_with, "reference", 1.0, ground_avoidance=False).ground_avoidance is None


def test_a_borrowed_floor_is_marked_in_the_label():
    """A report must not let a result look like something the policy did on its
    own terms when a layer it never trained with was added underneath."""
    from competition.evaluate import _floor_suffix

    without = {"environment": {}}
    with_one = {"environment": {"ground_avoidance": {"elevator": 0.6}}}

    assert _floor_suffix(without, True) == " +floor"
    assert _floor_suffix(with_one, False) == " -floor"
    assert _floor_suffix(without, None) == "", "nothing borrowed, nothing to declare"
    assert _floor_suffix(with_one, True) == "", "it already had one"


def test_the_setup_line_declares_a_borrowed_floor_too():
    """The table row is not the only place a reader looks. The line naming the
    session's setup has to describe the aircraft actually flown, or the report
    declares the borrowed floor in one place and hides it in the other."""
    from competition.evaluate import _describe

    without = {"environment": {}}
    with_one = {"environment": {"ground_avoidance": {"elevator": 0.6}}}

    assert "floor (borrowed)" in _describe(without, True)
    assert "no floor (removed)" in _describe(with_one, False)
    assert "floor" not in _describe(without, None)
    assert "floor" in _describe(with_one, None)
    assert "borrowed" not in _describe(with_one, True), "it already had one"


def test_a_recorded_floor_left_on_defaults_still_shows_up():
    """`ground_avoidance: {}` means a floor whose settings are all defaults.
    An empty dict is falsy, so describing it by truthiness printed a session
    that has a floor as one that does not."""
    from competition.evaluate import _describe

    assert "floor" in _describe({"environment": {"ground_avoidance": {}}})


def test_the_report_says_how_much_of_the_round_the_floor_flew():
    """Outcome columns cannot tell a policy that survived from one that was
    carried. run1 came back 0% crashed and 55% won on the corrected floor with
    a margin of +1, against +78 on the weaker one — a number that only means
    something next to how often the floor had the stick."""
    from competition.evaluate import Report, RoundReport, compare
    from competition.scoring import EndReason, RoundOutcome, Verdict

    def round_at(share: float) -> RoundReport:
        outcome = RoundOutcome(
            verdict=Verdict.BLUE,
            reason=EndReason.TIME,
            blue={"killed": False, "advantage_score": 0.0},
            red={"killed": False, "advantage_score": 0.0},
        )
        return RoundReport(
            seed=0,
            outcome=outcome,
            frames=18_000,
            min_distance_m=300.0,
            mean_distance_m=900.0,
            seconds_in_sweet_spot=0.3,
            floor_share=share,
        )

    report = Report(label="x", rounds=[round_at(0.10), round_at(0.30)])
    assert report.mean_floor_share == pytest.approx(0.20)
    assert "floor" in compare([report]).splitlines()[0]
    assert "20%" in compare([report])


def test_a_round_with_no_floor_reports_no_floor_time():
    """The default has to be zero, not absent, or the column reads as missing
    data on every session that never had one."""
    from competition.evaluate import play_round

    report = play_round(neutral_policy(), EnvConfig(), seed=7)
    assert report.floor_share == 0.0


def test_the_baseline_can_be_asked_for_on_its_own():
    """`--baseline` with no session is the question "what does a centred stick
    do on this floor", and it is the bar every trained policy has to clear."""
    from competition.evaluate import main

    with pytest.raises(SystemExit):
        main([])  # neither a session nor --baseline is an error, not a crash


def test_a_round_flown_with_a_floor_reports_the_frames_it_took():
    """The counting itself, not just the column.

    A first attempt here only checked the no-floor case, which reports 0.0
    whether or not anything is counted — deleting the counter left every test
    green. A centred stick on a floor spends about 6% of its frames under it,
    measured over 20 rounds, so a round that fires at all is the test.
    """
    from competition.evaluate import play_round
    from competition.safety import GroundAvoidance

    report = play_round(
        neutral_policy(),
        EnvConfig(ground_avoidance=GroundAvoidance()),
        seed=1000,
    )
    assert report.floor_share > 0.0, "a centred stick reaches the floor inside a round"
    assert report.floor_share < 1.0, "and is not flown by it the whole way"


# ------------------------------------------- one saved policy against another


def test_a_saved_policy_can_be_the_opponent():
    """The question neither built-in opponent can answer.

    A centred stick beats the drone 65% of the time and the pursuit controller
    90%, so beating either says nothing about whether one generation is better
    than the last. Self-play needs the generations to meet.
    """
    from competition.evaluate import config_from_card

    sentinel = object()
    config = config_from_card({}, "reference", 1.0, opponent_policy=sentinel)
    assert config.opponent_policy is sentinel


def test_each_session_meets_each_pool_member_and_the_row_says_which(monkeypatch, tmp_path):
    """The wiring and the labelling together.

    A row that reads `v5` when it was flown against v4 is worse than no row:
    the whole point of the pairing is that who it was against is the
    measurement. And one average over three different opponents would hide
    exactly the case worth seeing, which is losing to one of them.
    """
    from pathlib import Path

    import competition.evaluate as ev

    for name in ("v4", "v5"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "card.json").write_text("{}", encoding="utf-8")

    labels: list[str] = []

    def fake_evaluate(policy, config, *, rounds, seed, label, **kwargs):
        labels.append(label)
        return ev.Report(label=label)

    monkeypatch.setattr(ev, "evaluate", fake_evaluate)
    monkeypatch.setattr(ev, "load_policy", lambda *a, **k: lambda obs: obs)
    monkeypatch.setattr(
        "competition.league.collect_checkpoints",
        lambda paths: {"v4": Path("v4/checkpoint.zip"), "v3": Path("v3/checkpoint.zip")},
    )
    monkeypatch.setattr("competition.league.opponent_from_checkpoint", lambda *a, **k: object())

    ev.main([str(tmp_path / "v5"), "--opponent-pool", "anywhere", "--rounds", "1"])

    assert labels == ["v5 vs v4", "v5 vs v3"]


def test_the_aim_is_measured_separately_from_the_range():
    """Fifty-five seconds at the right distance and no kills says nothing.

    `152-500m` counts frames where the *aircraft* is in the right place; a
    kill needs the *nose* in the right place too, within one degree for three
    seconds. With only the first number measured, a policy 1.5 degrees off —
    which is a tuning problem — looked identical to one 40 degrees off, which
    is not. A centred stick is the clear case: it holds a heading and never
    points at anybody.
    """
    report = evaluate(neutral_policy(), rounds=2, seed=1000)

    assert report.best_track_angle_deg > 5.0, "a centred stick does not point at anything"
    assert report.mean_seconds_within_5deg == 0.0


def test_an_aim_never_taken_reads_as_no_aim_rather_than_a_perfect_one():
    """A round that never reaches the firing range has no aim to report, and a
    default of 0.0 would have read as dead on."""
    import dataclasses

    from competition.evaluate import RoundReport

    defaults = {f.name: f.default for f in dataclasses.fields(RoundReport)}

    assert defaults["best_track_angle_deg"] == 180.0
    assert defaults["seconds_within_5deg"] == 0.0


def test_a_checkpoint_wider_than_its_plant_says_which_file_is_missing(tmp_path, capsys):
    """The failure a downloaded session actually produces.

    A session copied off Kaggle without its card.json falls back to the
    reference setup, whose observation is ten numbers narrower than the
    extended one the policy was trained on. What came out was thirty lines of
    Stable-Baselines3 traceback ending in "Unexpected observation shape (20,)"
    — true, and no help at all in finding the file that is not there.
    """
    import competition.evaluate as ev

    session = tmp_path / "v6k"
    session.mkdir()
    (session / "checkpoint.zip").write_bytes(b"")

    def wide_policy(observation):
        return observation

    wide_policy.observation_width = 30

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ev, "load_policy", lambda *a, **k: wide_policy)
    try:
        code = ev.main([str(session), "--rounds", "1"])
    finally:
        monkeypatch.undo()

    printed = capsys.readouterr().out
    assert code == 1, "scoring a policy in the wrong plant must not be attempted"
    assert "card.json" in printed, "the message has to name the missing file"
    assert "30" in printed and "20" in printed, "and both widths, so the gap is visible"


def test_without_a_pool_the_row_is_just_the_session():
    """The built-in opponent is named in the header, not on every row."""
    import competition.evaluate as ev

    labels: list[str] = []

    def fake_evaluate(policy, config, *, rounds, seed, label, **kwargs):
        labels.append(label)
        return ev.Report(label=label)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(ev, "evaluate", fake_evaluate)
        ev.main(["--baseline", "--rounds", "1"])
    finally:
        monkeypatch.undo()

    assert labels == ["do nothing"]


def test_a_session_swallowed_by_the_opponent_pool_is_refused(tmp_path, capsys):
    """What produced a report of two baseline rows and nothing under test.

    `--opponent-pool` takes many paths, so `--baseline --opponent-pool
    v4/checkpoint.zip v5` put v5 in the pool and scored nothing. The output
    looked like a finished comparison — two rows, sensible numbers, a verdict
    — which is worse than an error.
    """
    import competition.evaluate as ev

    session = tmp_path / "v5"
    session.mkdir()
    (session / "card.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        ev.main(["--baseline", "--opponent-pool", "some/checkpoint.zip", str(session)])

    assert "Sessions go first" in capsys.readouterr().err


def test_a_pool_of_plain_checkpoints_with_no_session_is_still_allowed(tmp_path):
    """ "What does a centred stick do against v4" is a real question."""
    from pathlib import Path

    import competition.evaluate as ev

    labels: list[str] = []
    original = ev.evaluate

    def fake_evaluate(policy, config, *, rounds, seed, label, **kwargs):
        labels.append(label)
        return ev.Report(label=label)

    ev.evaluate = fake_evaluate
    try:
        import competition.league as league

        collect, build = league.collect_checkpoints, league.opponent_from_checkpoint
        league.collect_checkpoints = lambda paths: {"v4": Path("v4/checkpoint.zip")}
        league.opponent_from_checkpoint = lambda *a, **k: object()
        try:
            ev.main(["--baseline", "--opponent-pool", "v4/checkpoint.zip", "--rounds", "1"])
        finally:
            league.collect_checkpoints, league.opponent_from_checkpoint = collect, build
    finally:
        ev.evaluate = original

    assert labels == ["do nothing vs v4"]


def test_crashed_counts_our_aircraft_not_anyone_who_crashed():
    """The column that produced 85% won beside 90% crashed.

    EndReason.CRASH covers all three cases — we were lost, they were, or both
    — so reading it as our crash rate reported the opponent's deaths as ours.
    Against the drone and the pursuit controller the two coincided, because
    neither ever crashes; self-play is what pulled them apart, and 表 3 makes
    the pair impossible, which is how it was noticed.
    """
    from competition.evaluate import Report, RoundReport
    from competition.scoring import EndReason, RoundOutcome, Verdict

    def round_where(blue_crashed: bool, verdict: Verdict) -> RoundReport:
        return RoundReport(
            seed=0,
            outcome=RoundOutcome(
                verdict=verdict,
                reason=EndReason.CRASH,
                blue={"killed": False, "advantage_score": 0.0},
                red={"killed": False, "advantage_score": 0.0},
            ),
            frames=100,
            min_distance_m=300.0,
            mean_distance_m=900.0,
            seconds_in_sweet_spot=0.0,
            blue_crashed=blue_crashed,
        )

    theirs = Report(label="x", rounds=[round_where(False, Verdict.BLUE)] * 4)
    assert theirs.crash_rate == 0.0, "the opponent was lost, not us"
    assert theirs.win_rate == 1.0, "and that is why we won"

    ours = Report(label="y", rounds=[round_where(True, Verdict.RED)] * 4)
    assert ours.crash_rate == 1.0
    assert ours.win_rate == 0.0, "表 3: losing the aircraft loses the round"


def test_a_round_records_which_side_was_lost():
    """Read off the end reason the environment gave, which distinguishes them,
    rather than off the verdict, which a kill also decides."""
    from competition.evaluate import play_round
    from competition.safety import GroundAvoidance

    report = play_round(neutral_policy(), EnvConfig(ground_avoidance=GroundAvoidance()), seed=1000)

    assert report.blue_crashed is False, "a centred stick on a working floor survives"
    assert report.as_dict()["blue_crashed"] is False
