# Two systems, one repository

This repository holds two unrelated programs:

| | **AGMCIS** | **AIFCS** |
|---|---|---|
| What it is | A cryptocurrency trading platform | A fictional flight simulation platform for AI research |
| Where | the repository root | `AIFCS/` |
| Port | **8000** | **8080** (dashboard on 5173) |
| Virtualenv | `.venv` at the root | `AIFCS/.venv` |
| Database | `agmcis.db` | `AIFCS/data/aifcs.db` |
| How it runs | systemd: `agmcis.service` and three others | a terminal window, or `aifcs serve` |
| Where it runs | the VPS | your own machine |

They share a git repository and nothing else. **No code, no process, no
configuration and no data crosses between them.**

## Why they cannot interfere

**Separate virtualenvs.** AIFCS needs NumPy, FastAPI, PyTorch and JSBSim;
AGMCIS needs its own set. Installing one cannot disturb the other, because
neither can see the other's `site-packages`.

**Separate databases.** `agmcis.db` sits at the root; AIFCS writes only inside
`AIFCS/data/`. AIFCS has no code path that opens a file above its own
directory, and `backend/tests/test_path_safety.py` holds every identifier that
becomes a filename to that rule.

**Separate ports — and this one had to be fixed.** Both defaulted to 8000.
Nothing bad had happened yet, because the two had never run on the same machine
at the same time, but the trap was real: whichever started first would win the
port, and if that were AIFCS then `systemctl restart agmcis` would fail to bind
and **the trading dashboard would go down**.

AIFCS moved to 8080. The trading system keeps 8000, which is baked into its
systemd units and its own deployment guide and is the harder of the two to
change. `scripts/start.sh` now also refuses to take port 8000 by name, and
`aifcs doctor` reports both ports and says whether the trading system is running
beside it.

## Running them together on one machine

Nothing special is required — start each the way its own documentation says.

```bash
# The trading system (VPS)
systemctl status agmcis

# AIFCS (any machine)
cd AIFCS && ./scripts/aifcs doctor && ./scripts/start.sh
```

`aifcs doctor` will notice the trading system and say so:

```
Ports and neighbours
  OK   port 8080 free — AIFCS backend
  OK   port 5173 free — AIFCS dashboard
  OK   the AGMCIS trading system is running on port 8000 — AIFCS does not use it
```

## Should AIFCS run on the trading VPS?

**No — and not because it would break anything.** The ports no longer collide
and the data is separate, so it would work.

The reason is resources. AIFCS is built to use a machine: eight agents at 60 Hz
is real CPU, and a training job will take every core it is given for minutes at
a time. A VPS sized for a trading system that mostly waits on network I/O is
not sized for that, and the failure mode is the trading system getting slow at
the wrong moment.

Run AIFCS on your own machine. If it ever has to share the VPS, lower
`max_timesteps_per_job` and `max_evaluation_episodes` in
`AIFCS/configs/training.yaml` first, and know that one AIFCS job at a time is
already enforced.

## Updating one without touching the other

They are on the same branch, so `git pull` brings both. That is fine: pulling
AIFCS changes cannot affect a running `agmcis.service`, because systemd is
running the code it loaded at start and only `systemctl restart agmcis` would
pick anything up.

The reverse is also true. Nothing in AIFCS reads, writes or imports anything
above `AIFCS/`.
