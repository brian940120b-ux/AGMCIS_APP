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
| `core/event_bus.py` | Publish/subscribe system events, sequenced | 1, 7 |
| `core/telemetry.py` | WebSocket broadcaster with per-client cursors | 7 |
| `core/simulation_engine.py` | Tick loop, lifecycle, speed control | 1 |
| `simulation/aircraft.py` | Fictional airframe parameters and control demands | 2 |
| `simulation/physics.py` | Newton-Euler 6DOF, quaternion attitude, RK4 | 2 |
| `simulation/scenario.py` | Scenario loading and validation | 1 |
| `core/integrator.py` | Swappable physics backend protocol | 1 |
| `simulation/sensors.py` | Truth → Observation: range, noise, latency, dropout | 5 |
| `simulation/communications.py` | Datalink transport: latency, loss, bandwidth, blackout | 6 |
| `simulation/datalink.py` | Position-report sharing and track merging | 6 |
| `agents/base_agent.py` | Agent contract: Observation, Decision, Action | 3 |
| `agents/rule_agent.py` | Deterministic pilot: routes, formation, avoidance | 3 |
| `agents/guidance.py` | Cascaded guidance loops (moves to controllers in PHASE 4) | 3 |
| `agents/agent_manager.py` | Decision scheduling and action application | 3 |
| `controllers/limits.py` | Safety limits and violation taxonomy | 4 |
| `controllers/action_validator.py` | Reject or clamp a malformed command | 4 |
| `controllers/command_mapper.py` | Per-entity actuator rate limiting | 4 |
| `controllers/flight_controller.py` | The only writer of entity controls | 4 |
| `controllers/autopilot.py` | Guidance and stabilisation loops | 3 |
| `core/run_manager.py` | Ties recording, storage and scoring to a run | 9 |
| `replay/format.py` | The recording format: header, frame, end | 9 |
| `replay/recorder.py` | JSON Lines writer, and the engine-attached sampler | 9 |
| `replay/reader.py` | Loading, validating and seeking a recording | 9 |
| `replay/player.py` | Server-side playback cursor and transport | 9 |
| `scoring/metrics.py` | Measures a recording; has no opinion about it | 9 |
| `scoring/engine.py` | Turns measurements into weighted, explained scores | 9 |
| `storage/schema.py` | Tables and ordered migrations | 9 |
| `storage/database.py` | Connection per thread, WAL, foreign keys | 9 |
| `storage/repository.py` | The only module that knows SQL | 9 |
| `simulation/scenario.py` | Parsing and validating a scenario | 1 |
| `simulation/scenario_store.py` | The only module that writes scenario files | 10 |
| `training/policy_agent.py` | A BaseAgent flown by a learned policy | 11 |
| `training/observation_encoder.py` | Observation to a bounded fixed-size vector | 11 |
| `training/environment.py` | `AIFCSCombatEnv`, the Gymnasium environment | 11 |
| `training/reward.py` | Weighted, per-term explainable reward | 12 |
| `training/pipeline.py` | PPO/SAC training, evaluation, model cards | 13 |
| `simulation/communication_manager.py` | Drains each inbox once and routes by message type | 14 |
| `agents/team_manager.py` | A team's picture, built only from datalink reports | 14 |
| `agents/tasks.py` | `Task`, `TaskType`, reason codes | 15 |
| `agents/task_manager.py` | The only record of who was told what | 15 |
| `agents/commander_agent.py` | Allocates tasks; holds no aircraft and no controls | 15 |
| `core/physics_backend.py` | Builds the configured integrator; reports what is available | 16 |
| `simulation/jsbsim_airframe.py` | Writes a fictional airframe out as JSBSim XML | 16 |
| `simulation/jsbsim_adapter.py` | JSBSim behind the Integrator protocol, one FDM per entity | 16 |
| `analytics/series.py` | Stored rows to chart-ready series; cannot reach a tick | 17 |
| `training/jobs.py` | One cancellable training job at a time, with progress | 18 |
| `training/registry.py` | Saved policies, and whether each still means anything | 19 |

## Determinism

A run is reproducible from: `seed` + fixed timestep + scenario version +
model version + `config_hash`. The hash covers the fully-resolved configuration,
so any parameter change produces a different hash and the run is not mistaken for
an earlier one.

