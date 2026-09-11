"""
投資組合摘要。

Phase 0.5 的修正:槓桿改用每筆交易的真實值。
原本 get_trades() 的 SELECT 漏掉 leverage 欄位,所以 trade.get("leverage") 永遠是 None,
`float(trade.get("leverage") or 3)` 讓每一筆都被當成 3x —— 不管實際是 2x 還是 8x。
"""
from database_service import get_account, get_open_trades
from direction import is_long, is_short
from logger_service import logger
from market_data import get_price

DEFAULT_START_BALANCE = 10000


def get_portfolio_summary():
    open_trades = get_open_trades()
    account = get_account()

    balance = account.get("balance", DEFAULT_START_BALANCE)

    total_exposure = sum(float(t.get("size_usdt") or 0) for t in open_trades)
    exposure_ratio = (total_exposure / balance * 100) if balance > 0 else 0

    total_open_upnl = 0.0
    priced = 0

    for trade in open_trades:
        symbol = trade.get("symbol")
        signal = trade.get("signal")
        entry = float(trade.get("entry_price") or 0)
        size = float(trade.get("size_usdt") or 0)
        leverage = float(trade.get("leverage") or 1)

        current = get_price(symbol)

        if current is None or entry <= 0:
            logger.warning(
                "Portfolio | 無法計算浮動損益 | %s | price=%s entry=%s",
                symbol, current, entry,
            )
            continue

        if is_long(signal):
            change = (float(current) - entry) / entry
        elif is_short(signal):
            change = (entry - float(current)) / entry
        else:
            logger.error("Portfolio | 方向無法辨識 | %s | %r", symbol, signal)
            continue

        # 與已實現損益同一套算法:size_usdt 是保證金,名目 = size × leverage。
        total_open_upnl += size * change * leverage
        priced += 1

    allocation_map = {}
    for trade in open_trades:
        symbol = trade["symbol"]
        allocation_map[symbol] = allocation_map.get(symbol, 0) + float(trade.get("size_usdt") or 0)

    allocation = [
        {
            "symbol": symbol,
            "size_usdt": round(size, 2),
            "percent": round(size / total_exposure * 100, 2) if total_exposure > 0 else 0,
        }
        for symbol, size in allocation_map.items()
    ]
    allocation.sort(key=lambda x: x["size_usdt"], reverse=True)

    if exposure_ratio >= 80:
        risk_level = "高風險"
    elif exposure_ratio >= 40:
        risk_level = "中風險"
    elif exposure_ratio > 0:
        risk_level = "低風險"
    else:
        risk_level = "無持倉"

    return {
        "balance": round(balance, 2),
        "open_positions": len(open_trades),
        "total_exposure": round(total_exposure, 2),
        "exposure_ratio": round(exposure_ratio, 2),
        "risk_level": risk_level,
        "allocation": allocation,
        "open_trades": open_trades,
        "total_open_upnl": round(total_open_upnl, 2),
        # 有幾筆倉位真的取到價格。小於 open_positions 代表浮動損益不完整。
        "upnl_priced_positions": priced,
    }
