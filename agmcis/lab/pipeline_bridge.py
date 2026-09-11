"""
把**實際在用的訊號管線**接到回測引擎上。

## 為什麼需要這個

系統裡有兩組策略:

    agmcis/strategy/builtin.py   TrendFollowing / Breakout / Momentum /
                                 MeanReversion —— **live 實際在用的**
    strategies/*.py              ema / rsi / breakout —— 舊的模組式策略

Phase 8 的 Strategy Lab 驗證的是**第二組**。也就是說:
LIVE SAFETY GATE 第 4 項「至少一個策略通過樣本外驗證」,
驗的是一組**永遠不會下單的策略**。

那是 Phase 6 與 Phase 9 處理過兩次的同一類問題:兩條路徑,
而實際生效的是哪一條沒有人說得清楚。這裡把它收掉。

## 做法

回測引擎要的是 `signal_fn(history, index)`。訊號管線要的是
`(Indicators, MarketRegime)`。中間差的就是「把第 i 根的指標算出來」。

指標**一次算完整段**,再逐根取值 —— 每一根都重算一次是 O(n²),
在 1500 根上會慢到不能用。

這樣做不會偷看未來:EMA、RSI、MACD、ADX、ATR、布林、量能均線
每一列的值都只依賴它自己以前的價格。Phase 8 有一條測試
(`test_indicator_values_do_not_change_when_future_candles_are_added`)
把這件事釘住 —— 加上後面的 K 棒不會改變前面任何一列的值。

## 參數

Lab 需要一個可以掃的參數。這裡用 `min_score`(訊號分數門檻)——
它是 Phase 6 定下的「真正的品質門檻」,而且它是**單一參數**:
參數越多越容易在歷史上擬合出漂亮的曲線。
"""
import logging

from agmcis.analysis import indicators as indicators_module
from agmcis.analysis import regime as regime_module
from agmcis.analysis.indicators import Indicators
from agmcis.signal import scorer as scorer_module
from agmcis.strategy.registry import StrategyRegistry

logger = logging.getLogger("agmcis.lab.pipeline_bridge")

WARMUP_BARS = 60

# 指標欄位 -> Indicators 欄位。一次算完整段之後逐根取值。
INDICATOR_COLUMNS = (
    "ema20", "ema50", "ema60", "rsi", "macd", "macd_signal", "macd_hist",
    "adx", "atr", "bb_upper", "bb_lower", "volume_ma20",
)


def precompute(df, symbol="LAB", timeframe="1h"):
    """
    一次把整段的指標算好,回傳加了指標欄位的 DataFrame。

    用的是 `indicators.compute()` 內部同一套計算 —— 直接複製一份公式
    會讓 Lab 驗的東西與 live 用的東西悄悄分岔,那正是這個模組要修的問題。
    """
    from ta.momentum import RSIIndicator
    from ta.trend import ADXIndicator, EMAIndicator, MACD
    from ta.volatility import AverageTrueRange, BollingerBands

    frame = df.copy()
    close, high, low, volume = (
        frame["close"], frame["high"], frame["low"], frame["volume"],
    )

    macd_obj = MACD(close)
    bb = BollingerBands(close, window=20, window_dev=2)

    frame["ema20"] = EMAIndicator(close, window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(close, window=50).ema_indicator()
    frame["ema60"] = EMAIndicator(close, window=60).ema_indicator()
    frame["rsi"] = RSIIndicator(close, window=14).rsi()
    frame["macd"] = macd_obj.macd()
    frame["macd_signal"] = macd_obj.macd_signal()
    frame["macd_hist"] = macd_obj.macd_diff()
    frame["adx"] = ADXIndicator(high=high, low=low, close=close, window=14).adx()
    frame["atr"] = AverageTrueRange(
        high=high, low=low, close=close, window=14,
    ).average_true_range()
    frame["bb_upper"] = bb.bollinger_hband()
    frame["bb_lower"] = bb.bollinger_lband()
    frame["volume_ma20"] = volume.rolling(20).mean()

    return frame


# 與 indicators.compute() 一致:這幾個是 NaN 就整組不可用,
# 不要讓部分 None 混進評分。
CRITICAL = ("ema20", "ema50", "rsi", "macd", "macd_signal", "atr")


def indicators_at(frame, index, symbol="LAB", timeframe="1h"):
    """
    取第 index 根的指標。資料不足或含 NaN 時回傳 data_ok=False 的物件 ——
    與 live 的行為一致,而不是回 None 讓呼叫端自己判斷。
    """
    if index >= len(frame):
        return Indicators(
            symbol=symbol, timeframe=timeframe,
            data_ok=False, data_error="index_out_of_range",
        )

    row = frame.iloc[index]
    values = {name: _value(row, name) for name in INDICATOR_COLUMNS}

    result = Indicators(
        symbol=symbol,
        timeframe=timeframe,
        price=_value(row, "close"),
        volume=_value(row, "volume"),
        **values,
    )

    missing = [name for name in CRITICAL if getattr(result, name) is None]
    if missing:
        result.data_ok = False
        result.data_error = f"nan_indicators: {','.join(missing)}"
        return result

    if result.bb_upper and result.bb_lower and result.price:
        result.bb_width_pct = (
            (result.bb_upper - result.bb_lower) / result.price * 100
        )

    return result


def _value(row, name):
    if name not in row:
        return None
    value = row[name]
    # NaN != NaN
    if value is None or value != value:
        return None
    return float(value)


def build_signal_fn(df, params=None, symbol="LAB", timeframe="1h",
                    registry=None):
    """
    回傳回測引擎要的 signal_fn。

    params 目前只認 `min_score`。刻意只留一個可掃的參數 ——
    參數越多越容易在歷史上擬合出漂亮的曲線(Phase 8 的理由)。
    """
    params = params or {}
    min_score = params.get("min_score")
    registry = registry or StrategyRegistry()

    frame = precompute(df, symbol=symbol, timeframe=timeframe)

    def signal_fn(history, index):
        indicators = indicators_at(frame, index, symbol=symbol, timeframe=timeframe)

        if not indicators.data_ok:
            return None

        regime = regime_module.detect(indicators)
        consensus = registry.consensus(indicators, regime)

        if not consensus.is_actionable:
            return None

        entry = indicators.price
        if not entry or consensus.stop_loss is None:
            return None

        # 評分是 Phase 6 定下的真正品質門檻 —— Lab 也要走同一道。
        if min_score is not None:
            breakdown = scorer_module.score(
                consensus, indicators, regime,
                _risk_reward(entry, consensus.stop_loss, consensus.take_profit),
            )
            if breakdown.total < min_score:
                return None

        return {
            "direction": consensus.direction.value,
            "stop_loss": consensus.stop_loss,
            "take_profit": consensus.take_profit,
        }

    return signal_fn


def _risk_reward(entry, stop_loss, take_profit):
    if not entry or stop_loss is None or take_profit is None:
        return None
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    return abs(take_profit - entry) / risk


def default_param_sets():
    """
    要掃的參數組合。只有一個維度:訊號分數門檻。

    門檻越高交易越少 —— 這正是 Lab 要回答的取捨:
    嚴一點的門檻是不是真的換來更好的期望值,還是只是讓樣本變小到看不出東西。
    """
    return [{"min_score": score} for score in (0, 55, 65, 75)]
