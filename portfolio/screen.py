"""
合約幣種篩選 —— 為 U 本位標準合約挑交易池 · 2026-09-13

═══ 執政官要的 ═══
「就像我傳的幣種單一樣能夠篩選幣種,畢竟是做合約。」

═══ 這一支跟 portfolio/universe.py 的關係 ═══
`universe.screen()` 已經有了,而且它的原則是對的 ——
**只用結構性條件,不用預測性條件**:

  結構性(可以用):流動性門檻、歷史長度、交易所狀態。
    它們是「其他東西成立的前提」,不是策略選擇。
  預測性(不可以用):「漲最多的前 N 個」「動能最強的」。
    每一個都是自由參數,是過擬合的入口。
    2026-09-08 實測過「按離均線距離取前一半」——**輸給等權持有全部**。

這一支不重寫那些,它加上**這個產品自己的那一關**:

    這個幣在 U 本位標準合約上到底有沒有?

而那一關偏偏是最難答的:`contract/v1` **沒有 contracts 端點**。

═══ 所以每個幣有三種狀態,不是兩種 ═══
    ✓ 通過      —— 結構條件過了,而且這個產品認得它
    ✗ 擋下      —— 某一關明確沒過
    ? 不知道    —— 問不出來

**「不知道」不准被算成「通過」。** 這是這個專案反覆犯的那個錯:
一個因為沒問到而沒發現問題的檢查,如果回報通過,就是在說謊。

而且即使是 ✓ 也有保留:「allOrders 認得這個代號」是**必要條件,
不是充分條件** —— 它很可能拿全交易所的代號表驗證,而不是這個產品的。
完整名單只能在 App 上翻。這一支不假裝它知道。

═══ 為什麼流動性那一關對合約特別重要 ═══
成本模型的滑點 0.02% 是在**深盤口**實測的。實測漲幅榜的幣成交額只有
現役幣的 1/79,一張 700 USDT 的單會佔到日成交量的 0.24%
(BTC 是 0.0001%)—— 市場衝擊差 2,400 倍。

而合約還多一層:**流動性差的合約,強平時滑價更兇**。倉會不會被抬出去,
不只看價格走多遠,還看那一刻有沒有人接。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 每一關的名字。順序就是它們被檢查的順序。
GATES = ("交易所狀態", "流動性", "歷史長度", "標準合約認得")

PASS, BLOCK, UNKNOWN = "pass", "block", "unknown"


@dataclass
class Candidate:
    """一個候選幣,以及它每一關的結果。

    **每一關都留下答案**,不只留最後的通過與否 ——
    「為什麼這個幣不在裡面」必須是一個回答得出來的問題。
    """

    symbol: str                       # BTC-USDT(策略內部用)
    quote_volume: float | None = None
    bars: int | None = None
    gates: dict = field(default_factory=dict)   # 關名 -> pass/block/unknown
    notes: dict = field(default_factory=dict)   # 關名 -> 說明

    @property
    def app_symbol(self) -> str:
        """App 與 allPosition 用的無槓寫法。"""
        return self.symbol.replace("-", "")

    @property
    def blocked_by(self) -> list:
        return [g for g in GATES if self.gates.get(g) == BLOCK]

    @property
    def unknown_at(self) -> list:
        return [g for g in GATES if self.gates.get(g) == UNKNOWN]

    @property
    def tradable(self) -> bool:
        """每一關都明確通過才算數。

        **有任何一關是「不知道」就不算通過。** 一個因為沒問到而沒發現
        問題的檢查,如果回報通過,就是在說謊(第九十四條)。
        """
        return all(self.gates.get(g) == PASS for g in GATES)

    @property
    def verdict(self) -> str:
        if self.tradable:
            return "可以"
        if self.blocked_by:
            return "擋下:" + "、".join(self.blocked_by)
        return "不知道:" + "、".join(self.unknown_at or ["未檢查"])

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "app_symbol": self.app_symbol,
                "quote_volume": self.quote_volume, "bars": self.bars,
                "gates": dict(self.gates), "notes": dict(self.notes),
                "tradable": self.tradable, "verdict": self.verdict}


def evaluate(symbols, volumes: dict, bars: dict, recognised: dict,
             min_volume: float, min_bars: int,
             tradable_status: dict | None = None) -> list:
    """把各關的原始資料合成逐幣的結論。

    **這個函式不發任何請求** —— 資料由呼叫端餵進來,所以它可以在
    沒有網路、沒有金鑰的情況下被完整測試。

    volumes / bars / recognised 任何一個缺了某個幣,那一關就是
    「不知道」,**不是「沒過」也不是「過了」**。
    """
    out = []
    for sym in symbols:
        c = Candidate(symbol=sym,
                      quote_volume=volumes.get(sym),
                      bars=bars.get(sym))

        # 一、交易所狀態
        status = (tradable_status or {}).get(sym)
        if status is None:
            c.gates["交易所狀態"] = UNKNOWN
            c.notes["交易所狀態"] = "查不到合約規格"
        else:
            c.gates["交易所狀態"] = PASS if status else BLOCK
            if not status:
                c.notes["交易所狀態"] = "交易所標示不可交易"

        # 二、流動性
        vol = c.quote_volume
        if vol is None:
            c.gates["流動性"] = UNKNOWN
            c.notes["流動性"] = "問不到 24h 成交額"
        elif vol >= min_volume:
            c.gates["流動性"] = PASS
        else:
            c.gates["流動性"] = BLOCK
            c.notes["流動性"] = (
                f"24h 成交額 {vol:,.0f} < {min_volume:,.0f} —— "
                "滑點 0.02% 是在深盤口量出來的,薄的幣會把成本模型作廢;"
                "而合約還多一層:強平時沒有人接。")

        # 三、歷史長度
        n = c.bars
        if n is None:
            c.gates["歷史長度"] = UNKNOWN
            c.notes["歷史長度"] = "問不到日線"
        elif n >= min_bars:
            c.gates["歷史長度"] = PASS
        else:
            c.gates["歷史長度"] = BLOCK
            c.notes["歷史長度"] = (
                f"只有 {n} 根日線 < {min_bars} —— "
                "算不出 50 日均線的幣,策略根本無法運作")

        # 四、這個產品認不認得
        known = recognised.get(sym, recognised.get(c.app_symbol))
        if known is None:
            c.gates["標準合約認得"] = UNKNOWN
            c.notes["標準合約認得"] = "問不出來"
        elif known:
            c.gates["標準合約認得"] = PASS
            c.notes["標準合約認得"] = (
                "allOrders 認得這個代號 —— **必要條件,不是充分條件**。"
                "它可能拿全交易所的代號表驗證,而不是這個產品的清單。"
                "完整名單只能在 App 上翻。")
        else:
            c.gates["標準合約認得"] = BLOCK
            c.notes["標準合約認得"] = "allOrders 認不得 —— 這個產品沒有它"

        out.append(c)

    # 排序:能交易的在前,同組內成交額大的在前
    return sorted(out, key=lambda c: (not c.tradable,
                                      -(c.quote_volume or 0.0)))


def summary(candidates) -> dict:
    """一句話的統計。**「不知道」單獨算一格,不併進任何一邊。**"""
    return {
        "total": len(candidates),
        "tradable": sum(1 for c in candidates if c.tradable),
        "blocked": sum(1 for c in candidates
                       if c.blocked_by),
        "unknown": sum(1 for c in candidates
                       if not c.tradable and not c.blocked_by),
    }
