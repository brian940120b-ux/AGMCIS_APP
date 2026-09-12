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


def pnl_of(trade):
    """
    一筆交易的損益,**沒有就是 None**。

    ## 為什麼不是 `trade.get("pnl_usdt", 0)`

    那個寫法有一個具體的 bug:欄位**存在但值是 None** 的時候,
    `.get(key, default)` 回傳的是 None 不是 default —— 預設值永遠用不到。
    然後下一行的 `equity += pnl` 就會拋 TypeError,而那個例外會讓
    `/api/analytics_pro`、儀表板的分析區塊與日報整個掛掉。

    一筆 CLOSED 但 pnl_usdt 是 NULL 的資料列會怎麼出現?平倉寫到一半
    當機、手動改過的資料列、或從別處匯入的歷史。它是少見的,不是不可能的。

    ## 為什麼不把它當成 0

    那樣更糟:它會安靜地進統計。一筆損益不明的交易被當成打平,
    會讓「已實現收益」少算、讓勝率的分母多一個非勝利。
    **算不出來就排除,並且把排除幾筆講出來**(第九十四節)。
    """
    value = trade.get("pnl_usdt")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def with_pnl(closed_trades):
    """
    只留有損益數字的交易。回傳 (可用的, 排除幾筆)。

    排除的筆數一定要回報 —— 加總對不起來會讓人以為統計算錯。
    """
    usable, skipped = [], 0

    for trade in closed_trades:
        if pnl_of(trade) is None:
            skipped += 1
            continue
        usable.append(trade)

    return usable, skipped


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
    """
    資金曲線。損益不明的那幾筆**跳過** —— 見 pnl_of()。

    跳過而不是當成 0:當成 0 會在曲線上畫出一段「這裡什麼都沒發生」,
    而實際上是「這裡發生了什麼我們不知道」。
    """
    equity = START_BALANCE
    curve = [equity]

    for trade in closed_trades:
        pnl = pnl_of(trade)
        if pnl is None:
            continue
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


# 沒有虧損單時的 Profit Factor。
#
# **999 是一個哨兵值,不是一個測量結果。** 正確的答案是「無限大」,
# 而無限大沒辦法放進 JSON、也沒辦法排序。這裡沿用舊行為(999)
# 是為了不改契約 —— 前端與 Telegram 日報都在讀這個欄位。
#
# 新程式碼請用 agmcis/review/attribution.py 的 Bucket.profit_factor,
# 它在同樣的情況回 None,而 None 在畫面上會顯示「—」。
NO_LOSSES_PROFIT_FACTOR = 999


def calculate_profit_factor(closed_trades):
    """損益不明的那幾筆不參與 —— 見 pnl_of()。"""
    values = [pnl_of(trade) for trade in closed_trades]
    values = [value for value in values if value is not None]

    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))

    if gross_loss == 0:
        return 0 if gross_profit == 0 else NO_LOSSES_PROFIT_FACTOR

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


def _empty_analytics(account, legacy_summary, include_legacy):
    """
    沒有可比較的交易時的樣子。

    每一格都是 0 而不是 None,是**刻意保留舊行為** —— 前端與
    Telegram 日報都直接讀這些欄位。這一輪只拆函式,不改契約:
    一次改兩件事,壞掉的時候分不出是哪一件造成的。
    """
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
        "pnl_basis": "MIXED" if include_legacy else "LEVERAGED",
        "unpriced_trades": 0,
    }


def summarise_pnl(closed_trades):
    """
    整體損益統計。純函式 —— 吃一份交易清單,回一份數字。

    平均獲利與平均虧損分開算,因為 risk_reward_ratio 需要它們。
    沒有虧損單時 risk_reward_ratio 回 0 而不是無限大 ——
    無限大在排序與顯示上都會出事。
    """
    values = [pnl_of(trade) for trade in closed_trades]
    values = [value for value in values if value is not None]

    total_trades = len(values)
    total_pnl = sum(values)

    wins_list = [value for value in values if value > 0]
    losses_list = [value for value in values if value < 0]

    avg_win = sum(wins_list) / len(wins_list) if wins_list else 0
    avg_loss = sum(losses_list) / len(losses_list) if losses_list else 0

    return {
        "total_trades": total_trades,
        "total_pnl": total_pnl,
        "total_return_pct": total_pnl / START_BALANCE * 100,
        "win_rate": len(wins_list) / total_trades * 100 if total_trades else 0,
        "avg_pnl": total_pnl / total_trades if total_trades else 0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "risk_reward_ratio": abs(avg_win / avg_loss) if avg_loss != 0 else 0,
    }


