#!/usr/bin/env python
"""The ``aifcs`` command line (PHASE 20).

Everything the platform can do without a browser, behind one name. Until now it
was `scripts/dev_backend.sh`, `scripts/dev_frontend.sh`, `backend/train.py` and
a set of curl commands, which works but means the answer to "how do I…" depends
on which of those someone happened to find.

    aifcs doctor                  check the installation and say what is wrong
    aifcs serve                   run the backend
    aifcs run demo_alpha -s 60    fly a scenario headless and score it
    aifcs scenarios               what can be flown
    aifcs train --timesteps 20000 train a policy
    aifcs models                  saved policies and whether they still mean anything
    aifcs evaluate MODEL          measure one
    aifcs bench                   how fast the simulation runs here

``doctor`` is the one to reach for first. A beginner whose dashboard will not
load needs to know *which* part is wrong, and the answer is usually one of half
a dozen things this checks in a couple of seconds.

Everything here is fictional and abstract. The task is flight and navigation;
this platform models no weapon, engagement or targeting capability.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# Allow running this file directly from backend/ without installing anything.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import get_settings, load_settings
from core.logging_config import configure_logging

EXIT_OK = 0
EXIT_FAILED = 1

# ---------------------------------------------------------------- formatting

GREEN = "\033[32m"
AMBER = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def _colour(text: str, colour: str) -> str:
    """Colour only when a terminal is going to render it."""
    return f"{colour}{text}{RESET}" if sys.stdout.isatty() else text


def ok(message: str) -> None:
    print(f"  {_colour('OK  ', GREEN)} {message}")


def warn(message: str) -> None:
    print(f"  {_colour('WARN', AMBER)} {message}")


def fail(message: str) -> None:
    print(f"  {_colour('FAIL', RED)} {message}")


def heading(message: str) -> None:
    print(f"\n{message}")


# ------------------------------------------------------------------- doctor


def command_doctor(args: argparse.Namespace) -> int:
    """Check the installation and say plainly what is wrong with it."""
    problems = 0
    warnings = 0

    heading("Python")
    version = sys.version_info
    if version >= (3, 11):
        ok(f"Python {version.major}.{version.minor}.{version.micro}")
    else:
        fail(f"Python {version.major}.{version.minor} — AIFCS needs 3.11 or newer")
        problems += 1

    heading("Core dependencies")
    for module, purpose in (
        ("fastapi", "the API"),
        ("uvicorn", "the server"),
        ("pydantic", "configuration"),
        ("numpy", "the physics"),
        ("yaml", "config and scenario files"),
    ):
        try:
            __import__(module)
            ok(f"{module} — {purpose}")
        except ImportError:
            fail(f"{module} missing — {purpose}. Run: pip install -r requirements.txt")
            problems += 1

    heading("Optional dependencies")
    try:
        from training.pipeline import rl_available

        if rl_available():
            ok("reinforcement learning — training and evaluation available")
        else:
            warn("reinforcement learning not installed — the training centre will say so")
            warnings += 1
    except ImportError:
        warn("reinforcement learning not installed — the training centre will say so")
        warnings += 1

    from simulation.jsbsim_adapter import jsbsim_available, jsbsim_version

    if jsbsim_available():
        ok(f"JSBSim {jsbsim_version()} — the optional physics backend is available")
    else:
        warn("JSBSim not installed — the built-in physics backend is used")
        warnings += 1

    heading("Configuration")
    try:
        settings = load_settings()
        ok(f"configs load — hash {settings.config_hash}")
        ok(f"physics backend: {settings.physics.backend}")
    except Exception as exc:  # doctor reports, never raises
        fail(f"configuration will not load: {exc}")
        return EXIT_FAILED

    heading("Scenarios")
    from simulation.scenario import load_scenario

    directory = Path(settings.scenarios.directory)
    if not directory.is_absolute():
        directory = settings.project_root / directory
    files = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    if not files:
        fail(f"no scenarios in {directory}")
        problems += 1
    for path in files:
        try:
            scenario = load_scenario(path)
            ok(f"{scenario.name} — {len(scenario.entities)} units")
        except Exception as exc:
            fail(f"{path.name} will not load: {exc}")
            problems += 1

    heading("Storage")
    if settings.storage.enabled:
        try:
            from core.run_manager import RunManager

            manager = RunManager(settings)
            if manager.repository is None:
                warn("run storage is enabled but no database was opened")
                warnings += 1
            else:
                ok(f"database at {settings.storage.database_path}")
        except Exception as exc:
            fail(f"the database will not open: {exc}")
            problems += 1
    else:
        warn("run storage is disabled in configs/analysis.yaml — runs will not be recorded")
        warnings += 1

    heading("Ports and neighbours")
    # This repository holds two systems. They share nothing but a machine, and
    # the only thing they can collide over is a port — so that is what is
    # checked, by name, rather than left to be discovered as a bind failure.
    import socket

    def _free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            return probe.connect_ex(("127.0.0.1", port)) != 0

    def _is_aifcs(port: int) -> bool:
        """Whether the thing on this port is AIFCS itself, already running."""
        try:
            import urllib.request

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as reply:
                return json.loads(reply.read(2000)).get("app") == "AIFCS"
        except Exception:
            return False

    for port, who in ((8080, "AIFCS backend"), (5173, "AIFCS dashboard")):
        if _free(port):
            ok(f"port {port} free — {who}")
        elif port == 8080 and _is_aifcs(port):
            # Not a problem, and saying "cannot start" here would be wrong.
            ok(f"port {port} in use by the {who}, which is already running")
        else:
            warn(f"port {port} is in use by something else — {who} cannot start until it is free")
            warnings += 1

    trading = settings.project_root.parent
    if (trading / "main.py").is_file() and (trading / "database_service.py").is_file():
        if _free(8000):
            ok("the AGMCIS trading system shares this repository; its port 8000 is free")
        else:
            ok("the AGMCIS trading system is running on port 8000 — AIFCS does not use it")

    heading("Backend")
    # Everything above can pass while the server still refuses to start: the
    # checks above import pieces, and uvicorn imports the whole application. A
    # missing optional dependency reached through an eager import broke exactly
    # this way, and doctor said "Ready" right before the traceback.
    probe = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'backend'); import main"],
        cwd=settings.project_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode == 0:
        ok("the API application imports — the server can start")
    else:
        last = [line for line in probe.stderr.strip().splitlines() if line.strip()]
        fail("the API application will not import — the server cannot start")
        for line in last[-3:]:
            print(f"       {line}")
        problems += 1

    heading("Frontend")
    node_modules = settings.project_root / "frontend" / "node_modules"
    if node_modules.is_dir():
        ok("frontend dependencies installed")
    else:
        warn("frontend dependencies not installed — run: cd frontend && npm install")
        warnings += 1

    print()
    if problems:
        print(_colour(f"{problems} problem(s) to fix, {warnings} warning(s).", RED))
        return EXIT_FAILED
    if warnings:
        print(_colour(f"Ready, with {warnings} optional thing(s) not installed.", AMBER))
        return EXIT_OK
    print(_colour("Everything checks out.", GREEN))
    return EXIT_OK


# -------------------------------------------------------------------- serve


def command_serve(args: argparse.Namespace) -> int:
    """Run the backend."""
    import uvicorn

    print(f"AIFCS backend on http://{args.host}:{args.port}  (docs at /docs)")
    print("Press Ctrl+C to stop.")
    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(Path(__file__).resolve().parent),
    )
    return EXIT_OK


# ---------------------------------------------------------------------- run


def command_run(args: argparse.Namespace) -> int:
    """Fly a scenario headless, as fast as the machine allows, and score it."""
    from core.simulation_engine import SimulationEngine

    settings = get_settings()
    engine = SimulationEngine(settings)
    scenario = engine.load_scenario(args.scenario, seed=args.seed)
    ticks = int(args.seconds * settings.simulation.tick_rate_hz)

    print(f"{scenario.name}: {len(scenario.entities)} units, seed {engine.seed}, {args.seconds:g}s")
    print(f"physics: {engine.integrator.name}")

    started = time.perf_counter()
    engine.step(ticks)
    elapsed = time.perf_counter() - started

    print(f"\nran {engine.clock.tick_count} ticks in {elapsed:.1f}s ({ticks / elapsed:.0f} ticks/s)")
    print(f"state hash: {engine.world.state_hash}")
    print(f"\n{'unit':<10} {'status':<12} {'altitude':>10} {'speed':>8} {'heading':>8}")
    for entity in engine.world.entities.values():
        print(
            f"{entity.id:<10} {entity.status.value:<12} "
            f"{float(entity.position[2]):>9.1f}m {entity.speed:>7.1f} {entity.heading_deg:>7.1f}"
        )
    return EXIT_OK


def command_scenarios(args: argparse.Namespace) -> int:
    """List what can be flown, and say why anything unloadable will not."""
    from simulation.scenario import load_scenario

    settings = get_settings()
    directory = Path(settings.scenarios.directory)
    if not directory.is_absolute():
        directory = settings.project_root / directory

    for path in sorted(directory.glob("*.yaml")):
        try:
            scenario = load_scenario(path)
            teams = sorted({e.team.value for e in scenario.entities})
            print(
                f"{scenario.name:<22} {len(scenario.entities):>2} units  "
                f"{'/'.join(teams):<10} {scenario.duration_s:g}s"
            )
        except Exception as exc:
            print(f"{path.stem:<22} {_colour('will not load', RED)}: {exc}")
    return EXIT_OK


# ------------------------------------------------------------------ training


def command_train(args: argparse.Namespace) -> int:
    from training.pipeline import TrainingPipeline, TrainingUnavailableError, rl_available

    if not rl_available():
        print("The reinforcement-learning stack is not installed.")
        print("  pip install -r requirements-ml.txt")
        return EXIT_FAILED

    try:
        pipeline = TrainingPipeline(get_settings())
        result = pipeline.train(
            args.algorithm,
            total_timesteps=args.timesteps,
            seed=args.seed,
            evaluate_episodes=args.evaluate,
            progress=True,
        )
    except TrainingUnavailableError as exc:
        print(str(exc))
        return EXIT_FAILED

    print(f"\ntrained {result.total_timesteps} steps in {result.elapsed_s:.1f}s")
    print(f"model: {result.model_path}")
    if result.evaluation:
        print(f"evaluation: {json.dumps(result.evaluation, indent=2)}")
    return EXIT_OK


def command_models(args: argparse.Namespace) -> int:
    """Saved policies, and whether each still means anything."""
    from training.observation_encoder import LAYOUT_VERSION
    from training.registry import ModelRegistry

    registry = ModelRegistry(get_settings())
    models = registry.list_models(include_archived=args.archived)
    if not models:
        print("No saved policies. Train one with: aifcs train")
        return EXIT_OK

    print(f"observation layout v{LAYOUT_VERSION}\n")
    for model in models:
        verdict = model["compatibility"]["verdict"]
        colour = {"COMPATIBLE": GREEN, "DIFFERENT_REWARD": AMBER}.get(verdict, RED)
        score = model.get("evaluation") or {}
        print(
            f"{model['model_id']:<30} {_colour(verdict, colour):<24} "
            f"{(model['total_timesteps'] or 0):>8} steps  "
            f"{('mean ' + str(score['mean_reward'])) if score else 'not evaluated'}"
            f"{'  [archived]' if model['archived'] else ''}"
        )
    return EXIT_OK


def command_evaluate(args: argparse.Namespace) -> int:
    from training.pipeline import TrainingPipeline, rl_available
    from training.registry import ModelIncompatibleError, ModelNotFoundError, ModelRegistry

    if not rl_available():
        print("The reinforcement-learning stack is not installed.")
        print("  pip install -r requirements-ml.txt")
        return EXIT_FAILED

    registry = ModelRegistry(get_settings())
    try:
        entry = registry.require_runnable(args.model)
    except ModelNotFoundError as exc:
        print(f"{exc}. List them with: aifcs models")
        return EXIT_FAILED
    except ModelIncompatibleError as exc:
        print(f"Refused: {exc}")
        return EXIT_FAILED

    print(f"evaluating {args.model} over {args.episodes} episode(s)…")
    pipeline = TrainingPipeline(get_settings())
    evaluation = pipeline.evaluate_saved(entry["path"], episodes=args.episodes, seed=args.seed)
    registry.save_evaluation(args.model, evaluation)
    print(json.dumps(evaluation, indent=2))
    return EXIT_OK


# -------------------------------------------------------------------- bench


def command_bench(args: argparse.Namespace) -> int:
    """How fast the simulation runs on this machine.

    Reported per scenario, because the cost is dominated by the number of
    agents and a single figure would hide that.
    """
    from core.simulation_engine import SimulationEngine

    settings = get_settings()
    print(f"physics: {settings.physics.backend}   {args.ticks} ticks per scenario, after a warm-up\n")
    print(f"{'scenario':<22} {'units':>5} {'us/tick':>9} {'ticks/s':>9} {'x real time':>12}")

    for name in args.scenarios:
        engine = SimulationEngine(settings)
        scenario = engine.load_scenario(name)
        engine.step(min(200, args.ticks))
        started = time.perf_counter()
        engine.step(args.ticks)
        elapsed = time.perf_counter() - started
        rate = args.ticks / elapsed
        print(
            f"{name:<22} {len(scenario.entities):>5} {elapsed / args.ticks * 1e6:>9.1f} "
            f"{rate:>9.0f} {rate / settings.simulation.tick_rate_hz:>11.1f}x"
        )
    return EXIT_OK


# --------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aifcs",
        description=(
            "AIFCS — fictional multi-agent flight simulation for AI research. "
            "Everything is abstract: no real aircraft, weapon or targeting data."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check the installation and say what is wrong")
    doctor.set_defaults(handler=command_doctor)

    serve = sub.add_parser("serve", help="run the backend")
    serve.add_argument("--host", default="127.0.0.1")
    # 8080, not 8000: the trading system in this repository holds 8000.
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--reload", action="store_true", help="restart on source changes")
    serve.set_defaults(handler=command_serve)

    run = sub.add_parser("run", help="fly a scenario headless and report the result")
    run.add_argument("scenario", nargs="?", default=None, help="default: the configured scenario")
    run.add_argument("-s", "--seconds", type=float, default=30.0)
    run.add_argument("--seed", type=int, default=None)
    run.set_defaults(handler=command_run)

    scenarios = sub.add_parser("scenarios", help="list what can be flown")
    scenarios.set_defaults(handler=command_scenarios)

    train = sub.add_parser("train", help="train a flight policy")
    train.add_argument("--algorithm", choices=("ppo", "sac"), default="ppo")
    train.add_argument("--timesteps", type=int, default=None)
    train.add_argument("--seed", type=int, default=None)
    train.add_argument("--evaluate", type=int, default=0, help="episodes to evaluate after")
    train.set_defaults(handler=command_train)

    models = sub.add_parser("models", help="saved policies and their compatibility")
    models.add_argument("--archived", action="store_true", help="include archived policies")
    models.set_defaults(handler=command_models)

    evaluate = sub.add_parser("evaluate", help="measure a saved policy")
    evaluate.add_argument("model", help="a model id from: aifcs models")
    evaluate.add_argument("-e", "--episodes", type=int, default=5)
    evaluate.add_argument("--seed", type=int, default=None)
    evaluate.set_defaults(handler=command_evaluate)

    bench = sub.add_parser("bench", help="measure how fast the simulation runs here")
    bench.add_argument("--ticks", type=int, default=2000)
    bench.add_argument(
        "--scenarios",
        nargs="+",
        default=["demo_alpha", "team_eight"],
        help="scenarios to measure",
    )
    bench.set_defaults(handler=command_bench)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # The CLI's own output is the report; the structured log would drown it.
    configure_logging(level="WARNING", json_format=False, directory=None)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
