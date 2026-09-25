"""The backend must start when an optional dependency is absent (PHASE 20).

Optional means optional. `requirements.txt` installs the core; the RL stack
(`requirements-ml.txt`) and JSBSim (`requirements-physics.txt`) are separate on
purpose, and `aifcs doctor` tells the user that not having them only costs the
feature they belong to.

That promise was false. `training/environment.py` imports gymnasium at module
scope, `training/pipeline.py` imported the environment at module scope, and
`core/runtime.py` reaches pipeline through the job runner — so `backend.main`
pulled gymnasium in on every start. On a machine with only the core installed,
the API server died during startup with `ModuleNotFoundError: gymnasium`, and
nothing in the suite noticed: every RL test skips itself when the stack is
missing, which is exactly the condition that broke the app.

The tests run a subprocess with the named packages made unimportable. A
subprocess rather than monkeypatching because the import has to happen for the
first time under the block, and the test session has already imported most of
this.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The block has to be installed before anything imports the backend, so it goes
# in a preamble rather than a fixture.
PREAMBLE = """
import sys, importlib.abc, logging
BLOCKED = set({blocked!r})

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError(f"No module named {{fullname!r}}")
        return None

sys.meta_path.insert(0, Blocker())
sys.path.insert(0, "backend")
logging.disable(logging.CRITICAL)
"""

RL_STACK = ("gymnasium", "torch", "stable_baselines3")
PHYSICS_STACK = ("jsbsim",)


def _run(blocked: tuple[str, ...], body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", PREAMBLE.format(blocked=list(blocked)) + body],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


ALL_STACKS = [
    pytest.param(RL_STACK, id="without-rl"),
    pytest.param(PHYSICS_STACK, id="without-jsbsim"),
    pytest.param(RL_STACK + PHYSICS_STACK, id="without-either"),
]


@pytest.mark.parametrize("blocked", ALL_STACKS)
def test_the_backend_imports(blocked: tuple[str, ...]) -> None:
    result = _run(blocked, "import main\nprint('IMPORTED')\n")
    assert "IMPORTED" in result.stdout, result.stderr[-2500:]


@pytest.mark.parametrize("blocked", ALL_STACKS)
def test_the_api_starts_and_answers(blocked: tuple[str, ...]) -> None:
    """Importing is not starting: the lifespan has to survive it too."""
    result = _run(
        blocked,
        """
from fastapi.testclient import TestClient
import main

with TestClient(main.app) as client:
    health = client.get("/api/health")
    print("HEALTH", health.status_code, health.json()["app"])
    print("SCENARIOS", client.get("/api/scenarios").status_code)
""",
    )
    assert "HEALTH 200 AIFCS" in result.stdout, result.stderr[-2500:]
    assert "SCENARIOS 200" in result.stdout, result.stderr[-2500:]


@pytest.mark.parametrize("blocked", ALL_STACKS)
def test_the_simulation_runs(blocked: tuple[str, ...]) -> None:
    """The point of the platform still works — that is what optional means."""
    result = _run(
        blocked,
        """
import time
from fastapi.testclient import TestClient
import main

with TestClient(main.app) as client:
    print("START", client.post("/api/simulation/start", json={"scenario": "demo_alpha"}).status_code)
    time.sleep(2)
    world = client.get("/api/world/state").json()
    print("TICKS", world["tick"], "ENTITIES", len(world["entities"]))
    client.post("/api/simulation/stop")
""",
    )
    assert "START 200" in result.stdout, result.stderr[-2500:]
    ticks = int(result.stdout.split("TICKS")[1].split()[0])
    entities = int(result.stdout.split("ENTITIES")[1].split()[0])
    assert ticks > 30, f"the clock barely moved in two seconds: {result.stdout}"
    assert entities == 4, result.stdout


def test_the_training_endpoint_says_so_rather_than_failing() -> None:
    """Without the RL stack the feature is unavailable, not broken."""
    result = _run(
        RL_STACK,
        """
from fastapi.testclient import TestClient
import main

with TestClient(main.app) as client:
    body = client.get("/api/training/status").json()
    print("AVAILABLE", body["available"])
    print("HINT", "requirements-ml.txt" in body["install_hint"])
""",
    )
    assert "AVAILABLE False" in result.stdout, result.stderr[-2500:]
    assert "HINT True" in result.stdout, result.stdout


def test_rl_available_accounts_for_gymnasium() -> None:
    """It used to check torch and SB3 only, and answer True with no environment."""
    result = _run(
        ("gymnasium",),
        """
from training.pipeline import rl_available
print("AVAILABLE", rl_available())
""",
    )
    assert "AVAILABLE False" in result.stdout, result.stderr[-2500:]


# ------------------------------------------------------- doctor's own check

DOCTOR_SITECUSTOMIZE = """
import sys, importlib.abc

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] == "fastapi":
            raise ImportError("No module named 'fastapi'")
        return None

sys.meta_path.insert(0, Blocker())
"""


def _doctor(env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "backend/cli.py", "doctor"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def test_doctor_checks_that_the_application_imports() -> None:
    """Doctor said "Ready" and the server then died on an import it never tried."""
    result = _doctor()
    assert "the API application imports" in result.stdout, result.stdout[-2000:]


def test_doctor_fails_loudly_when_the_application_will_not_import(tmp_path: Path) -> None:
    """A sitecustomize the probe subprocess inherits makes the import fail for real."""
    (tmp_path / "sitecustomize.py").write_text(DOCTOR_SITECUSTOMIZE, encoding="utf-8")
    result = _doctor({"PYTHONPATH": str(tmp_path)})

    assert result.returncode != 0
    assert "the API application will not import" in result.stdout, result.stdout[-2000:]
    # And it says why, rather than only that.
    assert "fastapi" in result.stdout
