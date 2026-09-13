"""
候選訊號庫 · 2026-09-10

每個函式的介面與 `rules.ma_filter` 完全相同:
    f(i, dates, idx) -> {幣: 權重}
只用 dates[:i+1](含當日收盤),**看不到未來**。

參數一律教科書標準值(見 docs/preregistration-signal-lab.md),
**不搜尋、不調整**。訊號失敗後改參數就是一個新假說,必須重新登記。

═══ 為什麼要有這一支 ═══
執政官:「每個指標或每個策略方法都去試過測過之後再來評斷。」
他是對的 —— 我先前用「證據不足」推論「想法不行」,那是兩件事。
所以把候選訊號集中在這裡,一個一個跑同一套流程。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _series(idx: dict, sym: str, dates: list, i: int, n: int,
            field: str = "c") -> list[float]:
    """取第 i 日往前 n 根的某個欄位(不含未來)。"""
    out = []
    for j in range(max(0, i - n + 1), i + 1):
        b = idx.get(sym, {}).get(dates[j])
        if b is not None:
            out.append(getattr(b, field))
    return out


def _equal(on: list[str], universe: list[str]) -> dict[str, float]:
    """等權配置,分母固定為交易池大小 —— 與 ma_filter 同一個慣例,
    這樣不同訊號之間的曝險才可比。"""
    return {s: 1.0 / len(universe) for s in on} if on else {}


# ══════════════════════════════════════════════════════════
# 一、擺動結構反轉(執政官 2026-09-10 提出)
# ══════════════════════════════════════════════════════════
def swing_structure(symbols: list[str], n: int = 20):
    """收盤突破前 n 根的擺動高點 → 持有;跌破擺動低點 → 空手。

    這是「結構反轉」的**機械版**:結構 = 擺動高低點,突破 = 結構改變。
    主觀版(看圖說「這裡反轉了」)不能測,因為它事後看永遠很明顯、
    事前沒有明確定義 —— 那正是它危險的地方。
    """
    state: dict[str, bool] = {}

    def f(i, dates, idx):
        on = []
        for s in symbols:
            c = _series(idx, s, dates, i, n + 1)
            if len(c) < n + 1:
                continue
            prev, now = c[:-1], c[-1]
            hi, lo = max(prev), min(prev)
            if now > hi:
                state[s] = True
            elif now < lo:
                state[s] = False
            if state.get(s):
                on.append(s)
        return _equal(on, symbols)
    return f


# ══════════════════════════════════════════════════════════
# 二、RSI(14),30/70
# ══════════════════════════════════════════════════════════
def rsi_signal(symbols: list[str], n: int = 14,
               lo: float = 30.0, hi: float = 70.0):
    """RSI 由下向上穿越 30 → 持有;跌破 70 → 空手(經典超賣進場)。"""
    state: dict[str, bool] = {}

    def rsi(c):
        if len(c) < n + 1:
            return None
        gains = losses = 0.0
        for k in range(1, len(c)):
            d = c[k] - c[k - 1]
            gains += max(d, 0.0)
            losses += max(-d, 0.0)
        if losses <= 1e-12:
            return 100.0
        rs = (gains / (len(c) - 1)) / (losses / (len(c) - 1))
        return 100.0 - 100.0 / (1.0 + rs)

    def f(i, dates, idx):
        on = []
        for s in symbols:
            c = _series(idx, s, dates, i, n + 2)
            if len(c) < n + 2:
                continue
            r_now, r_prev = rsi(c[1:]), rsi(c[:-1])
            if r_now is None or r_prev is None:
                continue
            if r_prev <= lo < r_now:
                state[s] = True
            elif r_now > hi:
                state[s] = False
            if state.get(s):
                on.append(s)
        return _equal(on, symbols)
    return f


# ══════════════════════════════════════════════════════════
# 三、MACD(12/26/9)金叉死叉
# ══════════════════════════════════════════════════════════
def macd_signal(symbols: list[str], fast: int = 12, slow: int = 26,
                sig: int = 9):
    """MACD 上穿訊號線 → 持有,下穿 → 空手。"""
    def ema(vals, span):
        k = 2.0 / (span + 1)
        e = vals[0]
        out = [e]
        for v in vals[1:]:
            e = v * k + e * (1 - k)
            out.append(e)
        return out

    def f(i, dates, idx):
        on = []
        need = slow + sig + 5
        for s in symbols:
            c = _series(idx, s, dates, i, need)
            if len(c) < need:
                continue
            ef, es = ema(c, fast), ema(c, slow)
            line = [a - b for a, b in zip(ef, es)]
            sl = ema(line[slow - 1:], sig)
            if len(sl) < 2:
                continue
            m_now, m_prev = line[-1], line[-2]
            s_now, s_prev = sl[-1], sl[-2]
            if m_now > s_now and m_prev <= s_prev:
                on.append(s)
            elif m_now > s_now:
                on.append(s)
        return _equal(on, symbols)
    return f


# ══════════════════════════════════════════════════════════
# 四、布林通道(20, 2σ)突破
# ══════════════════════════════════════════════════════════
def bollinger_signal(symbols: list[str], n: int = 20, k: float = 2.0):
    """收盤突破上軌 → 持有;跌回中軌 → 空手。"""
    import statistics
    state: dict[str, bool] = {}

    def f(i, dates, idx):
        on = []
        for s in symbols:
            c = _series(idx, s, dates, i, n)
            if len(c) < n:
                continue
            m = sum(c) / len(c)
            sd = statistics.pstdev(c)
            if c[-1] > m + k * sd:
                state[s] = True
            elif c[-1] < m:
                state[s] = False
            if state.get(s):
                on.append(s)
        return _equal(on, symbols)
    return f


# ══════════════════════════════════════════════════════════
# 五、唐奇安通道(20)突破 —— 海龜交易法的核心
# ══════════════════════════════════════════════════════════
def donchian_signal(symbols: list[str], n: int = 20, exit_n: int = 10):
    """收盤創 n 日新高 → 持有;跌破 exit_n 日新低 → 空手。"""
    state: dict[str, bool] = {}

    def f(i, dates, idx):
        on = []
        for s in symbols:
            hi = _series(idx, s, dates, i, n + 1, "h")
            lo = _series(idx, s, dates, i, exit_n + 1, "l")
            c = _series(idx, s, dates, i, 1)
            if len(hi) < n + 1 or len(lo) < exit_n + 1 or not c:
                continue
            if c[-1] > max(hi[:-1]):
                state[s] = True
            elif c[-1] < min(lo[:-1]):
                state[s] = False
            if state.get(s):
                on.append(s)
        return _equal(on, symbols)
    return f


# ══════════════════════════════════════════════════════════
# 六、成交量突破(20 日均量 2 倍 + 收紅)
# ══════════════════════════════════════════════════════════
def volume_breakout(symbols: list[str], n: int = 20, mult: float = 2.0):
    """量能放大且收漲 → 持有;跌破 n 日均線 → 空手。"""
    state: dict[str, bool] = {}

    def f(i, dates, idx):
        on = []
        for s in symbols:
            c = _series(idx, s, dates, i, n + 1)
            v = _series(idx, s, dates, i, n + 1, "v")
            if len(c) < n + 1 or len(v) < n + 1:
                continue
            avg_v = sum(v[:-1]) / len(v[:-1])
            up = c[-1] > c[-2]
            if avg_v > 0 and v[-1] > avg_v * mult and up:
                state[s] = True
            elif c[-1] < sum(c[:-1]) / len(c[:-1]):
                state[s] = False
            if state.get(s):
                on.append(s)
        return _equal(on, symbols)
    return f


# ══════════════════════════════════════════════════════════
# 七、ATR 波動突破(14 日 ATR、1 倍)
# ══════════════════════════════════════════════════════════
def atr_breakout(symbols: list[str], n: int = 14, mult: float = 1.0):
    """收盤 > 前一日收盤 + ATR → 持有;< 前一日收盤 − ATR → 空手。"""
    state: dict[str, bool] = {}

    def f(i, dates, idx):
        on = []
        for s in symbols:
            h = _series(idx, s, dates, i, n + 1, "h")
            lo = _series(idx, s, dates, i, n + 1, "l")
            c = _series(idx, s, dates, i, n + 1)
            if min(len(h), len(lo), len(c)) < n + 1:
                continue
            trs = []
            for k in range(1, len(c)):
                trs.append(max(h[k] - lo[k], abs(h[k] - c[k - 1]),
                               abs(lo[k] - c[k - 1])))
            atr = sum(trs) / len(trs)
            if c[-1] > c[-2] + mult * atr:
                state[s] = True
            elif c[-1] < c[-2] - mult * atr:
                state[s] = False
            if state.get(s):
                on.append(s)
        return _equal(on, symbols)
    return f


# ══════════════════════════════════════════════════════════
# 候選清單 —— 新增一個就是一個新假說,必須計入測試次數
# ══════════════════════════════════════════════════════════
def candidates(symbols: list[str]) -> dict:
    return {
        "擺動結構反轉(20)": swing_structure(symbols, 20),
        "RSI(14,30/70)": rsi_signal(symbols, 14, 30, 70),
        "MACD(12/26/9)": macd_signal(symbols, 12, 26, 9),
        "布林通道(20,2σ)": bollinger_signal(symbols, 20, 2.0),
        "唐奇安通道(20/10)": donchian_signal(symbols, 20, 10),
        "成交量突破(20,2×)": volume_breakout(symbols, 20, 2.0),
        "ATR突破(14,1×)": atr_breakout(symbols, 14, 1.0),
    }
