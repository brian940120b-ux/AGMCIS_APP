"""
AGMCIS Phase 1 — 結構化日誌 (Structured Logging)
對應架構文件:驗收標準「所有重要操作都有結構化日誌與唯一追蹤 ID」

- 每條日誌都是一行 JSON,可直接餵給 Loki / ELK / grep
- trace_id 用 contextvars 傳遞:同一條決策鏈(從使用者提問到訂單結束)共用一個 ID
- 純標準庫,零依賴
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    """每次「一條完整決策鏈」開始時呼叫一次(如收到使用者問題、排程掃描觸發)。"""
    tid = str(uuid.uuid4())
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": get_trace_id(),
            "msg": record.getMessage(),
        }
        # 允許附帶結構化欄位:logger.info("...", extra={"data": {...}})
        data = getattr(record, "data", None)
        if data is not None:
            payload["data"] = data
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
