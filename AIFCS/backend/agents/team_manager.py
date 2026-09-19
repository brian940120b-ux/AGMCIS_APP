"""What a team knows about itself (PHASE 14).

A commander needs a picture of its team, and the only honest source for one is
what the team's own units have shared over the datalink. Reading the world
directly would give a commander perfect knowledge of units it has not heard
from in thirty seconds — and any coordination built on that would fall apart
the moment the link degraded, which is exactly the case the simulation exists
to study.

So the roster comes from the world (which units exist and whose side they are
on is not secret), but every *position* comes from a datalink report, with the
age of that report attached. A unit nobody has heard from recently is listed as
unheard rather than quietly assumed to be where it last was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.world_state import EntityStatus, Team, WorldState
from simulation.datalink import DatalinkService


@dataclass
class TeamMemberView:
    """One unit as its own team currently sees it."""

    entity_id: str
    # From the world: existence and side are not secret.
    status: EntityStatus
    # From the datalink: where the team last heard it was.
    position: np.ndarray | None = None
    velocity: np.ndarray | None = None
    report_age_s: float | None = None

    @property
    def heard_from(self) -> bool:
        return self.position is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "status": self.status.value,
            "heard_from": self.heard_from,
            "position": self.position.tolist() if self.position is not None else None,
            "report_age_s": round(self.report_age_s, 2) if self.report_age_s is not None else None,
        }


@dataclass
class TeamPicture:
    """A team's view of itself at one instant."""

    team: Team
    members: list[TeamMemberView] = field(default_factory=list)
    simulation_time: float = 0.0

    @property
    def active(self) -> list[TeamMemberView]:
        return [m for m in self.members if m.status is EntityStatus.ACTIVE]

    @property
    def heard(self) -> list[TeamMemberView]:
        return [m for m in self.active if m.heard_from]

    @property
    def unheard(self) -> list[TeamMemberView]:
        return [m for m in self.active if not m.heard_from]

    @property
    def coverage(self) -> float:
        """Share of active units the team currently has a report from."""
        return len(self.heard) / len(self.active) if self.active else 0.0

    def member(self, entity_id: str) -> TeamMemberView | None:
        return next((m for m in self.members if m.entity_id == entity_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team.value,
            "simulation_time": round(self.simulation_time, 3),
            "size": len(self.members),
            "active": len(self.active),
            "heard_from": len(self.heard),
            "unheard": [m.entity_id for m in self.unheard],
            "coverage": round(self.coverage, 3),
            "members": [m.to_dict() for m in self.members],
        }


class TeamManager:
    """Builds each team's picture of itself from what its units have shared."""

    def __init__(self, datalink: DatalinkService | None = None) -> None:
        self.datalink = datalink

    def teams(self, world: WorldState) -> list[Team]:
        return sorted({e.team for e in world.entities.values()}, key=lambda t: t.value)

    def roster(self, world: WorldState, team: Team) -> list[str]:
        return sorted(e.id for e in world.entities.values() if e.team is team)

    def picture(self, world: WorldState, team: Team) -> TeamPicture:
        """What this team currently knows about itself.

        Positions come from the pooled datalink reports of its own members, so
        a unit is only "seen" if somebody on the team heard from it.
        """
        now = world.simulation_time
        members: list[TeamMemberView] = []

        for entity_id in self.roster(world, team):
            entity = world.entities[entity_id]
            view = TeamMemberView(entity_id=entity_id, status=entity.status)
            report = self._freshest_report(entity_id, team, world)
            if report is not None:
                track, age = report
                view.position = track.estimate(now)
                view.velocity = track.velocity.copy()
                view.report_age_s = age
            members.append(view)

        return TeamPicture(team=team, members=members, simulation_time=now)

    def _freshest_report(self, entity_id: str, team: Team, world: WorldState) -> tuple[Any, float] | None:
        """The newest report about this unit held by anyone on its team."""
        if self.datalink is None:
            return None

        now = world.simulation_time
        best: tuple[Any, float] | None = None
        for holder in self.roster(world, team):
            track = self.datalink.tracks_for(holder).get(entity_id)
            if track is None:
                continue
            age = max(0.0, now - track.reported_time)
            if best is None or age < best[1]:
                best = (track, age)
        return best

    def status(self, world: WorldState) -> dict[str, Any]:
        return {
            "teams": [self.picture(world, team).to_dict() for team in self.teams(world)],
        }
