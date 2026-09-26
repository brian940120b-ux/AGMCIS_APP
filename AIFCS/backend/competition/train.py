"""Training that resumes: start it, stop it, shut the machine down, carry on.

    python backend/competition/train.py --name run1 --timesteps 5000000

`--timesteps` is a **total**, not an increment. Run that line on a session with
three million steps and it trains two million more; run it again and it says the
target is met and stops. "Train until it has had five million steps" is the
sentence someone means, and it is the one that survives being run twice.

Nothing survives a shutdown except what is on disk, so a checkpoint goes down
every `--checkpoint-every` steps and on the way out of any exit that is not a
power cut — Ctrl+C included. Losing the process costs one interval.

Run it as a file, with the `__main__` guard it has: `SubprocVecEnv` starts each
worker as a fresh interpreter on Windows, and each worker re-imports the
launching module. Without the guard the script re-runs itself and spawns workers
that spawn workers.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition.action import RUDDER_LIMIT
from competition.environment import EnvConfig, RoundSetup
from competition.gym_env import make_vec_env
from competition.rewards import RewardMode
from competition.safety import GroundAvoidance
from competition.session import (
    IncompatibleSession,
    KeepAwake,
    Session,
    SessionState,
    checkpoint_callback,
    describe_progress,
    human_duration,
    save_checkpoint,
    tensorboard_log_dir,
)

ALGORITHMS = ("ppo", "sac")


def build_config(args: argparse.Namespace) -> EnvConfig:
    setup = RoundSetup.reference() if args.reference_setup else RoundSetup()
    pool = {}
    if args.opponent_pool:
        from competition.league import PolicyOpponent, collect_checkpoints, load_predict

        for name, checkpoint in collect_checkpoints(args.opponent_pool).items():
            pool[name] = PolicyOpponent(
                load_predict(checkpoint, args.algorithm, device="cpu"),
                action_repeat=args.action_repeat,
                rudder_limit=args.rudder_limit,
            )

    return EnvConfig(
        setup=setup,
        opponent_pool=pool,
        observation=args.observation,
        jsbsim_root=args.jsbsim_root,
        rudder_enabled=args.rudder,
        rudder_limit=args.rudder_limit,
        opponent=args.opponent,
        opponent_aggression=args.opponent_aggression,
        speed_before_altitude=args.reference_speed_order,
        action_repeat=args.action_repeat,
        ground_avoidance=GroundAvoidance() if args.ground_avoidance else None,
    )


# Built line by line rather than as one block so the lint suppression can sit
# on the line that needs it. Inside a triple-quoted string a suppression is
# just text, and would be printed to the person reading this.
BANNER = "\n".join(
    (
        "AIFCS competition training",
        "==========================",
        "  Ctrl+C, or closing this window, stops it. Nothing is lost.",
        "  按 Ctrl+C 或關掉視窗就會停止，進度不會遺失。",  # noqa: RUF001
        "  Run it again to carry on from where it stopped.",
        "  再執行一次就會從停的地方繼續。",
        "",
    )
)


def _horizon_seconds(gamma: float, action_repeat: int = 1, tick_hz: float = 60.0) -> float:
    """How far ahead the value function can see, in seconds.

    1/(1-gamma) *decisions* is the standard reading of a discount as a horizon,
    and a decision lasts `action_repeat` frames — which is the whole point of
    repeating one. At 60 Hz and gamma 0.99 that is 1.7 s deciding every frame
    and 10 s deciding every sixth, from the same discount.

    Printed because 0.99 sounds like "almost everything", and because the first
    version of this line ignored the repeat and so reported the one number the
    reader would use to choose a discount, wrongly.
    """
    if gamma >= 1.0:
        return float("inf")
    return 1.0 / (1.0 - gamma) * action_repeat / tick_hz


def describe_hyperparameters(args: argparse.Namespace) -> dict[str, Any]:
    """The settings that make a run a different experiment, not a longer one."""
    described: dict[str, Any] = {
        "gamma": args.gamma,
        # Recorded beside it because a discount only means a horizon once you
        # know how long a decision lasts.
        "horizon_s": round(_horizon_seconds(args.gamma, args.action_repeat), 2),
        "learning_rate": args.learning_rate,
        "batch_size": args.batch_size,
        "sde": bool(args.sde),
        "hidden": list(args.hidden),
        "activation": "relu" if args.relu else "tanh",
    }
    if args.algorithm == "sac":
        described["gradient_steps"] = args.gradient_steps
    else:
        described["n_steps"] = args.n_steps
    return described


def train(args: argparse.Namespace) -> int:
    import torch as th
    from stable_baselines3 import PPO, SAC

    # Printed here rather than by train.bat: CMD's batch parser and UTF-8 do
    # not mix, and these lines in a .bat were taken as commands to run. Python
    # writes to the Windows console through an API that ignores the codepage.
    print(BANNER)

    config = build_config(args)
    session = Session(Path(args.output) / args.name)
    wanted = SessionState(
        name=args.name,
        algorithm=args.algorithm,
        target_timesteps=args.timesteps,
        reward=str(args.reward),
        environment=config.describe(),
        hyperparameters=describe_hyperparameters(args),
        seed=args.seed,
        workers=args.workers,
        created_at=datetime.now(UTC).isoformat(),
    )

    existing = session.read_state()
    if existing is not None:
        session.check_compatible(existing, wanted)
        state = existing
        state.target_timesteps = args.timesteps
        state.workers = args.workers
        remaining = args.timesteps - state.timesteps_done
        print(f"resuming {args.name}: {describe_progress(state, None)}")
        if remaining <= 0:
            print(f"target already met after {human_duration(state.wall_clock_s)} of training")
            return 0
        print(f"training {remaining:,} more steps\n")
    else:
        state = wanted
        print(f"starting {args.name}: {args.timesteps:,} steps\n")

    env = make_vec_env(args.workers, config, args.reward, seed=args.seed + state.runs)
    policy_kwargs: dict[str, Any] = {
        "net_arch": list(args.hidden),
        "activation_fn": th.nn.ReLU if args.relu else th.nn.Tanh,
    }
    if args.sde:
        policy_kwargs["use_sde"] = True
        policy_kwargs["log_std_init"] = -2
    common = {
        "env": env,
        "verbose": 1,
        "device": args.device,
        "tensorboard_log": tensorboard_log_dir(session.root),
        # Passed on both paths. `load` takes keyword overrides, so a resumed
        # model is trained under the settings asked for now rather than the
        # ones baked into the zip — and `check_compatible` has already refused
        # the resume if those differ from what the session was trained under.
        "gamma": args.gamma,
        "learning_rate": args.learning_rate,
    }
    if args.algorithm == "sac":
        common["gradient_steps"] = args.gradient_steps
    algorithm = PPO if args.algorithm == "ppo" else SAC

    model: PPO | SAC
    if session.exists:
        # The policy, its optimiser and — for SAC — everything it has seen.
        model = algorithm.load(session.model_path, **common)
        model.set_env(env)
        if isinstance(model, SAC):
            if session.buffer_path.is_file():
                model.load_replay_buffer(session.buffer_path)
                size_mb = session.buffer_path.stat().st_size / 1e6
                print(f"replay buffer restored ({size_mb:.0f} MB)")
            else:
                # PPO has no buffer to lose; SAC's is most of what it knows, and
                # resuming without it keeps the policy and restarts the experience.
                print("no replay buffer on disk — SAC restarts its experience, not its policy")
    else:
        extra = {"n_steps": args.n_steps} if args.algorithm == "ppo" else {}
        model = algorithm(
            "MlpPolicy",
            seed=args.seed,
            batch_size=args.batch_size,
            policy_kwargs=policy_kwargs,
            **common,
            **extra,
        )

    # Said plainly, on both paths. Stable-Baselines3 prints "Using cuda device"
    # when it constructs a model and says nothing at all when it loads one, so
    # on a resume — which is most runs — the one line telling you whether the
    # graphics card is being used simply is not there. Asking "is my GPU being
    # used?" should not require reading the library's source.
    print(f"device:   {model.device}")
    print(f"workers:  {args.workers} parallel simulations")
    horizon = _horizon_seconds(args.gamma, args.action_repeat)
    if args.action_repeat > 1:
        rate = 60.0 / args.action_repeat
        print(
            f"decisions: every {args.action_repeat} frames ({rate:.0f} Hz) — "
            f"{int(300 * rate):,} per round, and --timesteps counts these, not frames"
        )
    print(f"horizon:  gamma {args.gamma} — {horizon:.1f} s of future")

    remaining = args.timesteps - state.timesteps_done
    state.runs += 1
    callback = checkpoint_callback(session, state, args.checkpoint_every, args.save_buffer)

    started = time.perf_counter()
    interrupted = False
    try:
        with KeepAwake():
            model.learn(
                total_timesteps=remaining,
                reset_num_timesteps=True,
                progress_bar=False,
                callback=callback,
            )
    except KeyboardInterrupt:
        interrupted = True
        print("\nstopped — saving before exit")
    finally:
        elapsed = time.perf_counter() - started
        # The callback's own counter is the authority on steps taken this run.
        save_checkpoint(
            model,
            session,
            state,
            steps_this_run=int(getattr(callback, "num_timesteps", 0)),
            started_steps=callback.started_steps,
            wall_clock_before=callback.started_wall_clock,
            elapsed_s=elapsed,
            save_buffer=args.save_buffer,
        )
        env.close()

    rate = (state.timesteps_done - callback.started_steps) / elapsed if elapsed else None
    print(f"\n{describe_progress(state, rate)}")
    if rate:
        print(f"this run: {rate:.0f} steps/s over {human_duration(elapsed)}")
    print(f"session:  {session.root}")
    if state.timesteps_done < state.target_timesteps:
        print("\nrun the same command again to carry on — it resumes from here.")
    else:
        _write_card(session, state)
        print(f"card:     {session.card_path}")
    return 130 if interrupted else 0


def _write_card(session: Session, state: SessionState) -> None:
    """What this policy was trained against, for the registry and for a person."""
    card = state.as_dict()
    card["finished_at"] = datetime.now(UTC).isoformat()
    card["steps_per_second_mean"] = (
        round(state.timesteps_done / state.wall_clock_s, 1) if state.wall_clock_s else None
    )
    session.card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="run1", help="session directory name; resumed if it exists")
    parser.add_argument("--algorithm", choices=ALGORITHMS, default="sac")
    parser.add_argument(
        "--timesteps", type=int, default=5_000_000, help="TOTAL steps to reach, not additional"
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--n-steps", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument(
        "--gamma",
        type=float,
        default=0.99,
        help=(
            "discount. The effective horizon is 1/(1-gamma) frames at 60 Hz, so the "
            "default 0.99 sees 1.7 s — shorter than the 3 s of tracking a kill needs"
        ),
    )
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument(
        "--gradient-steps",
        type=int,
        default=1,
        help=(
            "SAC updates per rollout. train_freq counts vec-env iterations, not "
            "transitions, so with N workers the default does one update per N "
            "samples; -1 does as many as were collected"
        ),
    )
    parser.add_argument(
        "--sde",
        action="store_true",
        help="state-dependent exploration, as the organiser's own trainer uses",
    )
    parser.add_argument("--output", default="models/competition")
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=25_000,
        help="steps between checkpoints; a crash costs at most this many",
    )
    parser.add_argument(
        "--no-save-buffer",
        dest="save_buffer",
        action="store_false",
        help="skip SAC's replay buffer (smaller and faster checkpoints, colder resume)",
    )
    parser.add_argument(
        "--reward",
        choices=[mode.value for mode in RewardMode],
        default=RewardMode.REFERENCE.value,
    )
    parser.add_argument("--opponent", choices=["reference", "level", "pursuit"], default="reference")
    parser.add_argument(
        "--observation",
        choices=["reference", "extended"],
        default="reference",
        help=(
            '"extended" adds ten inputs from the same packet, among them our '
            "own G — which the scoring penalises and the reference state omits. "
            "Ends compatibility with the organiser's own policies"
        ),
    )
    parser.add_argument(
        "--opponent-pool",
        nargs="+",
        default=[],
        metavar="PATH",
        help=(
            "saved policies to fight, as files or directories of .zip. One is "
            "drawn per round; once every one has been met 100 times and the "
            "agent is winning overall, the hard ones come up more (PFSP)"
        ),
    )
    parser.add_argument(
        "--opponent-aggression",
        type=float,
        default=1.0,
        help='how hard "pursuit" pulls; a ladder of these is a curriculum',
    )
    parser.add_argument("--rudder", action="store_true", help="unlock the rudder channel")
    parser.add_argument(
        "--rudder-limit",
        type=float,
        default=RUDDER_LIMIT,
        help=(
            "how far the rudder may deflect. 表 2 of the specification allows "
            "-1~+1; the sample client clips itself to 0.2, which is the default here"
        ),
    )
    parser.add_argument(
        "--action-repeat",
        type=int,
        default=1,
        help=(
            "frames one decision is held for. 1 is the reference's 60 Hz and "
            "18,000 decisions a round; 6 is 10 Hz, the rate PHANG-MAN's "
            "high-level policy ran at. A command still goes back every frame"
        ),
    )
    parser.add_argument(
        "--ground-avoidance",
        action="store_true",
        help="a rule-based pull-up under the policy; allowed by 公告說明 一.1.(2)",
    )
    parser.add_argument(
        "--hidden",
        type=int,
        nargs="+",
        default=[256, 256],
        help=(
            "hidden layer widths. The sample uses 256 256; PHANG-MAN used a "
            "single layer of 12288, citing that wide and shallow beats narrow "
            "and deep at equal neuron count"
        ),
    )
    parser.add_argument("--relu", action="store_true", help="ReLU instead of Tanh, as PHANG-MAN used")
    parser.add_argument("--jsbsim-root", default=None)
    parser.add_argument("--reference-setup", action="store_true")
    parser.add_argument(
        "--reference-speed-order",
        action="store_true",
        help="reproduce the package's 106-knot-slow start; the real host does NOT do this",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(train(parse_args()))
    except IncompatibleSession as exc:
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
