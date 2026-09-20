"""Analytics endpoints (PHASE 17).

Read-only, and downstream of everything. These endpoints read rows a finished
run wrote; there is no path from here to a tick, which is what makes it safe to
add a chart without any risk of changing how an aircraft flies.

The aggregation happens server-side. A run holds thousands of telemetry samples
and up to five thousand decisions, and asking a dashboard to group them would
make every chart a download and let two clients disagree about the same run.

Runs with nothing stored are reported as unavailable with the reason, never as
empty series. An empty chart is a claim that the run was flat; no chart at all
is the truth when recording was switched off.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from analytics.series import (
    behaviour_shares,
    coordination_chart,
    flight_charts,
    score_matrix,
    survival_chart,
)
from core.run_manager import RunManager
from core.runtime import get_run_manager

router = APIRouter(tags=["analytics"])

# Enough to cover a long run at one sample per second per unit, and enough
# decisions to cover everything the repository will store for one run.
_SAMPLE_LIMIT = 20000
_DECISION_LIMIT = 5000


def _require_repository(runs: RunManager) -> Any:
    repository = runs.repository
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="run storage is disabled in configs/analysis.yaml, so there is nothing to analyse",
        )
    return repository


def _load(run_id: str, runs: RunManager) -> tuple[Any, dict[str, Any]]:
    repository = _require_repository(runs)
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return repository, run


@router.get("/analytics/runs/{run_id}")
def run_analytics(
    run_id: str,
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    """Every chart for one run, in the shape a chart draws."""
    repository, run = _load(run_id, runs)

    samples = repository.get_telemetry_samples(run_id, limit=_SAMPLE_LIMIT)
    decisions = repository.get_decisions(run_id, limit=_DECISION_LIMIT)
    scores = repository.get_scores(run_id)

    charts = [*flight_charts(samples), survival_chart(samples), coordination_chart(decisions)]

    return {
        "run": run,
        "charts": [c.to_dict() for c in charts],
        "scores": score_matrix(scores).to_dict(),
        "behaviours": behaviour_shares(decisions).to_dict(),
        "sampled": {
            "telemetry_samples": len(samples),
            "decisions": len(decisions),
            "decision_limit": _DECISION_LIMIT,
            # Decisions are capped per run when they are stored, so a long run's
            # behaviour shares describe the decisions that were kept, not all of
            # them. Saying so beats a chart that quietly means something else.
            "decisions_truncated": len(decisions) >= _DECISION_LIMIT,
        },
        "notice": (
            "Every unit, platform and parameter is fictional. These are simulation "
            "measurements, not observations of anything real."
        ),
    }


@router.get("/analytics/compare")
def compare_runs(
    runs_query: str = Query(alias="runs", description="Two or more run ids, comma separated"),
    runs: RunManager = Depends(get_run_manager),
) -> dict[str, Any]:
    """Team and per-term scores for several runs side by side.

    Comparing runs only means something when they were judged by the same
    standard, so the response carries each run's scoring ``weights_hash`` and
    says plainly when they differ rather than lining up numbers that were
    measured against different rulers.
    """
    wanted = [r.strip() for r in runs_query.split(",") if r.strip()]
    if len(wanted) < 2:
        raise HTTPException(status_code=400, detail="give at least two run ids to compare")
    if len(wanted) > 6:
        raise HTTPException(status_code=400, detail="compare at most six runs at once")

    repository = _require_repository(runs)
    compared: list[dict[str, Any]] = []
    weight_hashes: set[str] = set()

    for run_id in wanted:
        run = repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
        scores = repository.get_scores(run_id)
        matrix = score_matrix(scores)
        teams = {
            str(s["subject_id"]): round(float(s.get("total", 0.0)), 2)
            for s in scores
            if s.get("subject") == "team"
        }
        for score in scores:
            if score.get("weights_hash"):
                weight_hashes.add(str(score["weights_hash"]))

        compared.append(
            {
                "run_id": run_id,
                "scenario": run.get("scenario_name"),
                "seed": run.get("seed"),
                "integrator": run.get("integrator"),
                "config_hash": run.get("config_hash"),
                "ticks": run.get("ticks"),
                "end_reason": run.get("end_reason"),
                "teams": teams,
                "scores": matrix.to_dict(),
            }
        )

    return {
        "count": len(compared),
        "runs": compared,
        "comparable": len(weight_hashes) <= 1,
        "weights_hashes": sorted(weight_hashes),
        "detail": (
            "All runs were scored against the same weights."
            if len(weight_hashes) <= 1
            else "These runs were scored against different weights — the totals are not comparable."
        ),
    }
