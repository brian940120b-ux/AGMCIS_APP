"""The scripted opponent set.

What these pin is that each one actually flies the tactic its name claims, and
that none of them flies into the ground — a scripted opponent that crashes is
not a curriculum rung, it is a free win, which is precisely what v4 was to v5.
"""

from __future__ import annotations

import pytest

pytest.importorskip("jsbsim")

from competition.adversaries import ADVERSARIES, BreakTurn, Wanderer, build
from competition.environment import CompetitionRound, EnvConfig
from competition.evaluate import neutral_policy


def _fly(opponent: str, seconds: float = 60.0, seed: int = 1000):
    """One round against this opponent, with our side doing nothing."""
    game = CompetitionRound(config=EnvConfig(opponent=opponent), seed=seed)
    observation = game.reset(seed=seed)
    policy = neutral_policy()
    track = []
    for _ in range(int(seconds * 60)):
        observation, geometry, finished, reason = game.step(policy(observation))
        # The opponent's own bank, seen from its own cockpit. Not
        # `geometry.azimuth_deg`, which is *our* bearing to it and barely moves
        # while it turns — the first version of this test asserted on that and
        # called a 77-degree break turn a straight line.
        foe = game.foe.telemetry_against(game.own)
        track.append((game.foe.altitude_m, foe.own_roll_deg, geometry))
        if finished:
            break
    return track, reason


@pytest.mark.parametrize("name", sorted(ADVERSARIES))
def test_every_adversary_stays_airborne(name: str):
    """The rung has to be there for the rungs above it to mean anything."""
    track, reason = _fly(name)

    assert len(track) > 600, f"{name} ended the round in under ten seconds: {reason}"
    lowest = min(altitude for altitude, _, _ in track)
    assert lowest > 150.0, f"{name} got within {lowest:.0f} m of the ground"


@pytest.mark.parametrize("name", sorted(ADVERSARIES))
def test_every_adversary_is_reachable_by_name(name: str):
    assert build(name) is not None


def test_an_unknown_name_says_what_it_has():
    with pytest.raises(KeyError, match="break"):
        build("no-such-opponent")


def test_the_break_turn_actually_banks():
    """A break turn that does not roll is a straight-line opponent with a
    misleading name, which is worse than not having one. The reference drone
    holds 0.0 degrees of bank for the whole round, so this is the difference
    between the two."""
    track, _ = _fly("break", seconds=30.0)
    banks = [roll for _, roll, _ in track]

    assert max(abs(roll) for roll in banks) > 45.0, "a break turn is a hard bank"


def test_the_scissors_reverses():
    """A scissors that only ever turns one way is a break turn. What makes it
    its own rung is the reversal, which is what denies a tracking solution."""
    track, _ = _fly("scissors", seconds=30.0)
    banks = [roll for _, roll, _ in track]

    assert max(banks) > 40.0 and min(banks) < -40.0, "it has to go both ways"


def test_the_wanderer_is_reproducible_from_its_seed():
    """A random opponent that is not seeded makes every evaluation against it
    noise, and the whole point of a scripted opponent is a fixed ruler."""
    first, _ = _fly("wanderer", seconds=20.0, seed=4)
    again, _ = _fly("wanderer", seconds=20.0, seed=4)

    assert [g.distance_m for _, _, g in first] == [g.distance_m for _, _, g in again]


def test_two_wanderers_on_different_seeds_differ():
    """Otherwise the seed is decoration."""
    first, _ = _fly("wanderer", seconds=20.0, seed=4)
    other, _ = _fly("wanderer", seconds=20.0, seed=5)

    assert [g.distance_m for _, _, g in first] != [g.distance_m for _, _, g in other]


def test_a_reset_adversary_starts_over():
    """`_build_opponent` resets a policy opponent rather than rebuilding it, and
    these have to survive the same treatment."""
    wanderer = Wanderer(seed=3)
    wanderer.frame = 500
    wanderer.reset()

    assert wanderer.frame == 0


def test_the_floor_outranks_the_tactic():
    """Below the floor every one of them levels the wings and raises the nose,
    whatever it was doing. Checked on the break turn because it is the one that
    would otherwise fly a 75-degree bank into the ground."""
    from competition.state import Telemetry

    telemetry = Telemetry(
        **{**{f.name: 0.0 for f in Telemetry.__dataclass_fields__.values()}, "own_alt_ft": 500.0}
    )
    commanded = BreakTurn().airframe.fly_to(telemetry, roll_deg=75.0, pitch_deg=-10.0)

    # Nose-up is negative elevator in the organiser's convention.
    assert commanded[1] < 0.0, "it has to pull up near the ground"
