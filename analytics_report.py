"""
Telegram 的績效摘要。

## 這支檔案修過兩個會讓數字說謊的 bug

**一、`float(t.get("pnl_usdt") or 0)`。**
損益是 None 的交易被當成 0:它進了勝率的分母當作一筆非勝利,
而且從「已實現收益」裡安靜地消失。一筆損益不明的交易被當成打平,
會讓兩個數字同時偏低,而看報告的人不會知道。

**二、混用兩種損益基準。**
`get_closed_trades()` 回傳的包含 Phase 0.5 之前的舊資料,那些交易的
損益漏乘了槓桿。儀表板用 `analytics.split_by_pnl_basis()` 把它們分開,
這支檔案沒有 —— 於是同一個系統的 Telegram 勝率與網頁勝率不一樣,
而那種不一致會讓人不知道該相信哪一個。

現在兩邊走**同一個** `analytics.get_trade_analytics()`,所以不可能再分岔。
"""
from analytics import get_trade_analytics
from notifier import send_telegram


def build_report(stats=None):
    """
    報告內容。純函式,所以測試不用發 Telegram。
    """
    if stats is None:
        stats = get_trade_analytics()

    lines = [
        "📊 AGMCIS Analytics Report",
        "",
        f"已平倉交易:{stats['total_trades']}",
        f"勝率:{stats['win_rate']}%",
        "",
        f"已實現收益:{stats['total_pnl']} USDT",
        f"最佳交易:{_pnl_of(stats.get('best'))} USDT",
        f"最差交易:{_pnl_of(stats.get('worst'))} USDT",
    ]

    # 排除掉的資料要講。一份沒說自己漏了什麼的報告,
    # 讀起來跟一份完整的報告一模一樣。
    unpriced = stats.get("unpriced_trades") or 0
    if unpriced:
        lines += ["", f"⚠️ 有 {unpriced} 筆已平倉但損益不明,未納入以上數字。"]

    legacy = stats.get("legacy") or {}
    if legacy.get("count"):
        lines += [
            "",
            f"另有 {legacy['count']} 筆舊基準交易(損益漏乘槓桿),"
            f"**不可與以上數字比較**。",
        ]

    lines += ["", f"損益基準:{stats.get('pnl_basis', '?')}"]
    return "\n".join(lines)


def _pnl_of(trade):
    """
    best / worst 是整筆交易的 dict。取不到就顯示「—」而不是 0 ——
    「最佳交易 0 USDT」讀起來像一個真的結果。
    """
    if not isinstance(trade, dict):
        return "—"

    value = trade.get("pnl_usdt")
    return round(float(value), 2) if value is not None else "—"


def send_analytics_report():
    send_telegram(build_report())


if __name__ == "__main__":
    send_analytics_report()
