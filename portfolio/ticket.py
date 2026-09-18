"""
下單指令單 —— 最後一吋是人按的 · 2026-09-13

═══ 為什麼會有這個東西 ═══
執政官選了 U 本位標準合約,而 2026-09-13 的探測定案:

    /openApi/contract/v1/order        GET 100400  POST 100400
    /openApi/contract/v1/trade/order  GET 100400  POST 100400
    /openApi/contract/v1/allOrders    GET code 0  POST 100400   ← 探測是好的
    對照組 cswap/v1/trade/order       POST 100004(存在,只缺權限)

對照組問得出東西,所以「全是 100400」不是探測壞掉,是**真的沒有**。
**U 本位標準合約不能自動送單。**

那不代表這個系統只能停在這裡。整條鏈只有最後一吋是人做的:

    訊號 → 部位大小 → 風控閘 → 強平距離 → **指令單** → 人按 → 對帳
     自動    自動        自動      自動         自動       手動   自動

═══ 一張指令單不是「建議」,是一張有有效期的合約 ═══
把數字印出來給人照著按,聽起來很簡單,而它其實有三個會出人命的坑:

一、**價格會動。** 08:00 算出來的量,14:00 按下去是另一筆交易。
    所以每一張單帶 `valid_until` 與 `price_band` —— 超出就作廢,
    **不是「參考一下」**。

二、**人會按錯,而錯了沒有人會發現。** 所以按完要對帳:
    `verify()` 拿交易所真的成交的那一筆,回頭比對指令單。
    數量、方向、槓桿、保證金模式,差一格就報出來。

三、**人會按兩次。** `ticket_id` 是確定性雜湊 —— 同一個決定重算
    一百次是同一個 id,對帳時看得出來這是同一張單被執行了兩次。

═══ 這一支不決定任何事 ═══
它不算部位大小、不判斷該不該進場、不挑停損百分比。
那些是 size.py / signals.py / risk.py 的事(第七條)。
它只做一件事:**把已經決定好的東西,變成一張不會被誤讀的紙。**

所以它沒有任何網路行為,也不 import 交易所 —— 可以在沒有金鑰、
沒有網路的情況下被完整測試。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# 指令單預設多久過期。
#
# 依據:策略是日線的,訊號一天只換一次;但**價格不是**。
# 30 分鐘是一個「人看到通知、拿起手機、按完」合理的上限 ——
# 比它久就該重算,而不是照著一張舊的按。
DEFAULT_TTL_MIN = 30

# 價格偏離多少就作廢。
#
# 依據:這不是滑價容忍度(那是成交的事),是**指令單失效門檻**。
# 價格走了 1%,原本算出來的數量就對不上原本要冒的風險了 ——
# 而部位大小整套邏輯是建立在那個價格上的。
DEFAULT_BAND_PCT = 1.0

# BingX 的 App 連結。
#
# ═══ 2026-09-18:第一版三條都打不開,而我沒有辦法自己測 ═══
# 執政官回報「App 打不開」。`bingx://trade?symbol=…` 是我**猜**的 ——
# BingX 沒有公開任何 deeplink 規格(查過官方 API 文件、支援中心、
# GitHub),而開發環境連 bingx.com 都連不上(代理擋著),
# 所以我連「這個網址存不存在」都驗不了。
#
# **猜一條打不開的連結,比不給連結糟** —— 它讓人以為是自己手機的問題。
#
# 所以改成:預設只給一條**一定到得了**的網站首頁,其餘交給執政官。
# 真正會動的那條只有一個方法拿到:**在 BingX App 裡打開那個合約,
# 用「分享」複製連結貼給我**,我把它變成模板。在那之前這裡不猜。
#
# 可以用環境變數覆蓋,免得為了改一條網址還要動程式碼:
#   BINGX_LINK_TEMPLATE="https://…/{symbol}"
import os as _os

_TEMPLATE = _os.environ.get("BINGX_LINK_TEMPLATE", "").strip()

#: 還沒有人證實過任何一條 deeplink。這個旗標讓面板說實話。
BINGX_LINK_VERIFIED = bool(_TEMPLATE)


def bingx_links(symbol: str) -> list:
    """開啟 BingX 的連結。回 [(標籤, 網址)]。

    沒設 `BINGX_LINK_TEMPLATE` 就只給網站首頁 —— **不猜 deeplink**。
    """
    if _TEMPLATE:
        return [("在 BingX 開啟", _TEMPLATE.format(symbol=symbol))]
    return [("開啟 BingX(首頁,要自己找合約)", "https://bingx.com/")]


OPEN_LONG = "OPEN_LONG"
OPEN_SHORT = "OPEN_SHORT"
CLOSE = "CLOSE"

_ACTIONS = {
    OPEN_LONG: ("買入/做多", "LONG"),
    OPEN_SHORT: ("賣出/做空", "SHORT"),
    CLOSE: ("平倉", None),
}


class TicketRefused(RuntimeError):
    """這張單不該被開出來。**拋出比印出一張壞單好。**"""


def _utc(dt: datetime | None = None) -> datetime:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket_id(strategy: str, symbol: str, signal_day: str,
              action: str) -> str:
    """確定性冪等鍵。與 trade.client_order_id 同一個做法、同一個前綴。

    同一個決定重算一百次是同一個 id —— 對帳時看得出「這張單被按了
    兩次」,而不是看成兩個獨立的決定。
    """
    seed = f"{strategy}|{symbol}|{signal_day}|{action}"
    return "agm" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Ticket:
    """一張可以照著按的單。**凍結的** —— 開出來就不准改。

    改一張已經開出來的單,等於把「當時的決定」與「現在的決定」
    混成同一個東西,而對帳會分不出來人到底照哪一個按的。
    要改就重開一張,新的 id、新的有效期。
    """

    ticket_id: str
    created_utc: str
    valid_until_utc: str

    symbol: str                    # App 上顯示的代號,例如 BTCUSDT
    action: str                    # OPEN_LONG / OPEN_SHORT / CLOSE
    tap: str                       # App 上要按的那個字
    position_side: str | None      # LONG / SHORT / None(平倉)

    quantity: float
    leverage: float
    margin_mode: str               # 逐倉 / 全倉

    quoted_price: float            # 算這張單的時候的價格
    price_low: float               # 低於這個就作廢
    price_high: float              # 高於這個就作廢

    stop_price: float              # 後備停損。**沒有它不發單**
    est_liq_price: float | None    # 我方算的,偏樂觀
    est_margin: float | None
    est_notional: float | None

    strategy: str = ""
    signal_day: str = ""
    checks: tuple = ()
    warnings: tuple = ()

    #: **為什麼要開這一單。** 一張說不出理由的單不該被按下去 ——
    #: 那等於把判斷外包給一個你看不見的東西。
    reason: str = ""
    #: 訊號當下的數字:(收盤, 均線, 距離%)。理由要有證據,不能只有結論。
    signal: tuple | None = None
    #: 目標權重從多少變到多少。
    weight_from: float | None = None
    weight_to: float | None = None

    # ── 有效性 ────────────────────────────────────────
    def expired_at(self, now: datetime | None = None) -> bool:
        return _iso(_utc(now)) > self.valid_until_utc

    def price_in_band(self, price: float) -> bool:
        return self.price_low <= price <= self.price_high

    def executable(self, price: float,
                   now: datetime | None = None) -> tuple:
        """現在照這張單按,可不可以。回 (可不可以, 理由)。

        **兩個條件都要過。** 過期或跑出價格帶,答案就是不行 ——
        不是「參考一下」。原本的數量是照 quoted_price 算出來的,
        價格變了,那個數量代表的風險就不是原本那個了。
        """
        if self.expired_at(now):
            return False, (f"這張單已經過期({self.valid_until_utc})—— "
                           "重算一張,不要照舊的按")
        if not self.price_in_band(price):
            return False, (
                f"現價 {price:g} 跑出價格帶 "
                f"[{self.price_low:g}, {self.price_high:g}] —— "
                f"這張單是照 {self.quoted_price:g} 算的,"
                "數量已經對不上原本要冒的風險了,重算")
        return True, "可以照這張按"

    # ── 給人看 ────────────────────────────────────────
    def render(self) -> str:
        """印在手機上看得懂的一張單。"""
        lines = [
            "┌" + "─" * 44,
            f"│  {self.symbol}   {self.tap}",
            "├" + "─" * 44,
            f"│  數量      {self.quantity:g}",
            f"│  槓桿      {self.leverage:g}×",
            f"│  保證金    {self.margin_mode}",
            f"│  停損      {self.stop_price:g}   ← **一定要設**",
        ]
        if self.est_margin is not None:
            lines.append(f"│  預估佔用  {self.est_margin:.2f} USDT")
        if self.est_liq_price is not None:
            lines.append(f"│  預估強平  {self.est_liq_price:.6g}"
                         "  (我方算的,偏樂觀)")
        lines += [
            "├" + "─" * 44,
            f"│  報價      {self.quoted_price:g}",
            f"│  有效價格  {self.price_low:g} ~ {self.price_high:g}",
            f"│  有效到    {self.valid_until_utc}",
            f"│  單號      {self.ticket_id}",
            "└" + "─" * 44,
        ]
        for w in self.warnings:
            lines.append(f"  ⚠️ {w}")
        return "\n".join(lines)

    def links(self) -> list:
        """開啟 BingX 的連結。見 `bingx_links` —— **不猜 deeplink**。"""
        return bingx_links(self.symbol)

    def fields(self) -> list:
        """BingX 標準合約開單畫面要填的每一格。(標籤, 值, 說明)

        ═══ 順序照 App 的表單,不照我方便 ═══
        2026-09-18 執政官:「我希望資訊是能比照 BingX 標準合約開單
        畫面要填的資訊。」所以由上到下就是 App 上由上到下:
        保證金模式 → 槓桿 → 方向 → 數量 → 止損。

        ═══ 數量、保證金、交易總額**三個都給** ═══
        BingX 的開單框可以用「數量」也可以用「保證金」下單,而它
        顯示的是「交易總額」。**你會看到哪一個,我不知道** ——
        所以三個都算好,填到哪一格就用哪一個。
        少給一個,你就得在手機上自己乘除,而那是最容易打錯的一步。

        值是**乾淨的字串** —— 沒有千分位、沒有單位、沒有正負號裝飾,
        因為它要被原封不動貼進輸入框。多一個逗號就是一張被拒的單。
        """
        out = [
            ("保證金模式", self.margin_mode, "逐倉:這一倉爆掉不會拖累別倉"),
            ("槓桿", f"{self.leverage:.10g}", ""),
            ("方向", self.tap, ""),
            ("數量", f"{self.quantity:.10g}", "幣數"),
        ]
        if self.est_notional is not None:
            out.append(("交易總額", f"{self.est_notional:.2f}",
                        "USDT —— App 上多半顯示這個"))
        if self.est_margin is not None:
            out.append(("保證金", f"{self.est_margin:.2f}",
                        "USDT —— 用保證金下單就填這格"))
        out.append(("止損", f"{self.stop_price:.10g}",
                    "一定要設 —— 這是機器死掉時唯一的保護"))
        return out

    def why(self) -> list:
        """**為什麼要按這一單。** 結論 + 證據,不是只有結論。

        一張說不出理由的單不該被按下去 —— 那等於把判斷外包給一個
        你看不見的東西,而虧錢的時候你連哪裡想錯了都查不出來。
        """
        out = []
        if self.reason:
            out.append(("訊號", self.reason))
        if self.signal:
            px, ma, gap = self.signal
            out.append(("證據",
                        f"收盤 {px:,.6g} vs 50 日均線 {ma:,.6g},"
                        f"{'高出' if gap >= 0 else '低於'} {abs(gap):.2f}%"))
        if self.weight_from is not None and self.weight_to is not None:
            out.append(("配置", f"目標權重 {self.weight_from:.1%} → "
                                f"{self.weight_to:.1%}"))
        if self.est_notional is not None and self.quantity:
            out.append(("數量怎麼來的",
                        f"權益 × 目標權重 ÷ 報價 {self.quoted_price:,.6g} "
                        f"= {self.quantity:.8g},再套交易所的數量精度"))
        out.append(("止損怎麼來的",
                    f"進場價的 {abs((self.stop_price / self.quoted_price - 1)) * 100:.0f}%"
                    " —— 這是災難後備,不是策略出場:策略出場是均線,"
                    "而這一條只回答「機器死掉時這個倉最多虧多少」"))
        return out

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "created_utc": self.created_utc,
            "valid_until_utc": self.valid_until_utc,
            "symbol": self.symbol, "action": self.action, "tap": self.tap,
            "position_side": self.position_side,
            "quantity": self.quantity, "leverage": self.leverage,
            "margin_mode": self.margin_mode,
            "quoted_price": self.quoted_price,
            "price_low": self.price_low, "price_high": self.price_high,
            "stop_price": self.stop_price,
            "est_liq_price": self.est_liq_price,
            "est_margin": self.est_margin,
            "est_notional": self.est_notional,
            "strategy": self.strategy, "signal_day": self.signal_day,
            "checks": list(self.checks), "warnings": list(self.warnings),
            "reason": self.reason, "signal": self.signal,
            "weight_from": self.weight_from, "weight_to": self.weight_to,
            "why": self.why(),
        }


def build(symbol: str, action: str, quantity: float, price: float,
          leverage: float, stop_pct: float, *,
          strategy: str = "", signal_day: str = "",
          margin_mode: str = "逐倉",
          ttl_min: int = DEFAULT_TTL_MIN,
          band_pct: float = DEFAULT_BAND_PCT,
          min_liq_distance_pct: float = 20.0,
          reason: str = "", signal: tuple | None = None,
          weight_from: float | None = None,
          weight_to: float | None = None,
          now: datetime | None = None) -> Ticket:
    """開一張指令單。**任何一個閘沒過就拋,不印半張。**

    `stop_pct` 沒有預設值,而且必須明確傳進來 —— 這是第 102 條的
    Risk Limit,由執政官決定。一張沒有停損的單不該存在(§19),
    所以這裡不給它一個「看起來合理」的預設。
    """
    if action not in _ACTIONS:
        raise TicketRefused(f"不認得的動作 {action!r}")
    if quantity <= 0:
        raise TicketRefused(f"數量要是正數,收到 {quantity}")
    if price <= 0:
        raise TicketRefused(f"價格要是正數,收到 {price}")
    if leverage <= 0:
        raise TicketRefused(f"槓桿要是正數,收到 {leverage}")
    if not (0 < stop_pct < 100):
        raise TicketRefused(
            f"停損百分比要在 0 與 100 之間,收到 {stop_pct}。\n"
            "  這是一條 Risk Limit,依第 102 條由執政官決定 —— "
            "這裡不會替你挑一個。")

    tap, position_side = _ACTIONS[action]
    is_long = action == OPEN_LONG

    # 後備停損。空單的停損在上面,不是下面 —— 方向錯了等於沒有停損。
    stop = (price * (1 - stop_pct / 100.0) if is_long
            else price * (1 + stop_pct / 100.0))

    from exchange.bingx.standard_usdt import liquidation_price
    liq = (liquidation_price(price, leverage, is_long)
           if action != CLOSE else None)

    notional = quantity * price
    margin = notional / leverage if leverage else None

    checks, warnings = [], []

    # 閘一:停損要在強平之前。**擺在強平後面的停損等於沒有停損。**
    if liq is not None:
        inside = stop > liq if is_long else stop < liq
        checks.append(("停損在強平之前", inside))
        if not inside:
            raise TicketRefused(
                f"停損 {stop:.6g} 擺在強平價 {liq:.6g} 的另一邊 —— "
                f"倉會先被強平,停損永遠不會觸發。"
                f"停損 {stop_pct}% 對 {leverage:g}× 來說太遠了:"
                "降槓桿,或把停損拉近。")

        distance = abs(price - liq) / price * 100.0
        ok = distance >= min_liq_distance_pct
        checks.append((f"離強平 ≥{min_liq_distance_pct:g}%", ok))
        if not ok:
            raise TicketRefused(
                f"開倉當下離強平只有 {distance:.2f}%,"
                f"低於下限 {min_liq_distance_pct:g}%(§19)。"
                f"而且這個距離是**我方算的,偏樂觀** —— 實際更近。"
                "降槓桿。")
        warnings.append(
            "預估強平價是我方算的(這個產品交易所不回),"
            "MMR 取定值、未計維持保證金分層 —— **實際會更近**")

    start = _utc(now)
    return Ticket(
        ticket_id=ticket_id(strategy, symbol, signal_day, action),
        created_utc=_iso(start),
        valid_until_utc=_iso(start + timedelta(minutes=ttl_min)),
        symbol=symbol, action=action, tap=tap, position_side=position_side,
        quantity=quantity, leverage=leverage, margin_mode=margin_mode,
        quoted_price=price,
        price_low=price * (1 - band_pct / 100.0),
        price_high=price * (1 + band_pct / 100.0),
        stop_price=stop, est_liq_price=liq,
        est_margin=margin, est_notional=notional,
        strategy=strategy, signal_day=signal_day,
        checks=tuple(checks), warnings=tuple(warnings),
        reason=reason, signal=signal,
        weight_from=weight_from, weight_to=weight_to,
    )


# ══════════════════════════════════════════════════════════
# 按完之後:人有沒有照做
# ══════════════════════════════════════════════════════════

@dataclass
class Execution:
    """指令單 vs 交易所實際的那一筆。**只報告,不修正。**"""

    ticket_id: str
    found: bool
    matches: list = field(default_factory=list)
    mismatches: list = field(default_factory=list)
    slippage_pct: float | None = None

    @property
    def clean(self) -> bool:
        """找得到,而且每一格都對得上。**找不到不算乾淨。**"""
        return self.found and not self.mismatches

    def to_dict(self) -> dict:
        return {"ticket_id": self.ticket_id, "found": self.found,
                "clean": self.clean, "matches": list(self.matches),
                "mismatches": list(self.mismatches),
                "slippage_pct": self.slippage_pct}


def verify(ticket: Ticket, position, qty_tolerance: float = 1e-8
           ) -> Execution:
    """拿交易所實際的持倉,回頭比對指令單。

    `position` 是 `standard_usdt.StandardPosition`,或 None(沒開成)。

    ═══ 為什麼這一層不可省 ═══
    自動下單的系統知道自己送了什麼。**手動下單的系統不知道** ——
    人可能按錯數量、按錯方向、忘了改槓桿、忘了設停損,
    而這四件事裡沒有一件會自己發出聲音。

    這一層就是那個聲音。它**只報告,不修正**(第十七條):
    差異是一個訊號,不是一個待辦事項。
    """
    out = Execution(ticket_id=ticket.ticket_id, found=position is not None)
    if position is None:
        out.mismatches.append(
            "交易所找不到這個倉 —— 可能還沒按、按失敗、或按到別的標的")
        return out

    def cmp(what: str, want, got, ok: bool) -> None:
        (out.matches if ok else out.mismatches).append(
            f"{what}:指令單 {want} / 實際 {got}"
            + ("" if ok else "  ← **不一致**"))

    cmp("代號", ticket.symbol, position.symbol,
        position.symbol == ticket.symbol)

    if ticket.position_side is not None:
        cmp("方向", ticket.position_side, position.side,
            position.side == ticket.position_side)

    cmp("數量", f"{ticket.quantity:g}", f"{position.qty:g}",
        abs(position.qty - ticket.quantity) <= max(
            qty_tolerance, ticket.quantity * 1e-6))

    cmp("槓桿", f"{ticket.leverage:g}×", f"{position.leverage:g}×",
        abs(position.leverage - ticket.leverage) < 1e-9)

    want_isolated = ticket.margin_mode == "逐倉"
    cmp("保證金模式", ticket.margin_mode,
        "逐倉" if position.isolated else "全倉",
        position.isolated == want_isolated)

    if position.entry and ticket.quoted_price:
        diff = (position.entry - ticket.quoted_price) / ticket.quoted_price
        # 做空的滑價方向相反:成交價比報價低才是吃虧。
        out.slippage_pct = (diff * 100.0 if ticket.position_side != "SHORT"
                            else -diff * 100.0)

    return out


# ══════════════════════════════════════════════════════════
# 從今日計畫產生指令單
# ══════════════════════════════════════════════════════════
#
# 這一段本來住在 scripts/ticket.py 裡,2026-09-13 搬過來 ——
# 因為面板也要用它,而**面板不該 import 一支 CLI 腳本**:
# 那會讓「跑腳本」的副作用(argparse、interpreter.require)
# 變成網頁請求的一部分。

def app_symbol(symbol: str) -> str:
    """`BTC-USDT` → `BTCUSDT`。

    App 與 allPosition 用的是無槓的寫法(實測 FLOCKUSDT),
    而策略內部用有槓的。轉換只做一次,放在這裡。
    """
    return symbol.replace("-", "")


def make_tickets(plan: dict, stop_pct: float, leverage: float) -> tuple:
    """把今日訂單變成指令單。

    回 (開得出來的, 開不出來的)。**開不出來的不會消失** ——
    一張被閘門擋下的單如果安靜地不見了,看板上就會是「今天沒事」,
    而實際上是「今天有事,但系統拒絕告訴你」。
    """
    made, refused = [], []
    for order in plan.get("orders") or []:
        is_close = getattr(order, "weight_to", 0.0) == 0.0
        action = (CLOSE if is_close else
                  OPEN_LONG if order.side == "BUY" else OPEN_SHORT)
        try:
            made.append(build(
                symbol=app_symbol(order.symbol),
                action=action,
                quantity=abs(order.qty),
                price=order.price,
                leverage=leverage,
                stop_pct=stop_pct,
                strategy=str(plan.get("cfg").strategy if plan.get("cfg")
                             else ""),
                signal_day=str(plan.get("signal_day") or ""),
                reason=str(getattr(order, "reason", "") or ""),
                signal=_signal_of(plan, order.symbol),
                weight_from=getattr(order, "weight_from", None),
                weight_to=getattr(order, "weight_to", None),
            ))
        except TicketRefused as e:
            refused.append((order.symbol, str(e)))
    return made, refused


def _signal_of(plan: dict, symbol: str):
    """訊號當下的 (收盤, 均線, 距離%)。缺任何一個就回 None ——
    **理由可以少一行證據,但不能有一行是編的。**"""
    mas = plan.get("mas") or {}
    prices = plan.get("prices") or {}
    ma, px = mas.get(symbol), prices.get(symbol)
    if not ma or not px:
        return None
    return (float(px), float(ma), (float(px) - float(ma)) / float(ma) * 100.0)


# ══════════════════════════════════════════════════════════
# 對齊:讓真實帳戶追上模擬帳戶
# ══════════════════════════════════════════════════════════
#
# 2026-09-13 執政官把系統的樣子講清楚了:
#
#   「系統自己會有 $10,000 的模擬金,比照交易所裡面的 USDT 算法
#     去模擬開單,然後他所推送的訊號單…我點擊…就可以直接開單。」
#
# 也就是**鏡像**:模擬帳戶做了什麼,就推一張讓人跟。
# 而 `make_tickets()` 已經是這件事了 —— 它推的就是模擬今天要做的單。
#
# 但那有一個起點問題:模擬帳戶**幾天前就開好了 7 個倉**,而真實帳戶
# 是空的。鏡像只鏡像「從現在開始的變動」,所以真實帳戶永遠追不上。
#
# 「今天沒有要按的」在那個狀態下是真話,也是誤導:模擬確實沒有換手,
# 但你的真實帳戶跟模擬差了整整 7 個倉。
#
# `catch_up()` 補這一段:算出「要讓真實帳戶變成模擬現在的樣子,
# 得按哪幾張」。**它是一次性的**,按完之後就交給日常的鏡像。


def catch_up(sim_positions: dict, exchange_positions, prices: dict,
             stop_pct: float, leverage: float, *,
             strategy: str = "", signal_day: str = "",
             tolerance: float = 1e-8) -> tuple:
    """讓真實帳戶追上模擬帳戶要按哪幾張。

    sim_positions       {代號: 數量}(帶正負)—— 模擬帳戶現在持有的
    exchange_positions  StandardPosition 清單 —— 交易所實際的
    prices              {代號: 現價}

    回 (指令單, 開不出來的, 沒動的原因)。

    ═══ 為什麼不直接說「照模擬的倉開一遍」═══
    因為真實帳戶不一定是空的。它可能已經有倉、可能數量不一樣、
    也可能有模擬沒有的倉(手動開的)。三種都要處理,而**最後一種
    不會自動產生平倉單** —— 那是你自己開的倉,系統不該替你決定平掉。
    它只會列出來說「這個模擬裡沒有」。
    """
    have: dict = {}
    for pos in (exchange_positions or []):
        have[pos.symbol] = have.get(pos.symbol, 0.0) + pos.signed_qty

    want = {app_symbol(k): float(v or 0.0) for k, v in sim_positions.items()}
    px = {app_symbol(k): float(v) for k, v in (prices or {}).items() if v}

    made, refused, notes = [], [], []

    for symbol in sorted(set(want) | set(have)):
        target = want.get(symbol, 0.0)
        actual = have.get(symbol, 0.0)
        delta = target - actual

        if abs(delta) <= tolerance:
            continue

        if symbol not in want:
            # 交易所有、模擬沒有。**不自動產生平倉單。**
            notes.append(
                f"{symbol}:交易所有 {actual:+.8g},而模擬裡沒有這個倉 —— "
                "可能是你自己開的。系統不替你決定平掉它。")
            continue

        price = px.get(symbol)
        if price is None:
            refused.append((symbol, "問不到現價,算不出數量 —— 不猜"))
            continue

        # 減倉 / 反手先不處理:那需要知道現有倉的方向與可平量,
        # 而搞錯會開出一個反向的新倉。先列出來讓人看。
        if actual != 0.0 and (target * actual < 0 or abs(target) < abs(actual)):
            notes.append(
                f"{symbol}:要從 {actual:+.8g} 調到 {target:+.8g}"
                "(減倉或反手)—— 這一版不自動出單,手動處理。"
                "搞錯方向會開出一個反向的新倉,而那個倉沒有人在管。")
            continue

        action = OPEN_LONG if delta > 0 else OPEN_SHORT
        try:
            made.append(build(
                symbol=symbol, action=action, quantity=abs(delta),
                price=price, leverage=leverage, stop_pct=stop_pct,
                strategy=strategy, signal_day=signal_day))
        except TicketRefused as e:
            refused.append((symbol, str(e)))

    return made, refused, notes