Recording must not disturb this. The recorder is a read-only tick observer, and
`test_recording_does_not_change_the_simulation` asserts that a recorded run and
an unrecorded one reach the same `state_hash` — otherwise every recording would
be of a different world than the one that ran.

## Analysis is downstream of truth

```
Truth → Observation → Decision → Action → Physics → World Update
                                                        │
                                          ┌─────────────┴─────────────┐
                                          ▼                           ▼
                                    Recording                    Telemetry
                                          │
                              ┌───────────┴───────────┐
                              ▼                       ▼
                          Scoring                  Storage
```

Everything below the world update only reads. The scoring engine takes a
finished recording and returns numbers; it cannot be reached from a tick. That
is what makes it safe to change a weight, or add a term, without any risk of
changing how an aircraft flies.

## A policy is just another agent

`PolicyAgent` is a `BaseAgent`, so a learned policy flies the same path a rule
agent does:

```
sensor model → datalink → agent → action validation → envelope protection
→ actuator rate limiting → physics
```

Nothing is bypassed for training. The policy sees the delayed, noisy,
sometimes-missing picture the sensor model produced, never the truth state, and
its controls are validated and clamped like anyone else's. Encoding truth would
train a policy that cannot fly once it meets the real perception pipeline;
skipping the safety layer would train one that relies on commands the aircraft
will not accept.

The simulation engine needed no changes to support any of this — the
environment drives it through the ordinary `step()` and agent registration.

Reward is computed outside the engine, from measured state, and every term is
reported separately. Like scoring, it can be changed without any risk of
changing how an aircraft flies.

## A commander commands, it does not fly

`CommanderAgent` is deliberately **not** a `BaseAgent`. It has no entity, no
observation of its own and no reference that reaches a control surface. Its
whole output is `Task` objects:

```
datalink reports → TeamManager picture → CommanderAgent → Task
                                                            │
                                                            ▼
                                            TaskManager (the record)
                                                            │
                                                            ▼
                             RuleAgent.apply_task() → accepted or refused
                                                            │
                                                            ▼
                                    the ordinary agent → controller → physics
```

Three properties hold this apart from the flight path:

**A task is a request.** `apply_task()` returns a reason code whichever way it
goes, and a unit that cannot carry out an order keeps flying what it had. The
commander cannot make an aircraft do anything; it can only ask.

**A commander sees what the link delivered.** `TeamManager` builds its picture
from `datalink.tracks_for()` alone, so every position it reasons about is as
late, as lossy and as blackout-prone as any other message. It has no access to
`WorldState`. A commander that could read truth would be a second, privileged
observer, and coordination measured against it would mean nothing.

**Silence is not information.** Before the first report arrives — which latency
guarantees at t=0 — the commander trusts the plan rather than concluding its
leaders are gone. Absence of a report and evidence of a loss are different
things, and the code says which one it is looking at.

The flight controller remains the sole writer of entity controls. Adding a layer
of command above the agents did not change that, and a test asserts it.

## The physics is one of several

`Integrator` was declared in PHASE 1 with exactly one implementation, on the
argument that the engine should not know how motion is computed. PHASE 16 is
the test of that argument: JSBSim now advances the world through the same
protocol, and the engine did not change to allow it.

```
                      configs/simulation.yaml
                              physics.backend
                                    │
                                    ▼
                          build_integrator()
                  ┌────────────┬────┴───────┬────────────┐
                  ▼            ▼            ▼            ▼
           Simple6DOFModel  JSBSimAdapter  Kinematic    Null
                  └────────────┴────┬───────┴────────────┘
                                    ▼
                          Integrator.integrate(world, dt)
```

**A backend never quietly becomes a different one.** Requesting one that is not
installed raises, with the command that installs it. Falling back would leave
`config_hash` — the thing that makes a run reproducible — describing physics the
run never used.

**A backend with private state must follow truth, not lead it.** JSBSim holds
its own solution for each vehicle, so the adapter remembers what it last wrote
and re-initialises from the world whenever something else has written the truth
state. The one-way pipeline holds: a physics backend is downstream of truth in
exactly the same sense the scoring engine is.

