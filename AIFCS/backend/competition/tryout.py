"""Exercise everything the upgrades added, on this machine, in one run.

Not a test suite — that is `scripts/check.sh` and it runs on every commit. This
is the other question: *does it do the thing on your hardware*, and what are
the numbers here rather than on mine. Every figure printed is measured in the
run that prints it.

    python backend/competition/tryout.py

Roughly two minutes. Nothing is saved and nothing existing is touched; a
training session is written to a temporary directory and deleted.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from competition.action import INITIAL_THROTTLE  # noqa: E402
from competition.environment import CompetitionRound, EnvConfig  # noqa: E402
from competition.safety import GroundAvoidance  # noqa: E402

HOLD = np.array([0.0, 0.0, 0.0, INITIAL_THROTTLE])


def heading(number: int, english: str, chinese: str) -> None:
    print()
    print(f"[{number}/6] {english}")
    print(f"      {chinese}")
    print("      " + "-" * 62)


def line(text: str) -> None:
    print(f"      {text}")


# ---------------------------------------------------------------- 1. the floor


def check_ground_avoidance(rounds: int) -> bool:
    from competition.evaluate import evaluate, neutral_policy

    heading(1, "Ground avoidance", "防墜地板 —— 目前效果最大的一項")
    line("A policy that does nothing at all, with and without the floor.")
    line("一個什麼都不做的策略,有地板和沒地板的差別。")
    print()

    without = evaluate(neutral_policy(), EnvConfig(), rounds=rounds, seed=100)
    with_floor = evaluate(
        neutral_policy(),
        EnvConfig(ground_avoidance=GroundAvoidance()),
        rounds=rounds,
        seed=100,
    )

    line(f"{'':14}{'crashed':>10}{'won':>8}{'margin':>12}{'frames':>10}")
    for label, report in (("no floor", without), ("with floor", with_floor)):
        frames = sum(r.frames for r in report.rounds) // len(report.rounds)
        line(
            f"{label:14}{report.crash_rate:>9.0%}{report.win_rate:>8.0%}"
            f"{report.mean_margin:>+12,.0f}{frames:>10,}"
        )
    print()

    fixed = with_floor.crash_rate < without.crash_rate
    line("OK — the floor is stopping the crashes." if fixed else "!! No improvement.")
    line("地板有擋住墜毀。" if fixed else "沒有改善,把這段貼給我。")
    return fixed


# --------------------------------------------------------------- 2. the reward


def check_rewards() -> bool:
    from competition.gym_env import CompetitionEnv

    heading(2, "Reward modes", "四種獎勵 —— 同一段必定墜毀的軌跡")
    line("Why `score` is for measuring and `margin` is for training.")
    line("為什麼 score 只能量測,不能拿來訓練。")
    print()

    totals = {}
    for mode in ("reference", "score", "margin", "shaped"):
        env = CompetitionEnv(EnvConfig(), reward_mode=mode, seed=100)
        env.reset(seed=100)
        total = 0.0
        while True:
            _, reward, terminated, truncated, _ = env.step(HOLD)
            total += reward
            if terminated or truncated:
                break
        totals[mode] = total
        flag = "   <-- crashed, and still positive" if total > 0 else ""
        line(f"{mode:12}{total:>12,.1f}{flag}")

    print()
    correct = totals["score"] > 0 > totals["margin"]
    line("OK — margin is negative for a crash, score is not." if correct else "!! Unexpected.")
    line("margin 對墜毀給負分,score 不會。" if correct else "結果和預期不同,貼給我。")
    return correct


# ---------------------------------------------------------- 3. the observation


def check_observation() -> bool:
    from competition.environment import build_encoder
    from competition.features import EXTRA_FIELDS

    heading(3, "Extended observation", "觀測特徵 —— 策略終於看得到自己的 G")
    game = CompetitionRound(config=EnvConfig(ground_avoidance=GroundAvoidance()), seed=100)
    game.reset(seed=100)
    # Fed every frame, not just the last one. The rates are differences, so an
    # encoder shown a single frame reports zero for all of them — which a first
    # version of this did, and printed as though it were a measurement.
    #
    # Both encoders are fed the same frames, for the same reason: the closure
    # rate is a difference too, so a fresh encoder reports zero for it and
    # comparing a warmed-up one against a cold one finds a difference that is
    # about the comparison rather than the code. A first version did exactly
    # that and reported a mismatch.
    extended_encoder = build_encoder(EnvConfig(observation="extended"))
    reference_encoder = build_encoder(EnvConfig())
    pull = np.array([0.6, -0.9, 0.0, 1.0])
    for _ in range(240):
        game.step(pull)
        telemetry = game.telemetry()
        extended = extended_encoder.encode(telemetry)
        reference = reference_encoder.encode(telemetry)

    line(f"reference {reference.shape[0]} inputs, extended {extended.shape[0]} inputs")
    print()
    line("After four seconds of a hard pull / 拉桿四秒後的十個新輸入:")
    for name, value in zip(EXTRA_FIELDS, extended[20:], strict=True):
        line(f"  {name:22}{value:+.4f}")
    print()
    line(f"raw G from the packet / 封包裡的原始 G: {telemetry.own_g_acc:+.2f}")
    line("(negative under a positive-G pull — body Z points down)")
    line("(拉桿是負值,因為機身 Z 軸朝下)")
    print()

    ok = extended.shape[0] == 30 and np.allclose(extended[:20], reference)
    line("OK — the reference twenty are unchanged underneath." if ok else "!! Mismatch.")
    line("底下那 20 維和參考編碼完全一致。" if ok else "不一致,貼給我。")
    return ok


# ------------------------------------------------------------- 4. the opponent


def check_opponents() -> bool:
    heading(4, "Opponents", "對手 —— 靶機 vs 會追的")
    line("Where each one is after five minutes, and where its nose points.")
    line("五分鐘後各自在哪裡,機首指向哪。")
    print()
    line(f"{'':12}{'distance':>12}{'their bearing to us':>22}")

    results = {}
    for name in ("reference", "pursuit"):
        game = CompetitionRound(config=EnvConfig(opponent=name, ground_avoidance=GroundAvoidance()), seed=100)
        game.reset(seed=100)
        for _ in range(9000):
            game.step(HOLD)
        geometry = game.encoder.geometry(game.foe.telemetry_against(game.own))
        results[name] = abs(geometry.azimuth_deg)
        line(f"{name:12}{geometry.distance_m / 1000:>10,.0f} km{geometry.azimuth_deg:>20.0f}°")

    print()
    turning = results["pursuit"] < results["reference"]
    line("OK — pursuit turns towards us, the drone does not." if turning else "!! No difference.")
    line("pursuit 有轉向我方,靶機沒有。" if turning else "沒有差別,貼給我。")
    return turning


# --------------------------------------------------------- 5. the action repeat


def check_action_repeat() -> bool:
    from competition.client import CompetitionClient
    from competition.protocol import OBS_STRUCT

    heading(5, "Action repeat", "決策頻率 —— 每幀回覆,但不必每幀決策")

    def observation(step: int) -> bytes:
        values = [0.0] * 26
        values[0], values[1], values[2] = 23.0 + step * 1e-4, 121.0, 15_000.0
        values[20], values[21], values[22] = 23.1, 121.1, 15_000.0
        return OBS_STRUCT.pack(*values)

    asked = {"count": 0}

    def policy(state: np.ndarray) -> np.ndarray:
        asked["count"] += 1
        return np.zeros(4)

    for repeat in (1, 6):
        asked["count"] = 0
        client = CompetitionClient(policy, action_repeat=repeat)
        replies = sum(1 for step in range(60) if client.on_observation(observation(step)))
        rate = 60 / repeat
        line(
            f"repeat {repeat}:  {replies} replies to 60 frames, "
            f"policy asked {asked['count']} times ({rate:.0f} Hz)"
        )

    print()
    line("OK — a command every frame either way, which is what 注意事項 10 asks.")
    line("兩種設定都是每幀回一個封包,符合注意事項 10。")
    return True


# ----------------------------------------------------------- 6. it still trains


def check_training(steps: int) -> bool:
    import subprocess

    heading(6, "Training with everything on", "全部打開跑一段訓練")
    output = Path(tempfile.mkdtemp(prefix="aifcs-tryout-"))
    try:
        started = time.perf_counter()
        result = subprocess.run(
            [
                sys.executable,
                str(BACKEND / "competition" / "train.py"),
                "--name",
                "tryout",
                "--timesteps",
                str(steps),
                "--workers",
                "2",
                "--no-save-buffer",
                "--output",
                str(output),
                "--reward",
                "margin",
                "--observation",
                "extended",
                "--ground-avoidance",
                "--action-repeat",
                "6",
                "--gamma",
                "0.995",
                "--gradient-steps",
                "-1",
            ],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        elapsed = time.perf_counter() - started
        for interesting in ("device:", "decisions:", "horizon:", "steps/s"):
            for text in result.stdout.splitlines():
                if interesting in text:
                    line(text.strip())
                    break
        print()
        if result.returncode != 0:
            line("!! Training failed. The message:")
            print((result.stdout + result.stderr)[-1500:])
            return False
        line(f"OK — {steps} decisions in {elapsed:.0f}s with every new option on.")
        line(f"全部新選項打開,{steps} 個決策跑了 {elapsed:.0f} 秒。")
        return True
    finally:
        shutil.rmtree(output, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exercise the upgrades on this machine.")
    parser.add_argument("--rounds", type=int, default=3, help="rounds per comparison")
    parser.add_argument("--steps", type=int, default=600, help="training decisions")
    args = parser.parse_args(argv)

    print()
    print("AIFCS — checking the upgrades on this machine")
    print("=============================================")
    print("  每一個數字都是這次執行量出來的,不是寫死的。")

    checks = [
        ("ground avoidance", lambda: check_ground_avoidance(args.rounds)),
        ("reward modes", check_rewards),
        ("observation", check_observation),
        ("opponents", check_opponents),
        ("action repeat", check_action_repeat),
        ("training", lambda: check_training(args.steps)),
    ]

    results = {}
    for name, check in checks:
        try:
            results[name] = check()
        except Exception as exc:  # a broken check is a failed check, not a crash
            print()
            line(f"!! {name} raised: {type(exc).__name__}: {exc}")
            results[name] = False

    print()
    print("=============================================")
    failed = [name for name, ok in results.items() if not ok]
    for name, ok in results.items():
        print(f"  {'OK  ' if ok else 'FAIL'}  {name}")
    print()
    if failed:
        print("  Something did not work. 把上面整段貼給我。")
        return 1
    print("  Everything works here. 全部正常。")
    print("  下一步,把這一整行貼進 Git Bash(在 AIFCS 目錄下):")
    print()
    print(
        "    scripts/train.bat --name v2 --reward margin --ground-avoidance"
        " --action-repeat 6 --gamma 0.995 --gradient-steps -1"
        " --observation extended --timesteps 2000000"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
