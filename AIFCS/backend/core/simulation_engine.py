"""Simulation engine (PHASE 1).

Owns the tick loop and is the single authority over the truth state.

Two ways to drive it, sharing exactly one code path (``_tick``):

* ``step(n)`` — advance n ticks immediately. Deterministic, used by tests,
  training rollouts and single-step debugging.
* ``start()`` — run an asyncio task that calls ``_tick`` paced by the clock, so
  the dashboard sees the world evolve in real time.

Because both go through ``_tick``, a run stepped 600 times produces exactly the
same state as a run that ticked for 10 real seconds at 60 Hz.

The React layer never drives this loop; it only observes the result.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from agents.agent_manager import AgentManager
from agents.factory import build_agents
from controllers.flight_controller import FlightController
from controllers.limits import SafetyLimits, ViolationType
from core.clock import ClockState, SimulationClock
from core.config import Settings, get_settings
from core.event_bus import EventBus, EventType
from core.integrator import Integrator, clamp_to_bounds
from core.logging_config import get_logger
from core.world_state import EntityStatus, WorldState
from simulation.communications import CommsConfig, CommunicationModel
from simulation.datalink import DatalinkService
from simulation.physics import Simple6DOFModel
from simulation.scenario import Scenario, find_scenario
from simulation.sensors import SensorConfig, SensorModel

log = get_logger("simulation_engine")


class SimulationError(RuntimeError):
    """Raised when an operation is invalid for the engine's current state."""


