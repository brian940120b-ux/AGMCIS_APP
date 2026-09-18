"""Structured JSON logging for AIFCS.

Every subsystem logs one JSON object per line so runs can be parsed, diffed and
replayed offline. The schema is fixed (PHASE 38): timestamp, level, module,
event, entity_id, agent_id, message, metadata.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Attributes LogRecord always carries; anything else an adapter attached is
# treated as structured metadata.
_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "event": getattr(record, "event", record.funcName),
            "entity_id": getattr(record, "entity_id", None),
            "agent_id": getattr(record, "agent_id", None),
            "message": record.getMessage(),
        }
        metadata = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and k not in payload}
        payload["metadata"] = _json_safe(metadata)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _json_safe(value: Any) -> Any:
    """Best-effort conversion of metadata into JSON-serialisable values."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class HumanFormatter(logging.Formatter):
    """Readable console output for local development."""

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s  %(levelname)-7s %(name)-28s %(message)s", datefmt="%H:%M:%S")


def configure_logging(
    level: str = "INFO",
    json_format: bool = True,
    directory: str | Path | None = None,
    project_root: Path | None = None,
) -> logging.Logger:
    """Install AIFCS log handlers on the root logger.

    Console output is human-readable; the optional file sink is always JSON so
    downstream analysis never has to parse prose.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Replace handlers we installed previously so repeated calls stay idempotent.
    for handler in list(root.handlers):
        if getattr(handler, "_aifcs", False):
            root.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(JsonFormatter() if json_format else HumanFormatter())
    console._aifcs = True  # type: ignore[attr-defined]
    root.addHandler(console)

    if directory is not None:
        base = Path(project_root) if project_root else Path.cwd()
        log_dir = Path(directory)
        if not log_dir.is_absolute():
            log_dir = base / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "aifcs.jsonl", encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        file_handler._aifcs = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    return logging.getLogger("aifcs")


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced AIFCS logger (``aifcs.<name>``)."""
    return logging.getLogger(f"aifcs.{name}" if not name.startswith("aifcs") else name)