def summarise_by_symbol(closed_trades):
    """
    逐標的統計,依淨損益由高到低排序。

    打平的那一筆算在 losses 裡 —— 這是舊行為,保留。
    改成第三類會讓 wins + losses != trades,而前端假設它們相等。
    """
    symbol_map = {}

    for trade in closed_trades:
        pnl = pnl_of(trade)
        if pnl is None:
            continue

        symbol = trade["symbol"]
        stats = symbol_map.setdefault(symbol, {
            "symbol": symbol, "trades": 0, "wins": 0, "losses": 0, "pnl": 0,
        })

        stats["trades"] += 1
        stats["pnl"] += pnl
        stats["wins" if pnl > 0 else "losses"] += 1

    rows = list(symbol_map.values())

    for item in rows:
        item["win_rate"] = round(item["wins"] / item["trades"] * 100, 2)
        item["pnl"] = round(item["pnl"], 2)

    rows.sort(key=lambda item: item["pnl"], reverse=True)
    return rows


def get_trade_analytics(include_legacy=False):
    """
    交易統計。

    include_legacy=True 會把舊基準的交易也算進主統計。
    預設 False —— 兩種基準混在一起算出來的數字兩邊都不是。

    這個函式原本有 147 行,自己做完損益、逐標的、資金曲線、
    回撤、最佳最差標的全部的事(第九十五節的 God Function)。
    現在它只負責**組裝**,每一塊的算法各自是一個可以單獨測的純函式。
    """
    trades = load_trades()
    account = load_account()

    all_closed = [t for t in trades if t["status"] == "CLOSED"]
    comparable, legacy = split_by_pnl_basis(all_closed)

    closed_trades = all_closed if include_legacy else comparable
    legacy_summary = _legacy_summary(legacy)

    # 損益不明的那幾筆排除掉,但**把筆數講出來** ——
    # 加總對不起來會讓人以為統計算錯(第九十四節)。
    closed_trades, unpriced = with_pnl(closed_trades)

    if not closed_trades:
        payload = _empty_analytics(account, legacy_summary, include_legacy)
        payload["unpriced_trades"] = unpriced
        return payload

    totals = summarise_pnl(closed_trades)
    symbol_stats = summarise_by_symbol(closed_trades)
    equity_curve = build_equity_curve(closed_trades)

    recent_trades = list(closed_trades[-10:])
    recent_trades.reverse()

    return {
        "total_trades": totals["total_trades"],
        "total_pnl": round(totals["total_pnl"], 2),
        "total_return_pct": round(totals["total_return_pct"], 2),
        "win_rate": round(totals["win_rate"], 2),
        "avg_pnl": round(totals["avg_pnl"], 2),
        "avg_win": round(totals["avg_win"], 2),
        "avg_loss": round(totals["avg_loss"], 2),
        "risk_reward_ratio": round(totals["risk_reward_ratio"], 2),
        "profit_factor": calculate_profit_factor(closed_trades),
        "max_drawdown": calculate_max_drawdown(equity_curve),
        "best_symbol": symbol_stats[0]["symbol"],
        "worst_symbol": symbol_stats[-1]["symbol"],
        "symbol_stats": symbol_stats,
        "recent_trades": recent_trades,
        "equity_curve": equity_curve,
        "current_balance": round(account.get("balance", START_BALANCE), 2),
        "legacy": legacy_summary,
        "pnl_basis": "MIXED" if include_legacy else "LEVERAGED",
        # 已平倉但損益不明、因此沒有納入上面任何數字的筆數。
        "unpriced_trades": unpriced,
    }
