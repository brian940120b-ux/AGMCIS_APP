"""Scripted opponents in the pool, beside saved policies (hypothesis H2's
missing half: docs/PRIOR_ART.md 6.6, SRC-001 and SRC-012 both mix them)."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from competition.adversaries import ADVERSARIES, BreakTurn, Scissors
from competition.environment import (
    BUILTIN_OPPONENTS,
    CompetitionRound,
    EnvConfig,
    _build_opponent,
    opponent_names,
)
from competition.evaluate import OPPONENT_NAMES
from competition.gym_env import CompetitionEnv
from competition.safety import GroundAvoidance


def test_the_names_are_the_built_ins_then_the_scripted_set():
    assert opponent_names()[: len(BUILTIN_OPPONENTS)] == BUILTIN_OPPONENTS
    assert set(opponent_names()) == set(BUILTIN_OPPONENTS) | set(ADVERSARIES)


def test_evaluate_offers_the_same_names_and_no_others():
    """Two lists, one owner: evaluate's --opponent choices are what the
    environment can build, in its own order."""
    assert set(OPPONENT_NAMES) == set(opponent_names())


def test_a_string_in_the_pool_slot_builds_the_named_script():
    config = EnvConfig(opponent_policy="break")
    assert isinstance(_build_opponent(config, 15_000.0, 340.0, seed=1), BreakTurn)
    config = EnvConfig(opponent_policy="scissors")
    assert isinstance(_build_opponent(config, 15_000.0, 340.0, seed=1), Scissors)


def test_a_name_is_rebuilt_each_round_so_its_state_starts_fresh():
    config = EnvConfig(opponent_policy="scissors")
    first = _build_opponent(config, 15_000.0, 340.0, seed=1)
    second = _build_opponent(config, 15_000.0, 340.0, seed=1)
    assert first is not second


def test_a_pool_of_names_runs_a_round_against_one_of_them():
    pool = {"break": "break", "wanderer": "wanderer"}
    env = CompetitionEnv(
        EnvConfig(opponent_pool=pool, ground_avoidance=GroundAvoidance()),
        reward_mode="margin",
        seed=5,
    )
    env.reset(seed=11)
    assert env._facing in ("break", "wanderer")
    assert type(env.round.opponent).__name__ in ("BreakTurn", "Wanderer")
    hold = np.array([0.0, 0.0, 0.0, 0.8])
    for _ in range(30):
        _, _, terminated, truncated, _ = env.step(hold)
        if terminated or truncated:
            break


def test_the_league_scheme_reaches_the_card():
    assert EnvConfig().describe()["league_scheme"] == "paper"
    assert EnvConfig(league_scheme="ema").describe()["league_scheme"] == "ema"
    env = CompetitionEnv(EnvConfig(opponent_pool={"break": "break"}, league_scheme="ema"), seed=1)
    assert env._league is not None and env._league.scheme == "ema"


def test_a_round_with_a_named_opponent_flies(seed: int = 2):
    round_ = CompetitionRound(EnvConfig(opponent_policy="energy"), seed=seed)
    for _ in range(10):
        round_.step(np.array([0.0, 0.0, 0.0, 0.8]))
    assert round_.frame == 10


def test_build_config_splits_names_from_paths(tmp_path, monkeypatch):
    from competition import train

    calls = {}

    def fake_collect(paths):
        calls["paths"] = list(paths)
        return {}

    monkeypatch.setattr("competition.league.collect_checkpoints", fake_collect)
    args = argparse.Namespace(
        reference_setup=False,
        opponent_pool=["break", str(tmp_path / "v9"), "scissors"],
        observation="reference",
        jsbsim_root=None,
        rudder=False,
        rudder_limit=0.2,
        opponent="reference",
        opponent_aggression=1.0,
        reference_speed_order=False,
        action_repeat=1,
        ground_avoidance=False,
        g_limit=0.0,
        algorithm="sac",
        league="ema",
        geometry="published",
    )
    config = train.build_config(args)
    assert config.opponent_pool == {"break": "break", "scissors": "scissors"}
    assert calls["paths"] == [str(tmp_path / "v9")], "only the path went looking for a checkpoint"
    assert config.league_scheme == "ema"


def test_mirror_is_refused_for_ppo_before_anything_is_built(capsys):
    from competition import train

    args = train.parse_args(["--algorithm", "ppo", "--mirror", "--name", "nope", "--timesteps", "1"])
    assert args.mirror is True, "the parser accepts it; train() refuses it before building workers"
    assert train.DEFAULTS["mirror"] is False and train.DEFAULTS["league"] == "paper"
    assert train.INHERITED["mirror"] == ("hyperparameters", "mirror")
    assert train.INHERITED["league"] == ("environment", "league_scheme")


def test_the_defaults_are_still_the_organisers_recipe():
    """Neither new flag changes a run that does not ask for it."""
    from competition import train

    args = train.parse_args(["--name", "x"])
    assert args.mirror is None and args.league is None
    train.apply_defaults(args)
    assert args.mirror is False and args.league == "paper"
    assert train.describe_hyperparameters(args)["mirror"] is False


def test_the_kaggle_trainer_passes_scripted_names_through_and_looks_for_the_rest():
    script = Path(__file__).resolve().parents[2] / "kaggle" / "aifcs_train.py"
    spec = importlib.util.spec_from_file_location("aifcs_train_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    scripted, sessions = module.split_pool(["v4", "break", "v5", "scissors"])
    assert scripted == ["break", "scissors"]
    assert sessions == ["v4", "v5"]


@pytest.mark.parametrize("name", sorted(ADVERSARIES))
def test_every_scripted_name_is_accepted_in_a_pool(name: str):
    config = EnvConfig(opponent_policy=name)
    assert type(_build_opponent(config, 15_000.0, 340.0, seed=3)) is ADVERSARIES[name]


def test_the_kaggle_trainer_leaves_a_git_bundle_and_never_fails_on_it(tmp_path, monkeypatch, capsys):
    """Kaggle is the courier for a laptop that cannot reach GitHub: the run
    writes a bundle beside sessions/. A bundle that cannot be made is a line
    of output, not a failed run."""
    import subprocess

    script = Path(__file__).resolve().parents[2] / "kaggle" / "aifcs_train.py"
    spec = importlib.util.spec_from_file_location("aifcs_train_bundle_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    # A real repository, so the real git makes a real bundle.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "one"],
        cwd=repo,
        check=True,
    )
    target = tmp_path / "out.bundle"
    assert module.export_bundle(home=repo, target=target) is True
    assert target.is_file() and target.stat().st_size > 0
    assert (
        subprocess.run(["git", "bundle", "verify", str(target)], cwd=repo, capture_output=True).returncode
        == 0
    )
    assert "repository bundle for the laptop" in capsys.readouterr().out

    # Not a repository at all: reported, not raised.
    assert module.export_bundle(home=tmp_path / "nowhere", target=tmp_path / "no.bundle") is False
    assert "bundle skipped" in capsys.readouterr().out
