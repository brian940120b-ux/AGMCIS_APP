"""The competition entry: a trained session, flown against the organiser's host.

    python backend/competition/play.py models/competition/v6 \\
        --listen-ip 192.168.1.3 --listen-port 8199 --host-ip 192.168.1.1 --host-port 8099

Everything it does already existed in pieces: `CompetitionClient` decides,
`serve` moves datagrams, `evaluate.load_policy` loads a checkpoint and
`evaluate.config_from_card` reads what the policy trained under. What did not
exist was a command that joined them, so until this file the only program that
could talk to the host was the probe, which flies level and never attacks.

**The aircraft on the day is the aircraft in the card.** The observation
encoder, the decision rate, the rudder limit, the elevator limit, the ground
floor and the G-limit are all taken from `card.json`, never from flags, because
a policy handed a different stick from the one it learned has never flown the
aircraft it is flying. The only things a flag can set are the two network
endpoints and where to record.

Runs until Ctrl+C: the operator launches the host, presses INIT, waits for both
players, presses START, and a round is five minutes. Nothing here guesses when
that is over.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

# Runnable as a file as well as importable. The path has to be set before the
# package imports below, not inside the __main__ guard underneath them.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from competition.client import CompetitionClient, Endpoint, Observer, serve  # noqa: E402
from competition.environment import EnvConfig, build_encoder, observation_width  # noqa: E402
from competition.evaluate import config_from_card, load_policy  # noqa: E402
from competition.probe import Recorder  # noqa: E402

#: One frame at the host's rate. A decision slower than this is late for the
#: next packet, which the host does not wait for.
FRAME_BUDGET_S = 1.0 / 60.0


class SessionUnplayable(RuntimeError):
    """A session that cannot be flown as it is, with the reason and the remedy."""


def read_card(session_dir: Path) -> dict[str, Any]:
    """The card, or an explanation of what is missing from the directory."""
    card_path = session_dir / "card.json"
    checkpoint = session_dir / "checkpoint.zip"
    if not session_dir.is_dir():
        raise SessionUnplayable(f"{session_dir} is not a directory\n    找不到這個 session 資料夾。")
    missing = [
        name
        for name, path in (("card.json", card_path), ("checkpoint.zip", checkpoint))
        if not path.is_file()
    ]
    if missing:
        present = sorted(p.name for p in session_dir.iterdir())
        raise SessionUnplayable(
            f"{session_dir} is missing {', '.join(missing)} (it has: {', '.join(present) or 'nothing'})\n"
            "    The card says what aircraft the policy trained on; without it the client\n"
            "    would be guessing, and a guess is the one thing it must not fly.\n"
            "    這個資料夾少了 card.json 或 checkpoint.zip。從 Kaggle 下載時三個檔案都要放進來。"
        )
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SessionUnplayable(f"{card_path} is not JSON: {error}") from error
    if not isinstance(card, dict):
        raise SessionUnplayable(f"{card_path} does not hold a card")
    return card


def plant_from_card(card: dict[str, Any]) -> EnvConfig:
    """The aircraft the policy trained on, with no opponent in it.

    `config_from_card` wants an opponent because the evaluator flies one; the
    host supplies the opponent here, so the reference is named and ignored.
    """
    return config_from_card(card, opponent="reference", aggression=1.0)


def describe_plant(config: EnvConfig) -> str:
    parts = [
        f"observation {config.observation} ({observation_width(config.observation)} wide)",
        f"decision every {config.action_repeat} frame(s)",
        f"rudder limit {config.rudder_limit:g}",
        (
            f"G-limit {config.g_limit:g}"
            if config.g_limit is not None
            else f"elevator limit {config.high_speed_elevator_limit:g} above Mach 0.8"
        ),
        "ground floor on" if config.ground_avoidance is not None else "no ground floor",
    ]
    return ", ".join(parts)


def client_from_session(
    session_dir: Path,
    *,
    device: str = "cpu",
    observer: Observer | None = None,
) -> tuple[CompetitionClient, dict[str, Any], EnvConfig]:
    """A client flying `session_dir`'s policy under `session_dir`'s aircraft.

    Refuses, rather than flies, a checkpoint whose network takes a different
    observation width from the one its card names: that is a session copied
    with the wrong card, and thirty frames into a round is the wrong place to
    find out.
    """
    card = read_card(session_dir)
    config = plant_from_card(card)
    policy = load_policy(session_dir, algorithm=str(card.get("algorithm", "sac")), device=device)
    expected = observation_width(config.observation)
    actual = int(getattr(policy, "observation_width", expected))
    if actual != expected:
        raise SessionUnplayable(
            f"{session_dir}: the checkpoint takes {actual} numbers but the card says "
            f"observation '{config.observation}' ({expected}). The card and the checkpoint "
            "are from different sessions.\n"
            "    checkpoint.zip 和 card.json 不是同一個 session 的,重新下載整個資料夾。"
        )
    client = CompetitionClient(
        policy,
        observer,
        action_repeat=config.action_repeat,
        rudder_limit=config.rudder_limit,
        high_speed_elevator_limit=config.high_speed_elevator_limit,
        ground_avoidance=config.ground_avoidance,
        encoder=build_encoder(config),
        g_limit=config.g_limit,
    )
    return client, card, config


def warm_up(client: CompetitionClient, width: int) -> float:
    """One decision before the first packet, so the first frame is not the slow one.

    The first forward pass through a freshly loaded network pays for lazy
    initialisation — measured at over 100 ms on a laptop, six frames of budget
    — and it would otherwise be paid on the first observation of the round.
    Returns how long it took, for the operator to see.
    """
    started = time.perf_counter()
    client.policy(np.zeros(width, dtype=np.float64))
    return time.perf_counter() - started


# ------------------------------------------------------------------- running


def _status_line(client: CompetitionClient, waiting_since: float) -> str:
    stats = client.stats
    if stats.packets_received == 0:
        return (
            f"  waiting for the host… {time.time() - waiting_since:.0f}s "
            "(start it and press INIT, then START)"
        )
    return (
        f"  {stats.packets_received:6d} packets, round {stats.rounds_seen}, "
        f"frame {stats.frames_this_round:5d}, state {client.player_state.value}, "
        f"worst decision {stats.worst_decision_s * 1000:.1f} ms          "
    )


def run(args: argparse.Namespace) -> int:
    session_dir = Path(args.session)
    recorder = Recorder() if args.record else None
    try:
        client, card, config = client_from_session(session_dir, device=args.device, observer=recorder)
    except SessionUnplayable as error:
        print(f"cannot play {session_dir}: {error}", file=sys.stderr)
        return 2

    endpoint = Endpoint(
        listen_ip=args.listen_ip,
        listen_port=args.listen_port,
        host_ip=args.host_ip,
        host_port=args.host_port,
    )
    width = observation_width(config.observation)
    first = warm_up(client, width)

    steps = int(card.get("timesteps_done", 0) or 0)
    print(f"session:   {session_dir}  ({steps:,} steps, reward {card.get('reward', '?')})")
    print(f"aircraft:  {describe_plant(config)}")
    print(f"warm-up:   first decision {first * 1000:.1f} ms (frame budget {FRAME_BUDGET_S * 1000:.1f} ms)")
    print(f"listening: {endpoint.listen_ip}:{endpoint.listen_port}")
    print(f"replying:  {endpoint.host_ip}:{endpoint.host_port}")
    print("Ctrl+C to stop. 開主辦方 HOST,按 INIT,等兩邊都好,按 START。\n")

    listening = threading.Event()
    stop = threading.Event()
    worker = threading.Thread(
        target=serve,
        args=(client, endpoint),
        # Wakes once a second to notice `stop`; silence is never a reason to quit.
        kwargs={"timeout_s": 1.0, "ready": listening, "stop": stop},
        daemon=True,
    )
    worker.start()
    if not listening.wait(timeout=5.0):
        print(f"could not listen on {endpoint.listen_ip}:{endpoint.listen_port}", file=sys.stderr)
        return 1

    waiting_since = time.time()
    try:
        while worker.is_alive():
            time.sleep(1.0)
            print(_status_line(client, waiting_since), end="\r")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        stop.set()
        worker.join(timeout=3.0)

    return write_report(args, session_dir, card, config, client, recorder)


def write_report(
    args: argparse.Namespace,
    session_dir: Path,
    card: dict[str, Any],
    config: EnvConfig,
    client: CompetitionClient,
    recorder: Recorder | None,
) -> int:
    """What happened on the wire, written down whether or not anything arrived."""
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "session": str(session_dir),
        "timesteps_done": card.get("timesteps_done"),
        "aircraft": describe_plant(config),
        "endpoint": {
            "listen": f"{args.listen_ip}:{args.listen_port}",
            "host": f"{args.host_ip}:{args.host_port}",
        },
        "client_stats": client.stats.as_dict(),
        "decisions_made": client.stats.decisions_made,
        "frames_recorded": len(recorder.frames) if recorder else 0,
    }
    (output / f"play-{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if recorder is not None:
        with (output / f"play-{stamp}.jsonl").open("w", encoding="utf-8") as handle:
            for frame in recorder.frames:
                handle.write(json.dumps(frame) + "\n")
    print("\n" + json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nreport: {output / f'play-{stamp}.json'}")
    if recorder is not None:
        print(f"frames: {output / f'play-{stamp}.jsonl'}")
    if client.stats.packets_received == 0:
        print("\nno packets arrived. 沒有收到任何封包:檢查 HOST 的 IP/PORT 設定和防火牆。")
        return 1
    return 0


# ------------------------------------------------------------------ selftest


def synthetic_observation(frame: int) -> bytes:
    """The probe's self-test packet: a hold for 30 frames, then movement."""
    values = np.zeros(26, dtype=np.float64)
    values[0] = 23.060552 + (frame * 1e-5 if frame > 30 else 0.0)
    values[1] = 121.948555
    values[2] = 15_000.0
    values[12] = values[13] = 574.0
    values[20] = values[0] + 0.008
    values[21] = 121.948555
    values[22] = 15_000.0
    return struct.pack("<26d", *values)


