def buy_signal(
    price,
    ema20,
    ema50,
    rsi,
    macd,
    macd_signal,
    volume,
    vol_ma,
    atr,
    adx
):
    return (
        price > ema20
        and macd > macd_signal
        and rsi > 55
        and volume > vol_ma
        and adx > 25
    )


def sell_signal(
    price,
    ema20,
    ema50,
    rsi,
    macd,
    macd_signal,
    volume,
    vol_ma,
    atr,
    adx
):
    return (
        macd < macd_signal
        or adx < 20
    )