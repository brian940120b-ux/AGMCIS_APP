BONUS_MAP = {
    "WLD/USDT": 10,
    "XLM/USDT": 8,
    "ETH/USDT": 3,
    "XRP/USDT": 2,

    "HYPE/USDT": -10,
    "OKB/USDT": -10,
    "USDG/USDT": -10,
    "BENZ/USDT": -10,
    "RAINPROTOCOL/USDT": -10,
    "BASED/USDT": -10,
    "LIBBY/USDT": -10,
    "INTE/USDT": -10
}

def get_optimizer_bonus(symbol):
    return BONUS_MAP.get(symbol, 0)
