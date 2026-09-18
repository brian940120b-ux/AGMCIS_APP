# AIFCS — AI Flight Command & Simulation Platform

A research, education and AI-training platform for **multi-agent flight simulation**.
Every aircraft, sensor, parameter and scenario in AIFCS is **fictional and abstract**
(`BLUE-01`, `RED-02`, …). See [Safety Scope](#safety-scope).

> **Current status: PHASE 1 complete.** The simulation engine runs: a fixed 60 Hz
> deterministic clock, world state, event bus, scenario loading and full transport
> control (start / pause / resume / step / speed / reset) from the dashboard.
> Motion is currently **kinematic only** — 6DOF dynamics arrive in PHASE 2. Agents,
> sensors, communications, WebSocket, replay, scoring and training are **not
> implemented yet**; the dashboard reports each as `NOT_IMPLEMENTED` or `WARNING`
> rather than faking it.

---

## Table of contents

- [Project overview](#project-overview)
- [Architecture](#architecture)
- [Installation](#installation)
- [Running AIFCS](#running-aifcs)
- [API](#api)
- [Configuration](#configuration)
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

You need **two terminals**: one for the backend, one for the dashboard.

### Terminal 1 — backend

```bash
cd AIFCS
./scripts/dev_backend.sh
```

**Success looks like:**

```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

Leave this terminal running.

### Terminal 2 — dashboard

```bash
cd AIFCS
./scripts/dev_frontend.sh
```

**Success looks like:**

```
  VITE v6.x  ready in ### ms
  ➜  Local:   http://localhost:5173/
```

### Step 3 — open the dashboard

Open **http://localhost:5173** in your browser.

You should see the `AIFCS` boot screen listing each subsystem, then the
**ENTER COMMAND CENTER** button becomes clickable. Click it to reach the
Command Center.

To stop either server, click its terminal and press `Ctrl + C`.

---

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

Replay, training and the `/ws/simulation` WebSocket arrive in their respective
phases and are documented as they land.

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

## Testing

Run every gate — lint, format, types, backend tests, frontend lint and types:

```bash
./scripts/check.sh
```

Backend tests only:

```bash
cd backend && ../.venv/bin/python -m pytest
```

**Success looks like:** `121 passed`.

### End-to-end dashboard test

This one drives a real browser, so both servers must already be running.

```bash
cd frontend
npx playwright install chromium   # first time only
npm run test:e2e        # dashboard shell, status panel, error handling
npm run test:e2e:sim    # start / pause / step / reset drive the real engine
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

**Port 8000 or 5173 already in use.**
Another program holds the port. Stop it, or change the port
(`uvicorn main:app --port 8001`, and `server.port` in `frontend/vite.config.ts`).

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
| 2 | Entity and simplified 6DOF aircraft model | Next |
| 3 | Rule-based agent | Planned |
| 4 | Flight controller, action validation, safety layer | Planned |
| 5 | Sensor model: partial observation, noise, delay, dropout | Planned |
| 6 | Communication model: latency, loss, blackout | Planned |
| 7 | WebSocket telemetry | Planned |
| 8 | 3D Command Center (Three.js) | Planned |
| 9 | Replay, scoring, database | Planned |
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
