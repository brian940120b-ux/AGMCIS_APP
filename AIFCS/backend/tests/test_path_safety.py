"""Path-safety tests (PHASE 20).

Found by reviewing the branch rather than by a failure. Several endpoints build
a filename out of an identifier that arrives in a request, and one of those
identifiers was not validated:

* ``POST /api/replay/load`` takes ``run_id`` **in the body**, where a path
  separator survives intact, and turned it straight into
  ``data/replay/<run_id>.jsonl.gz``. A traversing id read any ``.jsonl`` file
  the process could reach.
* ``DELETE /api/runs/{run_id}`` did the same and then called ``unlink()``.

A run id is minted by the platform as ``YYYYMMDD-HHMMSS-xxxx``; anything else is
not one. These tests hold every identifier that becomes a filename to that rule,
because the next endpoint to take one will be written by someone who has not
read this file.
"""

from __future__ import annotations

import pytest

TRAVERSALS = [
    "../../../etc/passwd",
    "../../data/aifcs",
    "..",
    "a/b",
    "a\\b",
    "/etc/passwd",
]

# Every way a refusal can arrive. 400 and 422 are the endpoint saying no, 404 is
# the router declining to match a path with a separator in it, and 405 is the
# method not existing on what the path collapsed to. What matters is that none
# of them is a 200 and that nothing on disk moved.
REFUSED = {400, 404, 405, 422}


# --------------------------------------------------------------- replay ids


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_a_traversing_run_id_cannot_load_a_recording(client, evil):
    """This one was real: run_id arrives in the body, so a separator survives."""
    response = client.post("/api/replay/load", json={"run_id": evil})
    assert response.status_code in REFUSED
    assert response.status_code != 200


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_a_traversing_run_id_cannot_delete_a_file(client, evil):
    response = client.delete(f"/api/runs/{evil}")
    assert response.status_code in REFUSED


def test_a_run_id_that_is_not_one_is_refused_by_name(client):
    """The refusal should say what was wrong, not just fail."""
    response = client.post("/api/replay/load", json={"run_id": "../nope"})
    assert response.status_code == 400
    assert "not a valid run id" in response.json()["detail"]


def test_a_real_looking_run_id_still_gets_through_to_a_normal_404(client):
    """The guard must not be so tight that legitimate ids stop working."""
    response = client.post("/api/replay/load", json={"run_id": "20260101-120000-abcd"})
    assert response.status_code == 404
    assert "no recording" in response.json()["detail"]


def test_a_recording_path_outside_the_replay_directory_is_refused(client):
    response = client.post("/api/replay/load", json={"path": "/etc/passwd"})
    assert response.status_code == 400
    assert "inside the replay directory" in response.json()["detail"]


# ---------------------------------------------------------------- model ids


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_a_traversing_model_id_cannot_be_read_archived_or_deleted(client, evil):
    for call in (
        lambda: client.get(f"/api/models/{evil}"),
        lambda: client.post(f"/api/models/{evil}/archive"),
        lambda: client.delete(f"/api/models/{evil}"),
    ):
        assert call().status_code in REFUSED


# ------------------------------------------------------------- scenario ids


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_a_traversing_scenario_name_cannot_be_written_or_deleted(client, evil):
    assert client.get(f"/api/scenarios/{evil}").status_code in REFUSED
    assert client.delete(f"/api/scenarios/{evil}").status_code in REFUSED


def test_a_scenario_cannot_be_created_under_a_traversing_name(client):
    template = client.get("/api/scenarios/template").json()
    document = template.get("document") or template
    response = client.post("/api/scenarios", json={"name": "../escaped", "document": document})
    assert response.status_code in {400, 422}


# ------------------------------------------------------- no new surface area


def test_no_endpoint_builds_a_path_from_an_unvalidated_identifier():
    """A structural check, so the next such endpoint is caught by review.

    Every place that joins a request-supplied id onto a directory must sit
    behind a validator. This looks for the join and insists the module that
    does it also names one.
    """
    import re
    from pathlib import Path

    api = Path(__file__).resolve().parents[1] / "api"
    joins = re.compile(r"directory\s*/\s*f\"|base\s*/\s*f\"|root\s*/\s*f\"")
    for module in api.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        if joins.search(source):
            assert re.search(r"_require_run_id|_VALID_|is_relative_to|path_for", source), (
                f"{module.name} builds a path from an identifier without validating it"
            )


# ------------------------------------------------- the other system upstairs


def test_aifcs_never_reaches_above_its_own_directory():
    """Two systems share this repository; only a bug would make them meet.

    AIFCS lives in `AIFCS/`, the AGMCIS trading platform at the level above.
    Nothing here should read, write or import anything up there — so no module
    should be walking up past the project root.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    offenders = []
    for module in backend.rglob("*.py"):
        if "tests" in module.parts or ".venv" in module.parts:
            continue
        source = module.read_text(encoding="utf-8")
        for pattern in ("project_root.parent", 'ROOT / ".."', "parents[3]"):
            if pattern in source and "trading" not in source:
                offenders.append(f"{module.name}: {pattern}")
    assert not offenders, f"these reach above the project root: {offenders}"


def test_the_two_systems_do_not_share_a_port():
    """Both defaulted to 8000, which was a trap rather than a bug.

    Whichever started first would win it, and if that were AIFCS then
    restarting the trading service would fail to bind and its dashboard would
    go down. AIFCS moved to 8080; this holds it there.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    start = (root / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert "AIFCS_BACKEND_PORT:-8080" in start, "AIFCS must not default to the trading system's port"

    import cli

    port = cli.build_parser().parse_args(["serve"]).port
    assert port == 8080, f"aifcs serve defaults to {port}, which must not be 8000"

    vite = (root / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "127.0.0.1:8080" in vite, "the dev proxy must point at the port the backend uses"
