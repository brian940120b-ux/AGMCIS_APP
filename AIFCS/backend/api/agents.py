"""Agent and decision endpoints (PHASE 3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.runtime import get_engine
from core.simulation_engine import SimulationEngine

router = APIRouter(tags=["agents"])


@router.get("/agents")
def agents(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Every agent in the current run, with its latest decision."""
    return engine.agents.status()


@router.get("/agents/{agent_id}")
def agent_detail(agent_id: str, engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    agent = next((a for a in engine.agents.agents if a.agent_id == agent_id), None)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")

    return {
        **agent.status(),
        "recent_decisions": [
            d.to_dict() for d in engine.agents.recent_decisions(limit=20, agent_id=agent_id)
        ],
    }


@router.get("/decisions")
def decisions(
    limit: int = Query(default=50, ge=1, le=500),
    agent_id: str | None = Query(default=None),
    engine: SimulationEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Recent agent decisions, oldest first.

    Every record carries the observation summary, the chosen behaviour, the
    confidence and the reason codes behind it, so a decision can be audited
    against the state that produced it.
    """
    recent = engine.agents.recent_decisions(limit=limit, agent_id=agent_id)
    return {"count": len(recent), "decisions": [d.to_dict() for d in recent]}
