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

## PHASE 4 — Flight controller and safety layer — **Complete**

`ActionValidator` (reject on invalid aircraft state, clamp non-finite and
out-of-range channels), envelope protection (structural g limit, altitude floor
and ceiling with a soft buffer), `CommandMapper` (per-entity actuator rate
limiting), and `FlightController` tying them together. The autopilot guidance
loops moved from `agents/` to `controllers/autopilot.py`, where they belong.

The key structural change: the agent manager no longer writes to the aircraft.
It records a standing demand, and the flight controller — the only writer of
entity controls in the platform — consumes it every physics tick. One door
means the safety guarantees can actually be checked.

**Verified:** NaN and Inf demands become neutral while valid channels pass
through untouched; out-of-range demands are clamped on every channel; a
non-finite aircraft state rejects the command and keeps the last one; actuators
slew at exactly the configured rate and converge on the demand; each entity's
actuators are tracked independently; the load limiter eases pitch-up beyond the
g limit but never fights a recovery pitch-down; descent is blocked near the
floor while climbing stays free; normal flight triggers no corrections at all;
and the whole layer leaves the run deterministic. 34 new tests, 235 total.

**A note on the load factor test:** the first version asserted about 1 g for an
aircraft at zero pitch. That was wrong — this airframe has `cl_0 = 0`, so zero
angle of attack means zero lift and zero load factor. Level flight *is* the trim
attitude, around 1.27 degrees. The code was right and the test premise was not.

## PHASE 5 — Sensor model — **Complete**

`SensorModel` sits between the truth state and every agent: detection envelope
(range and field of regard), a latency buffer, dropout with track coasting and
memory, range-dependent measurement noise, imperfect ownship estimation, and a
derived confidence. `ContactView` gained `age_s`, `confidence` and `measured`.

The payoff of the PHASE 3 design showed up here: agents were written against
`Observation` and **not one line of agent code changed** when perception became
degraded. Only what fills the object changed.

**Verified:** contacts beyond range or outside the field of regard are absent
rather than flagged; measurements carry noise that grows with range; reports lag
the truth by the configured latency; a dropped contact is coasted and then
forgotten past track memory; a coasted track is dead-reckoned by the right
amount; confidence falls with range and staleness and stays in [0, 1]; the same
seed reproduces the same measurements and a different seed does not; disabling
the sensor restores perfect information; an agent cannot reach the truth state
through its observation; and the run stays deterministic. 24 new tests, 259
total.

**Three bugs worth recording:**

*The wiring silently did not apply.* The edit inserting the sensor hook into the
agent manager did not match, because an earlier `ruff format` had reformatted
the target. Everything ran and looked fine — all four agents reported identical
contact distances and a confidence of exactly 1.0, which is what gave it away.
Identical values across differently-positioned observers cannot come from a
noisy sensor.

*The wingman went blind and gave up.* With a forward-only field of regard, the
wingman overshot its station, the leader ended up 129 degrees off its nose, and
the track was lost permanently — the agent correctly reported
`LEADER_UNAVAILABLE` and fell back to `HOLD`, but the formation was finished.
Two fixes: the formation speed loop gained a damping term on closing rate so the
wingman stops flying past its station, and all-round coverage became the default
since a narrow sensor only becomes survivable once the PHASE 6 datalink exists.

*Dead reckoning used the wrong timestep.* A coasted track advanced by one
physics tick per call, but `observe` runs at the decision rate, so the estimate
crept forward six times too slowly. The track now stores its last measurement
and derives the coasted estimate from elapsed time, which is independent of how
often it is asked.

**An emergent interaction:** with noisy perception the guidance loops react more
sharply, and the PHASE 4 load-factor limiter now genuinely engages during turns.
Two phases apart, behaving exactly as intended.

## PHASE 6 — Communication model — **Complete**

`CommunicationModel` as a simulation transport: directed send and team
broadcast, latency with jitter, packet loss, per-sender bandwidth limits with a
sliding window, blackout windows, and out-of-order delivery that the receiver
can detect. `DatalinkService` turns it into shared tracks: each unit broadcasts
its own position estimate, teammates merge those reports into their picture, and
a measured contact always beats a relayed one.

**This phase closes the loop PHASE 5 opened.** A narrow sensor was abandoned in
PHASE 5 because a wingman that overshot lost its leader for good. With the
datalink it survives, and the numbers show the dependency directly — wingman
state after 150 simulation seconds on `demo_alpha`:

| Sensor coverage | Datalink | Result |
|---|---|---|
| All-round | on | `FORMATION`, station error 101 m |
| ±100° | off | `HOLD` — formation collapses |
| ±100° | on | `FORMATION`, station error 10 m |

The ±100° case with the link is *better* than all-round without it, because a
teammate's own report of itself is more accurate than a noisy remote
measurement. The default coverage went back to ±120°, so the demo genuinely
depends on the link — turn comms off and watch formation fall apart.

**Verified:** a broadcast reaches the sender's team and neither the sender nor
the other team; a message is not delivered before its latency elapses; jitter
reorders arrivals and the model counts it; loss is bounded and reproducible;
bandwidth drops the excess rather than queuing it, on a sliding window, per
sender; nothing crosses a blackout and the window boundaries are inclusive; a
disabled link sends nothing; the same seed loses the same messages and a
different seed does not; a datalink track appears as a `DATALINK` contact that
is never marked measured; and no unit ever appears twice from two sources.
28 new tests, 287 total.

**Two test-only bugs:** the partial-loss test forgot to raise the bandwidth
limit, so the limiter — working correctly — dropped 180 of 200 messages before
loss could apply. The datalink test left jitter at its default, so delivery
landed just after t=0 and `update(0.0)` legitimately delivered nothing. Both
times the model was right and the fixture was wrong.

## PHASE 7 — WebSocket telemetry — **Next**

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
