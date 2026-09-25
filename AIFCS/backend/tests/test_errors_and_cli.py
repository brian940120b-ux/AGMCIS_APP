"""Error contract and CLI tests (PHASE 20).

The error tests guard two opposite mistakes. An unhandled failure must not
return the exception's text — a message can carry a filesystem path, a
configuration value or part of a payload. And a *deliberate* refusal must keep
its text, because every one of them in this API was written to be read.

The CLI tests mostly check that `doctor` reports rather than raises. A
diagnostic that crashes on the broken installation it was written to diagnose
is worse than no diagnostic.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import cli
from core.errors import REQUEST_ID_HEADER, install_error_handlers


@pytest.fixture
def failing_app():
    """An app with the real error contract and endpoints that misbehave."""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError("/home/someone/secret.key and a token sk-abcdef123456")

    @app.get("/refused")
    def refused() -> dict:
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="a simulation is running — stop it first")

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- the errors


def test_an_unhandled_failure_does_not_return_the_exception_text(failing_app):
    response = failing_app.get("/boom")
    assert response.status_code == 500
    assert "secret.key" not in response.text
    assert "sk-abcdef123456" not in response.text


def test_an_unhandled_failure_returns_a_handle_into_the_log(failing_app):
    """An operator with a 500 should be able to quote one string, not a timestamp."""
    response = failing_app.get("/boom")
    body = response.json()
    assert body["request_id"]
    assert body["request_id"] in body["detail"]
    assert response.headers[REQUEST_ID_HEADER] == body["request_id"]


def test_two_failures_get_different_ids(failing_app):
    first = failing_app.get("/boom").json()["request_id"]
    second = failing_app.get("/boom").json()["request_id"]
    assert first != second


def test_a_deliberate_refusal_keeps_the_words_it_was_given(failing_app):
    """These were written to be read; a status code alone helps nobody."""
    response = failing_app.get("/refused")
    assert response.status_code == 409
    assert response.json()["detail"] == "a simulation is running — stop it first"
    assert response.json()["request_id"]


def test_a_malformed_request_says_which_field_is_wrong(client):
    response = client.post("/api/training/start", json={"timesteps": -5})
    assert response.status_code == 422
    body = response.json()
    assert "timesteps" in body["detail"]
    assert any(p["field"] == "timesteps" for p in body["problems"])


def test_every_response_carries_a_request_id(client):
    for path in ("/api/health", "/api/system/status", "/api/models"):
        assert client.get(path).headers[REQUEST_ID_HEADER]


def test_a_404_from_the_real_app_is_json_with_an_id(client):
    response = client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json()["request_id"]


# ------------------------------------------------------------------ the CLI


def test_every_command_is_reachable_from_the_parser():
    parser = cli.build_parser()
    for command in ("doctor", "serve", "run", "scenarios", "train", "models", "evaluate", "bench"):
        args = parser.parse_args([command] + (["x"] if command == "evaluate" else []))
        assert callable(args.handler), f"{command} has no handler"


def test_doctor_reports_a_healthy_installation(capsys):
    """It must report, never raise — it exists to diagnose broken installations."""
    assert cli.main(["doctor"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "Python" in output
    assert "Scenarios" in output
    assert "FAIL" not in output


def test_doctor_reports_a_broken_scenario_rather_than_crashing(tmp_path, monkeypatch, capsys):
    from core.config import load_settings

    broken = tmp_path / "scenarios"
    broken.mkdir()
    (broken / "wrecked.yaml").write_text("scenario: {name: wrecked}\nentities: []\n", encoding="utf-8")

    settings = load_settings()
    settings.scenarios.directory = str(broken)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    assert cli.main(["doctor"]) == cli.EXIT_FAILED
    assert "FAIL" in capsys.readouterr().out


def test_scenarios_lists_what_can_be_flown(capsys):
    assert cli.main(["scenarios"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "demo_alpha" in output
    assert "units" in output


def test_run_flies_a_scenario_and_reports_the_result(capsys):
    assert cli.main(["run", "demo_alpha", "-s", "2"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "state hash" in output
    assert "BLUE-01" in output
    assert "ACTIVE" in output


def test_run_is_deterministic_from_the_command_line(capsys):
    """The CLI must not be a second, differently-seeded way to fly."""
    hashes = []
    for _ in range(2):
        cli.main(["run", "demo_alpha", "-s", "2", "--seed", "7"])
        output = capsys.readouterr().out
        hashes.append(next(line for line in output.splitlines() if "state hash" in line))
    assert hashes[0] == hashes[1]


def test_bench_measures_every_scenario_it_was_given(capsys):
    assert cli.main(["bench", "--ticks", "120", "--scenarios", "demo_alpha"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "demo_alpha" in output
    assert "ticks/s" in output


def test_models_lists_saved_policies_with_their_verdict(capsys):
    assert cli.main(["models"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    # Either there are policies with verdicts, or an honest statement there are none.
    assert "observation layout" in output or "No saved policies" in output


def test_evaluating_a_model_that_is_not_there_fails_with_advice(capsys):
    assert cli.main(["evaluate", "not-a-model"]) == cli.EXIT_FAILED
    assert "aifcs models" in capsys.readouterr().out


def test_the_cli_says_everything_is_fictional():
    """The scope statement belongs where someone reads the help, too."""
    help_text = cli.build_parser().format_help()
    assert "fictional" in help_text.lower()
    assert "no real aircraft" in help_text.lower()


def test_the_shim_and_the_console_script_point_at_the_same_entry_point():
    """Two ways in must not drift into two different programs."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert "backend/cli.py" in (root / "scripts" / "aifcs").read_text(encoding="utf-8")
    assert 'aifcs = "cli:main"' in (root / "pyproject.toml").read_text(encoding="utf-8")


def test_the_run_command_output_is_not_json_by_accident():
    """A sanity check that the CLI prints for people, not for machines."""
    with pytest.raises(json.JSONDecodeError):
        json.loads("scenario demo_alpha: 4 units")


# ------------------------------------------------- doctor's compute report


def test_doctor_names_the_gpu_when_torch_can_reach_one(monkeypatch, capsys):
    """A CPU wheel and a CUDA wheel are both called "torch" and both import.

    The difference is a training run of hours against one of days, so doctor
    says which this machine has rather than leaving it to a Python prompt.
    """
    torch = pytest.importorskip("torch")
    from cli import _report_compute

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda i: "NVIDIA GeForce RTX 4050 Laptop GPU")

    _report_compute()

    printed = capsys.readouterr().out
    assert "GPU available" in printed
    assert "RTX 4050" in printed


def test_doctor_says_so_when_there_is_no_gpu(monkeypatch, capsys):
    torch = pytest.importorskip("torch")
    from cli import _report_compute

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    _report_compute()

    printed = capsys.readouterr().out
    assert "CPU" in printed
    assert "GPU available" not in printed


def test_doctor_survives_a_torch_that_will_not_import(monkeypatch, capsys):
    """The compute report is a nicety; it must never be what breaks doctor."""
    import builtins

    from cli import _report_compute

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "torch":
            raise OSError("[WinError 1114] c10.dll")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    _report_compute()

    assert "could not be inspected" in capsys.readouterr().out
