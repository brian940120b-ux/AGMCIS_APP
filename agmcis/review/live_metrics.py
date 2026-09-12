"""
實盤 / 模擬盤的績效指標(Master Prompt 第三十節 Agent 10、第六十一節)。

第三十節的 Performance Analyst 列了十一項:Win Rate、Profit Factor、
Expectancy、Sharpe、Sortino、Drawdown、Average Win、Average Loss、
MFE、MAE、Holding Time。

`agmcis/backtest/metrics.py` 早就全部算得出來 —— **但那是回測**。
真正發生過的交易只有勝率、PF 與期望值,而那三個答不出
「這套系統的報酬對得起它的波動嗎」。

## 兩邊為什麼不共用同一個模組

回測的 Metrics 吃的是引擎產生的 Trade 物件,有逐根 K 棒的資料;
這裡吃的是資料庫的 dict,只有進出場與 position_monitor 記下來的
MFE / MAE。強行共用的話,共用的那一份要能處理兩種資料源 ——
而它會在其中一邊算出一個「技術上正確但意義不同」的數字。

分開的代價是兩份公式。所以這裡的每一個指標都在
`tests/test_live_metrics.py` 裡跟回測那一份對過。

## 樣本不足時回 None

Sharpe 需要至少兩筆才有標準差;三筆算出來的 Sharpe 是一個數字,
但不是一個結論。低於門檻一律回 None,而呼叫端顯示「—」。
一個用五筆交易算出來的 Sharpe 2.4,比沒有這個數字更誤導。
"""
import logging
import math
import statistics
from datetime import datetime

logger = logging.getLogger("agmcis.review.live_metrics")

# 低於這個筆數不算比率型指標(Sharpe / Sortino)。
# 與 attribution.MIN_SAMPLE 同一個數字 —— 兩邊用不同門檻,
# 會讓同一批交易在兩份報告裡一份可信一份不可信。
MIN_SAMPLE = 20

# 每年交易日。年化用。加密貨幣全年無休,所以是 365 不是 252。
DAYS_PER_YEAR = 365


def _pnl(trade):
    value = trade.get("pnl_usdt")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def closed(trades):
    return [
        trade for trade in (trades or [])
        if trade.get("status") == "CLOSED" and _pnl(trade) is not None
    ]


def _returns(trades):
    """
    每筆交易的報酬率(佔保證金的比例)。

    用比例不用絕對金額算 Sharpe —— 帳戶規模會變,而一筆在 1000 USDT
    帳戶上賺 50 的交易,跟在 10000 USDT 帳戶上賺 50 的交易,
    風險調整後不是同一件事。

    保證金拿不到的那幾筆會被**排除**,不是當成 0 ——
    分母不知道的時候,報酬率沒有答案。
    """
    values = []

    for trade in trades:
        margin = trade.get("size_usdt")
        try:
            margin = float(margin) if margin is not None else None
        except (TypeError, ValueError):
            margin = None

        if not margin or margin <= 0:
            continue

        values.append(_pnl(trade) / margin)

    return values


def sharpe(trades, min_sample=MIN_SAMPLE):
    """
    Sharpe(逐筆,未年化)。

    **無風險利率當 0。** 這不是懶惰:期貨交易的保證金不會拿去買
    國債,所以那個減項在這裡沒有對應的機會成本。寫死 0 並說明,
    比填一個看起來嚴謹但沒有依據的數字誠實。

    標準差為 0(每一筆都一樣)時回 None,不回無限大。
    """
    values = _returns(trades)

    if len(values) < min_sample:
        return None

    deviation = statistics.pstdev(values)
    if deviation == 0:
        return None

    return statistics.fmean(values) / deviation


def sortino(trades, min_sample=MIN_SAMPLE):
    """
    Sortino。與 Sharpe 的差別:分母只算**下檔**波動。

    往上的波動不是風險 —— 一個常常大賺的策略在 Sharpe 上會被
    處罰,在 Sortino 上不會。

    沒有任何一筆虧損時回 None,不回無限大:無限大排序永遠第一,
    而「還沒虧過」通常只代表樣本不夠長。
    """
    values = _returns(trades)

    if len(values) < min_sample:
        return None

    downside = [value for value in values if value < 0]
    if not downside:
        return None

    deviation = math.sqrt(statistics.fmean([value ** 2 for value in downside]))
    if deviation == 0:
        return None

    return statistics.fmean(values) / deviation


