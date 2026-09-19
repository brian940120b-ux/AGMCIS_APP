"""Replay recording and playback tests (PHASE 9)."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import pytest

from core.simulation_engine import SimulationEngine
from replay import format as fmt
from replay.player import ReplayPlayer
from replay.reader import ReplayError, list_recordings, load_recording
from replay.recorder import ReplayRecorder, ReplayWriter, new_run_id


def _record(tmp_path, ticks=300, **kwargs):
    """Run the reference scenario with a recorder attached."""
    engine = SimulationEngine()
    engine.load_scenario("demo_alpha")
    recorder = ReplayRecorder(engine, directory=tmp_path, **kwargs)
    engine.tick_observers.append(recorder.capture)
    recorder.start()
    engine.step(ticks)
    summary = recorder.stop("test complete")
    return engine, summary


# --------------------------------------------------------------- determinism


def test_recording_does_not_change_the_simulation(tmp_path):
    """The recorder is an observer. A recorded run and an unrecorded one must
    produce the same state, or every recording would be of a different world
    than the one that ran."""
    plain = SimulationEngine()
    plain.load_scenario("demo_alpha")
    plain.step(600)

    recorded, _ = _record(tmp_path, ticks=600)

    assert recorded.world.state_hash == plain.world.state_hash
    assert recorded.world.tick == plain.world.tick


def test_recorded_state_hashes_match_the_live_ones(tmp_path):
    """Each frame's hash must be the hash the engine had at that tick."""
    engine = SimulationEngine()
    engine.load_scenario("demo_alpha")
    recorder = ReplayRecorder(engine, directory=tmp_path, record_rate_hz=20.0)
    engine.tick_observers.append(recorder.capture)
    recorder.start()

    live: dict[int, str] = {}
    for _ in range(300):
        engine.step(1)
        live[engine.world.tick] = engine.world.state_hash
    summary = recorder.stop("done")

    recording = load_recording(summary["path"])
    checked = 0
    for frame in recording.frames:
        if frame["tick"] in live:
            assert frame["state_hash"] == live[frame["tick"]]
            checked += 1
    assert checked > 5


# -------------------------------------------------------------------- writer


def test_writer_round_trip(tmp_path):
    path = tmp_path / "r.jsonl.gz"
    writer = ReplayWriter(path, compress=True)
    writer.open(
        fmt.header(
            run_id="R",
            scenario="s",
            seed=1,
            config_hash="c",
            tick_rate_hz=60.0,
            record_rate_hz=20.0,
            app_version="0.1.0",
            started_at=0.0,
        )
    )
    writer.write_frame({"kind": "frame", "tick": 1, "simulation_time": 0.0, "entities": []})
    writer.close(fmt.end(frames=1, ticks=1, simulation_time=0.0, end_reason="x", final_state_hash="h"))

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        kinds = [json.loads(line)["kind"] for line in handle if line.strip()]
    assert kinds == ["header", "frame", "end"]


def test_writer_stops_at_the_size_cap(tmp_path):
    """An unattended run must not be able to fill the disk."""
    writer = ReplayWriter(tmp_path / "r.jsonl", compress=False, max_bytes=200)
    writer.open({"kind": "header", "run_id": "R"})
    written = sum(1 for _ in range(100) if writer.write_frame({"kind": "frame", "padding": "x" * 50}))
    writer.close(
        fmt.end(
            frames=written,
            ticks=0,
            simulation_time=0.0,
            end_reason=None,
            final_state_hash=None,
        )
    )

    assert writer.truncated is True
    assert written < 100
    assert load_recording(tmp_path / "r.jsonl").truncated is True


# -------------------------------------------------------------------- reader


def test_gzip_is_detected_by_content_not_by_name(tmp_path):
    """A gzipped recording renamed to .jsonl should still open."""
    _, summary = _record(tmp_path, ticks=60, compress=True)
    renamed = tmp_path / "renamed.jsonl"
    renamed.write_bytes(Path(summary["path"]).read_bytes())

    recording = load_recording(renamed)
    assert recording.frame_count > 0


def test_a_recording_without_a_header_is_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"kind": "frame", "tick": 1}\n', encoding="utf-8")
    with pytest.raises(ReplayError, match="no header"):
        load_recording(path)


def test_a_run_killed_mid_write_still_loads(tmp_path):
    """A crashed run leaves a partial final line; what was written is still usable."""
    _, summary = _record(tmp_path, ticks=120, compress=False)
    path = Path(summary["path"])
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: int(len(text) * 0.6)] + '{"kind": "fra', encoding="utf-8")

    recording = load_recording(path)
    assert recording.frame_count > 0
    assert recording.complete is False


