"""Teams, tasks and commanders (PHASE 14-15).

Read-only. Allocation is the commander's job and happens inside the tick; an
endpoint that issued orders from outside would be a second source of truth for
what a unit has been told to do, and the two would disagree the moment the
commander ran again.

The team picture served here is the one the *team* has — built from what its
units shared over the datalink, with the age of each report attached — not the
world. A commander that could see units it had not heard from would coordinate
perfectly in exactly the conditions where coordination breaks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.runtime import get_engine
from core.simulation_engine import SimulationEngine
from core.world_state import Team

router = APIRouter(tags=["coordination"])


@router.get("/teams")
def teams(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Every team's view of itself, from the datalink rather than from truth."""
    pictures = engine.team_manager.status(engine.world)["teams"]
    return {"count": len(pictures), "teams": pictures}


@router.get("/teams/{team}")
def team_detail(team: str, engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    try:
        selected = Team(team.upper())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"no such team: {team}") from exc

    picture = engine.team_manager.picture(engine.world, selected)
    if not picture.members:
        raise HTTPException(status_code=404, detail=f"no units on team {selected.value}")

    return {
        **picture.to_dict(),
        "tasks": {
            member.entity_id: (
                record.to_dict() if (record := engine.tasks.active_for(member.entity_id)) else None
            )
            for member in picture.members
        },
    }


@router.get("/tasks")
def tasks(
    limit: int = Query(default=50, ge=1, le=500),
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    """What has been allocated, and what state each order is in."""
    recent = engine.tasks.recent(limit=limit)
    return {
        **engine.tasks.status(),
        "recent": [record.to_dict() for record in reversed(recent)],
        "applied": engine.agents.tasks_applied,
        "refused": engine.agents.tasks_refused,
    }


@router.get("/tasks/{entity_id}")
def task_for_entity(entity_id: str, engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    if engine.world.get(entity_id) is None:
        raise HTTPException(status_code=404, detail=f"no such unit: {entity_id}")
    record = engine.tasks.active_for(entity_id)
    return {
        "entity_id": entity_id,
        "active": record.to_dict() if record else None,
        "open": [r.to_dict() for r in engine.tasks.open_for(entity_id)],
    }


@router.get("/commanders")
def commanders(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Commander state, including the fact that none of them writes controls."""
    return {
        "enabled": engine.settings.agents.commander_enabled,
        "count": len(engine.commanders),
        "commanders": [c.status() for c in engine.commanders],
        "notice": (
            "A commander allocates high-level tasks only. It has no entity, is "
            "never registered with the agent manager, and there is no code path "
            "from it to a control surface."
        ),
    }
