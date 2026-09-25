# Deploying AIFCS

AIFCS is a **research and teaching platform**, and it is built as one. This page
says what that means for running it somewhere other than your own machine,
because the honest answer to "can I put this on the internet?" is *not without
doing the work described here first*.

Everything it simulates is fictional and abstract. It contains no real aircraft,
weapon, radar or targeting data, and no operational capability of any kind.

---

## The one thing to read first

**There is no authentication.** Not a weak scheme — none. Anyone who can reach
the backend can start and stop simulations, edit and delete scenarios, start
training jobs that consume the machine's cores, and delete recordings and saved
policies.

That is a deliberate choice for a tool that runs on a researcher's laptop, where
adding a login would be friction protecting nothing. It stops being an
acceptable choice the moment the port is reachable by anyone else.

So: **bind it to localhost, or put it behind something that authenticates.**
The default is `127.0.0.1` for exactly this reason, and changing that is a
decision, not a configuration detail.

| If you are… | Do this |
|---|---|
| Running it yourself | Nothing. The defaults are right. |
| Sharing with a lab on a trusted network | Put it behind a reverse proxy that requires a login, and keep the backend bound to localhost. |
| Putting it on the internet | Do not, without an authenticating proxy, TLS, and a read of everything below. |

---

## Running it

### On your own machine

```bash
./scripts/aifcs doctor     # check the installation first
./scripts/start.sh         # backend + dashboard, opens a browser
```

`aifcs doctor` is the thing to run when something is wrong. It checks Python,
every dependency, whether the optional ones are installed, that the configs
load, that every scenario parses, that the database opens, and that the frontend
has been installed — and says which of those failed.

### With Docker

```bash
docker compose up --build
# dashboard: http://localhost:3000    API: http://localhost:8000/docs
```

Two images: the backend (FastAPI on `python:3.11-slim`) and the frontend (built
with Vite, served by nginx, proxying `/api/` and `/ws/` to the backend). SQLite
lives in a named volume, so there is no database service.

> **Not verified here.** The environment this was developed in has the Docker
> client but no daemon, so the images have never been built. What *is* checked,
> by `backend/tests/test_docker.py`, is the class of mistake a build would
> catch: every `COPY` source exists, the compose file points at real
> Dockerfiles, every bind mount exists, nginx proxies both the API and the
> WebSocket, and the healthcheck probes an endpoint that is really there. That
> test was written after finding the backend image never copied `scenarios/` —
> compose bind-mounted them in, so the stack worked and the image alone did not.

### Headless

```bash
./scripts/aifcs run team_eight -s 120    # fly it, print the result
./scripts/aifcs bench                    # how fast it runs here
```

---

## What to change before exposing it

**Bind address.** `aifcs serve --host 0.0.0.0` and the Docker port mappings both
expose the backend. Neither adds a login.

**CORS.** `backend/main.py` allows the Vite dev server's origins. Serving the
dashboard from elsewhere means adding that origin — and `allow_credentials` is
on, so a wildcard is not an option and should not be made one.

**Resource ceilings.** A training job started through the API will use the
machine. Two ceilings exist in `configs/training.yaml`:

| Setting | Default | Why |
|---|---|---|
| `max_timesteps_per_job` | 500,000 | A browser request must not commit the server to a week of compute |
| `max_evaluation_episodes` | 20 | Each episode runs to the episode limit |

One job at a time, spanning training *and* evaluation, and a job is refused
while a simulation is running. Lower both on a shared machine.

**Writable directories.** The backend writes to `data/` (recordings, the SQLite
database, generated JSBSim airframes) and `models/` (saved policies and their
cards). Everything else can be read-only.

**Model files are pickles.** Stable-Baselines3 saves policies in an archive that
unpickles on load, so loading one executes code from it. Nothing in the API
accepts an uploaded model — a `.zip` only appears there by being trained in
this process — but **anything dropped into `models/` will be loaded if it is
evaluated.** Treat that directory as trusted input, exactly as you would a
directory of Python files.

---

## Errors, and finding the cause

Every response carries an `X-AIFCS-Request-Id` header, and every error body
carries the same id in `request_id`. An unhandled failure returns a 500 whose
message names that id and **does not include the exception text** — a message
can carry a path, a configuration value or part of a payload. The full traceback
is in the backend log against that id, so an operator with a failed request can
quote one string and have the whole thing found.

Deliberate refusals keep their own words: "a simulation is running", "above the
configured cap", "not a valid run id". Those were written to be read.

## Logs

Structured JSON on stdout, one object per line, with an `event` field —
`SIMULATION_STARTED`, `TRAINING_JOB_FINISHED`, `MODEL_ARCHIVED`,
`UNHANDLED_ERROR`. Level and destination are in `configs/simulation.yaml` under
`logging`. In Docker they go to the container log.

## Backups

Everything that matters is a file:

| Path | What |
|---|---|
| `data/aifcs.db` | Run history, scores, decisions, training runs |
| `data/replay/` | Recordings, one gzipped JSON Lines file per run |
| `models/` | Saved policies and their cards |
| `scenarios/` | Scenario definitions |
| `configs/` | Everything tunable |

`data/jsbsim/` is generated and needs no backup. SQLite is in WAL mode, so copy
`aifcs.db`, `aifcs.db-wal` and `aifcs.db-shm` together, or stop the backend
first.

## Upgrading

The database migrates itself on startup, in order, forwards only. A run recorded
under one configuration is tagged with its `config_hash`, so runs from before
and after a configuration change are distinguishable rather than silently
comparable.

Trained policies are a different matter: a policy records the observation layout
version it was trained against, and the model centre marks one from an older
layout `INCOMPATIBLE` and refuses to evaluate it. Such a policy still loads and
still produces actions — from numbers that no longer mean what they meant — so
the refusal is deliberate. Retrain instead.
