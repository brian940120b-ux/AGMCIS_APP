"""Tests for structured JSON logging (PHASE 0)."""

from __future__ import annotations

import json
import logging

from core.logging_config import configure_logging, get_logger

EXPECTED_KEYS = {
    "timestamp",
    "level",
    "module",
    "event",
    "entity_id",
    "agent_id",
    "message",
    "metadata",
}


def _parse_console(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "expected at least one log line"
    return json.loads(out[-1])


def test_json_log_has_required_schema(capsys):
    configure_logging(level="INFO", json_format=True)
    get_logger("test").info("hello", extra={"event": "UNIT_TEST", "entity_id": "BLUE-01"})

    record = _parse_console(capsys)
    assert set(record) >= EXPECTED_KEYS
    assert record["event"] == "UNIT_TEST"
    assert record["entity_id"] == "BLUE-01"
    assert record["message"] == "hello"
    assert record["level"] == "INFO"


def test_extra_fields_become_metadata(capsys):
    configure_logging(level="INFO", json_format=True)
    get_logger("test").info("tick", extra={"event": "SIM_TICK", "tick": 5, "dt": 0.016})

    record = _parse_console(capsys)
    assert record["metadata"]["tick"] == 5
    assert record["metadata"]["dt"] == 0.016


def test_non_serialisable_metadata_is_stringified(capsys):
    configure_logging(level="INFO", json_format=True)
    get_logger("test").info("obj", extra={"event": "X", "thing": object()})

    record = _parse_console(capsys)
    assert isinstance(record["metadata"]["thing"], str)


def test_file_sink_writes_jsonl(tmp_path, capsys):
    configure_logging(level="INFO", json_format=True, directory=tmp_path)
    get_logger("test").warning("degraded", extra={"event": "SENSOR_DROPOUT"})
    logging.shutdown()

    lines = (tmp_path / "aifcs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["event"] == "SENSOR_DROPOUT"
    assert record["level"] == "WARNING"


def test_repeated_configuration_does_not_duplicate_handlers(capsys):
    for _ in range(3):
        configure_logging(level="INFO", json_format=True)
    get_logger("test").info("once", extra={"event": "ONCE"})

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1, f"expected a single log line, got {len(lines)}"