def selftest(args: argparse.Namespace, frames: int = 120) -> int:
    """Load the session and answer synthetic packets over the loopback.

    The question a silent session leaves is whether nothing is arriving or
    nothing is answering, and those need opposite fixes. Run this before
    plugging into the host's hub: it proves the session loads, the sockets
    bind, every frame gets a reply and the replies arrive inside the frame.
    """
    session_dir = Path(args.session)
    print(f"self-test: {session_dir} against synthetic packets\n")
    try:
        client, _card, config = client_from_session(session_dir, device=args.device)
    except SessionUnplayable as error:
        print(f"FAIL  {error}")
        return 1
    print(f"  OK  session loaded: {describe_plant(config)}")
    first = warm_up(client, observation_width(config.observation))
    print(f"  OK  warm-up decision {first * 1000:.1f} ms")

    endpoint = Endpoint(
        listen_ip=args.listen_ip,
        listen_port=args.listen_port,
        host_ip=args.host_ip,
        host_port=args.host_port,
    )
    pretend_host = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pretend_host.settimeout(3.0)
    try:
        pretend_host.bind((endpoint.host_ip, endpoint.host_port))
    except OSError as exc:
        print(f"FAIL  could not bind {endpoint.host_ip}:{endpoint.host_port} — {exc}")
        print("      something else is using the host port; close it and try again")
        return 1

    listening = threading.Event()
    stop = threading.Event()
    worker = threading.Thread(
        target=serve,
        args=(client, endpoint),
        kwargs={"timeout_s": 0.2, "ready": listening, "stop": stop},
        daemon=True,
    )
    worker.start()
    if not listening.wait(timeout=5.0):
        print(f"FAIL  could not listen on {endpoint.listen_ip}:{endpoint.listen_port}")
        pretend_host.close()
        return 1
    print(f"  OK  listening on {endpoint.listen_ip}:{endpoint.listen_port}")

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    replies = 0
    try:
        for frame in range(frames):
            sender.sendto(synthetic_observation(frame), (endpoint.listen_ip, endpoint.listen_port))
            try:
                pretend_host.recvfrom(4096)
                replies += 1
            except TimeoutError:
                break
    finally:
        stop.set()
        worker.join(timeout=3.0)
        sender.close()
        pretend_host.close()

    stats = client.stats
    print(f"  OK  {replies} of {frames} synthetic frames were answered")
    print(f"  OK  {stats.decisions_made} decisions, {stats.rounds_seen} round(s) seen")
    worst_ms = stats.worst_decision_s * 1000
    budget_ms = FRAME_BUDGET_S * 1000
    verdict = "OK" if stats.worst_decision_s < FRAME_BUDGET_S else "WARN"
    mean_ms = stats.mean_decision_s * 1000
    print(f"  {verdict}  worst decision {worst_ms:.2f} ms, mean {mean_ms:.2f} ms (budget {budget_ms:.1f} ms)")
    if replies < frames:
        print("\nFAIL  the client did not answer every frame")
        return 1
    if stats.worst_decision_s >= FRAME_BUDGET_S:
        print("\nWARN  one decision took longer than a frame; the next packet would have been late.")
        print("      Close other programs and run again. 關掉其他程式再跑一次。")
    print("\nPASS  the session loads, listens, decides and replies.")
    print("      A silent run against the real host is therefore the host, its ports,")
    print("      or the firewall — not this program or this session.")
    return 0


# ---------------------------------------------------------------------- args


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fly a trained session against the organiser's host.",
        epilog=(
            "Only the endpoints are flags. The observation, decision rate, stick limits, "
            "ground floor and G-limit come from the session's card.json, because the "
            "policy has to meet the aircraft it trained on."
        ),
    )
    parser.add_argument("session", help="a session directory holding card.json and checkpoint.zip")
    # The defaults are the reference client's local block; on the day the
    # organiser assigns these at check-in (公告 四.3) and they are passed in.
    parser.add_argument("--listen-ip", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8199)
    parser.add_argument("--host-ip", default="127.0.0.1")
    parser.add_argument("--host-port", type=int, default=8099)
    parser.add_argument("--device", default="cpu", help="where the network runs; cpu is the measured choice")
    parser.add_argument("--output", default="data/play", help="where the report goes")
    parser.add_argument(
        "--record",
        action="store_true",
        help="also keep every accepted frame, as the probe does, for the post-round comparison",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="load the session and answer synthetic packets on the loopback, without the host",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.selftest:
        return selftest(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
