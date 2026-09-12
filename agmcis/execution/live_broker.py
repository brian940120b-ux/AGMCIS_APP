"""
LiveBroker:真的會送出真實訂單的那一個。

## 讀這個檔案之前

這個模組的存在**本身**會讓 LIVE SAFETY GATE 關閉。
`live_gate.check_live_broker_absent()` 掃整個 execution 套件,
掃到這裡就不放行 —— 直到有人在確認檔裡簽下這份原始碼的雜湊值,
明說「我讀過這一份 live 程式碼」。

那個簽名我產不出來:它要的是這個檔案當下的 SHA-256,
而檔案只要再被改一個字,舊的簽名就失效。這是刻意的 ——
第七十八節說 AI 不得自我修改 → 自我測試 → 自我核可 → 自我上線,
所以最後那一步必須由讀過這段程式碼的人按下。

## 這一層做什麼、不做什麼

**不做**:風控、精度對齊、狀態機、對帳、部位追蹤。
那些全部在上游,而且順序是固定的(第十九節、第十七節):

    Risk Engine → Trading Rules → Execution Engine → LiveBroker → BingX

**做**:把 Execution Engine 的請求翻成交易所呼叫,
把交易所的回應翻回 FillResult,以及**最後一道名目上限**。

那道上限是重複的 —— Risk Engine 已經擋過一次。重複是刻意的:
上游任何一段被繞過、被改壞、被新的呼叫路徑跳過,這裡還會擋。
而且它在**讀不到上限時擋下來**,不是放行(第九十四節)。

## 實盤與模擬盤真正不同的地方

  * 停損是交易所那邊一張獨立的掛單,不是資料庫欄位。
    送出成功不代表它存在 —— 觸發價越過現價會被交易所直接撤掉。
    所以 `ensure_stop_loss()` 回 True 只代表「請求被接受」,
    呼叫端**必須**再用 `has_protection()` 確認一次(第十八節)。

  * 部分成交會真的發生。`FillResult.requested_quantity` 一定要帶,
    否則 `is_partial()` 永遠回 False,剩餘量就沒有人去撤。

  * 查詢失敗與「查無此單」是兩件事。前者拋例外,後者回 None。
    把查詢失敗當成 None,會讓一張其實已經成交的單被標成 REJECTED。

## 還沒補完的一個洞

`fetch_order()` 目前**會拋例外,不會回答**。對帳是拿 clientOrderId 去
問交易所,而那要用哪一個 params 欄位還沒對著官方 API 確認過(第五節)。
猜錯的後果不是查不到,是查到「查無此單」,然後成交的單被標成 REJECTED。
細節見 `CLIENT_ID_LOOKUP_PARAM`。
"""
import logging

from agmcis.core.enums import MarketType, OrderSide, OrderType, PositionSide
from agmcis.execution.broker import Broker, FillResult

logger = logging.getLogger("agmcis.execution.live_broker")

# 交易所回報的訂單狀態 → 我們的說法。
# 沒有對應的狀態一律翻成 "unknown" —— 猜一個比較樂觀的值,
# 會讓對帳把一張還活著的單當成結案。
_ORDER_STATES = {
    "open": "submitted",
    "closed": "filled",
    "filled": "filled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "expired": "cancelled",
    "rejected": "rejected",
}


# 用 clientOrderId 查訂單時,要把它放進 params 的哪一個鍵。
#
# **None 代表還沒有人對著 BingX 官方 API 確認過。** 第五節:不要靠模型
# 記憶猜 API。猜錯的後果不是查不到,是查到「查無此單」——而對帳把那個
# 當成確定的答案,會把一張已經成交的單標成 REJECTED。
#
# 所以在確認之前,fetch_order 寧可拋例外(對帳記成「狀態不明,不可重送」)
# 也不回答。確認之後把鍵名填進來,那一行改動本身就是一次人工核可。
CLIENT_ID_LOOKUP_PARAM = None


