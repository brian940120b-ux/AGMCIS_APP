"""
風控參數的單一設定來源。

原本這些數字硬編碼在 risk_control.py 的模組層常數,改參數必須改程式碼。
現在全部從環境變數讀取,預設值偏保守 —— 在使用者明確指定數值之前,
寧可擋掉交易也不要放行。

⚠️ 這些預設值是佔位用的保守值,不是建議值。正式使用前請由使用者依實際帳戶規模決定。
"""
import os


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name, default):
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


# 單筆風險上限(佔帳戶權益百分比)。Position Sizing 於 Phase 5 接上後才會真正生效。
MAX_RISK_PER_TRADE_PCT = _f("MAX_RISK_PER_TRADE_PCT", 1.0)

# 回撤與曝險
MAX_DRAWDOWN_PCT = _f("MAX_DRAWDOWN_PCT", 15)
MAX_EXPOSURE_PCT = _f("MAX_EXPOSURE_PCT", 80)

# 部位數量與槓桿
MAX_OPEN_POSITIONS = _i("MAX_OPEN_POSITIONS", 5)
MAX_LEVERAGE = _f("MAX_LEVERAGE", 5)

# 虧損熔斷
MAX_DAILY_LOSS_USDT = _f("MAX_DAILY_LOSS_USDT", 300)          # 正數,代表可容忍的虧損額度
MAX_TOTAL_OPEN_LOSS_USDT = _f("MAX_TOTAL_OPEN_LOSS_USDT", -300)  # 負數,總浮虧下限
MAX_CONSECUTIVE_LOSSES = _i("MAX_CONSECUTIVE_LOSSES", 4)
MAX_TRADES_PER_DAY = _i("MAX_TRADES_PER_DAY", 10)

# 績效警示
MIN_PROFIT_FACTOR = _f("MIN_PROFIT_FACTOR", 0.8)

# 檔案旗標
EMERGENCY_STOP_FILE = os.getenv("EMERGENCY_STOP_FILE", "emergency.stop")
TRADING_PAUSE_FILE = os.getenv("TRADING_PAUSE_FILE", "trading_pause.flag")


def as_dict():
    return {
        "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
        "max_drawdown_pct": MAX_DRAWDOWN_PCT,
        "max_exposure_pct": MAX_EXPOSURE_PCT,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_leverage": MAX_LEVERAGE,
        "max_daily_loss_usdt": MAX_DAILY_LOSS_USDT,
        "max_total_open_loss_usdt": MAX_TOTAL_OPEN_LOSS_USDT,
        "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES,
        "max_trades_per_day": MAX_TRADES_PER_DAY,
        "min_profit_factor": MIN_PROFIT_FACTOR,
    }
