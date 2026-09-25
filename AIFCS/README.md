# AIFCS — AI Flight Command & Simulation Platform

A research, education and AI-training platform for **multi-agent flight simulation**.
Every aircraft, sensor, parameter and scenario in AIFCS is **fictional and abstract**
(`BLUE-01`, `RED-02`, …). See [Safety Scope](#safety-scope).

> **Current status: PHASE 13 complete.** A flight policy can now be **trained by
> reinforcement learning** against the real simulation — same 6DOF physics, same
> noisy sensors, same safety layer. `AIFCSCombatEnv` is a Gymnasium environment,
> PPO and SAC run through Stable-Baselines3, and every reward term is weighted
> from YAML and reported separately. Runs are recorded, stored and scored
> (PHASE 9), scenarios are editable from the dashboard (PHASE 10), and REPLAY
> mode plays runs back with per-term score breakdowns. Training itself is
> started from the command line; the dashboard shows what is trained and says
> so, rather than offering a button that cannot report progress or be cancelled.

---

## Table of contents

- [Two systems in one repository](#two-systems-in-one-repository)
- [Project overview](#project-overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Updating an existing copy](#updating-an-existing-copy)
- [The `aifcs` command](#the-aifcs-command)
- [Running AIFCS](#running-aifcs)
- [API](#api)
- [Configuration](#configuration)
- [Replay, scoring and run history](#replay-scoring-and-run-history)
- [Editing scenarios](#editing-scenarios)
- [Reinforcement learning](#reinforcement-learning)
- [Teams, tasks and the commander](#teams-tasks-and-the-commander)
- [Swapping the physics](#swapping-the-physics)
- [Analytics](#analytics)
- [The training centre](#the-training-centre)
- [The model centre](#the-model-centre)
- [Testing](#testing)
- [Docker](#docker)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Deploying it](#deploying-it)
- [Safety scope](#safety-scope)

---

## Two systems in one repository

This repository holds **two unrelated programs**. AIFCS is the one documented
here, in `AIFCS/`. The AGMCIS trading platform lives at the level above it.

They share a git repository and nothing else — separate virtualenvs, separate
databases, separate ports, separate processes. AIFCS listens on **8080** so it
cannot collide with the trading system's 8000, and `aifcs doctor` reports both.

Full detail, including why AIFCS should not run on the trading VPS:
[`docs/TWO-SYSTEMS.md`](docs/TWO-SYSTEMS.md).

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

### Updating an existing copy

One command. It refuses to run if you have uncommitted work, pulls the latest
code, installs whatever is new, checks the installation, and starts:

```bash
cd ~/Desktop/AGMCIS_APP/AIFCS && ./scripts/update.sh
```

On Windows, double-click **`scripts\update.bat`**.

Add `--no-start` to update and check without launching. If anything is broken it
stops at the `aifcs doctor` step and prints the `FAIL` lines rather than
starting something that will not work.

### The `aifcs` command

Everything the platform does without a browser, behind one name:

```bash
./scripts/aifcs doctor              # check the installation and say what is wrong
./scripts/aifcs serve               # run the backend
./scripts/aifcs run demo_alpha -s 60   # fly a scenario headless and report it
./scripts/aifcs scenarios           # what can be flown
./scripts/aifcs train --timesteps 20000
./scripts/aifcs models              # saved policies and their verdicts
./scripts/aifcs evaluate MODEL
./scripts/aifcs bench               # how fast the simulation runs here
```

**Run `aifcs doctor` first when anything is wrong.** It checks Python, every
dependency, the optional ones, whether the configs load, whether every scenario
parses, whether the database opens and whether the frontend is installed — and
tells you which of those failed, in about two seconds.

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
AIFCS_BACKEND_PORT=8081 AIFCS_FRONTEND_PORT=5174 ./scripts/start.sh
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

Interactive documentation: **http://127.0.0.1:8080/docs**

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
| `GET` | `/api/training/status` | Device, algorithms, and how to start a run |
| `GET` | `/api/training/environment` | The environment a policy is trained against |
| `GET` | `/api/training/reward` | Every reward term and its weight |
| `GET` | `/api/training/models` | Trained policies with their cards |
| `GET` | `/api/training/runs` | Recorded training runs |
| `GET` | `/api/scenarios` | Every scenario, with a reason for any that will not load |
| `GET` | `/api/scenarios/template` | A minimal valid scenario to start from |
| `GET` | `/api/scenarios/{name}` | One scenario, plus the document the editor edits |
| `GET` | `/api/scenarios/{name}/export` | The file's own text, comments included |
| `POST` | `/api/scenarios/validate` | Check a draft without saving it |
| `POST` | `/api/scenarios` | Create a scenario |
| `PUT` | `/api/scenarios/{name}` | Replace a scenario |
| `POST` | `/api/scenarios/{name}/clone` | Copy it under a new name |
| `POST` | `/api/scenarios/import` | Store pasted or uploaded YAML |
| `DELETE` | `/api/scenarios/{name}` | Delete a scenario |
| `GET` | `/api/teams` | Every team, its members and how much of it the link covers |
| `GET` | `/api/teams/{team}` | One team's picture, with the age of each report |
| `GET` | `/api/tasks` | Every task issued, its status and the reason behind it |
| `GET` | `/api/tasks/{entity_id}` | What one unit was told to do, and its history |
| `GET` | `/api/commanders` | Each commander, its standing order and what it allocated |
| `GET` | `/api/physics` | Which physics backend is configured, which is running, what else exists |
| `GET` | `/api/physics/airframes` | The fictional platforms both backends fly |
| `GET` | `/api/analytics/runs/{id}` | Every chart for one run, in the shape a chart draws |
| `GET` | `/api/analytics/compare` | Team and per-term scores for several runs side by side |
| `GET` | `/api/training/jobs` | The training job running now, and the ones this server has run |
| `GET` | `/api/training/jobs/{id}` | One job, with its progress curve |
| `POST` | `/api/training/start` | Start a training job in the background |
| `POST` | `/api/training/stop` | Stop it at the next step boundary, keeping what it trained |
| `GET` | `/api/models` | Every saved policy, each with a verdict on whether it still means anything |
| `GET` | `/api/models/compare` | Several policies side by side, and whether that comparison is valid |
| `GET` | `/api/models/{id}` | One policy, its card and its verdict |
| `POST` | `/api/models/{id}/evaluate` | Measure it over real episodes, as a background job |
| `POST` | `/api/models/{id}/archive` | Move it out of the active list, keeping the file |
| `POST` | `/api/models/{id}/restore` | Bring an archived policy back |
| `DELETE` | `/api/models/{id}` | Delete the policy and its card for good |

Endpoints for phases not yet built are absent rather than stubbed — this API
never answers for a capability the backend does not have.

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

### Analytics

Press **ANALYTICS** in the header. It is a fourth stage beside LIVE, REPLAY and
EDIT, and the only one with no tactical view — it is about *runs* rather than
about a run.

Pick a run on the left and you get, all measured from what that run stored:

| Chart | What it is |
|---|---|
| Altitude, Speed | one line per unit, against simulation time |
| Units flying | how many of each team were still active, from the sampled status |
| Units in formation | how many were coordinating, read from what the agents decided |
| Score | a heatmap of every unit against every scoring term |
| Behaviour | what share of its own decisions each unit spent in each behaviour |

Tick two or more runs and press **COMPARE** for their team scores side by side.
The comparison carries each run's scoring `weights_hash` and says plainly when
they differ — two runs judged by different rulers are not comparable totals.

**A run with nothing stored says so.** Recording can be switched off, and a run
shorter than a second was never sampled. Those charts print the reason instead
of drawing a flat line at zero, because an empty chart is a claim about the run.

**The aggregation is the backend's.** A run holds thousands of samples; the
dashboard would otherwise download them all to group them, and two clients could
disagree about the same run.

#### About the chart colours

They were not chosen by eye. Each set was checked against the panel surface for
lightness, chroma, colour-vision separation and contrast, and two results
changed the charts:

- **Six scoring terms are a heatmap, not six colours.** No six-hue set survives
  an all-pairs colour-vision check — blue and violet land ΔE 0.3 apart under
  deuteranopia however they are stepped. One hue light-to-dark also suits the
  question better: *where did this unit lose points* is magnitude, not identity.
- **Eight units are coloured by team, not one hue each,** with a direct label on
  the end of every line. Two hues are safe on every pair; eight are not.

### Swapping the physics

The engine never integrates motion itself — it delegates to an `Integrator`. Which
one is a configuration choice:

```yaml
# configs/simulation.yaml
physics:
  backend: simple_6dof    # or jsbsim, kinematic, null
```

| Backend | What it is |
|---|---|
| `simple_6dof` | This platform's own Newton-Euler model: quaternion attitude, RK4 at the fixed timestep, constant-density atmosphere from config. The default, and the only one with no extra dependency. |
| `jsbsim` | [JSBSim](https://github.com/JSBSim-Team/jsbsim), an established open-source flight dynamics model. Optional. |
| `kinematic` | Constant velocity. Real integration, but no forces at all — for isolating a problem from the aerodynamics. |
| `null` | Nothing moves. |

The Command Center's **Physics Backend** panel says which one is flying and
which ones are installed, so you never have to guess from the outside.

#### JSBSim

It is optional and the platform is complete without it. Install it with:

```bash
.venv/bin/pip install -r requirements-physics.txt
```

then set `backend: jsbsim` and restart the backend.

**No real aircraft is ever loaded.** JSBSim ships definitions for real airframes.
AIFCS points it at a data root under `data/jsbsim/` that it generates itself,
holding exactly the fictional airframes `backend/simulation/aircraft.py`
describes — same mass, same inertia, same geometry, same aerodynamic
coefficients. The two backends fly the *same invented platform*, so a difference
between them is a difference between the models. The generated files are
regenerated whenever those parameters change, and a test asserts the airframe
describes no weapon, store or targeting behaviour of any kind.

**Asking for it without it installed is an error, not a fallback.** You get the
install command, the dashboard marks the backend `NOT INSTALLED`, and the run
does not start. A run recorded under a model nobody chose is worse than a run
that refused to begin.

**The two backends do not agree, and should not.** The most visible difference
is the atmosphere: JSBSim models a standard one, so density falls with altitude,
while `simple_6dof` uses the constant `air_density_kgpm3`. At 6 km that is
0.66 against 1.225 kg/m³, and the same airframe at the same throttle will not
hold level flight under both. The agents fly closed-loop through the flight
controller and trim themselves — measured on `demo_alpha`, the route followers
settle back onto their assigned altitudes within about 100 s and hold them to
within a couple of metres.

**JSBSim is the faster of the two here**, which surprised us: 643 µs per tick
against 1024 for four aircraft, about 0.63×. Compiled C++ beats four NumPy
derivative evaluations per RK4 step in the interpreter.

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

### Teams, tasks and the commander

Each team has a **commander** that decides what its units should be doing. It has
no aircraft of its own, and no path to a control surface. Its whole output is a
task:

| Task | Meaning |
|---|---|
| `PATROL` | Fly your assigned circuit |
| `TRANSIT` | Fly to a point and hold there |
| `ESCORT` | Hold station on another unit |
| `HOLD` | Maintain what you have; no new objective |

Nothing here models weapons, engagement or targeting, and a test asserts the
list.

**A task is a request, not a write.** The unit's agent answers with a reason code
whichever way it goes — `ROUTE_AVAILABLE` and `LEADER_AVAILABLE` when it takes
the order, `NO_ROUTE` and `LEADER_UNKNOWN` when it cannot. A refused order leaves
the unit flying what it had. The Coordination panel shows both, so a team that is
not doing what it was told is visible rather than silent.

**A commander only knows what the link delivered.** Its picture of the team is
built from datalink reports alone, and every position it reasons about carries
the age of the report it came from. It cannot read the truth state. Cut the link
with a `blackout_windows` entry and the picture ages instead of updating — which
is the honest result, not a bug.

Run `team_eight` to see it: eight units in two teams of four, two two-ship
elements per team.

```
curl -X POST http://127.0.0.1:8080/api/simulation/start \
     -H 'Content-Type: application/json' -d '{"scenario":"team_eight"}'
curl http://127.0.0.1:8080/api/teams
curl http://127.0.0.1:8080/api/tasks
```

Disable a leader and the commander reallocates: within about two seconds its
wingman is on the leader's route, while the other element carries on escorting.
Set `commander_enabled: false` in `configs/agents.yaml` to fly the same scenario
with no commander at all.

**Eight agents cost eight times one agent** — measured, 3000 ticks per point:

| agents | µs / tick | ticks / s | × real time |
|-------:|----------:|----------:|------------:|
| 1 | 254 | 3934 | 65.6× |
| 2 | 500 | 2000 | 33.3× |
| 4 | 1023 | 977 | 16.3× |
| 8 | 2080 | 481 | 8.0× |

Linear, and eight agents still run at eight times real time. What will limit
larger runs is the datalink rather than the agents: each report is copied into
every teammate's inbox, so the copies grow with the size of a *team* (1.0, 3.9,
15.4 and 30.8 per report cycle for the rows above).

### Scenarios

Scenarios are YAML files in `scenarios/`. `demo_alpha` is the reference
scenario: four fictional units (`BLUE-01`, `BLUE-02`, `RED-01`, `RED-02`) on
converging transit tracks. `team_eight` exercises the commander at scale and
`training_navigation` is sized for RL episodes. Add a scenario by dropping a new
`.yaml` file beside them — it is validated on load, and a malformed file is
rejected with a clear error rather than silently producing a wrong run.

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

## Editing scenarios

Switch the Command Center header to **EDIT**. The right rail is the editor, the
centre is import/export, and the tactical view becomes a **preview of where the
units start** — so placing an aircraft is something you can see rather than
something you work out from three numbers.

### What the editor can do

| Action | Notes |
|---|---|
| New | Starts from a template the backend serves, so it is always valid |
| Edit | Name, description, duration, seed; per unit: id, team, agent, position, velocity, yaw, throttle, waypoints, formation leader and station offset |
| Save | Writes the YAML file, after the backend has validated it |
| Clone | Copies under a new name, renaming it inside the file too |
| Delete | With a confirmation step; the default scenario is protected |
| Import | Paste or upload YAML; validated before it is stored |
| Export | The file's own text, comments included, downloadable |

### Three refusals, and why each one is there

**A name is a filename, so it must be a plain identifier.** `../escape`,
`a/b`, `con` and anything with a space are refused rather than quietly
sanitised into a name you did not ask for. Without this, a name parameter is a
way to write anywhere on the machine.

**An invalid scenario is never written.** Every save parses the document first,
with the same parser the simulation uses, so a file in the directory always
loads. The editor validates as you type and shows the error against the
offending unit:

```
entity BLUE-02: formation leader 'GHOST-99' is not declared in this scenario
```

**The scenario a run is flying cannot be changed underneath it.** Overwriting or
deleting it returns `409` and names the reason. Stop the simulation first.

### Round-tripping

`parse(scenario.to_document())` must reproduce the scenario exactly, and a test
asserts it. Before PHASE 10 the serialiser dropped `orientation`, `health`,
`energy`, `fuel`, `route_loop` and `formation_offset` — harmless while nothing
wrote scenarios back, and silent data loss the moment something did. An editor
built on a lossy serialiser is a way to corrupt scenarios, not to write them.

Saved files keep coordinates on one line (`position: [-20000.0, 0.0, 6000.0]`)
so a scenario stays readable by hand after a round trip through the UI.

### Writing safely

Saves go to a temporary file in the same directory and are then renamed, which
is atomic: a crash or a full disk leaves the previous file intact rather than a
truncated one. The temporary file's permissions are corrected before the rename
— Python creates it at `0600`, and a scenario only its writer can read would
stop loading the moment the backend ran as a different user.

---

## Reinforcement learning

A flight policy can be trained against the simulation itself — not a reduced
model of it. The same fixed-timestep 6DOF physics, the same sensor model with
its noise and dropout, the same datalink, and the same safety layer between the
policy's output and the control surfaces. That costs speed and buys the only
thing that matters: a policy trained here has been trained against the system it
will actually fly.

**Nothing in this models weapons, engagement or targeting.** The task is flight
and navigation, and the reward has no term for anything else. A test asserts it.

### The training centre

Press **TRAINING** in the header. Pick an algorithm and a budget, press **START
TRAINING**, and watch the reward curve while it learns. **STOP** ends it at the
next step boundary.

Three things had to be true before the dashboard was allowed that button, and
`train.py` had said so since PHASE 13:

| | |
|---|---|
| **Progress** | timesteps and mean episode reward, reported by the trainer |
| **Cancellation** | stops cleanly, and **keeps the policy trained so far** |
| **Surviving a reload** | the job lives in the server, so refreshing rejoins it |

**What it refuses, and why.** Each refusal names its reason and the page prints
it:

- *A simulation is running* — both would contend for the same cores and
  neither's timings would mean anything. Stop the simulation first.
- *A job is already running* — one at a time. A silent queue would leave you
  watching someone else's progress bar.
- *Above the cap* — `max_timesteps_per_job` in `configs/training.yaml`, 500,000
  by default.

**A job does not survive a server restart.** It runs in the server process, so
it cannot. That is said on the panel rather than discovered: a run interrupted
by a restart is recorded as INTERRUPTED at the next startup instead of being
left looking like it is still going. A long run still belongs on the command
line, where it outlives the dashboard.

### The model centre

Below the training centre, in the same **TRAINING** stage. Every saved policy is
listed with a **verdict**, because a `.zip` on its own is not a usable policy —
it is weights that expect a particular observation vector, shaped by a
particular reward.

| Verdict | What it means |
|---|---|
| `COMPATIBLE` | Same observation layout, same reward. It means what it meant. |
| `OTHER REWARD` | Same layout, so it runs — but it was optimising something else, so its score is real and not comparable. |
| `INCOMPATIBLE` | The layout has moved on. **It cannot be evaluated.** |
| `UNKNOWN` | No card, so nothing can be said about it. |

**The refusal is the feature.** An incompatible policy loads cleanly, produces
actions and returns a score — from numbers that stopped meaning what they meant.
It would look exactly like a real measurement. So the evaluate button is
disabled for it and the API answers 409 with the reason.

Press the gauge icon to measure a policy over real episodes. That is slow —
three episodes took 76 seconds here — so it runs as a background job on the same
one-at-a-time runner training uses, and you watch it in the panel above. The
score is written onto the policy's card, so it survives a restart. A *cancelled*
evaluation is not: a mean over two of five episodes is a different measurement,
and filing it as the policy's score would misrepresent it ever after.

**Archiving is not deleting.** It moves the policy and its card into
`models/archive/`. Tick ARCHIVED to see them, and restore any of them. Delete is
separate and permanent.

Tick two or more and press **COMPARE**. The comparison says plainly when lining
them up means nothing — different layouts, different rewards, different
scenarios, or simply not all measured yet.

### Install the stack

The RL dependencies are large and optional, so they are kept out of the default
install. Everything else works without them.

```bash
.venv/bin/pip install -r requirements-ml.txt
```

### Train

```bash
cd backend
../.venv/bin/python train.py --algorithm ppo --timesteps 200000 --evaluate 5
../.venv/bin/python train.py --list          # what has been trained
```

Training is CLI-only for now. A long job needs progress reporting, cancellation
and survival across a page reload; a START button in the dashboard that cannot
do those things would be a control that does not do what it appears to. The
dashboard's Training panel shows the device, the environment, the reward and
every trained policy, and gives the command.

### The environment

| | |
|---|---|
| Id | `AIFCSCombatEnv-v0` |
| Observation | 46 bounded floats: 12 ego, 3 contacts x 9, 7 goal |
| Action | 4 channels in −1..1 — aileron, elevator, rudder, throttle |
| Step | One agent decision (6 physics ticks at 60 Hz / 10 Hz) |
| Task scenario | `training_navigation` |

One unit is flown by the policy; every other unit keeps its rule agent, so the
policy learns in traffic rather than in an empty sky.

The observation is what the **sensor model and datalink gave the agent** — noisy,
delayed, sometimes missing — never the truth state. Encoding truth would train a
policy that cannot fly once it meets the real perception pipeline. Every element
is normalised and clipped: an unbounded input is how a policy learns to exploit
one enormous number, and a NaN is how training dies twenty minutes in.

`training_navigation` exists because demo_alpha's first leg is 40 km — three
minutes at cruise — so a two-minute episode would end before the aircraft
reached a single waypoint and the navigation reward would never fire. Its legs
are about 5 km.

### The reward

Ten terms, each weighted from `configs/training.yaml` and reported separately,
so a reward is always explainable:

```json
{"total": 2.02, "terms": {"survival": 1.0, "navigation": 0.109, ...},
 "weighted": {"survival": 0.05, "navigation": 0.109, ...},
 "notes": {"goal_distance_m": 26772.5, "teammates_held": "1/1"}}
```

#### Why the per-step weights are small

The first trained policy scored **worse** than an untrained one: 704 against
782, and it reached no waypoints at all. Reading the term breakdown showed why.
Of ~780 total reward, survival contributed 400, coordination 200, information 85
and smoothness 78 — all nearly constant. Navigation, the only term the policy
could actually change, moved by ±40. **Five percent signal.**

Two changes fixed it, both in the measurement rather than the algorithm:

- **The terminal crash penalty was split out of the per-step survival term.**
  One term was doing two jobs: paying for each step alive, and punishing the
  loss of the aircraft. Rolling them together made a constant that paid every
  step regardless of behaviour.
- **The terms a policy cannot influence are now worth little per step, and the
  ones it can are worth a lot.** Survival 0.05, navigation 1.0, mission 5.0.

Retrained on the same seed and budget, navigation went from 36.6 to **241.7**,
and the policy beat both the untrained network and a hand-trimmed baseline:

| | reward | waypoints reached |
|---|---|---|
| Untrained network | 98.4 | 1.00 |
| Trimmed, no policy | 203.8 | 1.00 |
| **PPO, 60k steps** | **306.5** | 1.00 |

Also calibrated: `progress_scale_m` is derived from cruise speed x step
duration (about 22 m), not left at a round 200 m. At 200 m the navigation term
could never exceed 0.11 while survival paid 1.0.

### Saved policies

Every model is saved with a card naming the scenario, seed, hyperparameters,
observation layout version and reward weights. A `.zip` alone does not say which
environment shaped it, and a policy run against a different observation layout
is silently wrong rather than loudly broken — so loading one whose layout has
moved on logs a warning.

Training runs and models are recorded in the PHASE 9 database, so training
history is queryable the same way run history is.

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

**Success looks like:** `657 passed`, or `648 passed, 9 skipped` without the
optional JSBSim backend installed.

### End-to-end dashboard test

This one drives a real browser, so both servers must already be running.

```bash
cd frontend
npx playwright install chromium   # first time only
npm run test:e2e        # dashboard shell, status panel, error handling
npm run test:e2e:sim    # start / pause / step / reset drive the real engine
npm run test:e2e:3d     # 3D view renders, every camera mode works
npm run test:e2e:replay # record a run, then load, play, scrub and score it
npm run test:e2e:editor # build a scenario in the UI, save it, then fly it
npm run test:e2e:coordination  # eight units, two commanders, and no panel overlaps
npm run test:e2e:analytics     # record a run, then chart it
npm run test:e2e:training      # train a real policy from the browser
npm run test:e2e:models        # measure one, and refuse a stale one
```

Each suite sets up the scenario it asserts against, so they can be run in any
order against the same pair of servers.

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
- Backend API: http://localhost:8080/docs

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

**Port 8080 or 5173 already in use.**
On macOS and Linux, `start.sh` detects this and names the fix:
`AIFCS_BACKEND_PORT=8081 AIFCS_FRONTEND_PORT=5174 ./scripts/start.sh`
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
| 10 | Scenario editor | **Complete** |
| 11–13 | Gymnasium environment, PPO, SAC | **Complete** |
| 14–15 | Multi-agent, commander agent | **Complete** |
| 16 | JSBSim adapter (swappable physics backend) | **Complete** |
| 17 | Analytics: run charts and comparison | **Complete** |
| 18 | Training centre: start, watch and cancel a job | **Complete** |
| 19 | Model centre: verdicts, evaluation, comparison, archive | **Complete** |
| 20 | Production hardening: CLI, error contract, security review | **Complete** |

Detail: [`docs/PHASES.md`](docs/PHASES.md).

---

## Deploying it

Full detail in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). The part that matters
most:

> **There is no authentication.** Not a weak scheme — none. Anyone who can reach
> the backend can start and stop simulations, edit and delete scenarios, start
> training jobs that consume the machine, and delete recordings and saved
> policies.

That is the right call for a tool on a researcher's laptop, where a login would
be friction protecting nothing, and the wrong one the moment the port is
reachable by anyone else. The default bind address is `127.0.0.1` for exactly
this reason. Sharing it means putting an authenticating proxy in front.

Two more properties worth knowing before you deploy: a training job started
through the API will use the machine (there are ceilings in
`configs/training.yaml`, and one job at a time), and saved policies are pickles,
so anything dropped into `models/` executes when evaluated.

Every response carries an `X-AIFCS-Request-Id`; a 500 names that id and
deliberately does *not* include the exception text, so the operator gets a
handle and the log keeps the detail.

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
