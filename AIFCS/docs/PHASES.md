# AIFCS Development Phases

Phases are built in order. Each one must **implement → test → run → inspect → fix →
document** before the next begins. A subsystem is only marked `ONLINE` in
`backend/core/system_status.py` once it genuinely runs.

## PHASE 0 — Project setup — **Complete**

Backend skeleton (FastAPI app factory), frontend skeleton (React + Vite +
TypeScript + Tailwind), YAML configuration system with validation and a config
hash, structured JSON logging, health/status/compute/config API, subsystem
registry, Docker setup, dev scripts, README, 22 backend tests plus an end-to-end
browser check.

## PHASE 1 — Simulation core — **Complete**

`SimulationClock` keeping simulation, real and render time separate;
`WorldState` + `EntityState` as the authoritative truth; `EventBus`;
`SimulationEngine` with start / pause / resume / stop / step / speed / reset;
scenario loading and validation with the `demo_alpha` reference scenario; a
swappable `Integrator` interface; the simulation control API; and dashboard
transport controls with a live tactical plot.

**Verified:** the engine ticks at a fixed 60 Hz with a real-time factor of 1.00;
pause genuinely freezes the clock; `step` advances an exact tick count; reset
restores the initial `state_hash`; the same seed reproduces an identical state;
600 x 1 tick equals 1 x 600 ticks; and 0.25x and 50x produce identical
trajectories. 121 backend tests plus two browser end-to-end suites.

**Deliberately not done here:** motion is the `KinematicIntegrator`
(constant velocity, no forces). Gravity, drag, lift and thrust are PHASE 2, which
is why the dashboard reports the physics subsystem as `WARNING`, not `ONLINE`.

## PHASE 2 — Aircraft model and 6DOF physics — **Next**

`AircraftModel` interface with `Simple6DOFModel` (Newton-Euler rigid body:
gravity, drag, lift, thrust, mass), replacing `KinematicIntegrator` behind the
existing `Integrator` protocol. `EntityState` already carries the required
fields. Fictional platforms only.

## PHASE 3 — Rule agent

`BaseAgent` with `observe / think / act / update / reset`. `RuleAgent` performing
waypoint navigation and formation keeping. Every decision is recorded with reason
codes derived from actual state — never invented text.

## PHASE 4 — Flight controller and safety layer

Abstract action space (`aileron`, `elevator`, `rudder`, `throttle`).
`ActionValidator` rejecting NaN/Inf/out-of-range/excessive-rate actions, then
`CommandMapper` → `FlightController` → physics. Rejections are logged, never
silently swallowed.

## PHASE 5 — Sensor model

Separate `TruthState` and `Observation` types. Noise, delay, dropout, limited
field of regard, confidence estimates. Agents may read only `Observation`.

## PHASE 6 — Communication model

Latency, packet loss, reordering, bandwidth limits and blackout windows, with
`send_message` / `receive_message` / `broadcast` / `get_latency` / `get_packet_loss`.

## PHASE 7 — WebSocket telemetry

`/ws/simulation` broadcasting world state, entity state, agent state, events and
score at a configurable rate, decoupled from the physics tick.

## PHASE 8 — 3D Command Center

Three.js / React Three Fiber tactical view: abstract terrain, BLUE/RED entities
with ID, altitude, speed, heading, status, health and AI state. Orbit / follow /
free / top / side cameras.

## PHASE 9 — Replay, scoring, database

Replay recorder and player (play, pause, step, jump, fast-forward, slow motion,
camera follow, event jump). Independent scoring engine — scores never live inside
the simulation engine. SQLite schema for scenarios, runs, entities, decisions,
telemetry, events, replays, training runs, models and metrics.

## PHASE 10 — Scenario editor

Create / save / load / clone / delete / import / export scenarios from the UI.

## PHASE 11–13 — RL

`AIFCSCombatEnv` (Gymnasium), configurable and explainable reward engine with a
per-term breakdown, then PPO and SAC training pipelines with evaluation and model
save/load.

## PHASE 14–15 — Multi-agent and commander

Scale 1 → 2 → 4 → 8 agents. `AgentManager`, `TeamManager`, `CommunicationManager`,
`TaskManager`. `CommanderAgent` allocating high-level tasks only — it never touches
control surfaces.

## PHASE 16 — JSBSim adapter

JSBSim as one swappable physics backend behind the `AircraftModel` interface. The
platform must keep working without it.

## PHASE 17–19 — Analytics, training centre, model centre

Reward/score/survival/coordination charts, model comparison, live training metrics,
model lifecycle (load, unload, evaluate, compare, archive).

## PHASE 20 — Production hardening

Performance profiling, error handling, plugin architecture polish, CLI (`aifcs`),
deployment documentation.
