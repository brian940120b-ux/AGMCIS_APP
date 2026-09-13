"""
U 本位標準合約 —— 交易所少給的,我們自己算 · 2026-09-13

執政官 2026-09-13 裁定:**「補充做標準合約U本位」**。
這一支就是那個決定的落點。

═══ 先講清楚它缺什麼 ═══
`/openApi/contract/v1` 只有三個 GET 端點,而且回應少了三樣關鍵的東西。
下面每一行都是拿官方欄位表 + 2026-09-13 的實測回應對過的:

  持倉回應(allPosition)有:
      symbol / positionAmt / entryPrice / positionSide / leverage /
      isolated / initialMargin / unrealizedProfit / currentPrice / time
  **沒有 liquidationPrice** —— 強平價
  **沒有 markPrice**        —— 強平是用標記價判定的,不是最新價
  餘額回應(balance)**沒有 equity** —— 只有 balance / availableBalance /
      crossWalletBalance / crossUnPnl / maxWithdrawAmount

第一項最要命:風控的**強平距離下限**是硬閘(第十九條),
而它需要強平價。交易所不給,不代表這個數字不存在 —— 它只是要我們算。

═══ 我們算得出來,因為它是線性合約 ═══
2026-09-13 拿實測持倉驗過:

    FLOCKUSDT SHORT  數量 3911.11  開倉價 0.08088  槓桿 20  逐倉
    名目 = 3911.11 × 0.08088          = 316.33
    名目 ÷ 20                          = 15.82
    交易所回的 initialMargin           = 15.84   ← 對得上

**保證金 = 名目 ÷ 槓桿**,這就是線性(U 本位)合約的定義。
所以 `portfolio/account.py` 那條已經核對過的強平價公式**直接適用**:

    多單 = 開倉價 × (1 − 1/L + MMR)
    空單 = 開倉價 × (1 + 1/L − MMR)

這是 U 本位相對幣本位最大的優勢:**帳本不用重寫。**
幣本位是反向合約,盈虧對價格非線性,account.py 每一條算式都得改。

═══ 但這個強平價是「我們算的」,不是「交易所給的」═══
兩者的差別必須在資料結構上看得見,不能混成同一個欄位:

  · MMR(維持保證金率)是 account.py 取的 0.005,**交易所沒有回這個值**
  · 交易所有維持保證金分層,大倉位的 MMR 更高 —— 我們算的會**偏樂觀**
  · 有沒有追加過保證金、有沒有全倉共用,我們看不到

所以算出來的每一筆都帶 `source="computed"` 與 `optimistic=True`。
**風控可以用它,但要知道它偏哪一邊。** 在爆倉這件事上,
算得比實際樂觀是最不能接受的方向 —— 所以它只能用來**收緊**判斷,
不能用來放行:實際強平價會比這個更近,不會更遠。

═══ 規格:沒有端點,但有 47 筆成交史 ═══
`contract/v1` 沒有 contracts 端點,查不到數量精度、最小下單量、費率。
2026-09-09 的教訓正是「不知道精度就會產生七張全部被拒的單」。

但 `allOrders` 會回真實成交:executedQty 反推數量精度、avgPrice 反推
價格精度。那是**交易所自己講的事實**,比任何文件都硬。

`infer_spec()` 做這件事,而且它**只回它真的看到的**:
沒有樣本就回 None,不回預設值。一個猜出來的精度會產生一張被拒的單;
一個 None 會讓呼叫端當場停下來。後者好得多。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 維持保證金率。**交易所沒有回這個值** —— 跟 account.py 取同一個常數,
# 兩邊不准各取一個(第十二次「兩把尺」)。
from portfolio.account import MAINT_MARGIN_RATE


def _num(value):
    """轉成 float。轉不動回 None —— **不回 0**。

    回 0 會讓「這個欄位我讀不懂」看起來像「這個數字是零」,
    而零在保證金與數量上都是一個有意義的值。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def liquidation_price(entry: float, leverage: float, is_long: bool,
                      maint_rate: float = MAINT_MARGIN_RATE):
    """線性(U 本位)逐倉強平價。與 account.py 同一條公式。

    回 None 的情況一律是「算不出來」,不是「沒有風險」。
    """
    if not entry or entry <= 0 or not leverage or leverage <= 0:
        return None
    move = 1.0 / leverage - maint_rate
    if move <= 0:
        # 槓桿低到維持保證金率吃掉整個緩衝 —— 這種倉不會被價格打爆。
        return None
    return entry * (1 - move) if is_long else entry * (1 + move)


