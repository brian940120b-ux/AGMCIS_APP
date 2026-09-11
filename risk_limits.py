"""
向下相容 shim。

風控參數已集中到 agmcis/config/settings.py(Phase 1)。
新程式碼請直接用 settings。
"""
from agmcis.config.settings import (  # noqa: F401
    EMERGENCY_STOP_FILE,
    MAX_CONSECUTIVE_LOSSES,
    MAX_DAILY_LOSS_USDT,
    MAX_DRAWDOWN_PCT,
    MAX_EXPOSURE_PCT,
    MAX_LEVERAGE,
    MAX_OPEN_POSITIONS,
    MAX_RISK_PER_TRADE_PCT,
    MAX_TOTAL_OPEN_LOSS_USDT,
    MAX_TRADES_PER_DAY,
    MIN_PROFIT_FACTOR,
    TRADING_PAUSE_FILE,
    risk_limits_dict as as_dict,
)
