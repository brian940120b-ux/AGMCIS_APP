"""
向下相容 shim。

實作已移到 agmcis/risk/(Phase 5):
    engine.py           RiskEngine:閘門 + 倉位 + 槓桿的完整裁決
    position_sizing.py  由 權益 × 風險% ÷ 停損距離 反推倉位
    leverage.py         由停損距離與波動度決定槓桿(不是信心分數)
    kill_switch.py      停新單 / 撤單 / 平倉 / 稽核

這裡保留原本的函式名稱,讓既有呼叫端不用改。

新程式碼請直接用:
    from agmcis.risk.engine import get_engine
    decision = get_engine().evaluate(intent, state)
"""
from pathlib import Path

import risk_limits
from analytics import get_trade_analytics
from database_service import (
    count_trades_since,
    get_consecutive_losses,
    get_realized_pnl_since,
)
from logger_service import logger
from portfolio_manager import get_portfolio_summary


def _alert(level, title, message):
    return {"level": level, "title": title, "message": message}


# ---------------- 風控檢查 ----------------
#
# 每一項限制是一個函式,吃一份快照回一個 Breach 或 None。
#
# 原本這裡是一個 116 行的函式,九項檢查全部展開在裡面(第九十五節的
# God Function)。那個形狀有一個具體的問題:**加一條限制要改那個函式**,
# 而那個函式是自動交易唯一的帳戶層閘門。每一次動它都在動所有九項。
#
# 現在加一條限制是在 CHECKS 裡多一個項目,而每一項都可以單獨測。


class Breach:
    """
    一項限制被觸發。

    `emergency` 與 `blocker` 是分開的兩件事:
      blocker    擋住新開倉。
      emergency  進一步把系統狀態標成 EMERGENCY_STOP。

    回撤、單日虧損與緊急停止檔會設 emergency;持倉數達上限不會 ——
    後者是「今天不要再開了」,前者是「有事情不對勁」。
    把兩者混為一談,會讓一個正常的滿倉狀態看起來像出事了。
    """

    def __init__(self, code, level, title, message, emergency=False,
                 blocks=True):
        self.code = code
        self.level = level
        self.title = title
        self.message = message
        self.emergency = emergency
        self.blocks = blocks

    def alert(self):
        return _alert(self.level, self.title, self.message)


def _check_emergency_file(_):
    if not Path(risk_limits.EMERGENCY_STOP_FILE).exists():
        return None
    return Breach(
        "EMERGENCY_STOP_FILE", "HIGH", "緊急停止",
        "偵測到 emergency.stop,暫停新開倉。", emergency=True,
    )


def _check_pause_file(_):
    if not Path(risk_limits.TRADING_PAUSE_FILE).exists():
        return None
    return Breach(
        "TRADING_PAUSE_FLAG", "MEDIUM", "交易暫停",
        "偵測到 trading_pause.flag,暫停新開倉。",
    )


def _check_drawdown(snapshot):
    value = snapshot["max_drawdown"]
    if value < risk_limits.MAX_DRAWDOWN_PCT:
        return None
    return Breach(
        "MAX_DRAWDOWN", "HIGH", "最大回撤過高",
        f"目前最大回撤 {value}%,已超過限制 "
        f"{risk_limits.MAX_DRAWDOWN_PCT}%。",
        emergency=True,
    )


def _check_exposure(snapshot):
    value = snapshot["exposure_ratio"]
    if value < risk_limits.MAX_EXPOSURE_PCT:
        return None
    return Breach(
        "MAX_EXPOSURE", "HIGH", "總曝險過高",
        f"目前曝險 {value}%,已超過限制 {risk_limits.MAX_EXPOSURE_PCT}%。",
    )


def _check_open_positions(snapshot):
    value = snapshot["open_positions"]
    if value < risk_limits.MAX_OPEN_POSITIONS:
        return None
    return Breach(
        "MAX_OPEN_POSITIONS", "MEDIUM", "持倉數已達上限",
        f"目前 {value} 筆,上限 {risk_limits.MAX_OPEN_POSITIONS} 筆。",
    )


def _check_open_loss(snapshot):
    value = snapshot["total_open_upnl"]
    if value > risk_limits.MAX_TOTAL_OPEN_LOSS_USDT:
        return None
    return Breach(
        "MAX_TOTAL_OPEN_LOSS", "HIGH", "總浮虧過高",
        f"目前總浮虧 {value} USDT,已低於限制 "
        f"{risk_limits.MAX_TOTAL_OPEN_LOSS_USDT} USDT。",
    )


