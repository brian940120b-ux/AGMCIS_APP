"""Replay file format (PHASE 9).

A recording is JSON Lines — one JSON object per line — optionally gzipped:

    line 1   {"kind": "header", ...}    run identity and everything needed to
                                        reproduce it: scenario, seed, config
                                        hash, tick rate, record rate
    line 2+  {"kind": "frame", ...}     world state at one sampled tick, plus
                                        the events and decisions since the
                                        previous frame
    last     {"kind": "end", ...}       how the run finished, and the final
                                        state hash

JSON Lines rather than one big JSON document for two reasons: a run that
crashes still leaves a readable file up to the last flush, and a player can
stream frames without holding the whole run in memory.

Keys are spelled out rather than abbreviated. gzip removes almost all the cost
of repeating them, and a recording you can read in a text editor is worth far
more than the bytes saved.
"""

from __future__ import annotations

from typing import Any

from core.world_state import EntityState, WorldState

FORMAT_VERSION = 1

# Restated inside every recording, because a file outlives the conversation it
# came from and may be read without the rest of the project.
SCOPE_NOTICE = (
    "AIFCS research recording. All entities, platforms and parameters are "
    "fictional and abstract. No real-world platform, sensor or weapon data."
)


def entity_frame(entity: EntityState) -> dict[str, Any]:
    """The part of an entity a replay needs to redraw and analyse it.

    Deliberately not ``EntityState.to_dict()``: sensor and communication state
    are per-tick working memory that would dominate the file, and the derived
    fields can be recomputed. Position, attitude and controls cannot be.
    """
    return {
        "id": entity.id,
        "team": entity.team.value,
        "status": entity.status.value,
        "position": [round(v, 4) for v in entity.position.tolist()],
        "velocity": [round(v, 4) for v in entity.velocity.tolist()],
        "orientation": [round(v, 6) for v in entity.orientation.tolist()],
        "angular_velocity": [round(v, 6) for v in entity.angular_velocity.tolist()],
        "controls": {k: round(float(v), 5) for k, v in entity.controls.to_dict().items()},
        "health": round(entity.health, 4),
        "energy": round(entity.energy, 4),
        "fuel": round(entity.fuel, 4),
        "altitude": round(entity.altitude, 3),
        "speed": round(entity.speed, 3),
        "heading_deg": round(entity.heading_deg, 3),
    }


def world_frame(
    world: WorldState,
    *,
    events: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One recorded frame."""
    return {
        "kind": "frame",
        "tick": world.tick,
        "simulation_time": round(world.simulation_time, 6),
        "state_hash": world.state_hash,
        "entities": [entity_frame(e) for e in world.entities.values()],
        "events": events or [],
        "decisions": decisions or [],
    }


def header(
    *,
    run_id: str,
    scenario: str,
    seed: int,
    config_hash: str,
    tick_rate_hz: float,
    record_rate_hz: float,
    app_version: str,
    started_at: float,
    integrator: str = "",
    entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Everything needed to identify — and re-run — this recording."""
    return {
        "kind": "header",
        "format_version": FORMAT_VERSION,
        "run_id": run_id,
        "scenario": scenario,
        "seed": seed,
        "config_hash": config_hash,
        "integrator": integrator,
        "tick_rate_hz": tick_rate_hz,
        "record_rate_hz": record_rate_hz,
        "app_version": app_version,
        "started_at": started_at,
        "entities": entities or [],
        "notice": SCOPE_NOTICE,
    }


def end(
    *,
    frames: int,
    ticks: int,
    simulation_time: float,
    end_reason: str | None,
    final_state_hash: str | None,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "end",
        "frames": frames,
        "ticks": ticks,
        "simulation_time": round(simulation_time, 6),
        "end_reason": end_reason,
        "final_state_hash": final_state_hash,
        "truncated": truncated,
    }
