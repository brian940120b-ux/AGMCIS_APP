"""
v3 專屬設定。

共用的值一律從 agmcis.config.settings 取得 —— 不要在這裡複製一份,
否則同一個設定會有兩個互相矛盾的來源。
"""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.append(ROOT)

from agmcis.config.settings import (  # noqa: E402
    EXCHANGE as PRIMARY_EXCHANGE,
    PAPER_START_BALANCE,
    WEBSOCKET_INTERVAL,
    dashboard_key,
)

APP_NAME = "AGMCIS v3 Pro"
VERSION = "3.0.0-dev"

# 未設定時是空字串,而空金鑰代表「拒絕所有人」而不是「放行所有人」。
DASHBOARD_KEY = dashboard_key()