def _check_daily_loss(snapshot):
    value = snapshot["daily_pnl"]
    # 用 -abs():設定寫成 50 或 -50 都要當成「虧 50」。
    if value > -abs(risk_limits.MAX_DAILY_LOSS_USDT):
        return None
    return Breach(
        "MAX_DAILY_LOSS", "HIGH", "單日虧損達上限",
        f"近 24 小時已實現損益 {round(value, 2)} USDT,"
        f"已達單日虧損上限 {risk_limits.MAX_DAILY_LOSS_USDT} USDT。",
        emergency=True,
    )


def _check_consecutive_losses(snapshot):
    value = snapshot["consecutive_losses"]
    if value < risk_limits.MAX_CONSECUTIVE_LOSSES:
        return None
    return Breach(
        "MAX_CONSECUTIVE_LOSSES", "HIGH", "連續虧損熔斷",
        f"已連續虧損 {value} 筆,達熔斷門檻 "
        f"{risk_limits.MAX_CONSECUTIVE_LOSSES} 筆。",
    )


def _check_trades_today(snapshot):
    value = snapshot["trades_today"]
    if value < risk_limits.MAX_TRADES_PER_DAY:
        return None
    return Breach(
        "MAX_TRADES_PER_DAY", "MEDIUM", "單日交易次數達上限",
        f"近 24 小時已開倉 {value} 筆,上限 "
        f"{risk_limits.MAX_TRADES_PER_DAY} 筆。",
    )


def _check_profit_factor(snapshot):
    """
    Profit Factor 偏低是**警告不是封鎖**(blocks=False)。

    理由:它是一個回顧性的統計,而且 0 代表「還沒有資料」不是
    「表現很差」—— 所以 0 不觸發。用它擋住新開倉,會讓一個剛開始
    跑的系統永遠開不了第一筆。
    """
    value = snapshot["profit_factor"]
    if value == 0 or value >= risk_limits.MIN_PROFIT_FACTOR:
        return None
    return Breach(
        "LOW_PROFIT_FACTOR", "MEDIUM", "Profit Factor 偏低",
        f"目前 Profit Factor {value},低於建議值 "
        f"{risk_limits.MIN_PROFIT_FACTOR}。",
        blocks=False,
    )


# 順序就是顯示順序。緊急停止排最前面 —— 它是最需要先看到的一項。
CHECKS = (
    _check_emergency_file,
    _check_pause_file,
    _check_drawdown,
    _check_exposure,
    _check_open_positions,
    _check_open_loss,
    _check_daily_loss,
    _check_consecutive_losses,
    _check_trades_today,
    _check_profit_factor,
)


def build_snapshot():
    """
    跑一次檢查需要的所有數字。**一次抓齊** ——
    分次抓的話,回撤與曝險可能來自不同的時刻,而那兩個數字
    會並排顯示在同一個畫面上。
    """
    analytics = get_trade_analytics()
    portfolio = get_portfolio_summary()

    return {
        "max_drawdown": analytics.get("max_drawdown", 0),
        "profit_factor": analytics.get("profit_factor", 0),
        "exposure_ratio": portfolio.get("exposure_ratio", 0),
        "open_positions": portfolio.get("open_positions", 0),
        "total_open_upnl": portfolio.get("total_open_upnl", 0),
        "daily_pnl": get_realized_pnl_since(24),
        "trades_today": count_trades_since(24),
        "consecutive_losses": get_consecutive_losses(),
    }


def evaluate_limits(snapshot, checks=CHECKS):
    """
    跑完所有檢查。純函式 —— 吃一份快照,回一串 Breach。

    **一項檢查拋例外算成「這一項沒過」**,不是「跳過」。
    一個在自己壞掉時放行的風控檢查不是風控檢查。
    """
    breaches = []

    for check in checks:
        try:
            breach = check(snapshot)
        except Exception as exc:
            logger.exception("風控檢查失敗 | %s", getattr(check, "__name__", check))
            breach = Breach(
                f"CHECK_FAILED_{getattr(check, '__name__', 'UNKNOWN')}",
                "HIGH", "風控檢查失敗",
                f"{type(exc).__name__}: {exc} —— 算成沒通過。",
            )

        if breach is not None:
            breaches.append(breach)

    return breaches


