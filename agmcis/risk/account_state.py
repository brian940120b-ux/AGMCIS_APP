"""
從現有的資料層組出 AccountState。

把「怎麼查」與「怎麼判斷」分開:Risk Engine 只認 AccountState 這個資料結構,
不知道資料是從 PostgreSQL 還是別的地方來的。這讓引擎可以離線測試。
"""
import logging

from agmcis.risk.engine import AccountState

logger = logging.getLogger("agmcis.account_state")


def build_account_state():
    """從目前的資料庫與行情組出風控需要的帳戶快照。"""
    from analytics import get_trade_analytics
    from database_service import (
        count_trades_since,
        get_consecutive_losses,
        get_realized_pnl_since,
    )
    from portfolio_manager import get_portfolio_summary

    portfolio = get_portfolio_summary()
    analytics = get_trade_analytics()

    balance = float(portfolio.get("balance") or 0)
    exposure = float(portfolio.get("total_exposure") or 0)
    unrealized = float(portfolio.get("total_open_upnl") or 0)

    # 權益 = 餘額 + 未實現損益。只看餘額會低估或高估可承受的風險。
    equity = balance + unrealized

    return AccountState(
        equity=equity,
        available_balance=max(0.0, balance - exposure),
        open_positions=int(portfolio.get("open_positions") or 0),
        current_exposure_usdt=exposure,
        unrealized_pnl_usdt=unrealized,
        realized_pnl_24h=get_realized_pnl_since(24),
        realized_pnl_7d=get_realized_pnl_since(24 * 7),
        trades_24h=count_trades_since(24),
        consecutive_losses=get_consecutive_losses(),
        max_drawdown_pct=float(analytics.get("max_drawdown") or 0),
        profit_factor=float(analytics.get("profit_factor") or 0),
        open_symbols=[t.get("symbol") for t in portfolio.get("open_trades", [])],
        # 組合層風險要看每一腿的方向、名目與停損,不只是有哪些標的。
        open_legs=[_leg(t) for t in portfolio.get("open_trades", [])],
    )


def _leg(trade):
    """
    把一筆持倉整理成組合風險看得懂的樣子。

    名目價值優先用 position_value(建倉當下記下來的實際值);
    沒有的話才用 size_usdt × leverage 回推 —— 那是估計,
    因為 step size 對齊會讓實際名目略小於帳面。
    """
    notional = trade.get("position_value")
    if notional is None:
        notional = float(trade.get("size_usdt") or 0) * float(trade.get("leverage") or 1)

    return {
        "symbol": trade.get("symbol"),
        "direction": trade.get("signal") or trade.get("direction"),
        "notional_usdt": float(notional or 0.0),
        "entry": trade.get("entry_price"),
        "stop_loss": trade.get("stoploss"),
    }
