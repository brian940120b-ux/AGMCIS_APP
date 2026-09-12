"""
Dashboard Lite 的表格資料(Master Prompt 第九十五節)。

## 這個模組為什麼存在

`main.py` 原本用一個函式同時做三件事:抓現價、算 ROI 與未實現損益、
組 HTML 字串。那是第九十五節列的 God Function,而它有一個具體的後果:

**損益公式在系統裡出現了第二份。** `agmcis/core/models.py` 的
`Position.unrealized_pnl()` 是那條公式的正本,而 Dashboard 自己
用 `(current - entry) / entry * leverage` 又算了一次。兩份公式
在 Phase 0.5 修正槓桿計算的時候只改了一份 —— 這種 bug 只會在
「畫面上的數字跟 Telegram 通知對不起來」時被發現。

現在這裡**只做資料**,而且損益一律走 `Position` 的方法。
HTML 在 templates/dashboard_lite.html。

## 沒有現價時的做法

未平倉部位拿不到現價時,ROI 與 UPNL 回 None —— 呼叫端顯示「—」。
回 0 會讓一個看不到價格的部位顯示成「打平」,而那兩件事差很多:
前者是「不知道」,後者是「知道,而且是零」。
"""
import logging

logger = logging.getLogger("agmcis.api.dashboard_rows")


def _position_of(trade):
    """
    把資料庫的一列變成 Position,好用它的損益公式。

    方向或價格不合法時回 None —— Position 的建構會擋下來,
    而擋下來的原因值得記,不值得吞。
    """
    from agmcis.core.enums import Direction, MarketType
    from agmcis.core.models import Position

    try:
        return Position(
            symbol=trade.get("symbol") or "",
            market_type=MarketType.PERPETUAL,
            direction=Direction.parse(trade.get("signal"), Direction.WAIT),
            entry_price=float(trade.get("entry_price") or 0),
            size_usdt=float(trade.get("size_usdt") or 0),
            leverage=float(trade.get("leverage") or 1),
        )
    except Exception as exc:
        logger.warning(
            "Dashboard | 無法建立 Position | %s | %s: %s",
            trade.get("symbol"), type(exc).__name__, exc,
        )
        return None


def build_row(trade, price_of=None):
    """
    一列。純資料 —— 沒有 HTML,所以測試可以直接比對數字。

    price_of(symbol) 只在未平倉時呼叫。已平倉的用出場價,
    對一筆已平倉的交易查現價沒有意義,而且會白打一次 API。
    """
    if price_of is None:
        from agmcis.data.market_data import get_price as price_of

    is_open = trade.get("status") == "OPEN"
    symbol = trade.get("symbol")

    if is_open:
        try:
            current = price_of(symbol)
        except Exception as exc:
            # 一檔拿不到價格不該讓整張表消失。
            logger.warning("Dashboard | 現價查詢失敗 | %s | %s", symbol, exc)
            current = None
    else:
        current = trade.get("exit_price")

    position = _position_of(trade)
    roi = upnl = None

    if position is not None and current is not None:
        try:
            # **同一條公式。** 見模組說明 —— 這裡不再自己算一次。
            roi = position.roi_pct(float(current))
            upnl = position.unrealized_pnl(float(current))
        except Exception as exc:
            # 方向是 WAIT 的資料列會走到這裡(Direction.price_change_pct
            # 對沒有方向的部位拋例外,那是對的 —— 沒有方向就算不出損益)。
            # 一列算不出來不該讓整張表變成 500,但也不能假裝算出了 0。
            logger.warning(
                "Dashboard | 損益算不出來 | %s | %s: %s",
                symbol, type(exc).__name__, exc,
            )
            roi = upnl = None

    realized = trade.get("pnl_usdt")

    return {
        "symbol": symbol,
        "direction": trade.get("signal"),
        "leverage": position.leverage if position else None,
        "notional": position.notional if position else None,
        "entry": trade.get("entry_price"),
        "current": current,
        "exit": trade.get("exit_price"),
        "stop_loss": trade.get("stoploss"),
        "take_profit": trade.get("takeprofit"),
        "roi_pct": round(roi, 2) if roi is not None else None,
        "upnl_usdt": round(upnl, 2) if upnl is not None else None,
        "realized_usdt": realized,
        "status": trade.get("status"),
        "note": trade.get("close_reason") or trade.get("opened_at"),
        # 沒有停損的部位沒有虧損上限。這是這張表上唯一需要
        # 立刻行動的資訊,所以它有自己的旗標而不是靠看空欄位。
        "naked": is_open and trade.get("stoploss") is None,
        "sign": _sign(realized if not is_open else upnl),
    }


def _sign(value):
    """給 CSS 用的正負號。None 回空字串 —— 不知道不是打平。"""
    if value is None:
        return ""
    return "pos" if value > 0 else "neg" if value < 0 else ""


def build_rows(trades, price_of=None):
    return [build_row(trade, price_of=price_of) for trade in (trades or [])]