def _hours(trade):
    opened, closed_at = trade.get("opened_at"), trade.get("closed_at")

    for value in (opened, closed_at):
        if value is None:
            return None

    try:
        if isinstance(opened, str):
            opened = datetime.fromisoformat(opened)
        if isinstance(closed_at, str):
            closed_at = datetime.fromisoformat(closed_at)
        delta = (closed_at - opened).total_seconds() / 3600
    except Exception:
        return None

    # 負的持倉時間代表資料有問題。排除而不是取絕對值 ——
    # 取絕對值會讓一筆時間戳寫反的交易看起來正常。
    return delta if delta >= 0 else None


def holding_hours(trades):
    """
    持倉時間統計。回傳 (平均, 中位數, 有幾筆算得出來)。

    中位數一起回:少數幾筆放了一個月的交易會把平均拉得很難看,
    而那兩個數字差很多本身就是一個訊號。
    """
    values = [h for h in (_hours(t) for t in trades) if h is not None]

    if not values:
        return None, None, 0

    return statistics.fmean(values), statistics.median(values), len(values)


def _excursions(trades, field):
    """
    MFE 或 MAE。**None 的那幾筆會被排除,不是當成 0** ——
    migration 010 之前開的倉沒有量測過,把它們當成 0 會讓
    「這筆從來沒有浮虧」變成統計事實。
    """
    values = []

    for trade in trades:
        value = trade.get(field)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue

    return values


def excursions(trades):
    """
    MFE / MAE 統計(第三十節 Agent 10)。

    `measured` 是有量測到的筆數。它一定要顯示 —— 十筆交易裡只有
    兩筆有 MFE 的時候,那個平均值不代表這個系統。
    """
    favourable = _excursions(trades, "max_favourable_pct")
    adverse = _excursions(trades, "max_adverse_pct")

    return {
        "avg_mfe_pct": statistics.fmean(favourable) if favourable else None,
        "avg_mae_pct": statistics.fmean(adverse) if adverse else None,
        # 虧損單的 MAE 中位數回答「停損是不是設得太緊」——
        # 見 migrations/010_excursions.sql。
        "worst_mae_pct": min(adverse) if adverse else None,
        "best_mfe_pct": max(favourable) if favourable else None,
        "measured": len(favourable),
        "total": len(trades),
    }


def compute(trades):
    """
    第三十節 Agent 10 列的全部指標,能算的都算。

    算不出來的一律 None —— 在儀表板上「—」與「0」必須不一樣。
    """
    rows = closed(trades)

    from agmcis.review.attribution import Bucket

    bucket = Bucket(key="all")
    for trade in rows:
        bucket.add(_pnl(trade))

    average, median, timed = holding_hours(rows)

    payload = {
        "trades": bucket.trades,
        "wins": bucket.wins,
        "losses": bucket.losses,
        "win_rate": round(bucket.win_rate, 2) if bucket.trades else None,
        "profit_factor": (
            round(bucket.profit_factor, 3)
            if bucket.profit_factor is not None else None
        ),
        "expectancy_usdt": (
            round(bucket.expectancy, 4) if bucket.trades else None
        ),
        "avg_win": (
            round(bucket.gross_profit / bucket.wins, 4) if bucket.wins else None
        ),
        "avg_loss": (
            round(-bucket.gross_loss / bucket.losses, 4)
            if bucket.losses else None
        ),
        "net_pnl": round(bucket.net_pnl, 4) if bucket.trades else None,
        "avg_holding_hours": round(average, 2) if average is not None else None,
        "median_holding_hours": round(median, 2) if median is not None else None,
        "holding_measured": timed,
        # 樣本夠不夠下結論。第二節:一份沒說樣本數的勝率,
        # 連誠不誠實都判斷不了。
        "reliable": bucket.trades >= MIN_SAMPLE,
        "min_sample": MIN_SAMPLE,
    }

    ratio = sharpe(rows)
    payload["sharpe"] = round(ratio, 4) if ratio is not None else None

    ratio = sortino(rows)
    payload["sortino"] = round(ratio, 4) if ratio is not None else None

    payload.update({
        key: (round(value, 4) if isinstance(value, float) else value)
        for key, value in excursions(rows).items()
    })

    return payload
