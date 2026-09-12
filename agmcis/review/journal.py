"""
完整交易日誌(Master Prompt 第四十一節)。

第四十一節列了 27 個欄位。系統一直有存其中大部分,但沒有一個地方
把它們組成一筆完整的紀錄 —— trades 表有一些、trade_exits 有一些、
ai_decisions 有一些,要回答「這筆交易的全貌」得自己拼。

這個模組做的就是拼起來,而且**明確標出拼不起來的部分**。

## 三個算出來而不是存下來的欄位

  * **Slippage** = 實際成交價與看到的價格的差。兩個都存了
    (entry_price / requested_entry_price),但差值沒有存 —— 因為
    存衍生值遲早會與來源不一致。
  * **R Multiple** = 淨損益 ÷ 原本打算冒的風險。用**原始**停損算,
    移動過的停損會讓這個數字失真。
  * **Duration** = 平倉時間減開倉時間。

## 沒有的欄位不假裝有

舊交易沒有 agent_votes、沒有 market_regime、沒有成本。那些欄位在
輸出裡是 None,而且 `completeness` 會說明缺了幾項。填 0 或「未知」
會讓一批不能比較的資料混進統計。
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger("agmcis.review.journal")

# 第四十一節列的欄位。用來算完整度 —— 一筆缺了一半欄位的紀錄
# 與一筆完整的紀錄不該在報表裡長得一樣。
REQUIRED_FIELDS = (
    "trade_id", "opened_at", "exchange", "market_type", "symbol",
    "direction", "strategy", "entry_price", "exit_price", "stop_loss",
    "take_profit", "leverage", "size_usdt", "margin", "fees", "funding",
    "slippage_pct", "pnl_usdt", "r_multiple", "signal_score", "confidence",
    "market_regime", "agent_votes", "reason", "exit_reason",
)


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slippage_pct(actual, requested):
    """
    成交價偏離看到的價格多少。requested 沒存就回 None ——
    回 0 會讓「沒有記錄滑點」與「滑點是 0」變成同一件事。
    """
    actual, requested = _f(actual), _f(requested)
    if actual is None or requested is None or requested <= 0:
        return None
    return round((actual - requested) / requested * 100, 6)


def _r_multiple(trade):
    """
    賺賠倍數。用**原始**停損算 —— 移動過的停損會讓這個數字失真:
    停損被拉到成本價之後,分母趨近 0,R 倍數會爆掉。
    """
    pnl = _f(trade.get("pnl_usdt"))
    entry = _f(trade.get("entry_price"))
    stop = _f(trade.get("original_stoploss"))

    if stop is None:
        stop = _f(trade.get("stoploss"))

    notional = _f(trade.get("original_position_value")) or _f(
        trade.get("position_value")
    )

    if None in (pnl, entry, stop, notional) or entry <= 0 or notional <= 0:
        return None

    risk = abs(entry - stop) / entry * notional
    if risk <= 0:
        return None

    return round(pnl / risk, 3)


def _duration_hours(trade):
    opened, closed = trade.get("opened_at"), trade.get("closed_at")
    if not opened or not closed:
        return None
    try:
        start = datetime.fromisoformat(str(opened))
        end = datetime.fromisoformat(str(closed))
    except ValueError:
        return None
    return round((end - start).total_seconds() / 3600.0, 3)


@dataclass
class JournalEntry:
    trade_id: Optional[int] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    exchange: str = "BingX"
    market_type: Optional[str] = None
    symbol: Optional[str] = None
    direction: Optional[str] = None
    strategy: Optional[str] = None

    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    leverage: Optional[float] = None
    size_usdt: Optional[float] = None
    margin: Optional[float] = None

    fees: Optional[float] = None
    funding: Optional[float] = None
    slippage_pct: Optional[float] = None

    pnl_usdt: Optional[float] = None
    pnl_pct: Optional[float] = None
    r_multiple: Optional[float] = None
    duration_hours: Optional[float] = None

    signal_score: Optional[float] = None
    confidence: Optional[float] = None
    market_regime: Optional[str] = None
    agent_votes: Optional[dict] = None

    partials: List[dict] = field(default_factory=list)
    realized_partial_usdt: float = 0.0

    reason: Optional[str] = None
    exit_reason: Optional[str] = None

    missing: List[str] = field(default_factory=list)

    @property
    def completeness(self):
        """
        有幾成的欄位填得起來。一筆缺了一半欄位的紀錄與一筆完整的
        紀錄不該在報表裡長得一樣。
        """
        total = len(REQUIRED_FIELDS)
        return round((total - len(self.missing)) / total * 100, 1)

    def to_dict(self):
        payload = {
            key: value for key, value in self.__dict__.items()
            if key != "missing"
        }
        payload["missing"] = list(self.missing)
        payload["completeness"] = self.completeness
        return payload


def build(trade, decision=None, exits=None):
    """
    把一筆交易組成完整日誌。

    `decision` 是 ai_decisions 那一列(可以是 None),
    `exits` 是 trade_exits 的分批紀錄。
    """
    decision = decision or {}

    entry = JournalEntry(
        trade_id=trade.get("id"),
        opened_at=trade.get("opened_at"),
        closed_at=trade.get("closed_at"),
        market_type=decision.get("market_type"),
        symbol=trade.get("symbol"),
        direction=trade.get("signal"),
        strategy=trade.get("strategy"),
        entry_price=_f(trade.get("entry_price")),
        exit_price=_f(trade.get("exit_price")),
        stop_loss=_f(trade.get("original_stoploss")) or _f(trade.get("stoploss")),
        take_profit=_f(trade.get("takeprofit")),
        leverage=_f(trade.get("leverage")),
        size_usdt=_f(trade.get("original_position_value"))
        or _f(trade.get("position_value")),
        margin=_f(trade.get("size_usdt")),
        fees=round(
            (_f(trade.get("entry_fee")) or 0.0) + (_f(trade.get("exit_fee")) or 0.0),
            8,
        ) if trade.get("entry_fee") is not None
          or trade.get("exit_fee") is not None else None,
        funding=_f(trade.get("funding_usdt")),
        slippage_pct=_slippage_pct(
            trade.get("entry_price"), trade.get("requested_entry_price"),
        ),
        pnl_usdt=_f(trade.get("pnl_usdt")),
        pnl_pct=_f(trade.get("pnl_pct")),
        r_multiple=_r_multiple(trade),
        duration_hours=_duration_hours(trade),
        signal_score=_f(decision.get("score")),
        confidence=_f(trade.get("confidence")) or _f(decision.get("confidence")),
        market_regime=trade.get("market_regime") or decision.get("market_regime"),
        agent_votes=trade.get("agent_votes") or decision.get("agent_votes"),
        partials=list(exits or []),
        realized_partial_usdt=_f(trade.get("realized_partial_usdt")) or 0.0,
        reason=decision.get("reason"),
        exit_reason=trade.get("close_reason"),
    )

    entry.missing = [
        name for name in REQUIRED_FIELDS
        if getattr(entry, name, None) in (None, "", {})
    ]

    return entry


def build_many(trades, decisions=None, exits=None):
    """
    一批交易。decisions / exits 用 trade_id 對應。

    對不起來的**不會**讓那一筆消失 —— 它會是一筆 completeness 較低的
    紀錄。讓它消失會讓「有多少筆交易」這個數字變得不可信。
    """
    by_trade = {}
    for row in decisions or []:
        if row.get("trade_id"):
            by_trade[row["trade_id"]] = row

    exits_by_trade = {}
    for row in exits or []:
        exits_by_trade.setdefault(row.get("trade_id"), []).append(row)

    return [
        build(
            trade,
            decision=by_trade.get(trade.get("id")),
            exits=exits_by_trade.get(trade.get("id")),
        )
        for trade in trades or []
    ]


def summarise(entries):
    """
    這批日誌本身有多可信。

    **這不是績效報告。** 它報告的是「資料有多完整」——
    在拿一批 completeness 60% 的紀錄去算勝率之前,要先知道那件事。
    """
    entries = list(entries or [])
    if not entries:
        return {"count": 0, "avg_completeness": 0.0, "commonly_missing": []}

    counts = {}
    for entry in entries:
        for name in entry.missing:
            counts[name] = counts.get(name, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    return {
        "count": len(entries),
        "avg_completeness": round(
            sum(e.completeness for e in entries) / len(entries), 1,
        ),
        "commonly_missing": [
            {"field": name, "missing_in": n, "pct": round(n / len(entries) * 100, 1)}
            for name, n in ranked[:8]
        ],
    }
