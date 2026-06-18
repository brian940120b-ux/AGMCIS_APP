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
        rsi < 30
        and volume > vol_ma
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
    return rsi > 60