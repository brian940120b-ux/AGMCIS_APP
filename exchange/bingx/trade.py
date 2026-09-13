"""
下單 —— 交易所端保護單優先 · 2026-09-13(PHASE 3 第三步)

═══ 先講一個會毀掉 Calmar 的設計陷阱 ═══
直覺會想:策略的出場是 50 日均線,那就在均線那個價位掛一張停損單。

**那是錯的,而且錯得很安靜。**

回測的規則是「**收盤**跌破均線,隔日開盤出場」。掛單的規則是
「**盤中任何一刻**觸到就成交」。加密貨幣一天插針兩三次是常態 ——
把停損掛在均線上,會在無數個「盤中破線、收盤拉回」的日子被掃出去。

那是**另一條策略**,而 Calmar 1.33 不是它的數字。

所以這裡的交易所端停損**不是策略的出場**,是**災難後備**:
它擺在策略正常運作時永遠碰不到的地方,只回答一個問題 ——
**「這台機器死掉的時候,這個倉最多能虧多少?」**

現在的停損靠這台機器活著才成立。機器掛了、網路斷了、程式當了,
交易所那邊什麼保護都沒有。紙上交易永遠看不出這件事。

═══ 後備停損擺在哪裡 —— 2026-09-13 有答案了:25% ═══
`BACKSTOP_PCT = 25.0`。這是一條 Risk Limit,依第 102 條由執政官決定,
程式不自行挑數字 —— 所以旁邊有 `BACKSTOP_DECISION`,記著誰決定的、
什麼時候、根據什麼證據、以及**它的已知限制**。

證據來自 `scripts/stop_evidence.py`(260 段持有):20% 是分水嶺,
從那裡開始被掃出去的部位沒有一段最後是賺的;25% 只在 1.9% 的持倉上
觸發,而且離 3× 的強平 32.8% 還有 7.8 個百分點 —— 那個 32.8% 是
我方算的、偏樂觀的,所以那 7.8pp 是必要的餘裕不是浪費。

═══ 三道閘,任何一道不過就不送 ═══
一、**LIVE_ENABLED**(原始碼常數,目前 False)。它是 False 的時候,
    這個模組**只能對 Demo 下單**,連 BINGX_ENV=live 都沒有用。
二、**明確的 confirm=True**。預設是乾跑:把要送出去的東西完整印出來,
    不送。
三、**Risk Engine**。訂單產出之後、送出之前的硬閘(第十九條)。

═══ 冪等:每一張單都有 client order id ═══
第十六條。網路抖一下,你不知道交易所收到了沒有。沒有冪等鍵的話,
唯一的選擇是「賭一把重送」或「賭一把不送」——兩個都會錯。

client order id 由「策略 + 標的 + 訊號日 + 用途」決定,是**確定性的**:
同一個決定重跑一百次,產生的是同一個 id,交易所只會收一張。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core.logging import get_logger
from exchange.bingx import private

log = get_logger("bingx.trade")

ORDER_PATH = "/openApi/swap/v2/trade/order"
OPEN_ORDERS_PATH = private.READ_ONLY["perp_open_orders"]

MARKET = "MARKET"
STOP_MARKET = "STOP_MARKET"

BUY, SELL = "BUY", "SELL"
LONG, SHORT = "LONG", "SHORT"

# 單向持倉模式下,positionSide 必須是 BOTH。送 LONG 會被拒,
# 而錯誤訊息通常只說「參數錯誤」,不會告訴你是哪一個。
BOTH = "BOTH"


# 後備停損要擺在進場價下方多少 %。
#
# **2026-09-13 執政官裁定:25%。**  在那之前這裡是 None ——
# 而 None 代表的是「還沒有人決定」,不是「不設停損」。
#
# 這是一條 Risk Limit(第 102 條),程式不自行挑數字。下面記的是
# 誰決定的、什麼時候、根據什麼 —— **沒有這三樣的數字不算決定,
# 只算有人打了字**。有一條測試檢查這三樣都在。
BACKSTOP_PCT: float | None = 25.0

BACKSTOP_DECISION = {
    "value_pct": 25.0,
    "decided_by": "執政官",
    "decided_on": "2026-09-13",
    "evidence": "scripts/stop_evidence.py,50 日均線、7 幣、260 段持有",
    "why": (
        "20% 是分水嶺:從那裡開始,被掃出去的部位**沒有一段最後是賺的** "
        "(15% 還會犧牲 2 段賺的、40.5% 的獲利;20% 與 25% 都是 0)。"
        "選 25% 而不是 20%,是因為這是**災難後備**不是策略出場 —— "
        "它該擺在策略正常運作時永遠碰不到的地方。20% 會在 5.0% 的持倉上"
        "觸發(260 段裡 13 段),大約每二十次進場碰一次,太常;"
        "25% 只在 1.9% 上觸發(5 段)。"
        "上限那一側:3× 的強平距離約 32.8%,而**那是我方算的、偏樂觀的**"
        "(MMR 取定值,未計維持保證金分層)。25% 留 7.8 個百分點給誤差;"
        "30% 只留 2.8pp,太薄。"
    ),
    "known_limits": (
        "一、歷史最深是 36.7%(UNI)與 36.3%(SOL)—— 25% 掃得掉它們,"
        "而那兩段最後都是虧的,所以不算損失。但**歷史沒有上限保證**:"
        "明天可以比 36.7% 更深。"
        "二、BTC 與 BNB 歷史最深只有 11.1% / 12.1%,25% 對它們非常寬。"
        "逐幣停損會更貼,但後備停損的用途是止血不是優化,統一比較不容易錯。"
        "三、這個數字是拿**永續**的日線算的。U 本位標準合約的簿子與滑點"
        "沒有量過(costs.STANDARD_COSTS_VERIFIED is False)。"
    ),
    "revisit_when": (
        "槓桿上限改動(強平距離會跟著變,25% 可能不再有餘裕)、"
        "交易池改動(現在這 260 段是這七個幣的)、"
        "或標準合約的成本被實測之後。"
    ),
}


class NotAllowed(RuntimeError):
    """閘門擋下來了。訊息會說是哪一道。"""


class OrderFailed(RuntimeError):
    """交易所拒單。訊息不含簽章(見 private.py)。"""


def side_for_mode(mode: str | None, wanted: str = LONG) -> str:
    """
    照持倉模式決定 positionSide。

    **mode 是 None(查不到)時拋例外,不預設成任何一邊。**
    猜錯的代價是下單被拒,而在實盤那可能是「該平的倉沒平掉」。
    """
    if mode == "hedge":
        return wanted
    if mode == "one_way":
        return BOTH
    raise NotAllowed(
        "查不到帳戶是單向還是雙向持倉模式,而 positionSide 要送什麼"
        "完全取決於它:\n"
        "  · 雙向 -> LONG / SHORT\n"
        "  · 單向 -> BOTH\n"
        "猜錯會被拒單,而錯誤訊息通常只說「參數錯誤」。\n"
        "在 BingX 網頁的合約設定裡確認一次,或傳明確的 mode 進來。")



def client_order_id(strategy: str, symbol: str, signal_day: str,
                    purpose: str) -> str:
    """
    確定性的冪等鍵(第十六條)。

    同一個決定重跑一百次,產生同一個 id —— 交易所只會收一張。
    網路抖一下之後可以安心重送,因為重送的是**同一張單**。

    用雜湊而不是把欄位串起來,因為交易所對這個欄位有長度限制,
    而 `50日均線之上才持有-1000PEPE-USDT-2026-09-13-entry` 會超過。
    前綴留 `agm` 是為了在交易所的訂單列表裡一眼認得出是誰下的。
    """
    seed = f"{strategy}|{symbol}|{signal_day}|{purpose}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"agm{digest}"


@dataclass
class OrderPlan:
    """
    要送出去的東西,在送出去之前先變成一個可以印、可以檢查的物件。

    **這個 dataclass 是純資料,沒有任何網路行為** —— 所以它可以
    在沒有金鑰、沒有網路的情況下被完整測試。
    """

    symbol: str
    side: str
    position_side: str
    order_type: str
    quantity: float
    client_id: str
    stop_price: float | None = None
    reduce_only: bool = False
    reason: str = ""

    def params(self) -> dict:
        """
        送給交易所的參數。

        ⚠️ 這裡的鍵名是照 BingX Swap V2 的下單端點寫的,而**尚未經過
        實測驗證**。第一次送出必須是 Demo,讓交易所自己說對不對 ——
        參數錯了它會拒單,而在 Demo 裡拒單的成本是零。
        """
        out: dict[str, Any] = {
            "symbol": self.symbol,
            "side": self.side,
            "positionSide": self.position_side,
            "type": self.order_type,
            "quantity": self.quantity,
            "clientOrderID": self.client_id,
        }
        if self.stop_price is not None:
            out["stopPrice"] = self.stop_price
        if self.reduce_only:
            out["reduceOnly"] = "true"
        return out

    def describe(self) -> str:
        """給人看的一行。乾跑的時候印這個。"""
        what = (f"{self.side} {self.quantity} {self.symbol}"
                f" [{self.order_type}]")
        if self.stop_price is not None:
            what += f" 觸發價 {self.stop_price}"
        if self.reduce_only:
            what += " 只減倉"
        return f"{what}  id={self.client_id}  ({self.reason})"


def backstop_price(entry: float, pct: float | None = None) -> float:
    """
    後備停損的價格。

    pct 沒給就用 BACKSTOP_PCT(2026-09-13 起是 25.0)。

    BACKSTOP_PCT 若是 None,**這個函式會拋例外,不會挑一個數字給你** ——
    「沒有人決定停損擺哪裡」不該被一個看起來合理的預設值蓋掉。
    那條路徑留著:哪天有人把它改回 None,行為要跟以前一樣。
    """
    use = BACKSTOP_PCT if pct is None else pct
    if use is None:
        raise NotAllowed(
            "後備停損的百分比還沒有設定(BACKSTOP_PCT is None)。\n"
            "  這是一條 Risk Limit,依第 102 條由執政官決定。\n"
            "  決定之前需要證據:回測裡均線出場觸發之前,單一部位\n"
            "  最深的逆向走勢是多少?擺得比那個淺,就會在歷史上\n"
            "  真的發生過的正常波動裡被掃出去。")
    if use <= 0 or use >= 100:
        raise ValueError(f"後備停損百分比要在 0 與 100 之間,收到 {use}")
    if entry <= 0:
        raise ValueError(f"進場價要是正數,收到 {entry}")
    return entry * (1 - use / 100.0)


def check_size(symbol: str, quantity: float, price: float) -> None:
    """
    交易所的最小量與最小名目(第十二條)。

    ═══ 為什麼這裡要再檢查一次 ═══
    `orders.py` 已經檢查過了,但那條路是「策略產生訂單」。
    任何**繞過 build_orders() 直接組單**的地方(例如驗證腳本)
    就沒有人檢查 —— 而 2026-09-09 的事故正是「七個持倉的數量全部
    不符精度,真的送出去會被直接拒單」。

    紙上看不出來。所以檢查要放在**最靠近送出的地方**,不是只放在
    產生訂單的地方。
    """
    from portfolio import specs

    floor_qty = specs.min_qty(symbol)
    if quantity < floor_qty:
        raise NotAllowed(
            f"{symbol} 數量 {quantity} 低於交易所最小量 {floor_qty} "
            "—— 送出去一定被拒")

    notional = quantity * price
    floor_notional = specs.min_notional(symbol)
    if notional < floor_notional:
        raise NotAllowed(
            f"{symbol} 名目 {notional:.4f} 低於交易所最小名目 "
            f"{floor_notional} —— 送出去一定被拒")


def plan_entry(symbol: str, quantity: float, strategy: str, signal_day: str,
               position_side: str = LONG, reason: str = "") -> OrderPlan:
    """開倉(只做多 —— 這條策略沒有做空,見 §13/§14 的刻意分歧)。"""
    if quantity <= 0:
        raise ValueError(f"數量要是正數,收到 {quantity}")
    return OrderPlan(
        symbol=symbol, side=BUY, position_side=position_side,
        order_type=MARKET, quantity=quantity,
        client_id=client_order_id(strategy, symbol, signal_day, "entry"),
        reason=reason or "開倉")


def plan_backstop(symbol: str, quantity: float, entry: float, strategy: str,
                  signal_day: str, pct: float | None = None,
                  position_side: str = LONG) -> OrderPlan:
    """
    災難後備停損。**只減倉**,永遠不會反手開空。

    reduce_only 這個旗標很重要:沒有它的話,一張在倉已經被平掉之後
    才觸發的停損單,會開出一個**反向的新倉** —— 而那個倉沒有人在管。
    """
    if quantity <= 0:
        raise ValueError(f"數量要是正數,收到 {quantity}")
    return OrderPlan(
        symbol=symbol, side=SELL, position_side=position_side,
        order_type=STOP_MARKET, quantity=quantity,
        stop_price=backstop_price(entry, pct),
        reduce_only=True,
        client_id=client_order_id(strategy, symbol, signal_day, "backstop"),
        reason="災難後備 —— 不是策略出場")


def _reject_hint(code: str) -> str:
    """
    拒單原因的線索。

    2026-09-13 第一次送 Demo 單就是 100004 —— 而那其實是**好消息**:
    它代表簽章、路徑、參數結構全部過了,只卡在最後一道權限閘。
    把這件事寫在訊息裡,免得下一個人以為是參數寫錯。
    """
    if code == "100004":
        return ("\n  這是**權限**問題,不是參數問題 —— 簽章、路徑、"
                "參數結構都過了。"
                "\n  金鑰缺 Perpetual Futures Trading 權限。"
                "\n"
                "\n  加之前先想一件事:**Demo 與實盤共用同一把金鑰**,"
                "\n  給了交易權限,它在實盤也能下單。擋住的是我方的"
                "\n  BINGX_ENV 與 LIVE_ENABLED —— 但金鑰外洩時那兩道"
                "\n  保護不了你,對方不需要用你的程式。"
                "\n"
                "\n  所以要一起做:**IP 白名單**綁這台機器,"
                "\n  **提款權限永遠 OFF**。"
                "\n"
                "\n  只是要驗證持倉欄位的話,不需要交易權限 ——"
                "\n  在 BingX 的 Demo 介面手動開一個最小的倉就好。")
    if code == "80001":
        return "\n  參數格式問題。對一下 positionSide 與持倉模式是否相符。"
    return ""


class Trader:
    """
    會下單的那一層。**建立它本身就要過閘門。**
    """

    def __init__(self, creds=None, mode: str | None = None, session=None):
        self._assert_allowed(mode)
        self.creds = creds or private.Credentials.from_env()
        self.mode = mode
        self.base = private.host(mode)
        self._session = session

    @staticmethod
    def _assert_allowed(mode: str | None) -> None:
        """
        第一道閘:LIVE_ENABLED 是 False 的時候,**只能對 Demo 下單**。

        連 BINGX_ENV=live 都沒有用 —— 環境變數是設定,
        LIVE_ENABLED 是原始碼常數,改它要 commit + 部署(第 45/78 條)。
        """
        from portfolio.execution import LIVE_ENABLED

        if private.is_live(mode) and not LIVE_ENABLED:
            raise NotAllowed(
                "BINGX_ENV=live 但 LIVE_ENABLED=False。\n"
                "  LIVE_ENABLED 是原始碼常數,不讀環境變數、不讀設定檔。\n"
                "  要下實單必須 commit 那個改動並部署,而在那之前\n"
                "  還有畢業契約八條與人工簽署(第 45 / 79 條)。")

    def submit(self, plan: OrderPlan, confirm: bool = False) -> dict:
        """
        送單。**預設不送。**

        confirm=False(預設)只會把要送的東西組出來回傳,不碰網路。
        那不是一個安全網,是**主要用法** —— 第一次跑任何新的下單路徑,
        都應該先看清楚要送什麼。
        """
        params = plan.params()

        if not confirm:
            return {"dry_run": True, "path": ORDER_PATH, "params": params,
                    "描述": plan.describe(),
                    "說明": "這是乾跑,什麼都沒有送出去。"
                            "要真的送,傳 confirm=True。"}

        # 送出前再檢查一次閘門 —— 建立到送出之間可能隔了很久。
        self._assert_allowed(self.mode)

        import time
        from urllib.parse import urlencode

        from core import ratelimit

        query = dict(params)
        query["timestamp"] = int(time.time() * 1000)
        query["recvWindow"] = private.RECV_WINDOW_MS
        sig = private.signature(query, self.creds.secret)
        url = f"{self.base}{ORDER_PATH}?{urlencode(query)}&signature={sig}"

        log.warning(f"送出訂單 {plan.describe()}")

        try:
            resp = ratelimit.requests_request(
                self._session, "POST", url,
                headers={"X-BX-APIKEY": self.creds.key}, timeout=15.0)
        except (ratelimit.RateLimited, ratelimit.Banned):
            raise
        except Exception as e:
            # **訂單送出失敗時,狀態是未知的,不是失敗。**
            # 交易所可能收到了。呼叫端必須去查,不可以直接重送 ——
            # 而它可以安心去查,因為 client_id 是確定性的。
            raise OrderFailed(
                f"{ORDER_PATH} 連線失敗:{type(e).__name__} —— "
                f"**狀態未知,交易所可能已經收到**。"
                f"用 client id {plan.client_id} 查證,不要盲目重送"
            ) from None

        if resp.status_code != 200:
            raise OrderFailed(f"{ORDER_PATH} HTTP {resp.status_code}")

        try:
            body = resp.json()
        except ValueError:
            raise OrderFailed(f"{ORDER_PATH} 回傳的不是 JSON") from None

        code = str(body.get("code", "0"))
        if code != "0":
            raise OrderFailed(
                f"拒單 code={code} msg={body.get('msg')!r}"
                f"{_reject_hint(code)}"
                f"\n  送出的參數:{json.dumps(params, ensure_ascii=False)}")

        return {"dry_run": False, "client_id": plan.client_id,
                "data": body.get("data")}

    def protection_for(self, symbol: str) -> list:
        """
        這個標的在交易所那邊有沒有保護單。

        **只認 reduce-only 的停損單。** 一張沒有 reduceOnly 的停損單
        不是保護,它是一個會在倉不見之後開出反向新倉的陷阱。
        """
        reader = private.ReadOnlyClient(self.creds, self.mode,
                                        session=self._session)
        orders = reader.open_orders(symbol) or []
        rows = orders.get("orders") if isinstance(orders, dict) else orders

        out = []
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            kind = str(row.get("type") or "").upper()
            if "STOP" not in kind:
                continue
            reduce_only = row.get("reduceOnly")
            if reduce_only in (False, "false", "False"):
                continue
            out.append(row)
        return out

    def has_protection(self, symbol: str) -> bool:
        return bool(self.protection_for(symbol))


def unprotected(positions, trader: Trader) -> list:
    """
    有倉、但交易所那邊沒有保護單的標的(第十八條)。

    這是實盤最重要的一條巡檢:**帳本裡有沒有停損欄位,在實盤不代表
    任何事** —— 只有交易所那邊真的掛著一張單才算數。
    """
    out = []
    for row in (positions or []):
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not symbol:
            continue
        try:
            amount = float(row.get("positionAmt") or 0)
        except (TypeError, ValueError):
            # 讀不懂數量 -> **當成有倉**。當成沒倉會讓一個看不懂的
            # 部位安靜地失去保護。
            amount = 1.0
        if abs(amount) < 1e-12:
            continue
        if not trader.has_protection(symbol):
            out.append(symbol)
    return out
