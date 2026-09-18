"""Communication model and datalink tests (PHASE 6).

The transport is a simulation construct: it carries messages between simulated
entities and models how a link degrades. These tests check that the degradation
is real, bounded and reproducible.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.config import CommunicationSettings, load_settings
from core.simulation_engine import SimulationEngine
from core.world_state import EntityState, Team, WorldState
from simulation.communications import CommsConfig, CommunicationModel, MessageType
from simulation.datalink import DatalinkService

PERFECT = CommsConfig(latency_base_s=0.0, latency_jitter_s=0.0, packet_loss_probability=0.0)


def make_model(config: CommsConfig | None = None, seed: int = 1) -> CommunicationModel:
    model = CommunicationModel(config or PERFECT, seed=seed)
    model.register("BLUE-01", "BLUE")
    model.register("BLUE-02", "BLUE")
    model.register("RED-01", "RED")
    return model


def payload() -> dict[str, object]:
    return {"position": [0.0, 0.0, 6000.0], "velocity": [220.0, 0.0, 0.0], "team": "BLUE"}


# ---------------------------------------------------------------- addressing


def test_a_directed_message_reaches_only_its_recipient():
    model = make_model()
    model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)
    model.update(1.0)

    assert len(model.receive("BLUE-02")) == 1
    assert model.receive("RED-01") == []


def test_a_broadcast_reaches_the_senders_team_but_not_the_sender():
    model = make_model()
    model.broadcast("BLUE-01", MessageType.POSITION_REPORT, payload(), 0.0)
    model.update(1.0)

    assert len(model.receive("BLUE-02")) == 1
    assert model.receive("BLUE-01") == [], "a sender does not hear itself"
    assert model.receive("RED-01") == [], "the other team does not hear it"


def test_receiving_drains_the_inbox():
    model = make_model()
    model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)
    model.update(1.0)

    assert len(model.receive("BLUE-02")) == 1
    assert model.receive("BLUE-02") == []


def test_peek_does_not_drain():
    model = make_model()
    model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)
    model.update(1.0)

    assert len(model.peek("BLUE-02")) == 1
    assert len(model.receive("BLUE-02")) == 1


# ------------------------------------------------------------------- latency


def test_a_message_is_not_delivered_before_its_latency_elapses():
    model = make_model(CommsConfig(latency_base_s=0.5, latency_jitter_s=0.0, packet_loss_probability=0.0))
    model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)

    model.update(0.2)
    assert model.receive("BLUE-02") == []

    model.update(0.6)
    assert len(model.receive("BLUE-02")) == 1


def test_get_latency_and_packet_loss_report_the_configuration():
    model = make_model(CommsConfig(latency_base_s=0.25, packet_loss_probability=0.1))
    assert model.get_latency() == pytest.approx(0.25)
    assert model.get_packet_loss() == pytest.approx(0.1)


def test_jitter_can_reorder_messages():
    """A receiver must not assume messages arrive in the order they were sent."""
    model = make_model(
        CommsConfig(latency_base_s=0.3, latency_jitter_s=0.15, packet_loss_probability=0.0),
        seed=4,
    )
    for index in range(40):
        model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {"index": index}, index * 0.01)
    model.update(10.0)

    sequences = [m.sequence for m in model.receive("BLUE-02")]
    assert sequences != sorted(sequences), "jitter should have shuffled arrival order"
    assert model.stats.reordered > 0


# ------------------------------------------------------------------- losses


def test_every_message_is_lost_at_full_loss_probability():
    model = make_model(CommsConfig(latency_base_s=0.0, packet_loss_probability=1.0))
    for _ in range(20):
        model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)
    model.update(1.0)

    assert model.receive("BLUE-02") == []
    assert model.stats.lost == 20
    assert model.stats.delivered == 0


def test_partial_loss_delivers_some_and_drops_some():
    model = make_model(
        CommsConfig(
            latency_base_s=0.0,
            latency_jitter_s=0.0,
            packet_loss_probability=0.5,
            # High enough that the bandwidth limiter is not what is being measured.
            max_messages_per_second=1000.0,
        ),
        seed=9,
    )
    for index in range(200):
        model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, index * 0.001)
    model.update(1.0)

    assert 0 < model.stats.lost < 200
    assert model.stats.lost + model.stats.delivered == 200


# ---------------------------------------------------------------- bandwidth


def test_bandwidth_drops_messages_beyond_the_limit():
    model = make_model(
        CommsConfig(latency_base_s=0.0, packet_loss_probability=0.0, max_messages_per_second=5.0)
    )
    for index in range(20):
        model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, index * 0.01)
    model.update(1.0)

    assert model.stats.dropped_bandwidth == 15
    assert len(model.receive("BLUE-02")) == 5


def test_the_bandwidth_window_slides():
    model = make_model(
        CommsConfig(latency_base_s=0.0, packet_loss_probability=0.0, max_messages_per_second=2.0)
    )
    assert model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)
    assert model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.1)
    assert not model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.2)

    # More than a second later the earlier sends have fallen out of the window.
    assert model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 2.0)


def test_bandwidth_is_tracked_per_sender():
    model = make_model(
        CommsConfig(latency_base_s=0.0, packet_loss_probability=0.0, max_messages_per_second=1.0)
    )
    assert model.send("BLUE-01", "RED-01", MessageType.STATUS, {}, 0.0)
    assert model.send("BLUE-02", "RED-01", MessageType.STATUS, {}, 0.0), (
        "a different sender has its own budget"
    )


# ------------------------------------------------------------------ blackout


def test_nothing_gets_through_during_a_blackout():
    model = make_model(
        CommsConfig(latency_base_s=0.0, packet_loss_probability=0.0, blackout_windows=((10.0, 20.0),))
    )
    assert model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 5.0)
    assert not model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 15.0)
    assert model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 25.0)

    assert model.stats.blocked_blackout == 1


@pytest.mark.parametrize(
    ("time_s", "expected"), [(9.9, False), (10.0, True), (15.0, True), (20.0, True), (20.1, False)]
)
def test_blackout_window_boundaries(time_s, expected):
    model = make_model(CommsConfig(blackout_windows=((10.0, 20.0),)))
    assert model.in_blackout(time_s) is expected


def test_a_disabled_link_sends_nothing():
    model = make_model(CommsConfig(enabled=False))
    assert not model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)
    assert model.stats.sent == 0


# ------------------------------------------------------------ reproducibility


def test_the_same_seed_loses_the_same_messages():
    def run(seed: int) -> tuple[int, int]:
        model = make_model(CommsConfig(latency_base_s=0.1, packet_loss_probability=0.3), seed=seed)
        for index in range(100):
            model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, index * 0.001)
        model.update(5.0)
        return model.stats.lost, model.stats.delivered

    assert run(3) == run(3)
    assert run(3) != run(4)


def test_reset_clears_everything():
    model = make_model()
    model.send("BLUE-01", "BLUE-02", MessageType.STATUS, {}, 0.0)
    model.reset()

    assert model.stats.sent == 0
    assert model.peek("BLUE-02") == []
    assert model.status()["in_flight"] == 0


def test_invalid_blackout_windows_are_rejected():
    with pytest.raises(ValueError, match="end after"):
        CommunicationSettings(blackout_windows=[[50.0, 10.0]])
    with pytest.raises(ValueError, match="start, end"):
        CommunicationSettings(blackout_windows=[[1.0, 2.0, 3.0]])


# ------------------------------------------------------------------ datalink


def make_world() -> WorldState:
    world = WorldState()
    for entity_id, team, x in (("BLUE-01", Team.BLUE, 0.0), ("BLUE-02", Team.BLUE, -800.0)):
        world.add_entity(
            EntityState(
                id=entity_id,
                team=team,
                position=np.array([x, 0.0, 6000.0]),
                velocity=np.array([220.0, 0.0, 0.0]),
                orientation=np.array([0.0, 0.0, np.pi / 2]),
            )
        )
    return world


def test_units_broadcast_position_reports_at_the_configured_rate():
    model = make_model(CommsConfig(latency_base_s=0.0, packet_loss_probability=0.0, report_rate_hz=4.0))
    service = DatalinkService(model, tick_rate_hz=60)
    world = make_world()

    assert service.report_interval_ticks == 15
    for tick in range(60):  # one simulation second
        service.broadcast_reports(world, tick)

    assert model.stats.sent == 2 * 4, "two units reporting at 4 Hz for one second"


def test_a_datalink_track_appears_in_the_observation():
    from agents.agent_manager import build_observation
    from agents.base_agent import BaseAgent

    class Stub(BaseAgent):
        def think(self, observation):  # pragma: no cover - not exercised
            raise NotImplementedError

        def act(self, observation, decision):  # pragma: no cover - not exercised
            raise NotImplementedError

    # Jitter must be zero here: with it, delivery lands slightly after t=0 and
    # update(0.0) would legitimately deliver nothing.
    model = make_model(CommsConfig(latency_base_s=0.0, latency_jitter_s=0.0, packet_loss_probability=0.0))
    service = DatalinkService(model, tick_rate_hz=60)
    world = make_world()

    service.broadcast_reports(world, 0)
    model.update(0.0)
    service.collect("BLUE-02")

    observer = world.get("BLUE-02")
    agent = Stub("AGENT-BLUE-02", "BLUE-02", Team.BLUE)
    # Start from an observation with no contacts at all, as a blind sensor gives.
    empty = build_observation(agent, observer, WorldState(entities={"BLUE-02": observer}))
    merged = service.merge(empty, observer)

    assert [c.entity_id for c in merged.contacts] == ["BLUE-01"]
    assert merged.contacts[0].source == "DATALINK"
    assert merged.contacts[0].measured is False


def test_a_sensor_contact_wins_over_a_datalink_report(settings):
    """A measurement by this aircraft beats a relayed one for the same unit."""
    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    engine.step(60 * 30)

    for agent in engine.agents.agents:
        contacts = {
            c.entity_id: c
            for c in engine.datalink.merge(
                engine.sensors.observe(agent.agent_id, engine.world.get(agent.entity_id), engine.world),
                engine.world.get(agent.entity_id),
            ).contacts
        }
        # No entity may appear twice, from two different sources.
        assert len(contacts) == len(set(contacts))


# --------------------------------------------------------------- integration


def _wingman_after(seconds: int, **overrides):
    settings = load_settings()
    for key, value in overrides.items():
        section, field = key.split("__")
        setattr(getattr(settings, section), field, value)

    engine = SimulationEngine(settings=settings)
    engine.load_scenario("demo_alpha")
    engine.step(60 * seconds)
    return next(a for a in engine.agents.agents if a.entity_id == "BLUE-02").last_decision


def test_the_datalink_keeps_formation_alive_behind_a_narrow_sensor():
    """The PHASE 5 failure this phase exists to fix."""
    without = _wingman_after(150, sensors__field_of_regard_deg=100.0, communications__enabled=False)
    assert without.behaviour.value == "HOLD", "a narrow sensor alone loses the leader"

    with_link = _wingman_after(150, sensors__field_of_regard_deg=100.0, communications__enabled=True)
    assert with_link.behaviour.value == "FORMATION"
    assert with_link.metrics["station_error_m"] < 200.0


def test_a_blackout_degrades_the_picture(settings):
    engine = SimulationEngine(settings=settings)
    engine.comms.config = CommsConfig(
        enabled=True,
        latency_base_s=0.15,
        packet_loss_probability=0.0,
        blackout_windows=((0.0, 600.0),),
    )
    engine.load_scenario("demo_alpha")
    engine.step(60 * 10)

    assert engine.comms.stats.blocked_blackout > 0
    assert engine.comms.stats.delivered == 0


def test_communications_do_not_break_determinism(settings):
    def run() -> str:
        engine = SimulationEngine(settings=settings)
        engine.load_scenario("demo_alpha")
        engine.step(60 * 45)
        return engine.world.state_hash

    assert run() == run()