**Both 6DOF backends fly the same fictional airframe.** The JSBSim aircraft
files are generated from `AircraftParameters`, not written by hand, so the two
models cannot drift apart into two different invented platforms. JSBSim's own
bundled aircraft are never on the search path.

## Charts are the far end of the same one-way pipeline

`analytics/series.py` takes rows a finished run wrote and returns series. It
holds no reference to the engine, and `test_analytics.py` asserts it does not
even import one:

```
Truth → … → World Update → Recording → Storage
                                          │
                                          ▼
                                 analytics/series.py
                                          │
                                          ▼
                              /api/analytics → charts
```

That is what makes a chart cheap to add. Changing how a run is drawn, or adding
a whole new measurement, carries no risk of changing how an aircraft flies —
the same property that let PHASE 9 rebalance scoring weights safely.

Two consequences worth stating:

**The aggregation is server-side.** A run holds thousands of telemetry samples
and up to five thousand decisions. A dashboard that downloaded them to group
them would make every chart a transfer, and two clients grouping the same rows
differently would disagree about the same run.

**Empty is a fact, not a chart.** A run that recorded nothing returns
`available: false` with the reason, never empty series. Drawing a flat line at
zero would be a claim about the run rather than an absence of data.

## A model is only meaningful against the environment that shaped it

A saved policy is weights plus an implicit contract: this observation vector,
this reward. Break either and nothing throws — the policy loads, acts, and
scores. `training/registry.py` exists to make that contract explicit and to
refuse the cases where it no longer holds.

```
model.zip + model.json ─┐
                        ├─> assess() ─> COMPATIBLE       → run and compare
current layout ─────────┤              DIFFERENT_REWARD  → run, do not compare
current reward ─────────┘              INCOMPATIBLE      → refuse
                                       UNKNOWN           → refuse
```

The distinction between the two refusable verdicts and the two runnable ones is
the design. `DIFFERENT_REWARD` runs because measuring a policy shaped by another
reward is how you learn what that reward produced. `INCOMPATIBLE` does not,
because its inputs no longer line up and its output would be noise wearing the
shape of a decision.

This is the same rule PHASE 9 applied to scores and PHASE 17 to run comparisons:
**numbers measured against different rulers do not go in one table without a
warning**. Here the ruler is the environment itself.

## Scenarios are written through one door

`scenario.py` reads and validates; `scenario_store.py` is the only module that
writes. Every write parses the document first — with the same parser the
simulation uses — so a file in the scenario directory always loads, and the
editor cannot save something the engine would then refuse.

Writes are atomic (temporary file, then rename), names are checked rather than
sanitised so one can never escape the directory, and serialisation is lossless:
`parse_scenario(s.to_document()) == s` is a test, because an editor built on a
lossy serialiser is a way to corrupt scenarios rather than to write them.

## Frontend

`frontend/src` mirrors the backend contract:

- `types/api.ts` — response types matching the API exactly.
- `api/client.ts` — typed fetch wrapper; network failure yields a clear error.
- `stores/systemStore.ts` — Zustand store of live backend state.
- `components/`, `pages/` — Command Center panels.
- `three/` — 3D scene (PHASE 8): `coordinates.ts` maps ENU to Three.js,
  `Aircraft.tsx` draws the abstract marker, `CameraRig.tsx` holds the camera
  modes, `TacticalGround.tsx` the grid, `Trail.tsx` the motion trails.
- `stores/replayStore.ts` — playback state (PHASE 9). The cursor itself lives on
  the server; this mirrors it and polls the current frame while playing.
- `stores/scenarioStore.ts` — the scenario editor's draft (PHASE 10). Held as
  the YAML document shape, not as a view model, so what the editor holds is
  exactly what gets written to disk.
- `stores/stageStore.ts` — which of the three sources is on stage.
- `hooks/useStage.ts` — the single place that decides whether the views draw
  live telemetry, a recording, or the scenario being edited. Without it the 3D
  view, the 2D plot and the entity list could disagree, and a replay frame drawn
  beside live positions would be a picture of a moment that never happened.

The dashboard renders only data the backend actually returned. When the backend is
unreachable the UI says so instead of showing stale or invented values.
