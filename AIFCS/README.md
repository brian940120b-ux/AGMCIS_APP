# AIFCS — AI Flight Command & Simulation Platform

A research, education and AI-training platform for **multi-agent flight simulation**.
Every aircraft, sensor, parameter and scenario in AIFCS is **fictional and abstract**
(`BLUE-01`, `RED-02`, …). See [Safety Scope](#safety-scope).

> **Current status: PHASE 9 complete.** Every run is now **recorded, stored and
> scored**. Press STOP and the run is written to a replay file, its decisions and
> events land in SQLite, and an independent scoring engine grades it on six
> weighted flight-quality terms — each one reporting the measurement behind it.
> The Command Center has a REPLAY mode with real transport controls (play, pause,
> frame step, scrub, speed, jump-to-event) and a run history with per-term score
> breakdowns. Training is **not implemented yet**; the dashboard reports it as
> `NOT_IMPLEMENTED` rather than faking it.

---

## Table of contents

- [Project overview](#project-overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Running AIFCS](#running-aifcs)
- [API](#api)
- [Configuration](#configuration)
- [Replay, scoring and run history](#replay-scoring-and-run-history)
- [Testing](#testing)
- [Docker](#docker)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Safety scope](#safety-scope)

---

## Project overview

AIFCS closes this loop:

```
Truth State → Sensor → Observation → AI Agent → Decision → Action
   → Flight Controller → Physics → World Update → Scoring → Replay
   → Training Data → Improved Model → (next simulation)
```

The central rule: **an AI agent never writes to the truth state.** Agents read an
`Observation` (partial, noisy, delayed) and emit an `Action`. Everything else is the
simulator's job.

## Architecture

```
┌─────────────────────┐
│  AIFCS Dashboard    │  React + TypeScript + Three.js
└──────────┬──────────┘
           │ REST + WebSocket
┌──────────▼──────────┐
│ Simulation Manager  │  fixed 60 Hz clock, deterministic
└──────────┬──────────┘
     ┌─────┴─────┬───────────┐
     ▼           ▼           ▼
World Model  Sensor Model  Comm Model
     └─────┬─────┴───────────┘
           ▼
      Observation ──► Agent Manager ──► Action Space
                      (Commander / Pilot / Planner)
                                        │
                              Flight Controller
                                        │
                                 Physics Engine
                                        │
                                  World Update
                       ┌────────────────┼────────────────┐
                       ▼                ▼                ▼
                    Scoring          Replay           Logging
                       └────────────────┼────────────────┘
                                        ▼
                                Training Pipeline
```

Layer separation is deliberate:

| Concern | Rate | Owner |
|---|---|---|
| Physics / simulation | 60 Hz fixed timestep | `backend/core`, `backend/simulation` |
| Agent decisions | 10 Hz (configurable) | `backend/agents` |
| Telemetry broadcast | 20 Hz (configurable) | `backend/api` (WebSocket) |
| UI render | 30–60 FPS | `frontend/` |

The React layer **never** drives the physics loop.

### Repository layout

```
AIFCS/
├── backend/          FastAPI service + simulation core
│   ├── main.py           application factory, subsystem registry
│   ├── api/              REST + WebSocket routers
│   ├── core/             config, logging, clock, event bus, world state
│   ├── simulation/       aircraft, physics, sensors, comms, scenarios
│   ├── agents/           rule / behaviour-tree / RL / commander agents
│   ├── controllers/      action validation, command mapping, autopilot
│   ├── training/         Gymnasium env, rewards, PPO/SAC pipelines
│   ├── scoring/          score engine and metrics
│   ├── replay/           recorder, player, timeline
│   ├── storage/          SQLite models and access
│   └── tests/            unit + API tests
├── frontend/         React + TypeScript + Vite dashboard
├── configs/          YAML configuration (no tunables in code)
├── scenarios/        scenario definitions
├── models/           trained model artefacts
├── data/             replay / telemetry / training output
├── docker/           container build files
├── docs/             architecture and phase documentation
└── scripts/          setup, dev and check helpers
```

---

## Installation

**Prerequisites:** Python 3.11+, Node.js 20+, and (optionally) Docker.

> **Windows — use the native launcher.** Double-click **`scripts\start.bat`**.
> It needs nothing but Python and Node.js: it creates the virtual environment,
> installs both dependency sets on the first run, starts the backend and the
> dashboard, and opens your browser. Press `Ctrl + C` in its window to stop.
> If something is left holding a port, run `scripts\stop.bat`.
> The `.sh` scripts below are for macOS and Linux; they also work on Windows
> through [Git Bash](https://git-scm.com/download/win) if you prefer a shell.
>
> The Windows launcher is **not yet verified on a real Windows machine** — it
> was written against the documented behaviour but never executed there. If it
> fails, the error text is what to send back.

### Step 1 — Open a terminal and go to the project

```bash
cd AIFCS
```

### Step 2 — Run the setup script

```bash
./scripts/setup.sh
```

This creates the Python virtual environment in `.venv`, installs backend
dependencies, and installs frontend packages.

**Success looks like:** the last lines print `Setup complete.`

> You can skip this step entirely — `./scripts/start.sh` runs the same install
> automatically the first time it cannot find the dependencies.

<details>
<summary>Manual setup (if you prefer to run each step yourself)</summary>

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cd frontend && npm install && cd ..
```
</details>

---

## Running AIFCS

### The short way — one command

```bash
cd AIFCS
./scripts/start.sh
```

This checks your tools, installs anything missing on the first run, starts both
the backend and the dashboard, and opens your browser.

**Success looks like:**

```
================================================================
  AIFCS is running.  AIFCS 已啟動。

  Open this in your browser:
      http://localhost:5173
================================================================
```

**Leave that terminal open.** It is not a completion notice — it is the running
program. Closing the window stops AIFCS, and the browser will then say the
connection was refused.

Press `Ctrl + C` in that terminal to stop everything. It shuts down both
servers and releases the ports.

> The banner prints `http://127.0.0.1:5173` rather than `http://localhost:5173`
> on purpose: on Windows `localhost` can resolve to the IPv6 loopback, which the
> dev server does not listen on. If a link to `localhost` ever fails, try the
> numeric address.

If a port is already taken, the script says so and tells you how to change it:

```bash
AIFCS_BACKEND_PORT=8001 AIFCS_FRONTEND_PORT=5174 ./scripts/start.sh
```

### The manual way — two terminals

Use this when you want to watch the backend and dashboard logs separately.

**Terminal 1 — backend**

```bash
cd AIFCS
./scripts/dev_backend.sh
```

Success looks like `Application startup complete.` Leave it running.

**Terminal 2 — dashboard**

```bash
cd AIFCS
./scripts/dev_frontend.sh
```

Success looks like `Local: http://localhost:5173/`.

### Using the Command Center

Open **http://localhost:5173**. The boot screen lists each subsystem, then
**ENTER COMMAND CENTER** becomes clickable.

Inside, the transport controls drive the real engine:

| Control | What actually happens |
|---|---|
| **START** | Loads the scenario and runs the engine at 60 Hz |
| **PAUSE** | Stops the simulation clock — the tick counter genuinely freezes |
| **RESUME** | Continues from the exact tick where it paused |
| **STEP 1s** | Advances exactly 60 ticks, for frame-by-frame inspection |
| **RESET** | Rebuilds the world from the scenario, back to tick 0 |
| **Speed** | Changes wall-clock pacing only — the result is identical at 1x and 50x |

## API

Interactive documentation: **http://127.0.0.1:8000/docs**

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness, version, uptime, config hash |
| `GET` | `/api/system/status` | State of every subsystem |
| `GET` | `/api/system/compute` | CPU/GPU device detection |
| `GET` | `/api/config` | Resolved runtime configuration |
| `GET` | `/api/simulation/status` | Clock, scenario, seed, entity count, state hash |
| `POST` | `/api/simulation/start` | Load a scenario and run the engine loop |
| `POST` | `/api/simulation/pause` | Stop the simulation clock |
| `POST` | `/api/simulation/resume` | Continue from the paused tick |
| `POST` | `/api/simulation/stop` | Halt the loop, keeping the world |
| `POST` | `/api/simulation/reset` | Rebuild the world from the scenario |
| `POST` | `/api/simulation/step` | Advance an exact number of ticks |
| `POST` | `/api/simulation/speed` | Change wall-clock pacing (never the timestep) |
| `GET` | `/api/world/state` | Full truth state |
| `GET` | `/api/entities` | Every fictional unit with derived altitude/speed/heading |
| `GET` | `/api/events` | Recent system events |
| `GET` | `/api/scenarios` | Available and loaded scenarios |
| `GET` | `/api/agents` | Every agent with its latest decision |
| `GET` | `/api/agents/{id}` | One agent and its recent decisions |
| `GET` | `/api/decisions` | Decision log with reason codes and evidence |
| `GET` | `/api/controller` | Safety layer: commands applied, rejected, corrected |
| `GET` | `/api/sensors` | Sensor limits and tracks held per unit |
| `GET` | `/api/communications` | Datalink config, traffic stats, blackout state |
| `GET` | `/api/telemetry` | Broadcaster rate, connected clients, frames sent |
| `WS` | `/ws/simulation` | Live telemetry stream (see below) |
| `GET` | `/api/replay/recordings` | Every recording on disk, newest first |
| `POST` | `/api/replay/load` | Open a recording for playback |
| `POST` | `/api/replay/unload` | Close it |
| `GET` | `/api/replay/status` | Where the playback cursor is |
| `GET` | `/api/replay/frame` | The frame the cursor is on |
| `POST` | `/api/replay/play` | Start advancing the cursor |
| `POST` | `/api/replay/pause` | Hold position |
| `POST` | `/api/replay/step` | Move by a number of frames, forwards or back |
| `POST` | `/api/replay/seek` | Seek by frame, tick or simulation time |
| `POST` | `/api/replay/speed` | Playback multiplier from the allowed list |
| `POST` | `/api/replay/jump` | Jump to the next or previous notable event |
| `GET` | `/api/replay/events` | Notable events, for timeline markers |
| `GET` | `/api/runs` | Run history, newest first, with team scores |
| `GET` | `/api/runs/current` | What the run in progress is recording |
| `GET` | `/api/runs/{id}` | One run: entities, replay, scores, metrics |
| `GET` | `/api/runs/{id}/decisions` | Stored decisions with reason codes |
| `GET` | `/api/runs/{id}/events` | Stored events |
| `GET` | `/api/runs/{id}/telemetry` | One-per-second samples, for charting |
| `POST` | `/api/runs/{id}/score` | Recompute the score from the recording |
| `DELETE` | `/api/runs/{id}` | Delete a run, its rows and its recording |
| `GET` | `/api/scoring/weights` | The standard runs are judged against |

Training endpoints arrive in their respective phases and are documented as they
land.

### Flight model

Motion comes from Newton-Euler rigid-body dynamics, not a kinematic
approximation:

| Aspect | Implementation |
|---|---|
| Forces | Gravity, thrust, lift, drag (parasite + induced), side force |
| Moments | Control power, static stability, rotary damping, dihedral effect |
| Attitude | Quaternion — valid through vertical manoeuvres, no gimbal lock |
| Integration | Fixed-step RK4 at the simulation timestep |
| Stall | Lift coefficient saturates at `cl_max` |

The physics runs in the standard aerospace frames (body FRD, navigation NED) and
converts at the boundary to the platform's ENU world frame, so published
aerodynamic coefficient signs mean what they say.

Control inputs are **demands**, not surface deflections: `elevator > 0` pitches
the nose up, `aileron > 0` rolls right, `rudder > 0` yaws right, `throttle` runs
0 to 1. An agent does not need to know a sign convention.

All airframe parameters are fictional and live in
`backend/simulation/aircraft.py`. They are sized so the platform is stable and
flyable: full elevator commands roughly 19° angle of attack, full aileron rolls
at about 200°/s, and `demo_alpha` is trimmed to fly level hands-off at 220 m/s.

### Agents

Each unit can be flown by a rule-based pilot. An agent runs a fixed cycle —
`observe → think → act` — and picks a behaviour by priority:

| Priority | Behaviour | Trigger |
|---|---|---|
| 1 | `AVOID` | Another unit inside the collision radius |
| 2 | `FORMATION` | A leader is assigned; hold station on it |
| 3 | `PATROL` | A multi-waypoint route is assigned |
| 4 | `NAVIGATE` | A single waypoint is assigned |
| 5 | `HOLD` | Nothing assigned; maintain the current track |

Agents decide at `agents.decision_rate_hz` (10 Hz), independently of the 60 Hz
physics. The interval is counted in **ticks**, never wall time, so the decision
schedule is part of the deterministic run.

An agent receives an `Observation` and returns an `Action`. It never holds a
reference to the truth state, and the manager writes only to the entity's
control demands — never to position, velocity or attitude.

**Explainability.** Every decision is stored with its observation summary,
confidence and reason codes (`WAYPOINT_ACTIVE`, `FORMATION_SEPARATION_HIGH`,
`COLLISION_RISK`, `ALTITUDE_BELOW_TARGET` …). A reason code is emitted only when
the condition it names was computed and met, so `GET /api/decisions` can always
be checked against the numbers that produced it. Nothing in that feed is
generated for display.

### Perception

This is the rule the platform is built around: **an agent never reads the truth
state.** It receives an `Observation`, and since PHASE 5 that observation is an
estimate produced by the sensor model:

| Degradation | What it means |
|---|---|
| Range limit | Beyond it a contact is simply absent — the agent is not told something is out there but unseen |
| Field of regard | Angular coverage from the nose. All-round by default; narrow it to study partial observability |
| Latency | Contacts are reported where they **were**, not where they are |
| Dropout | A detection can be missed; a known contact is then *coasted* from its last fix, confidence decaying, until track memory expires |
| Noise | Gaussian error that grows with range, because angular error projects into larger cross-range error with distance |
| Ownship error | An aircraft does not know its own state exactly either |

`confidence` is **derived** from range, staleness and whether the track was
measured or coasted — never asserted. `measured` distinguishes a fresh detection
from a coasted one.

All randomness comes from a generator seeded with the run seed and consumed in
sorted entity order, so the same seed reproduces the same dropouts and the same
noise. Set `sensors.enabled: false` for a perfect-information baseline to
compare against.

The seam is one function. Agents were written against `Observation` in PHASE 3
and did not change at all when perception was degraded in PHASE 5.

### 3D tactical view

The centre stage is a Three.js scene driven entirely by the telemetry stream —
the browser draws, it does not simulate. Every position on screen was reported
by the engine.

| Camera | What it gives you |
|---|---|
| **Orbit** | Free camera framed on the units; drag to rotate, scroll to zoom |
| **Follow** | Chase camera locked to one unit, along its velocity |
| **Top** | Plan view, for geometry and separation |
| **Side** | Altitude profile |

Every mode still allows manual rotation — a preset is a starting point, not a
cage. The top-down 2D plot is one click away and stays useful for reading exact
separations.

Units are **abstract delta markers**, drawn as tactical symbols rather than to
scale (a 20 m airframe is sub-pixel across a 40 km engagement). They are not
modelled on any real aircraft, and there is no weapon or targeting
representation anywhere in the view.

Coordinates map ENU → Three.js uniformly, with no vertical exaggeration: a
research plot that distorts geometry is worse than one that is harder to read.
Altitude stalks down to the datum make height unambiguous instead.

Three.js is loaded on demand, so the initial bundle stays around 237 kB for
anyone who never opens the 3D view.

### Live telemetry

The dashboard subscribes to `/ws/simulation` and the server pushes frames at
`telemetry.broadcast_rate_hz`. Three clocks run independently, on purpose:

| Clock | Rate | Determines |
|---|---|---|
| Physics | fixed 60 Hz | the result — and nothing else does |
| Telemetry | 20 Hz, configurable | how smooth the display looks |
| Render | the browser's business | nothing about the simulation |

Each connection carries its own cursor over the event and decision streams, so a
client that connects mid-run receives what happens from then on rather than a
replay of the backlog, and two dashboards never interfere with each other. Only
new events and decisions go in each frame; entities and clock go in full because
they are small.

A slow or dead client is dropped rather than allowed to hold up the run.

The header badge shows which transport is live: **LIVE · N FRAMES** when the
socket is up, **POLLING** when it has fallen back. The dashboard never quietly
shows stale data.

### Datalink

The communication model carries messages between simulated units and models how
a link degrades: latency with jitter (which is what reorders messages), packet
loss, per-sender bandwidth limits, and blackout windows. It is a **simulation
transport only** — it does no real networking and implements nothing that scans,
manipulates or interferes with anything.

Each unit broadcasts its own position estimate at `report_rate_hz`. Teammates
fold those reports into their picture as `DATALINK` contacts. A contact the
aircraft measured itself always wins over a relayed one; the datalink fills the
gaps the sensor cannot see.

A relayed track is never better than a measurement: it carries the sender's own
imperfect estimate of itself, it is as stale as the link is slow, and it expires
when reports stop arriving.

**The coupling is the interesting part.** Measured on `demo_alpha`, wingman
state after 150 simulation seconds:

| Sensor coverage | Datalink | Result |
|---|---|---|
| All-round | on | `FORMATION`, station error 101 m |
| ±100° | **off** | `HOLD` — the leader is lost and formation collapses |
| ±100° | on | `FORMATION`, station error 10 m |

The default is ±120°, so the demo depends on the link. Set
`communications.enabled: false` in `configs/simulation.yaml` to watch formation
fall apart, or add a `blackout_windows` entry to cut the link mid-run.

### Safety layer

Nothing writes an aircraft's controls except the flight controller. That single
door is what makes the guarantees checkable. Every command — from a rule agent,
an RL policy or an API call — takes the same path:

```
Agent Action → Action Validation → Envelope Protection → Command Mapper → Physics
```

| Stage | What it enforces |
|---|---|
| Validation | Rejects a command when the aircraft state is non-finite; clamps NaN/Inf or out-of-range channels to neutral or to their bounds |
| Envelope protection | Eases back pitch demand above the structural g limit; blocks descent near the altitude floor and climb near the ceiling |
| Command mapper | Limits how fast a surface may slew, so a noisy policy cannot chatter the controls between ticks |

Corrections are counted and reported, never applied silently — `GET
/api/controller` and the dashboard's Safety Layer panel show exactly what the
layer did. Limits live in `configs/agents.yaml` under `safety`.

The controller runs every physics tick while agents decide at 10 Hz, so a
surface moves smoothly between two commands instead of stepping once per
decision.

### Performance

Measured on the development machine with `demo_alpha` (4 aircraft, 6DOF + RK4 +
agents):

| Metric | Value |
|---|---|
| Throughput | ~920 ticks/s |
| Real-time factor | ~15x |

The dashboard shows the **achieved** factor next to the tick counter, and turns
it amber when the host cannot sustain the requested speed. Selecting 50x on a
machine that can only manage 15x runs at 15x and says so — the result is
identical either way, because speed changes pacing and never the timestep.

### Scenarios

Scenarios are YAML files in `scenarios/`. `demo_alpha` is the reference
scenario: four fictional units (`BLUE-01`, `BLUE-02`, `RED-01`, `RED-02`) on
converging transit tracks. Add a scenario by dropping a new `.yaml` file beside
it — it is validated on load, and a malformed file is rejected with a clear
error rather than silently producing a wrong run.

---

## Configuration

Nothing is tuned in code. All parameters live in `configs/`:

| File | Contents |
|---|---|
| `simulation.yaml` | Tick rate, speeds, determinism, world bounds, telemetry, logging |
| `agents.yaml` | Agent type, decision rate, action validation |
| `scenarios.yaml` | Scenario directory and validation |
| `analysis.yaml` | Recording, scoring weights and thresholds, database |
| `training.yaml` | Device, PPO/SAC hyperparameters, reward weights |

Point the backend at a different directory with `AIFCS_CONFIG_DIR=/path/to/configs`.

### Determinism

`simulation.deterministic: true` plus a fixed `seed`, fixed timestep, scenario
version and `config_hash` make a run reproducible. The config hash is shown in
the dashboard footer and returned by `/api/health`.

The guarantee is enforced by tests: the same seed produces an identical
`state_hash`, stepping 600 x 1 tick equals 1 x 600 ticks, and running at 0.25x
produces exactly the same trajectory as 50x. **Speed changes pacing, never the
timestep** — that is what makes a fast run and a slow run comparable.

---

## Replay, scoring and run history

Every run is recorded, stored and scored. Nothing in this layer can reach the
truth state: the recorder observes, the scoring engine reads a finished file,
and the database only stores. The determinism test asserts it directly — a
recorded run and an unrecorded one produce the same `state_hash`.

### What happens when you press STOP

```
run  ──►  data/replay/<run-id>.jsonl.gz     every frame, event and decision
     ──►  data/aifcs.db                     queryable history
     ──►  score                             six weighted terms, per unit
```

The run id is the UTC timestamp plus four random characters
(`20260919-034203-6a94`), so recordings sort chronologically by name.

### The recording format

JSON Lines, gzipped. One object per line: a `header` carrying everything needed
to reproduce the run (scenario, seed, config hash, tick rate), then a `frame`
per sample, then an `end` record with the final state hash.

JSON Lines rather than one big document for two reasons: a run that crashes
still leaves a readable file up to the last flush, and the player can stream
frames without holding the whole run in memory. A recording renamed by hand
still opens — gzip is detected by content, not by extension.

Frames are written at `record_rate_hz` (20 Hz), not at the 60 Hz physics tick.
Playback is for watching and analysing, and recording every tick would triple
the file for nothing.

### Replay transport

Switch the Command Center header to **REPLAY** and pick a recording. The cursor
lives on the server, so the 3D view, the 2D plot and the entity list always
agree about which moment is on screen.

| Control | What it does |
|---|---|
| Play / Pause | Advances the real cursor at the recorded rate |
| Frame step | One recorded frame forward or back |
| Scrubber | Seeks to that frame; reports where the player actually is |
| Speed | 0.25x to 10x, from the allowed list |
| Event jump | Next / previous notable event, skipping the quiet stretches |

Seeking clears the motion trails: the frames they were built from are no longer
the ones leading up to the cursor, and a stale trail would draw something that
did not happen.

### Scoring

Scores live **outside** the simulation engine. Nothing here can be reached from
a tick, so a score can be recomputed with different weights without any risk of
changing how an aircraft flies. `POST /api/runs/<id>/score` rebuilds a score
from the recording under the current weights.

Six terms, each a fraction in 0..1 times a weight from
`configs/analysis.yaml`:

| Term | Points | What it measures |
|---|---|---|
| survival | 30 | Active through the run, and still active at the end |
| navigation | 25 | Course and altitude tracking, plus route progress |
| formation | 15 | Time held in station on its leader |
| safety | 15 | Commands rejected, envelope interventions, boundary events |
| efficiency | 10 | Control smoothness rather than thrashing |
| information | 5 | Confidence and freshness of the picture it actually held |

**There is no weapon, engagement or targeting term.** AIFCS models none, and
scoring one would imply a capability the platform does not have and must not
acquire.

Every term reports the measurement behind it, so a score can always be
explained:

```json
{ "name": "formation", "fraction": 0.568, "weight": 15.0, "points": 8.52,
  "detail": { "in_station_fraction": 0.6247, "median_station_error_m": 169.55,
              "formation_tolerance_m": 300.0, "samples_scored": 1500 } }
```

**A term that does not apply is redistributed, not forfeited.** A leader has no
leader of its own, so formation cannot apply to it; its 15 points are spread
across the terms that do apply, and the breakdown shows the term marked
inapplicable rather than hiding the adjustment inside a total. The same holds
for route progress on a wingman, which is never given waypoints.

Two scores are only comparable when `weights_hash` matches. Change a weight or
a threshold and every earlier score was measured against a different ruler,
which is why the hash is stored with the score and shown in the UI.

#### Two things this got wrong first, and how

Both were found by running the reference scenario and reading the numbers,
which is why the thresholds are shaped the way they are:

- **Envelope clamps were counted as safety failures.** `ACTION_REJECTED` covers
  both a command the controller refused and a demand the protection eased back.
  The second is the safety layer working, and it fires *every control tick* an
  aggressive manoeuvre lasts — so a clean 1.5-second waypoint turn produced 45
  "violations" and scored zero. They are now separate measurements, and
  interventions are scored as a fraction of the run rather than counted.
- **Formation was scored over the whole run.** A wingman spawns 1.7 km from
  station and takes about 90 seconds to close. Averaging that in measured the
  join-up, not the station keeping. Only the settled tail of the run is scored.

### The database

SQLite through the standard library — one file, no server, and it travels with
the project directory. `data/aifcs.db`, schema version 1:

`scenarios`, `runs`, `run_entities`, `events`, `decisions`,
`telemetry_samples`, `replays`, `scores`, `metrics`, and `training_runs` /
`models` declared now so the schema is stable from PHASE 9 on.

Deleting a run cascades to everything hanging off it. Decisions are capped per
run (`max_decisions_per_run`) because they are by far the highest-volume row
type; the replay file always holds the complete stream, which is what makes
capping acceptable at all.

---

## Testing

Run every gate — lint, format, types, backend tests, frontend lint and types:

```bash
./scripts/check.sh
```

Backend tests only:

```bash
cd backend && ../.venv/bin/python -m pytest
```

**Success looks like:** `306 passed`.

### End-to-end dashboard test

This one drives a real browser, so both servers must already be running.

```bash
cd frontend
npx playwright install chromium   # first time only
npm run test:e2e        # dashboard shell, status panel, error handling
npm run test:e2e:sim    # start / pause / step / reset drive the real engine
npm run test:e2e:3d     # 3D view renders, every camera mode works
npm run test:e2e:replay # record a run, then load, play, scrub and score it
```

On a headless machine without a GPU, run the 3D suite with a software renderer:

```bash
PLAYWRIGHT_GL=swiftshader npm run test:e2e:3d
```

**Success looks like:**
`E2E PASSED — dashboard renders live backend data on desktop and mobile.`
`SIMULATION E2E PASSED — start, pause, step and reset all drive the real engine.`

It checks that the boot screen lists real subsystems, that the Command Center
shows live configuration, that the mobile tab layout works, and that a stopped
backend produces a clear error rather than a fake dashboard.

---

## Docker

```bash
docker compose up --build
```

- Dashboard: http://localhost:3000
- Backend API: http://localhost:8000/docs

The database is SQLite stored in the `aifcs-data` volume, so it needs no separate
container.

---

## Troubleshooting

**The dashboard says "Cannot reach the AIFCS backend".**
The backend is not running. Start it in a second terminal with
`./scripts/dev_backend.sh` and confirm `Application startup complete.` appears.

**`./scripts/setup.sh: Permission denied`.**
Run `chmod +x scripts/*.sh` once, then retry.

**Windows: `python3: command not found` or `bash: ./scripts/start.sh: No such file`.**
You are running a `.sh` script from CMD or PowerShell. Use `scripts\start.bat`
instead, or open **Git Bash** (right-click in the project folder → "Open Git
Bash here").

**Windows: `start.bat` says Python is missing, but Python is installed.**
Windows ships a placeholder `python` command that opens the Microsoft Store.
`start.bat` ignores it on purpose, because it is not a real interpreter. Install
Python from [python.org](https://www.python.org/downloads/windows/) with **"Add
python.exe to PATH"** ticked, then close and reopen the window.

**Port 8000 or 5173 already in use.**
On macOS and Linux, `start.sh` detects this and names the fix:
`AIFCS_BACKEND_PORT=8001 AIFCS_FRONTEND_PORT=5174 ./scripts/start.sh`
On Windows, run `scripts\stop.bat` to clear whatever is holding the ports.

**The dashboard is still running after I closed the terminal.**
Press `Ctrl + C` in the terminal running the launcher rather than closing the
window — it stops both servers and releases the ports on interrupt. On Windows,
`scripts\stop.bat` clears anything that survived.

**The browser says the connection was refused / 無法連線.**
Nothing is listening on the port. Either the launcher was never started, or its
terminal window was closed — that window *is* AIFCS, not a notice that it
finished. Start it again and leave the window open. If it then reports the port
is in use, a process survived the last run: `scripts\stop.bat` on Windows, or
`kill $(lsof -ti :5173)` elsewhere.

**`No virtualenv found. Run: scripts/setup.sh`.**
`.venv` is missing — run the setup script first.

**Compute panel shows "PyTorch not installed".**
Expected before PHASE 11. AIFCS runs on CPU without PyTorch; install the RL stack
with `.venv/bin/pip install -r requirements-ml.txt` when you reach that phase.

---

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 0 | Project setup, config, logging, health API, dashboard shell, Docker | **Complete** |
| 1 | Simulation core: clock, world state, event bus, engine, scenarios | **Complete** |
| 2 | 6DOF flight physics, fictional aircraft model | **Complete** |
| 3 | Rule-based agent, guidance, decision records | **Complete** |
| 4 | Flight controller, action validation, safety layer | **Complete** |
| 5 | Sensor model: partial observation, noise, delay, dropout | **Complete** |
| 6 | Communication model: datalink, latency, loss, blackout | **Complete** |
| 7 | WebSocket telemetry | **Complete** |
| 8 | 3D Command Center (Three.js) | **Complete** |
| 9 | Replay, scoring, database | **Complete** |
| 10 | Scenario editor | Planned |
| 11–13 | Gymnasium environment, PPO, SAC | Planned |
| 14–15 | Multi-agent, commander agent | Planned |
| 16 | JSBSim adapter (swappable physics backend) | Planned |
| 17–19 | Analytics, training centre, model centre | Planned |
| 20 | Production hardening | Planned |

Detail: [`docs/PHASES.md`](docs/PHASES.md).

---

## Safety scope

AIFCS is built for **simulation research, education and AI training only**.

- All entities, platforms, sensors and parameters are **fictional and abstract**.
  No real aircraft, weapon, radar or targeting data is used or modelled.
- It contains **no** real-world weapon control, targeting, guidance or operational
  mission-planning capability, and none will be added.
- The networking layer carries simulation telemetry between local processes. It
  implements **no** scanning, packet manipulation, jamming or interference
  capability.
- Agent actions are limited to abstract flight-control and coordination commands
  (`aileron`, `elevator`, `rudder`, `throttle`, and high-level tasks such as
  `PATROL`, `REGROUP`, `HOLD`).

Reference material on simulation architecture (6DOF modelling, fixed-rate clocks,
UDP telemetry patterns) informs the **platform's structure only**.
