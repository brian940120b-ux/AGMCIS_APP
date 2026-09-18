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
# ═══ 2026-09-18:執政官分享了一條真的連結,而我猜錯了 scheme ═══
# 我猜的是 `bingx://`,三條全打不開。執政官從 App 分享出來的是:
#
#   https://bingx.com/perpetual/BTC-USDT?ref=…&liveTips=2
#     &activePageUrl=bingbon%3A%2F%2Ftrade%2Fdetail
#       %3FcoinName%3DBTC%26valuationCoinName%3DUSDT%26marginCoinName%3DUSDT
#
# 把 activePageUrl 解碼出來就是 App 真正吃的那一條:
#
#   bingbon://trade/detail?coinName=BTC&valuationCoinName=USDT
#                          &marginCoinName=USDT
#
# **scheme 是 `bingbon://`** —— BingX 的前身是 Bingbon,而 App 的
# URL scheme 沒有跟著改名。這種事情猜不到,只能拿一條真的連結來看。
#
# ⚠️ **我看到路徑裡的 /perpetual/ 就說它是永續頁 —— 那是我判斷錯了。**
# 執政官澄清:那條連結**就是從標準合約頁面分享出來的**。
# `/perpetual/` 是 BingX 分享功能的網址格式,不是產品別。
# 拿一個字串的長相去推斷它的語意,又一次。
#
# ═══ 而真正要緊的是這個 ═══
# App 連結的參數只有三個:coinName / valuationCoinName / marginCoinName。
# **沒有任何一個欄位說這是標準合約還是永續。**
#
# 所以連結能做的只有「把 App 開到這個幣」——**選哪個產品是 App 決定的,
# 不是連結決定的**。而同一個幣在永續與標準合約上的合約規格、槓桿
# 上限、費率都不一樣:指令單的數量是照標準合約算的,下到永續上
# 就是一張算錯的單,而它會成交。
#
# 所以每張單都要提醒:按之前先確認分頁是「U 本位標準合約」。
#
# ref= 那個推薦碼刻意不帶:那是執政官自己的分享碼,對他自己沒有作用,
# 而我不想把一個看不懂的追蹤參數寫死在程式裡。
import os as _os

#: 覆蓋用。拿到標準合約的正確連結之後設這個就好,不用改程式碼。
_TEMPLATE = _os.environ.get("BINGX_LINK_TEMPLATE", "").strip()

#: App 的 URL scheme。2026-09-18 由執政官的分享連結證實。
APP_LINK = ("bingbon://trade/detail?coinName={base}"
            "&valuationCoinName={quote}&marginCoinName=USDT")

#: 網頁。路徑裡的 `perpetual` 是 BingX 分享功能的格式,**不是產品別** ——
#: 執政官那條就是從標準合約頁分享出來的。
WEB_LINK = "https://bingx.com/perpetual/{base}-{quote}"

#: 按單之前一定要自己確認的那一件事。
#:
#: 連結不帶產品別,而同一個幣在永續與標準合約上的規格、槓桿上限、
#: 費率都不一樣。**指令單的數量是照標準合約算的,下到永續上就是
#: 一張算錯的單 —— 而它會成交。**
PRODUCT_WARNING = ("⚠️ 連結不會幫你選產品 —— 按之前先確認分頁是"
                   "「U 本位標準合約」。同一個幣在永續上的規格不一樣,"
                   "這張單的數量下到永續就是算錯的,而它會成交。")

#: 標準合約的連結有沒有被證實過。**沒有,而面板要說實話。**
BINGX_LINK_VERIFIED = bool(_TEMPLATE)


def split_symbol(symbol: str) -> tuple:
    """`BTCUSDT` / `BTC-USDT` → `("BTC", "USDT")`。

    切不開就回 (整串, "USDT") —— **不猜**,讓連結壞得看得出來,
    而不是產生一條指向別的幣的連結。那種錯最貴。
    """
    plain = symbol.replace("-", "").upper()
    for quote in ("USDT", "USDC", "USD"):
        if plain.endswith(quote) and len(plain) > len(quote):
            return plain[:-len(quote)], quote
    return plain, "USDT"


def _side_word(gap: float) -> str:
    """價差在現價的哪一邊。做空的止損在**上面**,寫死「下方」是假話。"""
    return "上方" if gap > 0 else "下方"


