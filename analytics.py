"""
績效分析。

⚠️ 損益基準分離(Phase 4)

Phase 0.5 之前寫入的已平倉資料,損益漏乘了槓桿。那些歷史數字**一律保留原值,
不回頭改寫** —— 改寫歷史財務紀錄不可逆,而且會讓過去的決策記錄失去意義。

但也不能讓兩種基準混在同一個勝率 / Profit Factor 裡:
一筆 5x 槓桿的交易在舊基準下損益只有實際的五分之一,
混著算出來的統計數字兩邊都不是。

所以:
  - 預設統計**只納入 LEVERAGED(基準正確)的交易**
  - 舊基準的交易另外列在 legacy 區塊,並標明不可與新資料直接比較
  - 兩者的筆數都會回報,不會讓人以為交易紀錄消失了
"""
from config import PAPER_START_BALANCE
from paper_trading import load_account, load_trades

START_BALANCE = float(PAPER_START_BALANCE)

LEVERAGED = "LEVERAGED"
LEGACY = "LEGACY_UNLEVERAGED"


def split_by_pnl_basis(closed_trades):
    """
    把已平倉交易依損益基準分成兩組。

    pnl_basis 是 None 的資料視為舊基準 —— Phase 0.5 的 migration 只回填了
    當時已經 CLOSED 的資料,而且保守地假設未標記就是舊的。
    """
    comparable, legacy = [], []
    for trade in closed_trades:
        if trade.get("pnl_basis") == LEVERAGED:
            comparable.append(trade)
        else:
            legacy.append(trade)
    return comparable, legacy


def build_equity_curve(closed_trades):
    equity = START_BALANCE
    curve = [equity]

    for trade in closed_trades:
        pnl = trade.get("pnl_usdt", 0)
        equity += pnl
        curve.append(round(equity, 2))

    return curve


def calculate_max_drawdown(equity_curve):
    if not equity_curve:
        return 0

    peak = equity_curve[0]
    max_drawdown = 0

    for value in equity_curve:
        if value > peak:
            peak = value

        drawdown = (peak - value) / peak * 100

        if drawdown > max_drawdown:
            max_drawdown = drawdown

    return round(max_drawdown, 2)


def calculate_profit_factor(closed_trades):
    gross_profit = sum(
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
        if trade.get("pnl_usdt", 0) > 0
    )

    gross_loss = abs(
        sum(
            trade.get("pnl_usdt", 0)
            for trade in closed_trades
            if trade.get("pnl_usdt", 0) < 0
        )
    )

    if gross_loss == 0:
        return 0 if gross_profit == 0 else 999

    return round(gross_profit / gross_loss, 2)


def _legacy_summary(legacy_trades):
    """舊基準交易的摘要。刻意只給最少的資訊,並標明不可比較。"""
    if not legacy_trades:
        return {"count": 0, "total_pnl": 0, "comparable": False}

    return {
        "count": len(legacy_trades),
        "total_pnl": round(sum(t.get("pnl_usdt") or 0 for t in legacy_trades), 2),
        "comparable": False,
        "note": (
            "這些交易的損益是在 Phase 0.5 之前的錯誤基準下寫入的(漏乘槓桿)。"
            "原值保留未改寫,但不應與新資料放在同一個勝率或 Profit Factor 裡比較。"
        ),
    }


def get_trade_analytics(include_legacy=False):
    """
    include_legacy=True 會把舊基準的交易也算進主統計。
    預設 False —— 兩種基準混在一起算出來的數字兩邊都不是。
    """
    trades = load_trades()
    account = load_account()

    all_closed = [t for t in trades if t["status"] == "CLOSED"]
    comparable, legacy = split_by_pnl_basis(all_closed)

    closed_trades = all_closed if include_legacy else comparable
    legacy_summary = _legacy_summary(legacy)

    total_trades = len(closed_trades)

    if total_trades == 0:
        return {
            "total_trades": 0,
            "total_pnl": 0,
            "total_return_pct": 0,
            "win_rate": 0,
            "avg_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "risk_reward_ratio": 0,
            "profit_factor": 0,
            "max_drawdown": 0,
            "best_symbol": "-",
            "worst_symbol": "-",
            "symbol_stats": [],
            "recent_trades": [],
            "equity_curve": [START_BALANCE],
            "current_balance": account.get("balance", START_BALANCE),
            "legacy": legacy_summary,
            "pnl_basis": "LEVERAGED" if not include_legacy else "MIXED",
        }

    total_pnl = sum(
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
    )

    total_return_pct = total_pnl / START_BALANCE * 100

    wins_list = [
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
        if trade.get("pnl_usdt", 0) > 0
    ]

    losses_list = [
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
        if trade.get("pnl_usdt", 0) < 0
    ]

    wins = len(wins_list)
    losses = len(losses_list)

    win_rate = wins / total_trades * 100
    avg_pnl = total_pnl / total_trades

    avg_win = (
        sum(wins_list) / len(wins_list)
        if wins_list
        else 0
    )

    avg_loss = (
        sum(losses_list) / len(losses_list)
        if losses_list
        else 0
    )

    risk_reward_ratio = (
        abs(avg_win / avg_loss)
        if avg_loss != 0
        else 0
    )

    equity_curve = build_equity_curve(closed_trades)
    max_drawdown = calculate_max_drawdown(equity_curve)
    profit_factor = calculate_profit_factor(closed_trades)

    symbol_map = {}

    for trade in closed_trades:
        symbol = trade["symbol"]
        pnl = trade.get("pnl_usdt", 0)

        if symbol not in symbol_map:
            symbol_map[symbol] = {
                "symbol": symbol,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0
            }

        symbol_map[symbol]["trades"] += 1
        symbol_map[symbol]["pnl"] += pnl

        if pnl > 0:
            symbol_map[symbol]["wins"] += 1
        else:
            symbol_map[symbol]["losses"] += 1

    symbol_stats = list(symbol_map.values())

    for item in symbol_stats:
        item["win_rate"] = round(
            item["wins"] / item["trades"] * 100,
            2
        )
        item["pnl"] = round(item["pnl"], 2)

    symbol_stats.sort(
        key=lambda x: x["pnl"],
        reverse=True
    )

    best_symbol = symbol_stats[0]["symbol"]
    worst_symbol = symbol_stats[-1]["symbol"]

    recent_trades = closed_trades[-10:]
    recent_trades.reverse()

    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return_pct, 2),
        "win_rate": round(win_rate, 2),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "risk_reward_ratio": round(risk_reward_ratio, 2),
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "symbol_stats": symbol_stats,
        "recent_trades": recent_trades,
        "equity_curve": equity_curve,
        "current_balance": round(account.get("balance", START_BALANCE), 2),
        "legacy": legacy_summary,
        "pnl_basis": "MIXED" if include_legacy else "LEVERAGED",
    }