class SimulationEngine:
    """Fixed-timestep, deterministic simulation engine."""

    def __init__(
        self,
        settings: Settings | None = None,
        event_bus: EventBus | None = None,
        integrator: Integrator | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.events = event_bus or EventBus()
        self.integrator: Integrator = integrator or Simple6DOFModel()

        self.clock = SimulationClock(
            tick_rate_hz=self.settings.simulation.tick_rate_hz,
            speed=self.settings.simulation.default_speed,
        )

        self.sensors = SensorModel(
            config=self._sensor_config(),
            seed=self.settings.simulation.seed,
            tick_rate_hz=self.settings.simulation.tick_rate_hz,
        )

        self.comms = CommunicationModel(config=self._comms_config(), seed=self.settings.simulation.seed)
        self.datalink = DatalinkService(self.comms, tick_rate_hz=self.settings.simulation.tick_rate_hz)

        self.agents = AgentManager(
            event_bus=self.events,
            tick_rate_hz=self.settings.simulation.tick_rate_hz,
            decision_rate_hz=self.settings.agents.decision_rate_hz,
            sensor_model=self.sensors,
            datalink=self.datalink,
        )

        self.controller = FlightController(
            SafetyLimits(
                max_control_rate_per_s=self.settings.safety.max_control_rate_per_s,
                max_load_factor=self.settings.safety.max_load_factor,
                min_altitude_m=self.settings.safety.min_altitude_m,
                max_altitude_m=self.settings.safety.max_altitude_m,
                altitude_buffer_m=self.settings.safety.altitude_buffer_m,
                reject_on_invalid_state=self.settings.safety.reject_on_invalid_state,
            )
        )

        self.scenario: Scenario | None = None
        self.world = WorldState()
        self.seed: int = self.settings.simulation.seed
        self.rng: np.random.Generator = np.random.default_rng(self.seed)

        # Things that want to look at the world after each tick — the replay
        # recorder, today. They only ever read: an observer that raises is
        # logged and dropped rather than being allowed to stop the simulation.
        self.tick_observers: list[Callable[[], None]] = []

        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._end_reason: str | None = None
        self._blackout_announced = False

    # ------------------------------------------------------------- scenarios

    def load_scenario(self, name: str | None = None, seed: int | None = None) -> Scenario:
        """Load a scenario and build its initial world. Stops any running loop."""
        scenario_name = name or self.settings.scenarios.default_scenario
        directory = self._resolve(self.settings.scenarios.directory)

        scenario = find_scenario(directory, scenario_name)
        self.scenario = scenario

        # Precedence: explicit argument > scenario file > global config.
        self.seed = seed if seed is not None else (scenario.seed or self.settings.simulation.seed)
        self.rng = np.random.default_rng(self.seed)

        self.world = scenario.build_world()
        self.world.global_status.update(
            {
                "seed": self.seed,
                "config_hash": self.settings.config_hash,
                "integrator": self.integrator.name,
            }
        )

        self.agents.clear()
        for agent in build_agents(scenario, self.settings):
            self.agents.register(agent)

        self.sensors.reset(seed=self.seed)

        self.comms.reset(seed=self.seed)
        self.comms.clear_participants()
        for entity in self.world.entities.values():
            self.comms.register(entity.id, entity.team.value)
        self.datalink.reset()

        # Actuators start where the scenario trimmed them, not at neutral.
        self.controller.reset()
        for entity in self.world.entities.values():
            self.controller.seed(entity)

        self.clock.reset()
        self.clock.speed = self.settings.simulation.default_speed
        self._end_reason = None
        self.events.clear_history()

        log.info(
            "scenario loaded",
            extra={
                "event": "SCENARIO_LOADED",
                "scenario": scenario.name,
                "entities": len(scenario.entities),
                "agents": self.agents.count,
                "seed": self.seed,
            },
        )
        return scenario

    def unload(self) -> None:
        """Let go of the loaded scenario and empty the world.

        Needed when the scenario file is deleted while it is loaded. Without
        this the engine keeps pointing at a file that no longer exists, the
        dashboard goes on offering it as the loaded scenario, and START fails
        with "scenario file not found" for no reason the operator can see.

        Refuses while running: a scenario in use is guarded further up, and
        pulling the world out from under a running loop would be worse than
        the stale pointer this exists to clear.
        """
        if self.clock.state is ClockState.RUNNING:
            raise SimulationError("cannot unload a scenario while the simulation is running")

        name = self.scenario.name if self.scenario else None
        self.scenario = None
        self.world = WorldState()
        self.agents.clear()
        self.comms.clear_participants()
        self.datalink.reset()
        self.controller.reset()
        self.clock.reset()
        self._end_reason = None
        log.info("scenario unloaded", extra={"event": "SCENARIO_UNLOADED", "scenario": name})

    def _sensor_config(self) -> SensorConfig:
        sensors = self.settings.sensors
        return SensorConfig(
            enabled=sensors.enabled,
            max_range_m=sensors.max_range_m,
            field_of_regard_deg=sensors.field_of_regard_deg,
            latency_s=sensors.latency_s,
            dropout_probability=sensors.dropout_probability,
            track_memory_s=sensors.track_memory_s,
            position_noise_base_m=sensors.position_noise_base_m,
            position_noise_per_km_m=sensors.position_noise_per_km_m,
            velocity_noise_mps=sensors.velocity_noise_mps,
            ownship_position_noise_m=sensors.ownship_position_noise_m,
            ownship_velocity_noise_mps=sensors.ownship_velocity_noise_mps,
        )

    def _comms_config(self) -> CommsConfig:
        comms = self.settings.communications
        return CommsConfig(
            enabled=comms.enabled,
            latency_base_s=comms.latency_base_s,
            latency_jitter_s=comms.latency_jitter_s,
            packet_loss_probability=comms.packet_loss_probability,
            max_messages_per_second=comms.max_messages_per_second,
            report_rate_hz=comms.report_rate_hz,
            blackout_windows=tuple((w[0], w[1]) for w in comms.blackout_windows),
        )

    def _resolve(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.settings.project_root / path

    def _require_scenario(self) -> None:
        if self.scenario is None:
            raise SimulationError("no scenario loaded — call load_scenario() first")

    # ------------------------------------------------------------ the tick

    def _tick(self) -> None:
        """Advance the world by exactly one fixed timestep.

        The single mutation point for the truth state. Order matters:
        integrate, enforce world bounds, then publish.
        """
        dt = self.clock.dt

        # Sensors capture the current truth first; what an agent then sees is
        # the delayed, noisy version of it.
        self.sensors.record(self.world)

        # Datalink: units share where they believe they are, and messages whose
        # latency has elapsed are delivered. Blackouts are announced once.
        self.datalink.broadcast_reports(self.world, self.clock.tick_count)
        self.comms.update(self.clock.simulation_time)
        self._track_blackout()

        # Agents decide at their own slower rate and leave a standing demand.
        self.agents.update(self.world, self.clock.tick_count)

        # The flight controller runs every tick: it validates the demand, keeps
        # the aircraft inside its envelope and slews the actuators. It is the
        # only thing that writes entity controls.
        for entity_id, outcome in self.controller.update(self.world, self.agents.demands, dt):
            if not outcome.accepted or outcome.violations:
                self._report_control_violations(entity_id, outcome)

        self.integrator.integrate(self.world, dt)

        bounds = self.settings.world.bounds.model_dump()
        for entity in self.world.entities.values():
            if entity.status is EntityStatus.ACTIVE and clamp_to_bounds(entity, bounds):
                self.events.emit(
                    EventType.ENTITY_OUT_OF_BOUNDS,
                    simulation_time=self.clock.simulation_time,
                    tick=self.clock.tick_count,
                    entity_id=entity.id,
                    message=f"{entity.id} reached the simulation boundary",
                    data={"position": entity.position.tolist()},
                )

        simulation_time = self.clock.advance()
        self.world.simulation_time = simulation_time
        self.world.tick = self.clock.tick_count

        self.events.emit(
            EventType.SIMULATION_TICK,
            simulation_time=simulation_time,
            tick=self.clock.tick_count,
        )

        self._notify_observers()

    def _notify_observers(self) -> None:
        """Let read-only observers see the settled state of this tick."""
        for observer in list(self.tick_observers):
            try:
                observer()
            except Exception:
                log.exception(
                    "tick observer failed; detaching it",
                    extra={"event": "TICK_OBSERVER_ERROR"},
                )
                with contextlib.suppress(ValueError):
                    self.tick_observers.remove(observer)

    def _track_blackout(self) -> None:
        """Publish an event when the datalink drops or comes back."""
        active = self.comms.in_blackout(self.clock.simulation_time)
        if active == self._blackout_announced:
            return

        self._blackout_announced = active
        self.events.emit(
            EventType.COMMUNICATION_EVENT,
            simulation_time=self.clock.simulation_time,
            tick=self.clock.tick_count,
            message="datalink blackout started" if active else "datalink restored",
            data={"blackout_active": active},
        )

    def _report_control_violations(self, entity_id: str, outcome: Any) -> None:
        """Publish a rejected or corrected command so it is never silent.

        Rate limiting is expected during any manoeuvre and would swamp the feed,
        so only genuinely notable corrections are published.
        """
        notable = [v for v in outcome.violations if v.type is not ViolationType.RATE_LIMITED]
        if not notable:
            return

        self.events.emit(
            EventType.ACTION_REJECTED,
            simulation_time=self.clock.simulation_time,
            tick=self.clock.tick_count,
            entity_id=entity_id,
            message=f"{entity_id}: {notable[0].type.value} on {notable[0].channel}",
            data={
                "accepted": outcome.accepted,
                "violations": [v.to_dict() for v in notable],
            },
        )

    def _duration_reached(self) -> bool:
        limit = self.scenario.duration_s if self.scenario else self.settings.simulation.max_duration_s
        limit = min(limit, self.settings.simulation.max_duration_s)
        return self.clock.simulation_time >= limit

    # --------------------------------------------------------------- control

    def step(self, ticks: int = 1) -> WorldState:
        """Advance a fixed number of ticks immediately, ignoring wall time."""
        self._require_scenario()
        if ticks < 1:
            raise SimulationError("ticks must be at least 1")
        if self.clock.state is ClockState.RUNNING:
            raise SimulationError("cannot single-step while running — pause first")

        for _ in range(ticks):
            if self._duration_reached():
                self._finish("duration reached")
                break
            self._tick()
        return self.world

    async def start(self, scenario: str | None = None, seed: int | None = None) -> None:
        """Load (if needed) and run the simulation loop in the background."""
        async with self._lock:
            if self._task is not None and not self._task.done():
                raise SimulationError("simulation is already running")

            if scenario is not None or self.scenario is None:
                self.load_scenario(scenario, seed)
            elif seed is not None:
                self.seed = seed
                self.rng = np.random.default_rng(seed)

            self.clock.start()
            self._end_reason = None
            self._task = asyncio.create_task(self._run_loop())

        self.events.emit(
            EventType.SIMULATION_STARTED,
            simulation_time=self.clock.simulation_time,
            tick=self.clock.tick_count,
            message=f"simulation started: {self.scenario.name if self.scenario else 'unknown'}",
            data={"seed": self.seed, "speed": self.clock.speed},
        )

    async def _run_loop(self) -> None:
        """Pace ticks against real time without letting drift accumulate."""
        loop = asyncio.get_running_loop()
        next_tick_at = loop.time()
        try:
            while self.clock.state is not ClockState.STOPPED:
                if self.clock.state is ClockState.PAUSED:
                    await asyncio.sleep(0.02)
                    next_tick_at = loop.time()
                    continue

                if self._duration_reached():
                    self._finish("duration reached")
                    break

                self._tick()

                next_tick_at += self.clock.target_interval_s
                delay = next_tick_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # Running behind: yield, and stop chasing a backlog we can
                    # never catch up on (realtime_factor reports the shortfall).
                    await asyncio.sleep(0)
                    next_tick_at = loop.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("simulation loop failed", extra={"event": "SIMULATION_LOOP_ERROR"})
            self.clock.stop()
            self._end_reason = "error"
            raise

    def pause(self) -> None:
        self._require_scenario()
        if self.clock.state is not ClockState.RUNNING:
            raise SimulationError(f"cannot pause while {self.clock.state.value}")
        self.clock.pause()
        self.events.emit(
            EventType.SIMULATION_PAUSED,
            simulation_time=self.clock.simulation_time,
            tick=self.clock.tick_count,
            message="simulation paused",
        )

    def resume(self) -> None:
        self._require_scenario()
        if self.clock.state is not ClockState.PAUSED:
            raise SimulationError(f"cannot resume while {self.clock.state.value}")
        self.clock.resume()
        self.events.emit(
            EventType.SIMULATION_RESUMED,
            simulation_time=self.clock.simulation_time,
            tick=self.clock.tick_count,
            message="simulation resumed",
        )

    def set_speed(self, speed: float) -> None:
        """Change the wall-clock pace. The timestep, and so the result, is unchanged."""
        allowed = self.settings.simulation.allowed_speeds
        if speed not in allowed:
            raise SimulationError(f"speed {speed} not allowed; choose one of {allowed}")
        self.clock.set_speed(speed)
        self.events.emit(
            EventType.SIMULATION_SPEED_CHANGED,
            simulation_time=self.clock.simulation_time,
            tick=self.clock.tick_count,
            message=f"speed set to {speed}x",
            data={"speed": speed},
        )

    async def stop(self) -> None:
        """Stop the loop and cancel the background task."""
        async with self._lock:
            self.clock.stop()
            task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def reset(self) -> None:
        """Stop and rebuild the world from the scenario, with the same seed."""
        await self.stop()
        if self.scenario is not None:
            self.world = self.scenario.build_world()
            self.world.global_status.update(
                {
                    "seed": self.seed,
                    "config_hash": self.settings.config_hash,
                    "integrator": self.integrator.name,
                }
            )
        self.clock.reset()
        self.rng = np.random.default_rng(self.seed)
        self.agents.reset()
        self.sensors.reset(seed=self.seed)
        self.comms.reset(seed=self.seed)
        self.datalink.reset()
        self.controller.reset()
        for entity in self.world.entities.values():
            self.controller.seed(entity)
        self._end_reason = None
        self.events.clear_history()
        self.events.emit(EventType.SIMULATION_RESET, message="simulation reset")

    def _finish(self, reason: str) -> None:
        self.clock.stop()
        self._end_reason = reason
        self.events.emit(
            EventType.SIMULATION_ENDED,
            simulation_time=self.clock.simulation_time,
            tick=self.clock.tick_count,
            message=f"simulation ended: {reason}",
            data={"reason": reason},
        )

    # ---------------------------------------------------------------- status

    @property
    def is_running(self) -> bool:
        return self.clock.state is ClockState.RUNNING

    def status(self) -> dict[str, Any]:
        """Full engine status for the API and the dashboard."""
        return {
            "scenario": self.scenario.name if self.scenario else None,
            "scenario_loaded": self.scenario is not None,
            "clock": self.clock.snapshot(),
            "seed": self.seed,
            "deterministic": self.settings.simulation.deterministic,
            "integrator": self.integrator.name,
            "config_hash": self.settings.config_hash,
            "entity_count": len(self.world.entities),
            "active_entities": len(self.world.active_entities),
            "duration_s": self.scenario.duration_s if self.scenario else None,
            "end_reason": self._end_reason,
            "state_hash": self.world.state_hash,
            "events_published": self.events.published_count,
            "agent_count": self.agents.count,
            "decision_count": self.agents.decision_count,
            "decision_rate_hz": self.agents.decision_rate_hz,
            "controller": self.controller.status(),
            "sensors": self.sensors.status(),
            "communications": self.comms.status(self.clock.simulation_time),
            "datalink": self.datalink.status(),
        }