@dataclass
class StandardPosition:
    """一筆 U 本位標準合約持倉,補上交易所沒給的欄位。

    `liq_price` 旁邊一定跟著 `liq_source`。看到數字就要看得到它從哪來。
    """

    symbol: str
    side: str                       # LONG / SHORT
    qty: float
    entry: float
    leverage: float
    isolated: bool
    initial_margin: float | None = None
    unrealized: float | None = None
    current_price: float | None = None
    opened_ms: int | None = None

    liq_price: float | None = None
    #: "exchange" = 交易所給的;"computed" = 我們算的;None = 算不出來
    liq_source: str | None = None
    #: 我們算的那個**偏樂觀**:沒有計入維持保證金分層與追加保證金。
    #: 實際強平價會比它更近,不會更遠。
    liq_optimistic: bool = False
    notes: list = field(default_factory=list)

    @property
    def is_long(self) -> bool:
        return self.side.upper() == "LONG"

    @property
    def signed_qty(self) -> float:
        return self.qty if self.is_long else -self.qty

    def notional(self, price: float | None = None) -> float | None:
        px = price if price is not None else self.current_price
        if px is None:
            return None
        return self.qty * px

    def liq_distance_pct(self, price: float | None = None):
        """離強平還有幾 %。**風控的硬閘要的就是這個數字。**

        回 None 表示算不出來 —— 呼叫端**必須**把它當成「不合格」,
        不是「沒問題」(第九十四條:沒檢查過不算通過)。
        """
        px = price if price is not None else self.current_price
        if px is None or self.liq_price is None or px <= 0:
            return None
        return abs(px - self.liq_price) / px * 100.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "side": self.side, "qty": self.qty,
            "entry": self.entry, "leverage": self.leverage,
            "isolated": self.isolated,
            "initial_margin": self.initial_margin,
            "unrealized": self.unrealized,
            "current_price": self.current_price,
            "liq_price": self.liq_price,
            "liq_source": self.liq_source,
            "liq_optimistic": self.liq_optimistic,
            "liq_distance_pct": self.liq_distance_pct(),
            "notes": list(self.notes),
        }


def parse_position(row: dict) -> StandardPosition | None:
    """把 allPosition 的一列變成 StandardPosition,並補上強平價。

    讀不出方向或數量就回 None —— **不猜方向**。方向猜錯,
    整筆的損益與強平價都是反的,而它會長得完全正常。
    """
    if not isinstance(row, dict):
        return None

    symbol = row.get("symbol")
    qty = _num(row.get("positionAmt"))
    entry = _num(row.get("entryPrice")) or _num(row.get("avgPrice"))
    if not symbol or qty is None or entry is None:
        return None

    side = str(row.get("positionSide") or "").upper()
    if side not in ("LONG", "SHORT"):
        # 有些回應靠數量正負表示方向。兩種都沒有就不猜。
        if qty > 0:
            side = "LONG"
        elif qty < 0:
            side = "SHORT"
        else:
            return None

    pos = StandardPosition(
        symbol=symbol,
        side=side,
        qty=abs(qty),
        entry=entry,
        leverage=_num(row.get("leverage")) or 0.0,
        isolated=bool(row.get("isolated", True)),
        initial_margin=_num(row.get("initialMargin")),
        unrealized=_num(row.get("unrealizedProfit")),
        current_price=(_num(row.get("currentPrice"))
                       or _num(row.get("markPrice"))),
        opened_ms=int(_num(row.get("time")) or 0) or None,
    )

    # 交易所有給就用交易所的 —— 我們算的只是備援。
    given = _num(row.get("liquidationPrice"))
    if given:
        pos.liq_price = given
        pos.liq_source = "exchange"
        return pos

    pos.liq_price = liquidation_price(pos.entry, pos.leverage, pos.is_long)
    if pos.liq_price is None:
        pos.liq_source = None
        pos.notes.append(
            "強平價**算不出來**(缺開倉價或槓桿)—— "
            "風控要把這筆當成不合格,不是當成沒問題")
    else:
        pos.liq_source = "computed"
        pos.liq_optimistic = True
        pos.notes.append(
            f"強平價是**我們算的**(交易所這個產品不回):"
            f"線性逐倉公式,MMR 取 {MAINT_MARGIN_RATE:.3%}。"
            "沒有計入維持保證金分層與追加保證金,所以**偏樂觀** —— "
            "實際強平價會比它更近,不會更遠。")

    if _num(row.get("markPrice")) is None:
        pos.notes.append(
            "這個產品不回 markPrice,只有 currentPrice(最新價)。"
            "強平是用標記價判定的 —— 用最新價估的距離會有誤差。")

    return pos


