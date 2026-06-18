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
        and ema20 > ema50
        and 45 <= rsi <= 70
        and macd > macd_signal
        and volume > vol_ma
        and adx > 25
        and atr > 0
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
        price < ema20
        or macd < macd_signal
        or adx < 20
    )