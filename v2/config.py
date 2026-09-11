"""
v2 專屬設定。

共用的值一律從 agmcis.config.settings 取得,不要在這裡複製一份。
"""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.append(ROOT)

from agmcis.config.settings import (  # noqa: E402
    EXCHANGE as PRIMARY_EXCHANGE,
    SCHEDULER_POSITION_INTERVAL as SCHEDULER_INTERVAL,
    dashboard_key,
)

APP_NAME = "AGMCIS v2"
VERSION = "2.0.0-dev"

DASHBOARD_KEY = dashboard_key()
