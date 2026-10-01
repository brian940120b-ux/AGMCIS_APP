"""The competition entry: a session's policy flown under the session's aircraft.

The checkpoints here are real Stable-Baselines3 models, untrained, saved on a
stand-in environment with the right spaces. What matters is that the loader
meets a genuine `.zip`, not what the network has learned.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("stable_baselines3")

from competition.client import CompetitionClient
from competition.environment import action_space, observation_width
from competition.play import (
    SessionUnplayable,
    client_from_session,
    describe_plant,
    main,
    parse_args,
    plant_from_card,
    selftest,
    synthetic_observation,
    warm_up,
)
from tests.test_competition_client import fly_a_round, level_flight

# ------------------------------------------------------------------ fixtures


def _save_checkpoint(folder: Path, width: int, rudder_enabled: bool) -> None:
    """A real SAC checkpoint with the given observation width, never trained."""
    import gymnasium as gym
    from stable_baselines3 import SAC

    class StandIn(gym.Env):
        def __init__(self) -> None:
            self.observation_space = gym.spaces.Box(-np.inf, np.inf, (width,), dtype=np.float32)
            self.action_space = action_space(rudder_enabled)

        def reset(self, *, seed=None, options=None):  # type: ignore[override]
            super().reset(seed=seed)
            return np.zeros(width, dtype=np.float32), {}

        def step(self, action):  # type: ignore[override]
            return np.zeros(width, dtype=np.float32), 0.0, False, False, {}

    model = SAC("MlpPolicy", StandIn(), policy_kwargs={"net_arch": [8, 8]}, device="cpu")
    model.save(str(folder / "checkpoint.zip"))


def _session(root: Path, name: str, card: dict, width: int | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    environment = card.get("environment", {})
    observation = environment.get("observation", "reference")
    _save_checkpoint(
        folder,
        width if width is not None else observation_width(observation),
        bool(environment.get("rudder_enabled", False)),
    )
    (folder / "card.json").write_text(json.dumps(card), encoding="utf-8")
    return folder


V6_LIKE_CARD = {
    "name": "v6",
    "algorithm": "sac",
    "timesteps_done": 2_000_000,
    "reward": "shaped",
    "environment": {
        "observation": "extended",
        "action_repeat": 6,
        "rudder_enabled": True,
        "rudder_limit": 0.6,
        "high_speed_elevator_limit": 0.4,
        "g_limit": 9.0,
        "ground_avoidance": {},
        "opponent_pool": ["v4", "v5"],
        "league_scheme": "ema",
    },
}

OFFICIAL_CARD = {
    "name": "official_sac_baseline",
    "algorithm": "sac",
    "timesteps_done": 2_000_000,
    "reward": "reference",
    "environment": {"observation": "reference", "action_repeat": 1},
}


@pytest.fixture(scope="module")
def sessions(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("sessions")
    return {
        "v6": _session(root, "v6", V6_LIKE_CARD),
        "official": _session(root, "official_sac_baseline", OFFICIAL_CARD),
        # A checkpoint from one session next to the card of another.
        "mismatched": _session(root, "mismatched", V6_LIKE_CARD, width=observation_width("reference")),
    }


# ------------------------------------------------------------ the aircraft


def test_the_client_flies_the_aircraft_on_the_card(sessions):
    client, card, config = client_from_session(sessions["v6"])

    assert card["name"] == "v6"
    assert client.action_repeat == 6
    assert client.rudder_limit == 0.6
    assert client.high_speed_elevator_limit == 0.4
    assert client.g_limit == 9.0, "the G-limit the policy trained under has to reach the stick"
    assert client.ground_avoidance is not None
    assert type(client.encoder).__name__ == "ExtendedEncoder"
    assert config.observation == "extended"


def test_the_organisers_recipe_gets_the_sample_clients_stick(sessions):
    client, _card, _config = client_from_session(sessions["official"])

    assert client.action_repeat == 1
    assert client.rudder_limit == 0.2
    assert client.g_limit is None
    assert client.ground_avoidance is None
    assert type(client.encoder).__name__ == "StateEncoder"


def test_the_plant_has_no_opponent_of_its_own():
    """The host supplies the opponent; the card's pool is training history."""
    config = plant_from_card(V6_LIKE_CARD)

    assert config.opponent == "reference"
    assert config.opponent_policy is None


def test_the_description_names_what_the_policy_will_meet():
    text = describe_plant(plant_from_card(V6_LIKE_CARD))

    assert "extended (30 wide)" in text
    assert "every 6 frame" in text
    assert "G-limit 9" in text
    assert "ground floor on" in text

    official = describe_plant(plant_from_card(OFFICIAL_CARD))
    assert "elevator limit 0.4 above Mach 0.8" in official
    assert "no ground floor" in official


# --------------------------------------------------------------- refusals


def test_a_checkpoint_from_another_session_is_refused(sessions):
    with pytest.raises(SessionUnplayable, match="different sessions"):
        client_from_session(sessions["mismatched"])