def positions_from(rows) -> list:
    """整批轉換。讀不懂的那幾列**不會消失**,會變成一筆警告。

    安靜地丟掉讀不懂的持倉,就是讓風控以為倉比實際少 —— 那是
    最壞的一種錯:它給出一個確定的答案,而那個答案是錯的。
    """
    out, bad = [], 0
    for row in (rows or []):
        pos = parse_position(row)
        if pos is None:
            bad += 1
            continue
        out.append(pos)
    if bad:
        raise ValueError(
            f"allPosition 有 {bad} 筆讀不出方向或數量 —— "
            "**不當成沒有倉**。先看清楚交易所回了什麼再繼續。")
    return out


# ══════════════════════════════════════════════════════════
# 規格:從成交史反推
# ══════════════════════════════════════════════════════════

@dataclass
class InferredSpec:
    """從真實成交反推出來的規格。**每一格都可能是 None。**"""

    symbol: str
    samples: int = 0
    quantity_precision: int | None = None
    price_precision: int | None = None
    min_executed_qty: float | None = None
    leverages: list = field(default_factory=list)
    isolated_only: bool | None = None
    reason: str | None = None

    @property
    def usable(self) -> bool:
        """夠不夠拿來下單。**兩個精度缺一不可。**"""
        return (self.samples > 0
                and self.quantity_precision is not None
                and self.price_precision is not None)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "samples": self.samples,
            "usable": self.usable,
            "quantity_precision": self.quantity_precision,
            "price_precision": self.price_precision,
            "min_executed_qty": self.min_executed_qty,
            "leverages": sorted(set(self.leverages)),
            "isolated_only": self.isolated_only,
            "reason": self.reason,
        }


def _decimals(value) -> int | None:
    """一個數字實際用了幾位小數。用 Decimal 讀原始字串,不經過 float。

    float("0.001") 的十進位展開是 0.001000000000000000020816...,
    拿 float 去數小數位會數出 20 位。精度這種東西不能經過二進位。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        dec = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return None
    exponent = dec.as_tuple().exponent
    if not isinstance(exponent, int):
        return None
    return max(0, -exponent)


def infer_spec(symbol: str, orders) -> InferredSpec:
    """拿成交史反推規格。

    ⚠️ **這是下界,不是規格。** 看到最細 3 位小數,只能說「至少支援
    3 位」,不能說「精度就是 3」—— 也許只是從來沒有人下過更細的單。

    所以它的用途是**擋掉明顯不合規的單**,不是拿來當精度四捨五入的
    依據。真正的精度要嘛交易所給,要嘛用一張最小單試出來。
    """
    spec = InferredSpec(symbol=symbol)
    rows = [r for r in (orders or []) if isinstance(r, dict)]
    if not rows:
        spec.reason = ("這個標的沒有成交史 —— **反推不出任何規格**。"
                       "不要套用其他標的的精度:每個標的都不一樣。")
        return spec

    qty_dp, px_dp, qtys = [], [], []
    for row in rows:
        qty = row.get("executedQty")
        if _num(qty):
            dp = _decimals(qty)
            if dp is not None:
                qty_dp.append(dp)
            qtys.append(abs(_num(qty)))
        for name in ("avgPrice", "closePrice"):
            if _num(row.get(name)):
                dp = _decimals(row.get(name))
                if dp is not None:
                    px_dp.append(dp)

    spec.samples = len(rows)
    spec.quantity_precision = max(qty_dp) if qty_dp else None
    spec.price_precision = max(px_dp) if px_dp else None
    spec.min_executed_qty = min(qtys) if qtys else None
    spec.leverages = [_num(r.get("leverage")) for r in rows
                      if _num(r.get("leverage"))]

    flags = [r.get("isolated") for r in rows if "isolated" in r]
    spec.isolated_only = all(flags) if flags else None

    if not spec.usable:
        missing = []
        if spec.quantity_precision is None:
            missing.append("數量精度")
        if spec.price_precision is None:
            missing.append("價格精度")
        spec.reason = (f"成交史有 {spec.samples} 筆,但反推不出"
                       f"{'、'.join(missing)} —— 不得下單。")
    else:
        spec.reason = (
            f"從 {spec.samples} 筆真實成交反推。"
            "⚠️ 這是**下界**:看到最細幾位,只代表至少支援幾位,"
            "不代表精度就是幾位。用來擋不合規的單可以,"
            "用來四捨五入不行。")
    return spec