def test_listing_reports_an_unreadable_file_rather_than_hiding_it(tmp_path):
    _record(tmp_path, ticks=60)
    (tmp_path / "junk.jsonl").write_text("not json at all\n", encoding="utf-8")

    listed = list_recordings(tmp_path)
    assert len(listed) == 2
    assert sum(1 for entry in listed if not entry["readable"]) == 1


def test_seeking_by_tick_and_time(tmp_path):
    _, summary = _record(tmp_path, ticks=600)
    recording = load_recording(summary["path"])

    index = recording.index_for_tick(300)
    assert recording.frames[index]["tick"] >= 300
    assert recording.frames[index - 1]["tick"] < 300
    assert recording.index_for_time(5.0) == recording.index_for_tick(300)
    # Out of range clamps rather than raising.
    assert recording.index_for_tick(10**9) == recording.frame_count - 1


# -------------------------------------------------------------------- player


def test_player_transport(tmp_path):
    _, summary = _record(tmp_path, ticks=600)
    player = ReplayPlayer()
    player.load(summary["path"])

    assert player.seek_tick(300) > 0
    assert player.current_frame()["tick"] >= 300
    before = player.index
    assert player.step(-5) == before - 5
    assert player.seek_frame(-10) == 0
    assert player.seek_frame(10**9) == player.recording.frame_count - 1

    with pytest.raises(ReplayError):
        player.set_speed(3.7)
    assert player.set_speed(2.0) == 2.0


def test_player_refuses_to_act_without_a_recording():
    player = ReplayPlayer()
    assert player.status() == {"loaded": False, "playing": False}
    with pytest.raises(ReplayError):
        player.step(1)


def test_playback_advances_at_the_recorded_rate(tmp_path):
    """Playing at 5x for a second should cover five seconds of recording."""
    _, summary = _record(tmp_path, ticks=1200)

    async def run() -> tuple[int, float]:
        player = ReplayPlayer()
        recording = player.load(summary["path"])
        player.set_speed(5.0)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await player.play()
        await asyncio.sleep(1.0)
        elapsed = loop.time() - started
        index = player.index
        await player.stop()
        return index, recording.record_rate_hz * 5.0 * elapsed

    advanced, expected = asyncio.run(run())
    # Generous bounds: this asserts the pacing is right, not the scheduler.
    assert 0.5 * expected < advanced < 1.5 * expected


def _synthetic_recording(path, event_frames=(10, 25)):
    """A small recording with events at known frames.

    Built by hand rather than by running the engine: which frames carry an
    event is the thing under test, and a real run only produces them where the
    scenario happens to.
    """
    writer = ReplayWriter(path, compress=False)
    writer.open(
        fmt.header(
            run_id="SYNTH",
            scenario="synthetic",
            seed=1,
            config_hash="c",
            tick_rate_hz=60.0,
            record_rate_hz=20.0,
            app_version="0.1.0",
            started_at=0.0,
        )
    )
    for index in range(40):
        events = (
            [{"type": "ENTITY_OUT_OF_BOUNDS", "entity_id": "BLUE-01", "message": "edge"}]
            if index in event_frames
            else []
        )
        writer.write_frame(
            {
                "kind": "frame",
                "tick": index * 3,
                "simulation_time": index * 0.05,
                "state_hash": f"h{index}",
                "entities": [],
                "events": events,
                "decisions": [],
            }
        )
    writer.close(
        fmt.end(frames=40, ticks=117, simulation_time=1.95, end_reason="done", final_state_hash="h39")
    )
    return path


def test_jump_to_event_moves_between_markers(tmp_path):
    player = ReplayPlayer()
    recording = player.load(_synthetic_recording(tmp_path / "s.jsonl"))
    assert [e["frame"] for e in recording.event_index()] == [10, 25]

    player.seek_frame(0)
    assert player.jump_to_event(1)["frame"] == 10
    assert player.index == 10
    assert player.jump_to_event(1)["frame"] == 25
    # Nothing further forward; the cursor must not move.
    assert player.jump_to_event(1) is None
    assert player.index == 25
    assert player.jump_to_event(-1)["frame"] == 10


def test_jump_to_event_does_nothing_when_there_are_none(tmp_path):
    player = ReplayPlayer()
    player.load(_synthetic_recording(tmp_path / "quiet.jsonl", event_frames=()))
    player.seek_frame(5)
    assert player.jump_to_event(1) is None
    assert player.index == 5


def test_run_ids_are_unique():
    assert len({new_run_id() for _ in range(50)}) > 1