def test_a_session_without_its_card_is_refused_by_name(tmp_path):
    folder = tmp_path / "bare"
    folder.mkdir()
    (folder / "checkpoint.zip").write_bytes(b"")

    with pytest.raises(SessionUnplayable, match=r"card\.json"):
        client_from_session(folder)


def test_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(SessionUnplayable, match="not a directory"):
        client_from_session(tmp_path / "nowhere")


def test_a_refusal_is_an_exit_code_not_a_traceback(tmp_path, capsys):
    code = main([str(tmp_path / "nowhere")])

    assert code == 2
    assert "cannot play" in capsys.readouterr().err


# --------------------------------------------------------------- the round


def test_a_round_is_answered_frame_by_frame_and_decided_every_sixth(sessions):
    client, _card, _config = client_from_session(sessions["v6"])
    warm_up(client, observation_width("extended"))

    replies = fly_a_round(client, start_lat=23.06, hold_frames=90, moving_frames=120)

    assert all(reply is not None for reply in replies), "every observation gets a command"
    assert len(replies) == 210
    # The hold before START and the movement after it are one round: a round
    # begins when the held position starts to move, not when it stops.
    assert client.stats.rounds_seen == 1
    assert client.stats.decisions_made == 210 / 6


def test_warm_up_makes_one_decision_and_reports_its_time(sessions):
    client, _card, _config = client_from_session(sessions["official"])

    seconds = warm_up(client, observation_width("reference"))

    assert seconds > 0.0
    assert client.stats.decisions_made == 0, "a warm-up is not a decision the host asked for"


# ------------------------------------------------------- the G-limit plumbing


def _observation_at(g_load: float, speed_fps: float = 574.0) -> bytes:
    values = np.zeros(26, dtype=np.float64)
    values[0], values[1], values[2] = 23.06, 121.95, 15_000.0
    values[12] = values[13] = speed_fps
    values[14] = g_load
    values[20], values[21], values[22] = 23.07, 121.95, 15_000.0
    return struct.pack("<26d", *values)


def _full_pull():
    def policy(_state: np.ndarray) -> np.ndarray:
        return np.array([0.0, -1.0, 0.0, 0.8])

    return policy


def test_the_g_limit_reaches_the_stick():
    """At 9.3 G with a full pull, a client that knows the limit backs off and
    one that does not passes the pull straight through. Before this was plumbed
    every client was the second kind, whatever the card said."""
    from competition.protocol import decode_command

    limited = CompetitionClient(_full_pull(), g_limit=9.0)
    unlimited = CompetitionClient(_full_pull())
    for _ in range(40):  # the stick slews; let both reach where they are going
        limited_reply = limited.on_observation(_observation_at(-9.3))
        unlimited_reply = unlimited.on_observation(_observation_at(-9.3))

    assert limited_reply is not None and unlimited_reply is not None
    limited_pitch = decode_command(limited_reply).pitch
    unlimited_pitch = decode_command(unlimited_reply).pitch
    assert abs(limited_pitch) < abs(unlimited_pitch)
    assert abs(limited_pitch) < 0.5


def test_without_a_g_limit_the_stick_is_the_sample_clients():
    """Below the limit, and with none set, the two shapings agree exactly."""
    from competition.protocol import decode_command

    with_limit = CompetitionClient(level_flight(), g_limit=9.0)
    without = CompetitionClient(level_flight())
    for _ in range(5):
        a = with_limit.on_observation(_observation_at(1.0))
        b = without.on_observation(_observation_at(1.0))

    assert a is not None and b is not None
    assert decode_command(a) == decode_command(b)


# ----------------------------------------------------------------- selftest


def _free_udp_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_self_test_passes_against_itself(sessions, capsys):
    args = parse_args(
        [
            str(sessions["v6"]),
            "--selftest",
            "--listen-port",
            str(_free_udp_port()),
            "--host-port",
            str(_free_udp_port()),
        ]
    )

    code = selftest(args, frames=30)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "PASS" in out
    assert "30 of 30" in out


def test_the_self_test_fails_when_the_session_cannot_load(tmp_path, capsys):
    args = parse_args([str(tmp_path / "nowhere"), "--selftest"])

    assert selftest(args) == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_synthetic_packets_hold_then_move():
    first = struct.unpack("<26d", synthetic_observation(0))
    held = struct.unpack("<26d", synthetic_observation(30))
    moving = struct.unpack("<26d", synthetic_observation(31))

    assert first[0] == held[0]
    assert moving[0] > held[0]
    assert len(synthetic_observation(0)) == 208


# --------------------------------------------------------------------- args


def test_only_the_endpoints_are_flags():
    """No flag can change the aircraft: the card is the authority on it."""
    args = parse_args(["models/competition/v6"])
    names = set(vars(args))

    assert names == {
        "session",
        "listen_ip",
        "listen_port",
        "host_ip",
        "host_port",
        "device",
        "output",
        "record",
        "selftest",
    }
    assert args.listen_port == 8199 and args.host_port == 8099, "the reference client's local block"
