"""
技術指標計算。

Phase 0.5 的修正:原本整段 try/except 是 `except Exception: pass`,
指標算失敗時回傳全 None 與 trend="UNKNOWN",不記錄也不告警。
下游評分函式遇到 None 會給出 confidence = 50 的「中性」分數,
系統因此無法區分「市場中性」與「資料壞掉」。

現在:
  1. 失敗會被記錄(含 symbol / timeframe / 例外內容)。
  2. 回傳值帶上 data_ok 與 data_error,讓訊號層可以據此回 NO TRADE。
  3. K 棒數量不足以算出指標時也視為 data_ok=False,而不是硬算出無意義的數字。
"""
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from logger_service import logger
from market_data import get_ohlcv, get_price

MIN_CANDLES = 60  # EMA60 需要的最小長度


def _empty(symbol, price, reason):
    return {
        "symbol": symbol,
        "price": price,
        "rsi": None,
        "macd": None,
        "macd_signal": None,
        "macd_hist": None,
        "atr": None,
        "ema20": None,
        "ema60": None,
        "trend": "UNKNOWN",
        "signal": "HOLD",
        "data_ok": False,
        "data_error": reason,
    }


def get_indicators(symbol, timeframe="1h"):
    price = get_price(symbol)

    try:
        df = get_ohlcv(symbol, timeframe, 100)
    except Exception as exc:
        logger.error("get_indicators | OHLCV 取得失敗 | %s %s | %s", symbol, timeframe, exc)
        return _empty(symbol, price, f"ohlcv_fetch_failed: {exc}")

    if df is None or len(df) < MIN_CANDLES:
        have = 0 if df is None else len(df)
        logger.warning(
            "get_indicators | K 棒不足 | %s %s | 需要 %d 根,只有 %d 根",
            symbol, timeframe, MIN_CANDLES, have,
        )
        return _empty(symbol, price, f"insufficient_candles: {have}/{MIN_CANDLES}")

    try:
        close = df["close"]

        rsi = round(float(RSIIndicator(close, window=14).rsi().iloc[-1]), 2)
        ema20 = round(float(EMAIndicator(close, window=20).ema_indicator().iloc[-1]), 4)
        ema60 = round(float(EMAIndicator(close, window=60).ema_indicator().iloc[-1]), 4)

        macd_obj = MACD(close)
        macd = round(float(macd_obj.macd().iloc[-1]), 4)
        macd_signal = round(float(macd_obj.macd_signal().iloc[-1]), 4)
        macd_hist = round(float(macd_obj.macd_diff().iloc[-1]), 4)

        atr = round(float(
            AverageTrueRange(
                high=df["high"], low=df["low"], close=close, window=14,
            ).average_true_range().iloc[-1]
        ), 4)

    except Exception as exc:
        logger.exception(
            "get_indicators | 指標計算失敗 | %s %s | %s", symbol, timeframe, exc,
        )
        return _empty(symbol, price, f"indicator_calc_failed: {exc}")

    # 任何一個關鍵指標是 NaN 都不能當成有效資料。
    values = {"rsi": rsi, "ema20": ema20, "ema60": ema60,
              "macd": macd, "macd_signal": macd_signal, "macd_hist": macd_hist, "atr": atr}
    nan_keys = [k for k, v in values.items() if v != v]

    if nan_keys:
        logger.warning(
            "get_indicators | 指標含 NaN | %s %s | %s", symbol, timeframe, nan_keys,
        )
        return _empty(symbol, price, f"nan_indicators: {','.join(nan_keys)}")

    return {
        "symbol": symbol,
        "price": price,
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr": atr,
        "ema20": ema20,
        "ema60": ema60,
        "trend": "BULLISH" if ema20 > ema60 else "BEARISH",
        "signal": "HOLD",
        "data_ok": True,
        "data_error": None,
    }
