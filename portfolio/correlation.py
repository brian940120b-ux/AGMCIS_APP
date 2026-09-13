"""
相關性引擎 —— Master Prompt 第六十條 · 2026-09-13

═══ 為什麼需要這一支 ═══
風控目前有一條「總曝險上限」,擋的是 Σ名目 / 權益。它預設了一件事:
**七個倉是七個不同的賭注。**

這批幣不是。BTC、ETH、SOL、BNB、XRP、AAVE、UNI 在下跌的日子幾乎
一起跌 —— 加密貨幣的橫斷面相關性在恐慌時會往 1 靠攏,而那正是
風控唯一真的重要的時候。

所以「七個倉、每個 7%」在帳面上是分散,在相關性 0.9 的時候
實際上比較接近**一個 49% 的倉**。總曝險 49% 這個數字不會告訴你
這件事,它從頭到尾都只是加總。

2026-09-10 的事故是同一個形狀:六個倉全部在懸崖邊。它們不是六件
獨立的壞事,是同一件壞事的六個面。

═══ 這裡算什麼 ═══
給定各幣權重 w(名目 / 權益)與相關性矩陣 R:

    等效單一標的曝險 = √(wᵀ R w)
    有效持倉檔數(ENP) = (Σw)² / (wᵀ R w)

兩個數字來自同一個二次式,意思互補:

  · 七個 7% 的倉、相關性全部 = 1
      → 等效曝險 49%、有效檔數 1.0
      (帳面七檔,實際上就是一個 49% 的倉)

  · 七個 7% 的倉、完全不相關
      → 等效曝險 18.5%、有效檔數 7.0

√(wᵀRw) 之所以是「等效單一標的曝險」:在各幣波動相近時,
組合波動 = √(wᵀRw) × σ,而單一標的曝險 x 的波動是 x × σ。
兩者相等的 x 就是它。

═══ 為什麼用共同日期而不是逐對配對 ═══
逐對配對(每一對各用各自有資料的日子)看起來比較不浪費資料,
但算出來的矩陣可能不是半正定的 —— 那會讓 wᵀRw 變成負數,
然後 √ 一個負數。一個在極端情況下會給出無意義答案的風險指標,
比沒有那個指標更危險。

取交集,矩陣保證半正定。代價是資料少一點,而那個代價是誠實的。

═══ 算不出來就說算不出來 ═══
樣本不足、資料缺漏、某個幣沒有歷史 —— 一律回 None,不回 0、
不回 1、不回「假設不相關」。假設不相關是這裡所有錯誤裡最貴的一種:
它剛好在最危險的時候給出最樂觀的答案。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 算一組相關性最少要幾個共同觀測日。
#
# 依據:相關係數的標準誤約 (1−ρ²)/√(n−3)。n=60 時,ρ=0.8 的標準誤
# 約 0.047 —— 夠分辨 0.8 與 0.9。n=20 時是 0.09,兩者就分不開了。
# 而 0.8 與 0.9 在等效曝險上差得很多。
MIN_OVERLAP = 60


@dataclass(frozen=True)
class Concentration:
    """相關性看出來的集中度。算不到的欄位一律 None,不填預設值。"""

    effective_positions: float | None      # 有效持倉檔數
    equivalent_exposure_pct: float | None   # 等效單一標的曝險(%)
    total_exposure_pct: float               # 帳面總曝險(%),永遠算得出來
    positions: int                          # 帳面檔數
    observations: int                       # 共同觀測日數
    unmeasured: list                        # 沒有足夠歷史的幣
    reason: str | None                      # 算不出來時的理由

    @property
    def measured(self) -> bool:
        return self.effective_positions is not None

    def to_dict(self) -> dict:
        return {
            "effective_positions": self.effective_positions,
            "equivalent_exposure_pct": self.equivalent_exposure_pct,
            "total_exposure_pct": self.total_exposure_pct,
            "positions": self.positions,
            "observations": self.observations,
            "unmeasured": list(self.unmeasured),
            "reason": self.reason,
        }


def daily_returns(idx: dict, symbols, dates: list, i: int,
                  lookback: int) -> dict:
    """
    各幣到第 i 日為止的日報酬(對數報酬)。

    **只讀 dates[:i+1]。** 這條規則在整個專案裡只有一個意思:
    第 i 日做的決定,不可以用到第 i 日之後才知道的事(第三十四條)。

    回傳 {幣: {日期: 報酬}} —— 帶日期而不是純陣列,因為之後要取交集,
    而「第 3 筆」在不同幣可能是不同天。
    """
    out: dict[str, dict] = {}
    window = dates[max(0, i - lookback + 1): i + 1]

    for sym in symbols:
        bars = idx.get(sym) or {}
        series: dict = {}
        previous = None
        for day in window:
            bar = bars.get(day)
            close = getattr(bar, "c", None) if bar is not None else None
            if close is None or close <= 0:
                # 缺一天就斷開,不跨過缺口硬算報酬 —— 那個「報酬」
                # 其實是兩天以上的變化,會低估波動也扭曲相關性。
                previous = None
                continue
            if previous is not None:
                series[day] = math.log(close / previous)
            previous = close
        if series:
            out[sym] = series

    return out


def _common_dates(returns: dict, symbols) -> list:
    """所有幣都有報酬的日子。"""
    have = [set(returns[s]) for s in symbols if s in returns]
    if len(have) != len(list(symbols)) or not have:
        return []
    common = set.intersection(*have)
    return sorted(common)


def _pearson(a: list, b: list) -> float | None:
    n = len(a)
    if n < 2:
        return None
    mean_a, mean_b = sum(a) / n, sum(b) / n
    sa = sum((x - mean_a) ** 2 for x in a)
    sb = sum((x - mean_b) ** 2 for x in b)
    if sa <= 0 or sb <= 0:
        # 一整段完全沒動 —— 相關性無定義。不要回 0(那是「不相關」,
        # 是一個確定的判斷),回 None。
        return None
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    return cov / math.sqrt(sa * sb)


def matrix(returns: dict, symbols) -> tuple:
    """
    相關性矩陣。回傳 (R, 共同日數, 沒有足夠歷史的幣)。

    R 是 {幣: {幣: ρ}}。任何一對算不出來就整個回 None ——
    半殘的矩陣沒有辦法拿去算二次式,而「跳過那一對」等於
    偷偷假設它不相關。
    """
    symbols = list(symbols)
    unmeasured = [s for s in symbols
                  if len(returns.get(s) or {}) < MIN_OVERLAP]

    usable = [s for s in symbols if s not in unmeasured]
    if len(usable) < 2:
        return None, 0, unmeasured

    common = _common_dates(returns, usable)
    if len(common) < MIN_OVERLAP:
        return None, len(common), unmeasured

    series = {s: [returns[s][d] for d in common] for s in usable}

    R: dict = {s: {} for s in usable}
    for a_i, a in enumerate(usable):
        R[a][a] = 1.0
        for b in usable[a_i + 1:]:
            rho = _pearson(series[a], series[b])
            if rho is None:
                return None, len(common), unmeasured
            R[a][b] = R[b][a] = rho

    return R, len(common), unmeasured


def concentration(weights: dict, returns: dict) -> Concentration:
    """
    把權重與報酬歷史變成集中度。

    weights 用「名目 / 權益」的小數(0.07 = 7%),不是百分比。
    只看有部位的幣 —— 權重 0 的幣不影響二次式,但會讓
    「有效檔數」看起來比實際多。
    """
    live = {s: abs(w) for s, w in (weights or {}).items() if abs(w) > 1e-12}
    total_pct = sum(live.values()) * 100

    empty = Concentration(None, None, total_pct, len(live), 0, [], None)

    if not live:
        return Concentration(None, None, 0.0, 0, 0, [], "沒有部位")
    if len(live) == 1:
        # 一檔的時候相關性沒有意義,但答案是確定的:等效曝險就是它自己。
        return Concentration(1.0, total_pct, total_pct, 1, 0, [], None)

    R, observations, unmeasured = matrix(returns, live.keys())

    # 有部位卻量不到相關性的幣,**不可以**從二次式裡漏掉。
    #
    # 漏掉它等於把它當成不存在,而它的曝險真真實實地在帳上 ——
    # 算出來的集中度會比實際低,而且是在「有一個幣我們不了解」
    # 的時候給出比較樂觀的數字。方向剛好是反的。
    #
    # (第一版就是這樣寫的,測試抓到了。)
    if R is not None and set(R) != set(live):
        missing = sorted(set(live) - set(R))
        names = "、".join(m.replace("-USDT", "") for m in missing)
        return Concentration(
            None, None, total_pct, len(live), observations,
            sorted(set(unmeasured) | set(missing)),
            f"有部位但算不出相關性:{names} —— 少算它會低估集中度")

    if R is None:
        missing = "、".join(s.replace("-USDT", "") for s in unmeasured)
        return Concentration(
            None, None, total_pct, len(live), observations, unmeasured,
            f"共同觀測 {observations} 天(至少要 {MIN_OVERLAP} 天)"
            + (f";歷史不足:{missing}" if unmeasured else ""))

    syms = list(R)
    w = {s: live[s] for s in syms}

    # wᵀ R w
    quadratic = 0.0
    for a in syms:
        for b in syms:
            quadratic += w[a] * R[a][b] * w[b]

    if quadratic <= 0:
        # 取交集之後矩陣應該是半正定的,所以這一條理論上走不到。
        # 但「理論上走不到」不是把 √(負數) 留在風控裡的理由。
        return Concentration(
            None, None, total_pct, len(live), observations, unmeasured,
            "二次式非正定 —— 相關性矩陣有問題,不給答案")

    gross = sum(w.values())
    return Concentration(
        effective_positions=(gross ** 2) / quadratic,
        equivalent_exposure_pct=math.sqrt(quadratic) * 100,
        total_exposure_pct=total_pct,
        positions=len(live),
        observations=observations,
        unmeasured=unmeasured,
        reason=None,
    )
