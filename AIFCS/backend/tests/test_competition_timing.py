"""Where a training step's time goes, and what to buy about it (COMP PHASE 16).

Built before spending money on a machine, because which machine depends on the
answer. Measured mid-run on the laptop: the GPU sat at 33% utilisation holding
429 MB of 6 GB, so the limit is neither GPU compute nor video memory, and a
bigger GPU was about to be bought on the assumption that it was.
"""

from __future__ import annotations

from competition.timing import verdict


def test_learning_dominating_says_do_not_buy_a_bigger_gpu():
    """The case the 33% reading pointed at.

    gradient_steps -1 on a two-layer network is many tiny GPU calls, and a
    kernel launch does not get faster on a better card. The advice has to be
    the settings, not the hardware.
    """
    lines = "\n".join(verdict(env_s=2.0, total_s=10.0, workers=8))

    assert "wrong purchase" in lines
    assert "--gradient-steps" in lines
    assert "--batch-size" in lines


def test_simulation_dominating_says_buy_cores_not_a_card():
    """JSBSim is CPU and parallelises across workers. The mistake available
    here is renting a machine chosen by its GPU."""
    lines = "\n".join(verdict(env_s=9.0, total_s=10.0, workers=8))

    assert "--workers" in lines
    assert "cores, not a bigger GPU" in lines


def test_a_split_with_no_bottleneck_says_buy_nothing():
    """The answer that saves the most money is the one that is easiest to talk
    yourself out of reporting."""
    lines = "\n".join(verdict(env_s=5.0, total_s=10.0, workers=8))

    assert "no single bottleneck" in lines
    assert "worth buying hardware" in lines


def test_the_percentages_are_of_the_whole_and_add_up():
    """A reader compares the two numbers in the first line; they have to be
    halves of the same thing, not two rates."""
    lines = verdict(env_s=3.0, total_s=10.0, workers=8)

    assert "70%" in lines[0] and "30%" in lines[0]


def test_a_session_that_never_finished_is_refused_not_guessed_at(tmp_path, capsys):
    """No card means no hyperparameters, and timing a run against defaults it
    never used would produce a confident wrong answer."""
    from competition.timing import main

    (tmp_path / "v9").mkdir()
    assert main([str(tmp_path / "v9")]) == 1
    assert "card.json" in capsys.readouterr().out


def test_no_module_here_shadows_one_the_standard_library_needs():
    """The bug that cost this file its first name.

    `train.py` runs as a script, so `backend/competition` becomes sys.path[0]
    and every module in it outranks the standard library for the whole
    process. `competition/profile.py` therefore became what `cProfile` imports
    on its first line, torch imports cProfile, and training died with an
    AttributeError six frames inside torch._dynamo naming neither the file nor
    the collision.

    Checked by name rather than by importing, because importing a shadowing
    module is how the damage happens.
    """
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent / "competition"
    ours = {path.stem for path in here.glob("*.py")} - {"__init__", "__main__"}

    collisions = sorted(ours & set(sys.stdlib_module_names))
    assert not collisions, f"these shadow the standard library for a script run: {collisions}"