def get_risk_control_status():
    """
    帳戶層級的風控現況。

    這個函式現在只做組裝:抓快照 → 跑檢查 → 把結果攤平成
    呼叫端要的那份 dict。每一項限制的判斷在自己的函式裡。
    """
    snapshot = build_snapshot()
    breaches = evaluate_limits(snapshot)

    blockers = [b.code for b in breaches if b.blocks]
    emergency_stop = any(b.emergency for b in breaches)
    allow_new_trade = not blockers

    alerts = [b.alert() for b in breaches]
    if not alerts:
        alerts.append(_alert(
            "NORMAL", "風控狀態正常",
            "回撤、曝險、持倉數與虧損額度皆在安全範圍內。",
        ))

    if emergency_stop:
        system_status = "EMERGENCY_STOP"
    elif allow_new_trade:
        system_status = "ACTIVE"
    else:
        system_status = "LIMITED"

    return {
        "system_status": system_status,
        "allow_new_trade": allow_new_trade,
        "emergency_stop": emergency_stop,
        "blockers": blockers,
        "max_drawdown": snapshot["max_drawdown"],
        "exposure_ratio": snapshot["exposure_ratio"],
        "open_positions": snapshot["open_positions"],
        "profit_factor": snapshot["profit_factor"],
        "total_open_upnl": snapshot["total_open_upnl"],
        "daily_realized_pnl": round(snapshot["daily_pnl"], 2),
        "trades_last_24h": snapshot["trades_today"],
        "consecutive_losses": snapshot["consecutive_losses"],
        "limits": risk_limits.as_dict(),
        "alerts": alerts,
    }


def cap_leverage(requested_leverage):
    """槓桿上限由風控決定,任何策略或 Agent 都不能超過。"""
    try:
        requested = float(requested_leverage)
    except (TypeError, ValueError):
        requested = 1.0
    return max(1.0, min(requested, risk_limits.MAX_LEVERAGE))


def assert_can_open(symbol=None):
    """
    自動開倉前的 hard gate。

    回傳 (allowed: bool, reason: str|None, status: dict)。
    任何自動交易路徑都必須先呼叫這個函式並尊重結果。

    ⚠️ 這只是帳戶層級的閘門,**不決定倉位大小**。
    需要完整裁決(含 sizing 與槓桿)請用 evaluate_intent()。
    """
    status = get_risk_control_status()

    if not status["allow_new_trade"]:
        reason = ",".join(status["blockers"])
        logger.warning(
            "Risk gate BLOCKED | symbol=%s | status=%s | blockers=%s",
            symbol, status["system_status"], reason,
        )
        return False, reason, status

    logger.info("Risk gate PASSED | symbol=%s | status=%s", symbol, status["system_status"])
    return True, None, status


def evaluate_intent(intent, atr=None, mtf_score=None,
                    contract_max_leverage=None, min_notional=None):
    """
    完整風險裁決:閘門 + 槓桿 + 倉位大小。

    這是 Phase 5 之後的正式入口。回傳 RiskDecision ——
    approved=False 時 size_usdt 與 leverage 沒有意義。

    倉位由 `權益 × MAX_RISK_PER_TRADE_PCT ÷ 停損距離` 決定,
    不再是固定的 1000 USDT。
    """
    from agmcis.risk.account_state import build_account_state
    from agmcis.risk.engine import get_engine

    state = build_account_state()

    return get_engine().evaluate(
        intent, state,
        atr=atr, mtf_score=mtf_score,
        contract_max_leverage=contract_max_leverage,
        min_notional=min_notional,
        correlation=_correlation_for(intent, state),
        news=_news_risk(),
    )


def _news_risk():
    """
    消息面風險(第五十一節)。取不到就回 None ——
    None 代表「這一層沒有生效」,而那件事由 LIVE SAFETY GATE
    在實單那一側擋住,不是靠這裡假裝一切正常。
    """
    from agmcis.risk import news_risk

    try:
        return news_risk.current()
    except Exception as exc:
        logger.warning("消息面風險取得失敗 | %s", exc)
        return None


def _correlation_for(intent, state):
    """
    組合風險要用的相關係數矩陣。

    取不到就回 None —— portfolio.assess() 收到 None 會把所有同向部位
    當成同一群,也就是最保守的那一邊。這裡刻意不讓失敗變成「不相關」。
    """
    from agmcis.risk import correlation

    symbols = list(dict.fromkeys([intent.symbol] + list(state.open_symbols or [])))

    if len(symbols) < 2:
        return None      # 只有一檔的時候沒有相關性可言

    try:
        return correlation.get_matrix(symbols)
    except Exception as exc:
        logger.warning("相關係數取得失敗,改用保守假設 | %s", exc)
        return None
