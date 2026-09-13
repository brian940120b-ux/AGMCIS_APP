"""
對帳 —— Master Prompt 第十七條 · 2026-09-13(PHASE 3 第二步)

═══ 這一層要防的事 ═══
紙上帳本是一個**模擬**:它相信自己記的每一筆。實盤不是 ——
交易所那邊才是真相,而兩邊會不一樣的原因多到列不完:部分成交、
下單被拒、手動在 App 上改倉、強平、系統斷線期間發生的事。

沒有這一層,那些差異會**安靜地**累積,而帳本會一直很有自信。

═══ 兩件不同的事,不要混在一起 ═══
一、**欄位形狀對不對**(`check_fields`)
    `account.py` 的檔頭寫著它對齊 BingX Swap V2 的欄位名。
    那是一句**宣稱**,不是事實 —— 從來沒有人拿真的回應去對過。

    2026-09-13 拿 Demo 帳戶問到的餘額端點其實是 **v3** 不是 v2,
    這已經說明「照文件記憶寫死」有多危險。

二、**數量對不對**(`compare`)
    我方帳本說持有什麼、多少;交易所說持有什麼、多少。
    這一層只**報告**差異,**絕不自動修正任何一邊**。

═══ 為什麼「只報告,不修正」═══
自動修正有兩種寫法,兩種都是災難:

  · 拿交易所覆蓋帳本 —— 差異就此消失,而**造成差異的那個 bug
    永遠不會被發現**。
  · 拿帳本去交易所補單 —— 一個算錯的帳本會開始下真實的單。

差異是一個訊號,不是一個待辦事項。它要被人看見。

═══ 持倉欄位到現在還沒有被驗證過 ═══
2026-09-13 的 Demo 帳戶有 100,000 VST、**0 個持倉**。餘額那組欄位
因此被驗證了,持倉那組**一個字都沒有**。

而持倉欄位裡有 `liquidationPrice` —— 強平價。算錯的後果不是數字
難看,是倉沒了。

所以 `check_fields` 在沒有持倉時**明確回報「未驗證」,不回報「通過」**。
第一個 Demo 倉開出來的那一刻,它會是第一個說話的東西。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── 我方程式碼實際會讀的欄位 ─────────────────────────────
#
# 這兩份清單不是抄文件抄來的,是從 account.py / paper.py 實際用到的
# 欄位反推的。少一個,對應的那個計算就會拿到 None 或預設值。
#
# 標成 critical 的:少了它會讓**風險計算**出錯,而不只是顯示難看。
BALANCE_FIELDS = {
    "equity": True,
    "balance": True,
    "availableMargin": False,
    "usedMargin": False,
    "unrealizedProfit": False,
}

POSITION_FIELDS = {
    "positionAmt": True,        # 正=多 負=空。錯了方向就錯了
    "avgPrice": True,           # 開倉均價 —— 損益與強平價都靠它
    "liquidationPrice": True,   # 強平價。算錯的後果是倉沒了
    "markPrice": True,          # 強平用標記價判定,不是最新價
    "unrealizedProfit": False,
    "initialMargin": False,
    "leverage": False,
}

# 同義詞。交易所改版時欄位名會變(v2 -> v3 已經發生過一次),
# 所以認得幾個常見的別名,但**認出來要說**,不要靜靜換掉。
ALIASES = {
    "availableMargin": ("availableBalance", "available"),
    "usedMargin": ("usedBalance", "positionMargin"),
    "positionAmt": ("positionAmount", "amount"),
    "avgPrice": ("entryPrice", "averagePrice"),
    "liquidationPrice": ("liqPrice", "forceLiquidationPrice"),
    "unrealizedProfit": ("unrealizedPnl", "unRealizedProfit"),
}


# ══════════════════════════════════════════════════════════
# 產品不給的欄位:不是「缺」,也不是「有」,是**要我們補**
# ══════════════════════════════════════════════════════════
#
# 2026-09-13。U 本位標準合約(/openApi/contract/v1)的回應
# **結構性地**少了三個欄位 —— 官方欄位表就沒有,不是這次沒回到。
#
# 這種情況不能報成「缺欄位」:報成缺欄位會讓人去找 bug,
# 而那裡沒有 bug。也不能放寬期望值把它們拿掉:拿掉就等於宣稱
# 「我們不需要強平價」,而風控的硬閘需要。
#
# 第三種答案:**這個欄位由我方補,而且要標明是補的。**
#   · liquidationPrice -> exchange/bingx/standard_usdt.liquidation_price()
#   · markPrice        -> 只有 currentPrice,用它會有誤差
#   · equity           -> balance + crossUnPnl 可以湊,但那是推的
KNOWN_ABSENT = {
    "bingx-standard-usdt": {
        "持倉": {
            "liquidationPrice":
                "這個產品不回強平價 —— 由 standard_usdt.liquidation_price()"
                "算出來,線性逐倉公式,**偏樂觀**(未計維持保證金分層)",
            "markPrice":
                "只有 currentPrice(最新價)。強平是用標記價判定的,"
                "拿最新價估距離會有誤差",
        },
        "餘額": {
            "equity":
                "這個產品不回權益。balance + crossUnPnl 可以湊出一個數字,"
                "但那是**推的**,不是交易所說的",
        },
    },
}


@dataclass
class FieldReport:
    """一組欄位的檢查結果。"""

    what: str
    checked: bool                      # False = 沒有樣本可檢查
    present: list = field(default_factory=list)
    via_alias: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    missing_critical: list = field(default_factory=list)
    #: 這個產品**結構性地**不提供、而由我方補上的欄位。
    #: 它不算缺,但也**絕不是**交易所給的 —— 見 KNOWN_ABSENT。
    supplied_by_us: dict = field(default_factory=dict)
    reason: str | None = None

    @property
    def ok(self) -> bool:
        """
        沒檢查過**不算通過**。

        這是整支檔案最重要的一行:一個「因為沒有樣本所以沒發現問題」
        的檢查,如果回報通過,就是在說謊。
        """
        return self.checked and not self.missing_critical

    @property
    def fully_from_exchange(self) -> bool:
        """每一格都是交易所給的。

        `ok` 為 True 但這個是 False,意思是:**對得上,但其中幾格
        是我們自己算的。** 那個差別在強平價上是生死之別 ——
        我們算的偏樂觀,實際強平會更近。
        """
        return self.ok and not self.supplied_by_us

    def to_dict(self) -> dict:
        return {
            "what": self.what, "checked": self.checked, "ok": self.ok,
            "fully_from_exchange": self.fully_from_exchange,
            "present": list(self.present), "via_alias": dict(self.via_alias),
            "missing": list(self.missing),
            "missing_critical": list(self.missing_critical),
            "supplied_by_us": dict(self.supplied_by_us),
            "reason": self.reason,
        }


def check_fields(sample, expected: dict, what: str,
                 product: str | None = None) -> FieldReport:
    """
    拿交易所真的回傳的一筆資料,對照我方實際會讀的欄位。

    sample 給 None 或空 -> `checked=False`,而 `ok` 是 False。
    **「沒有樣本」不是「沒有問題」。**

    `product` 給了的話,`KNOWN_ABSENT` 裡登記過的欄位會被歸到
    `supplied_by_us`,而不是 `missing_critical` —— 因為那些欄位
    **交易所本來就不提供**,去找它是浪費時間。但它們也不會被當成
    通過:`fully_from_exchange` 會是 False。
    """
    if isinstance(sample, list):
        sample = sample[0] if sample else None
    if isinstance(sample, dict) and "balance" in sample and \
            isinstance(sample["balance"], dict):
        sample = sample["balance"]

    if not isinstance(sample, dict) or not sample:
        return FieldReport(
            what=what, checked=False,
            reason="沒有樣本可以對照 —— 這不代表欄位是對的,"
                   "代表還沒有人驗證過")

    absent = (KNOWN_ABSENT.get(product or "", {}) or {}).get(what, {})

    report = FieldReport(what=what, checked=True)
    for name, critical in expected.items():
        if name in sample:
            report.present.append(name)
            continue

        found = next((a for a in ALIASES.get(name, ()) if a in sample), None)
        if found:
            # 認得別名,但**要說出來** —— 靜靜換掉會讓「交易所改版了」
            # 這件事永遠不被發現。
            report.via_alias[name] = found
            continue

        if name in absent:
            # 這個產品本來就不給。**不報成缺,也不報成有。**
            report.supplied_by_us[name] = absent[name]
            continue

        report.missing.append(name)
        if critical:
            report.missing_critical.append(name)

    return report


@dataclass
class Difference:
    symbol: str
    kind: str                 # 只在帳本 / 只在交易所 / 數量不符
    ours: float | None
    theirs: float | None
    detail: str

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "kind": self.kind,
                "ours": self.ours, "theirs": self.theirs,
                "detail": self.detail}


ONLY_OURS = "只在帳本"
ONLY_THEIRS = "只在交易所"
AMOUNT = "數量不符"

# 數量差多少才算不符。
#
# 依據:交易所的數量精度最細到小數 8 位,而浮點運算會在末位造成
# 1e-12 級的雜訊。1e-8 是「比交易所能表達的最小單位還小」——
# 比它小的差異在交易所那邊根本不存在。
#
# **不要把這個值放大來讓紅字消失。** 真正的部分成交會遠大於它。
TOLERANCE = 1e-8


def _amount_of(row) -> float | None:
    """
    從交易所的持倉列拿數量。**拿不到就回 None,不回 0。**

    回 0 會讓「這個欄位我讀不懂」看起來像「這個倉是空的」,
    然後對帳就會安靜地說一切正常(第九十四條)。
    """
    if not isinstance(row, dict):
        return None
    for name in ("positionAmt",) + ALIASES["positionAmt"]:
        if row.get(name) is not None:
            try:
                value = float(row[name])
            except (TypeError, ValueError):
                return None
            side = str(row.get("positionSide") or "").upper()
            # 有些回應用 positionSide 表示方向而數量恆為正
            if side == "SHORT" and value > 0:
                return -value
            return value
    return None


def compare(ours: dict, theirs, tolerance: float = TOLERANCE) -> list:
    """
    比對兩邊的持倉。

    ours   {幣: 數量} —— 我方帳本
    theirs 交易所回傳的持倉清單

    回傳差異清單。**這個函式不會修改任何東西。**
    """
    exchange: dict = {}
    unreadable: list = []

    for row in (theirs or []):
        symbol = (row.get("symbol") if isinstance(row, dict) else None)
        if not symbol:
            unreadable.append(row)
            continue
        amount = _amount_of(row)
        if amount is None:
            unreadable.append(symbol)
            continue
        exchange[symbol] = exchange.get(symbol, 0.0) + amount

    out: list = []

    for symbol in unreadable:
        out.append(Difference(
            symbol=str(symbol), kind=ONLY_THEIRS, ours=None, theirs=None,
            detail="交易所回了這一筆,但讀不出數量 —— "
                   "當成「有倉但不知道多少」,不是當成沒有"))

    for symbol in sorted(set(ours) | set(exchange)):
        mine = float(ours.get(symbol) or 0.0)
        yours = exchange.get(symbol)

        if yours is None:
            if abs(mine) > tolerance:
                out.append(Difference(
                    symbol=symbol, kind=ONLY_OURS, ours=mine, theirs=0.0,
                    detail=f"帳本有 {mine:+.8g},交易所沒有這個倉"))
            continue

        if symbol not in ours:
            if abs(yours) > tolerance:
                out.append(Difference(
                    symbol=symbol, kind=ONLY_THEIRS, ours=0.0, theirs=yours,
                    detail=f"交易所有 {yours:+.8g},帳本沒有記到"))
            continue

        if abs(mine - yours) > tolerance:
            out.append(Difference(
                symbol=symbol, kind=AMOUNT, ours=mine, theirs=yours,
                detail=f"帳本 {mine:+.8g} vs 交易所 {yours:+.8g}"
                       f"(差 {yours - mine:+.8g})"))

    return out


@dataclass
class Reconciliation:
    balance_fields: FieldReport
    position_fields: FieldReport
    differences: list
    ours_count: int
    theirs_count: int

    @property
    def clean(self) -> bool:
        """
        兩本對得起來,**而且欄位形狀被驗證過**。

        差異為零但欄位沒驗證過,不算乾淨 —— 那可能只是因為
        我們讀不懂交易所的回應,所以什麼都沒看到。
        """
        return (not self.differences
                and self.balance_fields.ok
                and self.position_fields.ok)

    def to_dict(self) -> dict:
        return {
            "clean": self.clean,
            "ours_count": self.ours_count,
            "theirs_count": self.theirs_count,
            "balance_fields": self.balance_fields.to_dict(),
            "position_fields": self.position_fields.to_dict(),
            "differences": [d.to_dict() for d in self.differences],
        }


def reconcile(ours: dict, balance, positions,
              product: str | None = None) -> Reconciliation:
    """
    一次做完:欄位形狀 + 數量差異。

    `ours` 是 {幣: 數量}。`balance` / `positions` 是交易所的原始回應。
    `product` 是 adapter 的 name(例如 "bingx-standard-usdt")——
    它決定哪些欄位算「這個產品本來就不給」,見 KNOWN_ABSENT。
    """
    return Reconciliation(
        balance_fields=check_fields(balance, BALANCE_FIELDS, "餘額", product),
        position_fields=check_fields(positions, POSITION_FIELDS, "持倉",
                                     product),
        differences=compare(ours, positions),
        ours_count=sum(1 for v in ours.values() if abs(float(v or 0)) > TOLERANCE),
        theirs_count=len(positions or []),
    )