def bingx_links(symbol: str) -> list:
    """開啟 BingX 的連結。回 [(標籤, 網址, 註記)]。"""
    base, quote = split_symbol(symbol)
    if _TEMPLATE:
        return [("在 BingX 開啟",
                 _TEMPLATE.format(symbol=symbol, base=base, quote=quote), "")]
    return [
        ("開 App", APP_LINK.format(base=base, quote=quote),
         "連結只帶幣種,**不帶產品別**"),
        ("開網頁", WEB_LINK.format(base=base, quote=quote),
         "同上"),
    ]


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

    #: **策略的出場價**(2026-09-18 執政官:「我希望還有出場價」)。
    #: 跟 stop_price 是兩件不同的東西,見 `exit_plan()`。
    exit_price: float | None = None
    #: 那個價格是怎麼來的,例如「跌破 50 日均線」。
    exit_rule: str = ""

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
        """BingX 標準合約開單畫面**真的要你填**的那幾格。(標籤, 值, 說明)

        ═══ 2026-09-18:執政官拍了開單畫面,而我的欄位是錯的 ═══
        那張表單長這樣:

            逐倉 ▾                          ← 右上角切
            開多 / 開空                      ← 不是「買入/做多」
            市價 / 計劃委託
            槓桿  5x 10x 17x 19x 20x 40x ✏️  ← **預設檔位,3× 要用鉛筆自訂**
            本金  [輸入]  10% 20% 50% 100%   ← **這是唯一的輸入框**
            交易總額 --                      ← 系統算給你看的,不能填
            止盈止損                         ← 要點進去才設得到

        **根本沒有「數量」這一格。** 標準合約是用**本金**下單的 ——
        我把「數量」排在第一個要填的位置,那一格在 App 上根本不存在。

        所以這裡只留真的要填的五格,順序照表單由上到下。
        「數量」與「交易總額」搬到 `verify_after()` —— 它們是
        **填完之後用來核對的**,不是拿來填的。
        """
        # ── 平倉是**完全不同的一張單** ────────────────────
        # 2026-09-18 執政官:「檢查訊號單資訊…有些資訊也不太對。」
        # 查下去,平倉單原本印的是:
        #   ④ 本金 231.34 / ⑤ 止損 160.65 / 槓桿 3
        # 那是**開倉**的欄位。照著按會開一個新倉,而不是把舊的平掉 ——
        # 一張叫你平倉的單,把人帶去開倉,這是這張卡上最貴的一種錯。
        #
        # 平倉在 App 上是從持倉那一列進去的:沒有本金、沒有槓桿、
        # 沒有止損(倉都沒了,止損要保護什麼)。
        if self.action == CLOSE:
            return [
                ("① 方向", "平倉", "從**持倉**那一列點平倉,"
                                 "不是回開單畫面 —— 回去按會開新倉"),
                ("② 數量", f"{self.quantity:.8g}",
                 "幣數 —— 這是**全部平掉**的量"),
            ]

        out = [
            ("① 保證金模式", self.margin_mode, "右上角切"),
            ("② 槓桿", f"{self.leverage:.10g}",
             "預設檔位最低 5x —— 這個值要用 ✏️ 自訂"
             if self.leverage < 5 else ""),
            ("③ 方向", "開多" if self.action == OPEN_LONG else
             ("開空" if self.action == OPEN_SHORT else "平倉"), ""),
        ]
        if self.est_margin is not None:
            out.append(("④ 本金", f"{self.est_margin:.2f}",
                        "USDT —— 這是唯一的輸入框"))
        out.append(("⑤ 止損", f"{self.stop_price:.10g}",
                    "點「止盈止損」進去設。一定要設 —— "
                    "這是機器死掉時唯一的保護"))
        return out

    def verify_after(self) -> list:
        """填完本金之後,**畫面上應該出現的數字**。

        它們不是拿來填的,是拿來核對的 —— 對不上就代表某一格填錯了,
        而那比填漏一格更難發現:一張數量錯十倍的單會成交。
        """
        if self.action == CLOSE:
            # 平倉之後這一檔應該**消失**。原本這裡印的是要平掉的數量
            # (5.4),而那個數字在平倉之後出現在畫面上代表**沒平乾淨**。
            return [("成交後持倉", "0",
                     f"這一檔應該完全消失。還看得到 {self.quantity:.8g} "
                     "的話就是沒平乾淨,再平一次")]

        out = []
        if self.est_notional is not None:
            out.append(("交易總額", f"{self.est_notional:,.2f}",
                        "USDT —— 填完本金之後畫面會自己算出來"))
        out.append(("成交後持倉", f"{self.quantity:.8g}",
                    "幣數 —— 成交後對一下,差很多就是填錯了"))
        if self.est_liq_price is not None:
            out.append(("預估強平", f"{self.est_liq_price:,.6g}",
                        "App 也會顯示一個 —— 2026-09-18 實測兩邊差 0.07%"))
        return out

    def exit_plan(self) -> list:
        """**這一單打算在哪裡結束。**(標籤, 值, 說明)

        ═══ 兩條線,不是一條 ═══
        · **出場價** —— 策略真正的出場。它就是進場條件的反面:
          站上均線才持有,跌破就放掉。**這條線每天會動。**
        · **止損** —— 機器死掉時的後備。它是一個固定的災難上限,
          不是策略的一部分。

        正常情況下出場價比止損近得多,所以先到的永遠是出場價,
        止損一輩子不會被碰到 —— **那正是它該有的樣子**。
        反過來(止損比出場價近)代表後備線在替策略做決定,
        那是一個要看見的異常,所以下面會講出來。

        ═══ 為什麼出場價不填進 App ═══
        兩個理由,第二個才是關鍵:

        一、**它每天會動。** 今天填進去,明天就是舊的。
        二、**判定方式不一樣。** 策略用**收盤價**判斷跌破(插針穿過去
            再收回來不算,這條是 donchian_breakout 裡擋掉最多假突破
            的規則);而 App 的止損是**盤中觸價**就成交。
            把均線填進 App 的止損框,等於把「收盤跌破才走」偷偷換成
            「盤中戳到就走」—— 那會被上下影線掃出場,而回測裡
            **從來沒發生過這件事**。實際交易於是系統性地輸給回測,
            且看不出來是為什麼。

        所以這條線由系統盯著,到了會推一張平倉指令單。
        """
        # 平倉單本身就是出場 —— 再列一次「打算在哪裡結束」是廢話,
        # 而且會讓人以為平完之後還有一條線要顧。
        if self.action == CLOSE:
            return [("這張就是出場單", "—",
                     f"{self.exit_rule or '策略的出場條件'}已經觸發,"
                     "所以才有這張單。按完這一檔就結束了")]

        if self.exit_price is None:
            return [("出場線", "—", self.exit_rule or "這個策略沒給出場價")]

        px = self.quoted_price
        exit_gap = (self.exit_price - px) / px * 100.0
        stop_gap = (self.stop_price - px) / px * 100.0
        # ── 這一格為什麼叫「出場線」不叫「出場價」 ──────────
        # 2026-09-18 執政官:「做多出場價怎麼會比開倉價低?」
        #
        # **值是對的,是我的標籤在騙人。** 這不是停利價。
        # 這套策略做多的理由就是「價格在均線之上」,所以那條均線
        # **必然在進場價下面** —— 它是趨勢結束的位置,不是獲利目標。
        #
        # 叫它「出場價」、又擺在止損旁邊,讀起來就是一個停利單,
        # 而一個比進場價低的停利單當然看起來像壞掉了。
        #
        # 這套策略**沒有停利**:趨勢還在就一直抱著,賺多少由市場決定。
        # 上限來自「什麼時候結束」,不是「賺到多少就走」。
        out = [
            ("出場線(會移動)", f"{self.exit_price:,.6g}",
             f"{self.exit_rule} —— 現價{_side_word(exit_gap)} "
             f"{abs(exit_gap):.1f}%。"
             "**這不是停利**:做多的理由就是價格在均線之上,"
             "所以那條線本來就在進場價下面。均線往上走,它就跟著往上,"
             "等於一條會自己收緊的移動出場。<br>"
             "**不要填進 App** —— 它每天都在動,而且策略用收盤判定、"
             "App 的止損是盤中觸價,填進去會被上下影線掃出場"),
            ("止損(填這個)", f"{self.stop_price:,.10g}",
             f"現價{_side_word(stop_gap)} {abs(stop_gap):.1f}%"
             " —— 機器死掉時的後備"),
        ]

        # 方向對不對:做多的出場線該在現價**下面**。
        # 在上面代表現價已經跌破均線了 —— 這張單一開就該出場。
        wrong_side = ((self.action == OPEN_LONG and exit_gap > 0) or
                      (self.action == OPEN_SHORT and exit_gap < 0))
        if wrong_side:
            out.append(("⚠️ 方向不對", "出場線跑到現價的另一邊",
                        f"{self.exit_rule}的線在現價{'上' if exit_gap > 0 else '下'}"
                        "面 —— 訊號日到現在價格已經穿回去了,"
                        "這張單一開就符合出場條件。**先別按**"))
        elif abs(stop_gap) < abs(exit_gap):
            out.append(("⚠️ 止損比出場價近", "後備線在替策略做決定",
                        f"止損 {abs(stop_gap):.1f}% < 出場 {abs(exit_gap):.1f}%"
                        " —— 會先被止損掃掉,而回測裡出場的是策略那條線。"
                        "要嘛降槓桿把止損拉遠,要嘛這檔現在太遠離均線"))
        else:
            out.append(("哪一條先到", self.exit_rule,
                        f"出場 {abs(exit_gap):.1f}% 比止損 "
                        f"{abs(stop_gap):.1f}% 近 —— 正常,"
                        "止損是碰不到才對"))
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
            # ⚠️ 這裡原本寫死「50 日均線」。
            #
            # 那是**第三次**同一個錯:出場價寫死均線、止損說明寫死均線,
            # 現在是證據。策略換成 100 日均線或突破族,這行會繼續說
            # 「50 日均線」,而旁邊的數字是另一條線的值 —— 一句
            # 看起來有憑有據的假話,比沒有證據糟得多。
            #
            # 這條線現在跟出場規則要(exit_rule),因為它們本來就是
            # 同一條:進場條件與出場條件是一體兩面。
            line = (self.exit_rule.replace("跌破 ", "").replace("突破 ", "")
                    if self.exit_rule else "訊號線")
            out.append(("證據",
                        f"收盤 {px:,.6g} vs {line} {ma:,.6g},"
                        f"{'高出' if gap >= 0 else '低於'} {abs(gap):.2f}%"))
        if self.weight_from is not None and self.weight_to is not None:
            out.append(("配置", f"目標權重 {self.weight_from:.1%} → "
                                f"{self.weight_to:.1%}"))
        if self.est_notional is not None and self.quantity:
            out.append(("數量怎麼來的",
                        f"權益 × 目標權重 ÷ 報價 {self.quoted_price:,.6g} "
                        f"= {self.quantity:.8g},再套交易所的數量精度"))
        # 「策略出場是均線」原本寫死在這裡 —— 策略換成突破就變成假話。
        # 改成用這張單自己帶的 exit_rule。
        real_exit = self.exit_rule or "策略自己的出場條件"
        out.append(("止損怎麼來的",
                    f"進場價的 {abs((self.stop_price / self.quoted_price - 1)) * 100:.0f}%"
                    f" —— 這是災難後備,不是策略出場:策略出場是{real_exit},"
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
            "exit_price": self.exit_price, "exit_rule": self.exit_rule,
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
          exit_price: float | None = None,
          exit_rule: str = "",
          stale_price: float | None = None,
          now: datetime | None = None) -> Ticket:
    """開一張指令單。**任何一個閘沒過就拋,不印半張。**

    `stop_pct` 沒有預設值,而且必須明確傳進來 —— 這是第 102 條的
    Risk Limit,由執政官決定。一張沒有停損的單不該存在(§19),
    所以這裡不給它一個「看起來合理」的預設。
    """
    if action not in _ACTIONS:
        raise TicketRefused(f"不認得的動作 {action!r}")
    # 報價不是現價的時候**一定要講**。一張用昨天開盤價報的單,
    # 跟一張用現價報的,在畫面上長得一模一樣 —— 而前者的本金、數量、
    # 止損全部是照一個已經不存在的價格算的。
    stale_note = None
    if stale_price:
        stale_note = (
            f"⚠️ **問不到現價,這張單是用訊號日的開盤價 "
            f"{float(stale_price):,.6g} 報的** —— 本金、數量、止損"
            "全部照那個價格算。**按之前先對一下交易所現價**,"
            "差超過 1% 就不要按,等下一張。")
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

    checks, warnings = [], [PRODUCT_WARNING]
    if stale_note:
        warnings.append(stale_note)

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
        exit_price=exit_price, exit_rule=exit_rule,
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


def make_tickets(plan: dict, stop_pct: float, leverage: float,
                 marks: dict | None = None) -> tuple:
    """把今日訂單變成指令單。

    ═══ marks:**用現價報價,不要用昨天的開盤價** ═══
    2026-09-18 執政官:「價格跟交易所不一樣啊。」

    對。`Order.price` 是 `plan()` 裡的 `idx[s][exec_day].o` ——
    **成交日那根日線的開盤價**,註解自己寫著「預期成交價(隔日開盤)」。
    那是給回測用的:訊號用收盤、成交在隔日開盤,兩邊隔一個可交易的
    間隙。但人是**現在**在按的,而現在離那個開盤最多差 24 小時。

    後果不只是數字難看:本金、數量、止損全部是照那個價格算的,
    所以照著填會用一個錯的規模去冒一個不是原本那個的風險 ——
    而這張單自己的價格帶是 ±1%,它**照自己的規則早就作廢了**。

    所以有現價就用現價報價。**維持的是名目金額**(那才是策略指定的
    東西:權益 × 目標權重),數量跟著現價重算。

    ⚠️ 平倉單不重算數量 —— 平 5.4 顆就是 5.4 顆,跟價格無關。
    

    回 (開得出來的, 開不出來的)。**開不出來的不會消失** ——
    一張被閘門擋下的單如果安靜地不見了,看板上就會是「今天沒事」,
    而實際上是「今天有事,但系統拒絕告訴你」。
    """
    px = {app_symbol(k): float(v) for k, v in (marks or {}).items() if v}
    made, refused = [], []
    for order in plan.get("orders") or []:
        is_close = getattr(order, "weight_to", 0.0) == 0.0
        action = (CLOSE if is_close else
                  OPEN_LONG if order.side == "BUY" else OPEN_SHORT)
        sym = app_symbol(order.symbol)

        # 現價優先。拿不到就退回開盤價,**而那件事會寫在警語裡** ——
        # 一張用昨天價格報的單看起來跟用現價報的一模一樣。
        live = px.get(sym)
        price = live if live else order.price
        if is_close:
            qty = abs(order.qty)          # 平倉的量與價格無關
        elif live and order.price:
            # 維持名目(權益 × 目標權重),數量跟著現價走
            qty = abs(order.qty) * float(order.price) / float(live)
        else:
            qty = abs(order.qty)

        try:
            made.append(build(
                symbol=sym,
                action=action,
                quantity=qty,
                price=price,
                stale_price=(None if live else order.price),
                leverage=leverage,
                stop_pct=stop_pct,
                strategy=str(plan.get("cfg").strategy if plan.get("cfg")
                             else ""),
                signal_day=str(plan.get("signal_day") or ""),
                reason=str(getattr(order, "reason", "") or ""),
                signal=_signal_of(plan, order.symbol),
                weight_from=getattr(order, "weight_from", None),
                weight_to=getattr(order, "weight_to", None),
                **_exit_of(plan, order.symbol),
            ))
        except TicketRefused as e:
            refused.append((order.symbol, str(e)))
    return made, refused


def _exit_of(plan: dict, symbol: str) -> dict:
    """這一檔的出場價 —— **從 plan 拿,plan 從策略拿**(rules.exit_level_of)。

    拿不到就是 `exit_price=None` + 一句說明為什麼。
    說明不能省:「沒有出場價」和「有但算不出來」是兩件事,
    印成同一個空白的話,一個壞掉的策略看起來會像一個買入持有的策略。
    """
    got = (plan.get("exits") or {}).get(symbol)
    if not got:
        return {"exit_price": None, "exit_rule": "這個計畫沒帶出場價"}
    price, how = got
    return {"exit_price": None if price is None else float(price),
            "exit_rule": str(how)}


def _signal_of(plan: dict, symbol: str):
    """訊號當下的 (收盤, 均線, 距離%)。缺任何一個就回 None ——
    **理由可以少一行證據,但不能有一行是編的。**"""
    prices = plan.get("prices") or {}
    px = prices.get(symbol)
    if not px:
        return None
    # 先跟**策略**要那條線(exits 來自 rules.exit_level_of)。
    # plan["mas"] 是用 cfg.vol_lookback 算的,現在剛好也是 50 ——
    # 但那是巧合。策略換成 100 日均線,mas 不會跟著變,而證據那一行
    # 會安安靜靜地拿另一條線的數字當證據。
    got = (plan.get("exits") or {}).get(symbol)
    ma = got[0] if got and got[0] is not None else None
    if ma is None:
        # 退回 mas,但**這件事要留在證據裡**:策略沒給線的時候,
        # 拿一條剛好同長度的線來比,至少要說得出它是哪一條。
        ma = (plan.get("mas") or {}).get(symbol)
    if not ma:
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
             exits: dict | None = None,
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
    # 出場價的 key 來自 plan(BTC-USDT),迴圈裡的 symbol 是 App 代號
    # (BTCUSDT)—— 不轉的話每張對齊單都會安靜地變成「沒有出場價」,
    # 而畫面上看起來只像這個策略本來就沒有出場條件。
    ex = {app_symbol(k): v for k, v in (exits or {}).items()}

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
                strategy=strategy, signal_day=signal_day,
                # 對齊單開的是真倉,它跟鏡像單一樣要有出場價 ——
                # 少給的話,補上來的倉會是唯一一批沒人知道何時該放掉的。
                **_exit_of({"exits": ex}, symbol)))
        except TicketRefused as e:
            refused.append((symbol, str(e)))

    return made, refused, notes
