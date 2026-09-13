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

  * 對帳是拿 **clientOrderId** 去問「這張單怎麼了」,而 ccxt 的
    `fetch_order(id, ...)` 那個 id 是交易所的訂單編號。所以
    `fetch_order()` 改成列舉未結與歷史清單再在本地比對 ——
    細節與證據見那個方法的說明。
"""
import logging
import time

from agmcis.core.enums import MarketType, OrderSide, OrderType, PositionSide
from agmcis.execution.broker import Broker, FillResult

logger = logging.getLogger("agmcis.execution.live_broker")

# 交易所回報的訂單狀態 → 我們的說法。
# 沒有對應的狀態一律翻成 "unknown" —— 猜一個比較樂觀的值,
# 會讓對帳把一張還活著的單當成結案。
#
# 這裡的鍵是 **ccxt 統一之後**的狀態,不是 BingX 的原始字串。
# ccxt 4.5.78 的 bingx parse_order_status() 產生的是:
#
#     NEW / PENDING / PARTIALLY_FILLED / RUNNING -> open
#     FILLED                                     -> closed
#     CANCELED / CANCELLED / FAILED              -> canceled
#     (認不出來的原樣回傳)
#
# 注意 **PARTIALLY_FILLED 也是 open** —— 部分成交與完全沒成交在這個
# 欄位上長得一模一樣,所以 _as_order_state() 還要看成交量。
#
# expired 與 rejected 這兩個 bingx 目前不會產生(FAILED 走 canceled),
# 留著是因為認不出來的狀態會原樣回傳,而別的路徑可能送這兩個字進來。
# ⚠️ 值必須是 OrderState 認得的字串。"submitted" 不是 —— 狀態機叫
# ACCEPTED。翻出一個狀態機不認得的字串,OrderState.parse() 會退回
# UNKNOWN,對帳就永遠解不掉那張單。有測試釘住這件事。
_ORDER_STATES = {
    "open": "accepted",
    "closed": "filled",
    "filled": "filled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "expired": "cancelled",
    "rejected": "rejected",
}


# 查訂單歷史時往回看多久。
#
# 對帳問的是「這張剛剛送出去、狀態不明的單怎麼了」,所以它一定是近期的。
# 七天很寬鬆是刻意的:窗口太窄會讓「不在窗口內」被誤讀成「不存在」。
#
# ⚠️ 這仍然是一個窗口。一張卡在 UNKNOWN 超過七天的單,這裡會回 None
# (也就是「交易所沒有這張單」),而那個答案可能是錯的。不過一張卡在
# UNKNOWN 七天的單本身就是更大的問題,不該靠對帳自動收尾。
HISTORY_LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000


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
        # 帳戶層級的持倉模式,查到才快取(見 _hedge_mode)。
        self._hedge_mode_cache = None

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

    def _hedge_mode(self):
        """
        帳戶是不是雙向持倉。**不知道就回 None。**

        不能讀部位裡的 `hedged` 欄位 —— ccxt 4.5.78 的 bingx
        `parse_position()` 把它**寫死成 None**,所以那個欄位永遠是假值。
        第一版就是讀它,結果是雙向持倉永遠不會帶 positionSide,
        等於那個修正從來沒有生效過。

        正確來源是 `fetch_position_mode`(BingX 的 positionSideDual
        端點),它直接回答這個問題。這是帳戶層級的設定,不會每張單改,
        所以查一次就快取 —— 但**查失敗不快取**,免得一次網路抖動
        讓整個行程都用錯的假設下單。
        """
        if self._hedge_mode_cache is not None:
            return self._hedge_mode_cache

        try:
            mode = self._adapter.get_position_mode(
                market_type=self._market_type,
            )
        except Exception as exc:
            logger.error("LIVE | 查不到持倉模式 | %s", exc)
            return None

        hedged = (mode or {}).get("hedged")
        if not isinstance(hedged, bool):
            logger.error("LIVE | 持倉模式回應看不懂:%r", mode)
            return None

        self._hedge_mode_cache = hedged
        return hedged

    def _exit_params(self, position, extra=None):
        """
        出場單(停損、縮倉、平倉)要帶的參數。

        進場單的 positionSide 來自 order_request,出場單沒有那個東西。
        第一版的出場單完全沒帶 —— 單向持倉沒事,雙向持倉就是進場帶了、
        出場沒帶,而那正是「平倉單變成反手開倉」的那條路。

        只有在**確定是雙向持倉**而且部位方向明確時才帶。
        不知道就不帶:少一個參數會被交易所拒絕(而拒絕掉的停損會被
        第十八節的緊急流程接住),帶錯一個參數是開了一張反向的單。
        """
        params = dict(extra or {})

        if self._hedge_mode() is not True:
            return params or None

        side = str(position.get("side") or "").lower()
        if side not in ("long", "short"):
            logger.error(
                "LIVE | 雙向持倉但部位方向是 %r,不帶 positionSide", side,
            )
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

        ## 為什麼是列舉再比對,不是直接查

        介面給的是 client_order_id,而 ccxt 的 `fetch_order(id, ...)` 那個
        id 是**交易所的訂單編號**。看 ccxt 4.5.78 的 bingx 實作:

          * `create_order` 與 `cancel_order` 都認得 clientOrderId,
            送給 BingX 永續的欄位是 `clientOrderID`。
          * `cancel_order` 有分支:帶了 clientOrderID 就**不送 orderId**。
          * `fetch_order` **沒有那個分支** —— 它一律送 `orderId: id`,
            然後把 params merge 進去。

        所以把 client id 塞進 fetch_order,會同時送出一個假的 orderId。
        交易所很可能因此回錯或回查無此單,而對帳把「查無此單」當成確定
        的答案,直接標 REJECTED。

        列舉(未結 + 歷史)再比對 ccxt 統一後的 `clientOrderId` 欄位,
        走的全是有文件的路徑,而且不會送出任何假欄位。

        ## 「查不到」在這裡是什麼意思

        只有在**兩份清單都成功取得**、而且兩邊都沒有這個 client id 時
        才回 None。任何一邊查詢失敗都拋例外 —— 查不到與查詢失敗混在
        一起,正是這個方法要避免的事。

        ⚠️ 歷史查詢有時間窗(見 HISTORY_LOOKBACK_MS)。
        """
        wanted = str(client_order_id)

        # 先看未結的。一張還活著的單在這裡,而且這份清單沒有時間窗。
        for order in self._adapter.get_open_orders(
            symbol, market_type=self._market_type,
        ) or []:
            if _client_id_of(order) == wanted:
                return self._as_order_state(order)

        # 再看歷史。已成交、已撤銷、被拒絕的都在這裡。
        #
        # since 用本機時鐘算。系統別的地方會為了簽章去對交易所的時間
        # (時鐘偏移超過 EXCHANGE_MAX_CLOCK_SKEW_MS 會直接報錯),
        # 但那是毫秒級的事;這裡的視窗是七天,差幾秒不影響結果。
        # 為了一個七天的視窗多打一次 API 是浪費。
        since = int(time.time() * 1000) - HISTORY_LOOKBACK_MS

        for order in self._adapter.get_order_history(
            symbol, since=since, market_type=self._market_type,
        ) or []:
            if _client_id_of(order) == wanted:
                return self._as_order_state(order)

        # 兩份清單都拿到了,兩邊都沒有 —— 這才是「交易所沒有這張單」。
        logger.info(
            "LIVE | 未結與歷史清單都沒有這張單 | %s | %s", symbol, wanted,
        )
        return None

    def _as_order_state(self, order):
        """
        把 ccxt 的訂單翻成狀態機認得的說法。

        **不能只查狀態字串。** ccxt 4.5.78 的 bingx `parse_order_status()`
        把 `PARTIALLY_FILLED` 映射成 `open` —— 部分成交與完全沒成交在
        那個欄位上長得一模一樣。照著翻,一張部分成交的單會被對帳標成
        SUBMITTED,然後**剩餘量沒有人去撤**,而那張還活著的掛單會在
        稍後成交,那時候沒有人在管它。

        所以 open 還要看成交量:成交量大於 0 就是部分成交。
        """
        status = str(order.get("status") or "").lower()
        state = _ORDER_STATES.get(status, "unknown")
        filled = _as_float(order.get("filled"))

        if state == "accepted" and filled is not None and filled > 0:
            state = "partially_filled"

        return {
            "state": state,
            "filled_quantity": filled,
            "average_price": _as_float(order.get("average")),
            "exchange_order_id": str(order.get("id") or "") or None,
            # 原始狀態字串留著。事後要查「為什麼判成這個」,
            # 需要看到翻譯之前的那個值。
            "exchange_status": order.get("status"),
            "raw": order,
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


def _client_id_of(order):
    """
    ccxt 統一之後的 clientOrderId。

    BingX 永續回的欄位是 `clientOrderID`(大寫 ID),現貨是
    `origClientOrderId` —— ccxt 的 parse_order 會把它們收斂成
    `clientOrderId`。這裡讀統一後的那個,並且留 info 當退路,
    因為退路不花錢而少讀一個欄位會讓比對整個失效。
    """
    value = order.get("clientOrderId")
    if value:
        return str(value)

    info = order.get("info") or {}
    for key in ("clientOrderID", "clientOrderId", "origClientOrderId", "c"):
        value = info.get(key)
        if value:
            return str(value)

    return None


def _is_stop_order(order):
    """
    這張掛單是不是停損。

    **不能只看 `type`。** ccxt 4.5.78 的 bingx `parse_order_type()` 把
    `stop_market` 映射成 `market`、`stop_limit` 映射成 `limit` ——
    停損單回來之後,`type` 裡的 "stop" 字樣已經不見了。

    第一版就是只看 `type` 加上一個讀原始欄位字串的退路。主判斷永遠
    失敗,整個功能靠那條退路撐著,而退路是在賭 BingX 的拼法。
    如果兩邊都沒中,`has_protection()` 會永遠回 False ——
    緊急流程就會在每一次輪詢重新撤掉再掛上停損,沒完沒了。

    可靠的訊號是 ccxt **明文承諾**會設的那兩個價格欄位:
    `parse_order()` 看到 stopPrice 就會填 `stopLossPrice` 或
    `triggerPrice`。有觸發價的掛單就是條件單。

    字串比對留著當補充,不當主力。
    """
    if order.get("stopLossPrice") is not None:
        return True
    if order.get("triggerPrice") is not None:
        return True

    kind = str(order.get("type") or "").lower()
    if "stop" in kind:
        return True

    info = order.get("info") or {}
    for key in ("type", "origType", "stopPrice", "StopPrice"):
        value = info.get(key)
        if value in (None, "", "0", 0):
            continue
        if key in ("stopPrice", "StopPrice"):
            return True
        if "stop" in str(value).lower():
            return True

    return False


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