def _as_float(value):
    """None 就是 None。缺資料不可以變成 0 —— 0 是一個會被拿去算的數字。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class LiveBroker(Broker):
    """
    真實資金。每一個公開方法都會讓交易所那邊發生事情。
    """

    name = "live"
    is_live = True

    def __init__(self, adapter=None, notional_cap=None, market_type=None):
        if adapter is None:
            from agmcis.data.market_data import get_adapter
            adapter = get_adapter()

        self._adapter = adapter
        self._notional_cap = notional_cap
        self._market_type = market_type or MarketType.PERPETUAL

    # ---------------- 最後一道上限 ----------------

    def _max_notional(self):
        """
        單筆名目上限。**讀不到就回 None,而 None 代表擋下來**,
        不是「沒有限制」。

        這與 `safe_live.max_notional()` 的語意刻意相反:那個函式回 None
        代表「這不是實單模式,不必限制」。到了這裡已經確定是實單,
        所以同一個 None 只能解釋成「上限不知道」。
        """
        if self._notional_cap is not None:
            return _as_float(self._notional_cap)

        from agmcis.safety import safe_live
        return safe_live.max_notional(mode="live")

    def _within_cap(self, notional):
        """
        回 (可以送嗎, 說明)。

        三種情況都擋:名目算不出來、上限讀不到、超過上限。
        「算不出來」也擋,是因為一個不知道自己多大的訂單,
        沒有任何理由被送出去。
        """
        notional = _as_float(notional)
        if notional is None:
            return False, "算不出這筆訂單的名目價值,不送 —— 不知道多大就不下單。"

        cap = self._max_notional()
        if cap is None:
            return False, (
                f"讀不到實單名目上限({safe_live_key()}),不送。"
                f"讀不到上限不等於沒有上限。"
            )

        if notional > cap:
            return False, (
                f"名目 {notional:.2f} USDT 超過實單上限 {cap:.2f} USDT。"
            )

        return True, ""

    # ---------------- 進場 ----------------

    def submit_entry(self, order_request, intent, size_usdt, leverage):
        """
        用 **order_request.quantity** 送單。

        那個數量已經被 Trading Rules Engine 對齊過 step size。
        在這裡重算會讓送出去的量與上游驗證過的量不一致,
        而上游驗證過的才是「送得出去」的那一個。
        """
        symbol = intent.symbol
        quantity = _as_float(order_request.quantity)

        if quantity is None or quantity <= 0:
            return FillResult(
                ok=False, requested_quantity=quantity,
                reason=f"{symbol} 數量不合法:{order_request.quantity!r}",
            )

        ok, why = self._within_cap(order_request.notional)
        if not ok:
            logger.error("LIVE | 名目上限擋下 | %s | %s", symbol, why)
            return FillResult(ok=False, requested_quantity=quantity, reason=why)

        # 槓桿要在下單之前設定好。設定失敗就不送 ——
        # 用交易所帳戶上一次的槓桿去開倉,倉位大小會不是我們算的那個。
        if leverage:
            try:
                self._adapter.set_leverage(
                    float(leverage), symbol, market_type=self._market_type,
                )
            except Exception as exc:
                logger.error("LIVE | 設定槓桿失敗 | %s | %s", symbol, exc)
                return FillResult(
                    ok=False, requested_quantity=quantity,
                    reason=f"設定槓桿 {leverage}x 失敗,不送單:"
                           f"{type(exc).__name__}: {exc}",
                )

        raw = self._adapter.create_order(
            symbol=symbol,
            side=order_request.side,
            quantity=quantity,
            order_type=order_request.order_type,
            price=order_request.price,
            market_type=self._market_type,
            client_order_id=order_request.client_order_id,
            params=self._position_side_params(order_request.position_side),
        )
        # 這裡**不接例外**。送單之後連線斷掉,交易所可能已經收到了 ——
        # Execution Engine 會把例外變成 UNKNOWN 交給對帳,那是對的處理。
        # 在這裡吞掉例外並回一個 ok=False,等於宣稱「沒有送出去」。

        filled = _as_float((raw or {}).get("filled"))
        average = _as_float((raw or {}).get("average")) or _as_float(
            (raw or {}).get("price")
        )

        if filled is None:
            # 交易所回了東西,但沒說成交多少。這不是「成交 0」。
            return FillResult(
                ok=False, requested_quantity=quantity, raw=raw or {},
                exchange_order_id=str((raw or {}).get("id") or "") or None,
                reason=f"{symbol} 訂單回應沒有成交量欄位,狀態不明,需對帳。",
            )

        if filled <= 0:
            return FillResult(
                ok=False, requested_quantity=quantity, raw=raw or {},
                exchange_order_id=str((raw or {}).get("id") or "") or None,
                reason=f"{symbol} 沒有成交(狀態 "
                       f"{(raw or {}).get('status')!r})。",
            )

        return FillResult(
            ok=True,
            filled_quantity=filled,
            requested_quantity=quantity,
            average_price=average,
            exchange_order_id=str((raw or {}).get("id") or "") or None,
            raw=raw or {},
        )

    def _position_side_params(self, position_side):
        """
        單向持倉不需要帶 positionSide,雙向持倉一定要帶。
        帶錯會讓平倉單變成反手開倉 —— 這是實盤最貴的一種錯。
        """
        if position_side in (None, PositionSide.BOTH):
            return None
        return {"positionSide": PositionSide.parse(
            position_side, PositionSide.BOTH,
        ).value.upper()}

    def _exit_params(self, position, extra=None):
        """
        出場單(停損、縮倉、平倉)要帶的參數。

        進場單的 positionSide 來自 order_request,出場單沒有那個東西 ——
        所以從**交易所回報的部位**推。這一段是後補的:第一版的出場單
        完全沒帶 positionSide,單向持倉沒事,雙向持倉就是進場帶了、
        出場沒帶,而那正是「平倉單變成反手開倉」的那條路。

        只在部位自己說 hedged 的時候才帶。單向持倉的帳戶送
        positionSide 會被交易所拒絕,而猜錯的方向要往「不送」倒 ——
        少一個參數是被拒絕,帶錯一個參數是開了一張反向的單。
        """
        params = dict(extra or {})

        if not position.get("hedged"):
            return params or None

        side = str(position.get("side") or "").lower()
        if side not in ("long", "short"):
            return params or None

        params["positionSide"] = side.upper()
        return params

    # ---------------- 停損 ----------------

    def has_protection(self, symbol):
        """
        交易所那邊現在有沒有一張活著的、會平掉這個部位的停損單。

        查詢失敗回 False。這裡的「不知道」必須等於「沒有保護」——
        第十八節的緊急流程接下來會重掛停損,重掛一張已經存在的停損
        比放著一個可能沒有保護的部位安全得多。
        """
        try:
            orders = self._adapter.get_open_orders(
                symbol, market_type=self._market_type,
            )
        except Exception as exc:
            logger.error("LIVE | 查不到掛單,視為沒有保護 | %s | %s", symbol, exc)
            return False

        for order in orders or []:
            if _is_stop_order(order) and _is_reduce_only(order):
                return True

        return False

    def ensure_stop_loss(self, symbol, stop_price):
        """
        (重新)掛停損。**回 True 只代表請求被接受。**

        交易所收下請求、回 200,然後因為觸發價已經越過現價而把它撤掉,
        是真的會發生的。所以呼叫端必須再用 has_protection() 確認一次 ——
        broker 介面的說明也是這樣寫的,這裡只是真的做得到那件事。
        """
        stop_price = _as_float(stop_price)
        if stop_price is None or stop_price <= 0:
            return False

        position = self.get_position(symbol)
        if not position:
            logger.error("LIVE | 沒有部位,不掛停損 | %s", symbol)
            return False

        quantity = _as_float(position.get("contracts"))
        if quantity is None or quantity <= 0:
            logger.error("LIVE | 部位數量不明,不掛停損 | %s", symbol)
            return False

        side = _exit_side(position)
        if side is None:
            logger.error("LIVE | 部位方向不明,不掛停損 | %s", symbol)
            return False

        # 先撤掉舊的停損,再掛新的。順序反過來會有一瞬間掛著兩張,
        # 兩張都觸發就變成反向開倉。
        self._cancel_existing_stops(symbol)

        try:
            self._adapter.create_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=OrderType.STOP_MARKET,
                market_type=self._market_type,
                reduce_only=True,
                params=self._exit_params(position, {"stopPrice": stop_price}),
            )
        except Exception as exc:
            logger.error("LIVE | 停損掛單失敗 | %s | %s", symbol, exc)
            return False

        return True

    def _cancel_existing_stops(self, symbol):
        try:
            orders = self._adapter.get_open_orders(
                symbol, market_type=self._market_type,
            )
        except Exception as exc:
            logger.error("LIVE | 撤舊停損前查不到掛單 | %s | %s", symbol, exc)
            return

        for order in orders or []:
            if not _is_stop_order(order):
                continue
            order_id = order.get("id")
            if not order_id:
                continue
            try:
                self._adapter.cancel_order(
                    order_id, symbol, market_type=self._market_type,
                )
            except Exception as exc:
                # 撤不掉要說出來。靜默失敗在這裡的代價是兩張停損
                # 同時掛著(第九十四節)。
                logger.error(
                    "LIVE | 撤舊停損失敗 | %s | %s | %s", symbol, order_id, exc,
                )

    # ---------------- 縮倉與平倉 ----------------

    def reduce_position(self, symbol, fraction, reason=""):
        """第十八節緊急保護的第 3 步:縮掉一部分。"""
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            return False

        if not 0 < fraction < 1:
            return False

        position = self.get_position(symbol)
        if not position:
            return False

        held = _as_float(position.get("contracts"))
        side = _exit_side(position)
        if held is None or held <= 0 or side is None:
            return False

        result = self._close(symbol, held * fraction, side, reason or "REDUCE", position)
        return result.is_filled

    def close_position(self, symbol, price=None, reason=""):
        """
        整筆平掉。price 在實盤沒有作用 —— 平倉一律市價,
        帶一個限價會讓「平倉」變成「掛一張可能不會成交的單」,
        而緊急流程需要的是部位真的消失。
        """
        position = self.get_position(symbol)
        if not position:
            return FillResult(ok=False, reason=f"{symbol} 交易所上沒有這個部位")

        quantity = _as_float(position.get("contracts"))
        side = _exit_side(position)

        if quantity is None or quantity <= 0:
            return FillResult(ok=False, reason=f"{symbol} 部位數量不明,不平倉")
        if side is None:
            return FillResult(ok=False, reason=f"{symbol} 部位方向不明,不平倉")

        # 平倉前先撤掉停損。留著的話停損會在平倉之後變成反向開倉。
        self._cancel_existing_stops(symbol)

        return self._close(symbol, quantity, side, reason, position)

    def _close(self, symbol, quantity, side, reason, position):
        """
        平倉不受名目上限限制。

        上限的目的是擋住「開太大」,而平倉是把風險變小 ——
        一個因為超過上限而平不掉的部位,是這道上限能造成的最壞結果。
        """
        raw = self._adapter.create_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            market_type=self._market_type,
            reduce_only=True,
            params=self._exit_params(position),
        )

        filled = _as_float((raw or {}).get("filled"))
        if filled is None or filled <= 0:
            return FillResult(
                ok=False, requested_quantity=quantity, raw=raw or {},
                reason=f"{symbol} 平倉沒有成交({reason})。",
            )

        return FillResult(
            ok=True,
            filled_quantity=filled,
            requested_quantity=quantity,
            average_price=_as_float((raw or {}).get("average")),
            exchange_order_id=str((raw or {}).get("id") or "") or None,
            raw=raw or {},
        )

    def cancel_remainder(self, order):
        """
        撤掉部分成交訂單的未成交剩餘量。

        撤不掉回 False,而且**上游必須當成問題** ——
        一張還活著的掛單會在稍後成交,而那時候沒有人在管它。
        """
        order_id = getattr(order, "exchange_order_id", None)
        symbol = getattr(order, "symbol", None)

        if not order_id or not symbol:
            logger.error("LIVE | 沒有交易所訂單編號,撤不了剩餘量 | %s", symbol)
            return False

        try:
            self._adapter.cancel_order(
                order_id, symbol, market_type=self._market_type,
            )
        except Exception as exc:
            logger.error("LIVE | 撤剩餘量失敗 | %s | %s | %s", symbol, order_id, exc)
            return False

        return True

    # ---------------- 查詢 ----------------

    def get_position(self, symbol):
        """
        單一標的的部位。沒有部位回 None,**查詢失敗拋例外**。

        把查詢失敗變成 None,會讓緊急流程認為「已經沒有部位了」
        而停止動作 —— 那正好是最需要動作的時候。
        """
        for position in self._adapter.get_positions([symbol]) or []:
            if position.get("symbol") != symbol:
                continue
            if not _as_float(position.get("contracts")):
                continue
            return position

        return None

    def fetch_order(self, client_order_id, symbol):
        """
        回 dict 或 None(交易所查無此單)。查詢失敗拋例外。

        兩者混在一起的後果:一張其實已經成交的單被標成 REJECTED,
        然後對帳會看到一個沒有任何本地紀錄的部位。

        ⚠️ 這一段目前**會拋例外,不會回答**。原因見 CLIENT_ID_LOOKUP_PARAM。
        """
        # 介面給的是 client_order_id,而 ccxt 的 fetch_order 第一個參數
        # 是**交易所的訂單編號**。用 client id 去查交易所的 id 欄位,
        # 交易所會回「查無此單」——而對帳把「查無此單」當成確定的答案,
        # 直接把訂單標成 REJECTED。一張其實已經成交的單被標成沒送出去,
        # 是這整個模組裡最貴的一個錯。
        #
        # 正確做法是把 client id 放進 params 的某個鍵。那個鍵叫什麼,
        # 第五節說了不要靠記憶猜 —— 所以在有人用 scripts/verify_bingx.py
        # 對著官方 API 確認之前,這裡不回答,而是拋例外。
        #
        # 拋例外的結果是對帳記一筆 ORDER_STILL_UNKNOWN(狀態不明,
        # 這張單不可以重送),那是安全的方向;回 None 不是。
        if CLIENT_ID_LOOKUP_PARAM is None:
            raise LookupError(
                "用 clientOrderId 查訂單的參數名稱還沒有對著 BingX 官方 API "
                "確認過(第五節:不要靠模型記憶猜 API)。\n"
                "在確認並設定 live_broker.CLIENT_ID_LOOKUP_PARAM 之前,"
                "這裡不回答 —— 猜錯會讓已成交的單被標成 REJECTED。\n"
                "確認方式:.venv/bin/python scripts/verify_bingx.py"
            )

        try:
            raw = self._adapter.get_order(
                client_order_id, symbol, market_type=self._market_type,
                params={CLIENT_ID_LOOKUP_PARAM: client_order_id},
            )
        except Exception as exc:
            if _is_order_not_found(exc):
                return None
            raise

        if not raw:
            return None

        return {
            "state": _ORDER_STATES.get(
                str(raw.get("status") or "").lower(), "unknown",
            ),
            "filled_quantity": _as_float(raw.get("filled")),
            "average_price": _as_float(raw.get("average")),
            "exchange_order_id": str(raw.get("id") or "") or None,
            "raw": raw,
        }

    def fetch_positions(self):
        """交易所那邊現在有哪些部位。拿不到就拋例外,不要回空清單。"""
        return [
            position for position in self._adapter.get_positions() or []
            if _as_float(position.get("contracts"))
        ]


# ---------------- 模組層的小工具 ----------------


def safe_live_key():
    from agmcis.safety import safe_live
    return safe_live.LIVE_NOTIONAL_KEY


def _is_stop_order(order):
    kind = str(order.get("type") or "").lower()
    if "stop" in kind:
        return True
    info = order.get("info") or {}
    return "stop" in str(info.get("type") or "").lower()


def _is_reduce_only(order):
    if order.get("reduceOnly"):
        return True
    info = order.get("info") or {}
    return str(info.get("reduceOnly")).lower() == "true"


def _exit_side(position):
    """
    平掉這個部位要送哪一邊。方向不明回 None ——
    在這裡猜錯的代價是把倉位開成兩倍,不是平掉。
    """
    side = str(position.get("side") or "").lower()
    if side == "long":
        return OrderSide.SELL
    if side == "short":
        return OrderSide.BUY
    return None


def _is_order_not_found(exc):
    """
    「查無此單」與「查詢失敗」的分界線。

    只認 ccxt 明確的 OrderNotFound。用字串比對去猜其他例外,
    會把一個逾時錯誤解釋成「這張單不存在」。
    """
    try:
        import ccxt
    except ImportError:
        return False

    return isinstance(exc, ccxt.OrderNotFound)
