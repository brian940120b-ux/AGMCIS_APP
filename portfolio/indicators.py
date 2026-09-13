"""
新系統 — 技術指標 · 2026-09-08

═══ 參數一律用 BingX App 的預設值,一個都不搜 ═══
  MA 5/10/30 · EMA 15/30 · BOLL 20/2
  MACD 15/30/60 · KDJ 9/3/3 · RSI 15/30/60

為什麼這件事比指標本身重要:
每加一個可調的數字,搜尋空間就乘一次。3 個指標 × 3 個參數值 = 27 種組合,
在 27 種裡就算完全沒有優勢,最好的那個也一定會很好看 ——
那不是找到優勢,是排序造成的假象。

舊系統測了 1,391 個配置,以實測 σ=1.378R 計,預期假陽性約 52 個,
而實際通過只有 15 案 —— **比雜訊還低**。
所以這裡的紀律是:**用交易所的預設值,測完就算,不回頭換參數。**

═══ 這一支只算數字,不做決策 ═══
誰要用、怎麼組合、有沒有通過驗收,在 rules.py 與 judge 那邊。
"""
from __future__ import annotations

# ── BingX App 預設參數:不得為了讓結果好看而調整 ─────────────
MA_FAST, MA_MID, MA_SLOW = 5, 10, 30
EMA_FAST, EMA_SLOW = 15, 30
BOLL_N, BOLL_K = 20, 2.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 15, 30, 60
KDJ_N, KDJ_M1, KDJ_M2 = 9, 3, 3
RSI_FAST, RSI_MID, RSI_SLOW = 15, 30, 60
# ─────────────────────────────────────────────────────────


def sma(v: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(v: list[float], n: int) -> list[float | None]:
    if not v:
        return []
    k = 2.0 / (n + 1)
    out: list[float | None] = [v[0]]
    for i in range(1, len(v)):
        out.append(v[i] * k + out[-1] * (1 - k))
    return out


def macd(closes: list[float]) -> tuple[list, list, list]:
    """回傳 (DIF, DEA, 柱)。DIF > DEA 即一般所稱的「金叉狀態」。"""
    f, s = ema(closes, MACD_FAST), ema(closes, MACD_SLOW)
    dif = [a - b for a, b in zip(f, s)]
    dea = ema(dif, MACD_SIGNAL)
    bar = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, bar


def kdj(highs: list[float], lows: list[float], closes: list[float]):
    """KDJ(9,3,3)。K > D 為多方狀態。"""
    n = KDJ_N
    K: list[float | None] = []
    D: list[float | None] = []
    J: list[float | None] = []
    pk = pd = 50.0
    for i in range(len(closes)):
        if i < n - 1:
            K.append(None); D.append(None); J.append(None)
            continue
        hi = max(highs[i - n + 1:i + 1])
        lo = min(lows[i - n + 1:i + 1])
        rsv = 50.0 if hi - lo < 1e-12 else (closes[i] - lo) / (hi - lo) * 100
        pk = (rsv + (KDJ_M1 - 1) * pk) / KDJ_M1
        pd = (pk + (KDJ_M2 - 1) * pd) / KDJ_M2
        K.append(pk); D.append(pd); J.append(3 * pk - 2 * pd)
    return K, D, J


def rsi(closes: list[float], n: int) -> list[float | None]:
    """Wilder RSI。"""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    g = l_ = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0.0)
        l_ += max(-d, 0.0)
    ag, al = g / n, l_ / n
    out[n] = 100.0 if al <= 1e-12 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al <= 1e-12 else 100 - 100 / (1 + ag / al)
    return out


def boll(closes: list[float]) -> tuple[list, list, list]:
    """布林通道 (20, 2)。回傳 (上軌, 中軌, 下軌)。"""
    mid = sma(closes, BOLL_N)
    up: list[float | None] = []
    dn: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            up.append(None); dn.append(None); continue
        win = closes[i - BOLL_N + 1:i + 1]
        var = sum((x - m) ** 2 for x in win) / BOLL_N
        sd = var ** 0.5
        up.append(m + BOLL_K * sd)
        dn.append(m - BOLL_K * sd)
    return up, mid, dn


# ══════════════════════════════════════════════════════════
# 訊號:每個指標的**教科書標準用法**,一個都不自創
# 回傳 True = 看多,False = 看空/不看多,None = 資料不足
# ══════════════════════════════════════════════════════════
def sig_macd(closes: list[float], i: int) -> bool | None:
    if i < MACD_SLOW:
        return None
    dif, dea, _ = macd(closes[:i + 1])
    return dif[-1] > dea[-1]


def sig_kdj(highs, lows, closes, i: int) -> bool | None:
    if i < KDJ_N + 2:
        return None
    K, D, _ = kdj(highs[:i + 1], lows[:i + 1], closes[:i + 1])
    return None if K[-1] is None else K[-1] > D[-1]


def sig_rsi(closes: list[float], i: int) -> bool | None:
    if i < RSI_FAST + 1:
        return None
    r = rsi(closes[:i + 1], RSI_FAST)
    return None if r[-1] is None else r[-1] > 50.0


def sig_ma(closes: list[float], i: int) -> bool | None:
    if i < MA_SLOW:
        return None
    f = sma(closes[:i + 1], MA_FAST)[-1]
    s = sma(closes[:i + 1], MA_SLOW)[-1]
    return None if (f is None or s is None) else f > s


def sig_boll(closes: list[float], i: int) -> bool | None:
    if i < BOLL_N:
        return None
    _, mid, _ = boll(closes[:i + 1])
    return None if mid[-1] is None else closes[i] > mid[-1]


SIGNALS = {
    "MACD 金叉(DIF>DEA)": "macd",
    "KDJ 多方(K>D)": "kdj",
    "RSI15 > 50": "rsi",
    "MA5 > MA30": "ma",
    "BOLL 中軌之上": "boll",
}
