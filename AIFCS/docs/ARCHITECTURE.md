# AIFCS Architecture

## Design principles

1. **Truth is never written by an agent.** The pipeline is one-directional:
   `Truth → Observation → Decision → Action → Physics → World Update → Evaluation`.
2. **Layers are swappable.** Physics, agents, sensors, scenarios, renderers,
   training and transport are interfaces with implementations behind them, so a
   `Simple6DOFModel` can be replaced by a `JSBSimAdapter` without touching the engine.
3. **Rates are independent.** Simulation (60 Hz), agent decisions (10 Hz), telemetry
   broadcast (20 Hz) and UI render (30–60 FPS) each run on their own clock.
4. **Nothing is tuned in code.** Parameters live in `configs/*.yaml`.
5. **No fake capability.** An unbuilt subsystem reports `NOT_IMPLEMENTED`; it never
   presents a button that does nothing.

## Module map

| Package | Responsibility | Phase |
|---|---|---|
| `core/config.py` | Typed YAML configuration, config hash | 0 |
| `core/logging_config.py` | Structured JSON logging | 0 |
| `core/system_status.py` | Subsystem registry driving the status panel | 0 |
| `core/compute.py` | CPU/GPU detection with CPU fallback | 0 |
| `core/clock.py` | Fixed-timestep clock; simulation vs. real vs. render time | 1 |
| `core/world_state.py` | Authoritative truth state | 1 |
| `core/event_bus.py` | Publish/subscribe system events | 1 |
| `core/simulation_engine.py` | Tick loop, lifecycle, speed control | 1 |
| `simulation/aircraft.py` | Fictional airframe parameters and control demands | 2 |
| `simulation/physics.py` | Newton-Euler 6DOF, quaternion attitude, RK4 | 2 |
| `simulation/scenario.py` | Scenario loading and validation | 1 |
| `core/integrator.py` | Swappable physics backend protocol | 1 |
| `simulation/sensors.py` | Truth → Observation degradation | 5 |
| `simulation/communications.py` | Latency, loss, blackout | 6 |
| `agents/base_agent.py` | Agent contract: Observation, Decision, Action | 3 |
| `agents/rule_agent.py` | Deterministic pilot: routes, formation, avoidance | 3 |
| `agents/guidance.py` | Cascaded guidance loops (moves to controllers in PHASE 4) | 3 |
| `agents/agent_manager.py` | Decision scheduling and action application | 3 |
| `controllers/limits.py` | Safety limits and violation taxonomy | 4 |
| `controllers/action_validator.py` | Reject or clamp a malformed command | 4 |
| `controllers/command_mapper.py` | Per-entity actuator rate limiting | 4 |
| `controllers/flight_controller.py` | The only writer of entity controls | 4 |
| `controllers/autopilot.py` | Guidance and stabilisation loops | 3 |
| `scoring/*` | Independent score engine and metrics | 9 |
| `replay/*` | Recorder, player, timeline | 9 |
| `storage/*` | SQLite models and access | 9 |
| `training/*` | Gymnasium env, rewards, PPO/SAC, evaluation | 11–13 |

## Determinism

A run is reproducible from: `seed` + fixed timestep + scenario version +
model version + `config_hash`. The hash covers the fully-resolved configuration,
so any parameter change produces a different hash and the run is not mistaken for
an earlier one.

## Frontend

`frontend/src` mirrors the backend contract:

- `types/api.ts` — response types matching the API exactly.
- `api/client.ts` — typed fetch wrapper; network failure yields a clear error.
- `stores/systemStore.ts` — Zustand store of live backend state.
- `components/`, `pages/` — Command Center panels.
- `three/` — 3D scene (PHASE 8).

The dashboard renders only data the backend actually returned. When the backend is
unreachable the UI says so instead of showing stale or invented values.
