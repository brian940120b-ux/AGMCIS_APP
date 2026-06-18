from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange


def analyze_symbol(symbol, df):
    close = df["close"]

    df["ema20"] = EMAIndicator(
        close=close,
        window=20
    ).ema_indicator()

    df["ema50"] = EMAIndicator(
        close=close,
        window=50
    ).ema_indicator()

    df["rsi"] = RSIIndicator(
        close=close,
        window=14
    ).rsi()

    macd = MACD(close=close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    adx = ADXIndicator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["adx"] = adx.adx()

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["atr"] = atr.average_true_range()

    df["vol_ma20"] = df["volume"].rolling(20).mean()

    last = df.iloc[-1]

    price = float(last["close"])
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    rsi = float(last["rsi"])
    macd_now = float(last["macd"])
    macd_signal = float(last["macd_signal"])
    adx_now = float(last["adx"])
    atr_now = float(last["atr"])
    volume = float(last["volume"])
    vol_ma20 = float(last["vol_ma20"])

    score = 0
    reasons = []

    if price > ema20:
        score += 15
        reasons.append("價格高於 EMA20")
    else:
        reasons.append("價格低於 EMA20")

    if ema20 > ema50:
        score += 20
        reasons.append("EMA20 高於 EMA50")
    else:
        reasons.append("EMA20 低於 EMA50")

    if 45 <= rsi <= 70:
        score += 15
        reasons.append("RSI 位於健康區間")
    elif rsi > 70:
        reasons.append("RSI 過熱")
    elif rsi < 30:
        reasons.append("RSI 超賣")
    else:
        reasons.append("RSI 訊號普通")

    if macd_now > macd_signal:
        score += 20
        reasons.append("MACD 偏多")
    else:
        reasons.append("MACD 偏空")

    if volume > vol_ma20:
        score += 15
        reasons.append("成交量高於20期均量")
    else:
        reasons.append("成交量不足")

    if adx_now > 25:
        score += 15
        reasons.append("ADX 顯示趨勢強")
    else:
        reasons.append("ADX 顯示趨勢不明顯")

    if score >= 75:
        signal = "做多"
        entry = price
        stoploss = price - atr_now * 1.5
        takeprofit = price + atr_now * 3

    elif score <= 25:
        signal = "做空"
        entry = price
        stoploss = price + atr_now * 1.5
        takeprofit = price - atr_now * 3

    else:
        signal = "觀望"
        entry = "-"
        stoploss = "-"
        takeprofit = "-"

    return {
        "symbol": symbol,
        "price": round(price, 4),
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4),
        "rsi": round(rsi, 2),
        "macd": round(macd_now, 4),
        "macd_signal": round(macd_signal, 4),
        "adx": round(adx_now, 2),
        "atr": round(atr_now, 4),
        "volume": round(volume, 2),
        "vol_ma20": round(vol_ma20, 2),
        "signal": signal,
        "score": score,
        "entry": round(entry, 4) if entry != "-" else "-",
        "stoploss": round(stoploss, 4) if stoploss != "-" else "-",
        "takeprofit": round(takeprofit, 4) if takeprofit != "-" else "-",
        "reason": "、".join(reasons)
    }