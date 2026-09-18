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

## PHASE 2 — Aircraft model and 6DOF physics — **Complete**

`Simple6DOFModel` replaces `KinematicIntegrator` behind the existing
`Integrator` protocol, so the engine itself did not change. Newton-Euler rigid
body with gravity, thrust, lift, drag, side force, control moments, static
stability and rotary damping; quaternion attitude integrated with RK4;
`ControlInputs` on every entity; fictional airframes in a catalogue scenarios
select by name.

**Verified:** free fall matches ½gt²; a vertical dive reaches a finite terminal
speed; static stability drives the angle of attack to the predicted trim value
(`cm_0 / -cm_alpha`); `demo_alpha` holds its altitude within 85 m over a minute
hands-off; every control channel moves the aircraft the way its sign says;
control authority is bounded (≈19° alpha, ≈200°/s roll); vertical flight does
not hit gimbal lock; non-finite control input cannot poison the state; and the
physics is bit-for-bit deterministic. 29 physics tests, 152 backend tests total.

**A note on frames:** the first implementation used a body frame with +Y left
and +Z up. That silently inverts every pitch and yaw moment relative to the
convention published aerodynamic coefficients assume, which turned static
stability into divergence — the aircraft tumbled. The model now runs in FRD/NED
and converts at the ENU boundary.

## PHASE 3 — Rule agent — **Complete**

`BaseAgent` with the `observe → think → act` cycle plus `update` and `reset`;
`RuleAgent` doing waypoint navigation, patrol routes, formation keeping and
collision avoidance; cascaded guidance loops with an altitude integrator;
`AgentManager` scheduling decisions on a tick-based interval; scenario support
for routes and formation assignments; the agents and decisions API; and the
dashboard decision feed.

**Verified:** agents decide exactly 10 times per simulation second at 10 Hz; the
schedule is tick-based and repeats across runs; the lead tracks its route and
reaches the first waypoint at the algebraically correct time; altitude hold
settles within a metre; the wingman closes to within 20 m of station; a failing
agent is logged and skipped rather than stopping the simulation; out-of-range
commands are clamped before reaching the entity; an agent cannot write to the
truth state or reach it through its observation; and agent-driven runs remain
deterministic. 49 new tests, 201 backend tests total.

**Two bugs worth recording:**

*Formation partners fought each other.* The collision radius (400 m) was larger
than the commanded formation separation (424 m), so each aircraft treated its own
wingman as a conflict and turned away — which dragged the lead off its route.
The collision radius must sit well inside the formation spacing, and the config
now rejects a setting where it does not.

*The heading loop hunted.* The outer loop had no derivative term, so a large
heading error commanded a steep bank, the aircraft overshot, and the cycle
repeated with the sign flipped. Damping on the turn rate fixed it. The same
absence of integral action in the altitude loop left a constant 62 m droop,
because a proportional loop must hold an error to command the trim attitude; a
bounded integrator removed it.

**Performance:** replacing NumPy's generic `cross`, `clip` and `norm` on
3-element vectors with explicit scalar arithmetic more than doubled throughput,
from 425 to ~920 ticks/s (7x to 15x real time). Those calls were spending more
time in axis-normalisation machinery than in arithmetic.

## PHASE 4 — Flight controller and safety layer — **Next**

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
