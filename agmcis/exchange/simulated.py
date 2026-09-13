"""
模擬交易所(SIMULATED LIVE)。

## 這是什麼

一個假的 BingX。它實作 `LiveBroker` 會用到的每一個 adapter 方法,
**不連線、不需要金鑰、一毛錢都不會動**,但回傳的資料形狀與 ccxt
4.5.78 的 bingx 真正會產生的一模一樣。

## 為什麼需要它

這個專案在實單路徑上找到的每一個 bug,都是同一種:
**假物件跟真的不一樣,而測試照著假物件寫,所以是綠的。**

    * position["hedged"] —— ccxt 寫死 None,我讀了它
    * 停損單的 type —— ccxt 把 stop_market 映射成 market
    * PARTIALLY_FILLED —— ccxt 映射成 open
    * "submitted" —— 狀態機根本沒有這個狀態

四個都是綠燈狀態下的 bug。原因不是測試寫得不夠多,是**測試餵的
形狀是我想像的**。所以這裡只有一條規則:

    這個模擬器產生的每一個欄位,都要照 ccxt 的 parse_* 真的會
    產生的樣子。不確定的就去讀 ccxt 的原始碼,不要猜(第五節)。

## 它模擬什麼

實盤與模擬盤真正不同的地方 —— 也就是 PaperBroker 驗不到的那些:

    * 停損是交易所那邊一張獨立的掛單,可以被拒絕、可以被撤掉
    * 部分成交
    * 訂單查詢的時間窗
    * 單向 / 雙向持倉
    * 各種失敗:逾時、拒單、查無此單

## 它不模擬什麼

**價格。** 這裡沒有行情、沒有撮合、沒有滑點模型 —— 那些在
`agmcis/backtest/` 與 `paper_trading`。這個模擬器回答的是
「交易所會怎麼回應這個請求」,不是「市場會怎麼走」。

把兩件事混在一起的話,一個回測結果好看的策略會讓人以為
實單路徑也驗過了,而那是兩件完全不同的事。
"""
import itertools
import logging
import time

from agmcis.core.enums import MarketType, OrderSide, OrderType

logger = logging.getLogger("agmcis.exchange.simulated")

# ccxt 的 parse_order_status() 會產生的值。模擬器只用這些 ——
# 產生一個 ccxt 不會產生的狀態,等於又在驗一個想像中的世界。
CCXT_OPEN = "open"
CCXT_CLOSED = "closed"
CCXT_CANCELED = "canceled"

# BingX 原始回應裡的狀態字串(ccxt 映射的來源)。
RAW_STATUS = {
    CCXT_OPEN: "NEW",
    CCXT_CLOSED: "FILLED",
    CCXT_CANCELED: "CANCELED",
}
RAW_PARTIAL = "PARTIALLY_FILLED"


class Failure:
    """
    要模擬的失敗。每一個都對應一件實盤真的會發生、而模擬盤不會的事。
    """

    #: 送單直接拋例外(連線斷掉 —— 交易所可能已經收到了)
    SUBMIT_RAISES = "submit_raises"
    #: 交易所收下訂單但回應裡沒有成交量欄位
    SUBMIT_NO_FILLED_FIELD = "submit_no_filled_field"
    #: 只成交一部分
    PARTIAL_FILL = "partial_fill"
    #: 停損掛單被拒絕(觸發價越過現價是最常見的原因)
    STOP_REJECTED = "stop_rejected"
    #: 停損送出成功,但交易所隨即把它撤掉 —— 回 200 不代表它存在
    STOP_VANISHES = "stop_vanishes"
    #: 撤單失敗
    CANCEL_FAILS = "cancel_fails"
    #: 查詢掛單失敗
    OPEN_ORDERS_FAIL = "open_orders_fail"
    #: 查詢歷史失敗
    HISTORY_FAILS = "history_fails"
    #: 查詢部位失敗
    POSITIONS_FAIL = "positions_fail"
    #: 查不到持倉模式
    POSITION_MODE_FAILS = "position_mode_fails"
    #: 設定槓桿失敗
    LEVERAGE_FAILS = "leverage_fails"
    #: 平倉送出去但沒有成交
    CLOSE_DOES_NOT_FILL = "close_does_not_fill"

    ALL = (
        SUBMIT_RAISES, SUBMIT_NO_FILLED_FIELD, PARTIAL_FILL,
        STOP_REJECTED, STOP_VANISHES, CANCEL_FAILS, OPEN_ORDERS_FAIL,
        HISTORY_FAILS, POSITIONS_FAIL, POSITION_MODE_FAILS,
        LEVERAGE_FAILS, CLOSE_DOES_NOT_FILL,
    )


class SimulatedExchangeError(RuntimeError):
    """模擬出來的交易所錯誤。真實世界對應的是 ccxt 的 NetworkError 等。"""


class SimulatedExchange:
    """
    假的 BingX adapter。

    `failures` 是要啟用的失敗集合(見 `Failure`)。
    `hedged` 決定帳戶是單向還是雙向持倉。
    `partial_ratio` 是 PARTIAL_FILL 啟用時的成交比例。
    """

    name = "simulated"

    def __init__(self, failures=(), hedged=False, partial_ratio=0.4,
                 price=50000.0, clock=None):
        self.failures = set(failures)
        self.hedged = bool(hedged)
        self.partial_ratio = float(partial_ratio)
        self.price = float(price)
        self._clock = clock or (lambda: int(time.time() * 1000))

        self._ids = itertools.count(1)
        self._open_orders = {}      # exchange id -> ccxt 形狀的訂單
        self._history = []          # 已結束的訂單
        self._positions = {}        # symbol -> 內部部位
        self.calls = []             # 被呼叫了什麼,給測試斷言用
        self.leverage = {}

    # ---------------- 內部工具 ----------------

    def _fails(self, name):
        return name in self.failures

    def _record(self, what, **kwargs):
        self.calls.append((what, kwargs))

    def _next_id(self):
        return f"sim-{next(self._ids)}"

    def _order(self, symbol, side, quantity, order_type, price=None,
               client_order_id=None, reduce_only=False, params=None,
               filled=0.0, status=CCXT_OPEN, stop_price=None):
        """
        造一張**ccxt 形狀**的訂單。

        關鍵的幾個欄位刻意照 ccxt 的 bingx 實作:

          * `type` 走 parse_order_type():stop_market 會變成 market,
            所以停損單的 type 是 "market" 而不是 "stop_market"。
          * 觸發價放在 `stopLossPrice`(ccxt 從 stopPrice 推導)。
          * clientOrderId 在頂層是統一欄位,`info` 裡是 BingX 的
            原始拼法 `clientOrderID`(大寫 ID)。
          * 部分成交的 status 是 "open",不是某個獨立的狀態。
        """
        kind = OrderType.parse(order_type, OrderType.MARKET)
        is_stop = kind in (OrderType.STOP, OrderType.STOP_MARKET)

        # ccxt 的 parse_order_type 映射
        unified_type = {
            OrderType.STOP_MARKET: "market",
            OrderType.STOP: "limit",
            OrderType.TAKE_PROFIT_MARKET: "market",
            OrderType.MARKET: "market",
            OrderType.LIMIT: "limit",
        }.get(kind, "market")

        raw_status = RAW_STATUS[status]
        if status == CCXT_OPEN and filled:
            raw_status = RAW_PARTIAL

        side_value = OrderSide.parse(side, OrderSide.BUY).value

        return {
            "id": self._next_id(),
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "type": unified_type,
            "side": side_value,
            "price": price,
            "amount": quantity,
            "filled": filled,
            "average": self.price if filled else None,
            "status": status,
            "reduceOnly": bool(reduce_only),
            # ccxt 從原始的 stopPrice 推導出這個欄位。type 裡的 "stop"
            # 已經被 parse_order_type 洗掉了,所以這是唯一可靠的訊號。
            "stopLossPrice": stop_price if is_stop else None,
            "triggerPrice": None,
            "timestamp": self._clock(),
            "info": {
                "orderId": None,
                "clientOrderID": client_order_id,
                "type": kind.value.upper(),
                "status": raw_status,
                "stopPrice": str(stop_price) if stop_price else "0",
                "positionSide": (params or {}).get("positionSide", "BOTH"),
            },
        }

    def _position(self, symbol):
        return self._positions.get(symbol)

    # ---------------- adapter 介面 ----------------

    def set_leverage(self, leverage, symbol, market_type=None):
        self._record("set_leverage", leverage=leverage, symbol=symbol)
        if self._fails(Failure.LEVERAGE_FAILS):
            raise SimulatedExchangeError(f"設定 {symbol} 槓桿失敗")
        self.leverage[symbol] = float(leverage)

    def get_position_mode(self, symbol=None, market_type=None):
        self._record("get_position_mode", symbol=symbol)
        if self._fails(Failure.POSITION_MODE_FAILS):
            raise SimulatedExchangeError("查不到持倉模式")
        # ccxt 的 fetch_position_mode 回這個形狀。
        return {"info": {"dualSidePosition": str(self.hedged).lower()},
                "hedged": self.hedged}

    def create_order(self, symbol, side, quantity, order_type=OrderType.MARKET,
                     price=None, market_type=None, client_order_id=None,
                     reduce_only=False, params=None):
        self._record(
            "create_order", symbol=symbol, side=side, quantity=quantity,
            order_type=order_type, reduce_only=reduce_only, params=params,
        )

        kind = OrderType.parse(order_type, OrderType.MARKET)
        is_stop = kind in (OrderType.STOP, OrderType.STOP_MARKET)

        if is_stop:
            return self._create_stop(
                symbol, side, quantity, kind, client_order_id, params,
            )

        if self._fails(Failure.SUBMIT_RAISES):
            # 連線斷掉。交易所**可能已經收到了** —— 這正是不可以重送的
            # 那個狀態,所以模擬器也真的把訂單記下來。
            order = self._order(
                symbol, side, quantity, kind, price, client_order_id,
                reduce_only, params, filled=quantity, status=CCXT_CLOSED,
            )
            self._history.append(order)
            self._apply_fill(symbol, side, quantity, reduce_only)
            raise SimulatedExchangeError("送單後連線中斷,狀態不明")

        if reduce_only and self._fails(Failure.CLOSE_DOES_NOT_FILL):
            order = self._order(
                symbol, side, quantity, kind, price, client_order_id,
                reduce_only, params, filled=0.0, status=CCXT_OPEN,
            )
            self._open_orders[order["id"]] = order
            return order

        if self._fails(Failure.SUBMIT_NO_FILLED_FIELD):
            order = self._order(
                symbol, side, quantity, kind, price, client_order_id,
                reduce_only, params, filled=quantity, status=CCXT_CLOSED,
            )
            order.pop("filled")
            self._history.append(order)
            self._apply_fill(symbol, side, quantity, reduce_only)
            return order

        filled = quantity
        status = CCXT_CLOSED

        if self._fails(Failure.PARTIAL_FILL) and not reduce_only:
            filled = round(quantity * self.partial_ratio, 10)
            status = CCXT_OPEN

        order = self._order(
            symbol, side, quantity, kind, price, client_order_id,
            reduce_only, params, filled=filled, status=status,
        )

        if status == CCXT_OPEN:
            self._open_orders[order["id"]] = order
        else:
            self._history.append(order)

        self._apply_fill(symbol, side, filled, reduce_only)
        return order

    def _create_stop(self, symbol, side, quantity, kind,
                     client_order_id, params):
        if self._fails(Failure.STOP_REJECTED):
            raise SimulatedExchangeError(
                f"{symbol} 停損掛單被拒(觸發價已越過現價)"
            )

        stop_price = (params or {}).get("stopPrice")
        order = self._order(
            symbol, side, quantity, kind, None, client_order_id,
            True, params, filled=0.0, status=CCXT_OPEN,
            stop_price=stop_price,
        )

        if self._fails(Failure.STOP_VANISHES):
            # 交易所收下了、回了 200,然後自己把它撤掉。
            # 這是 ensure_stop_loss() 回 True 卻沒有保護的那條路。
            order["status"] = CCXT_CANCELED
            self._history.append(order)
            return order

        self._open_orders[order["id"]] = order
        return order

    def cancel_order(self, order_id, symbol, market_type=None):
        self._record("cancel_order", order_id=order_id, symbol=symbol)

        if self._fails(Failure.CANCEL_FAILS):
            raise SimulatedExchangeError(f"撤單 {order_id} 失敗")

        order = self._open_orders.pop(order_id, None)
        if order is None:
            raise SimulatedExchangeError(f"查無此單 {order_id}")

        order["status"] = CCXT_CANCELED
        order["info"]["status"] = RAW_STATUS[CCXT_CANCELED]
        self._history.append(order)
        return order

    def get_order(self, order_id, symbol, market_type=None, params=None):
        self._record("get_order", order_id=order_id, symbol=symbol)
        order = self._open_orders.get(order_id)
        if order:
            return dict(order)
        for done in self._history:
            if done["id"] == order_id:
                return dict(done)
        return {}

    def get_open_orders(self, symbol=None, market_type=None):
        self._record("get_open_orders", symbol=symbol)
        if self._fails(Failure.OPEN_ORDERS_FAIL):
            raise SimulatedExchangeError("查不到未結掛單")
        return [
            dict(order) for order in self._open_orders.values()
            if symbol is None or order["symbol"] == symbol
        ]

    def get_order_history(self, symbol=None, since=None, limit=None,
                          market_type=None):
        self._record("get_order_history", symbol=symbol, since=since)
        if self._fails(Failure.HISTORY_FAILS):
            raise SimulatedExchangeError("查不到訂單歷史")

        rows = [
            dict(order) for order in self._history
            if symbol is None or order["symbol"] == symbol
        ]
        if since is not None:
            rows = [r for r in rows if (r.get("timestamp") or 0) >= since]
        return rows

    def get_positions(self, symbols=None):
        self._record("get_positions", symbols=symbols)
        if self._fails(Failure.POSITIONS_FAIL):
            raise SimulatedExchangeError("查不到部位")

        rows = []
        for symbol, held in self._positions.items():
            if symbols and symbol not in symbols:
                continue
            if not held["contracts"]:
                continue
            rows.append({
                "symbol": symbol,
                "contracts": held["contracts"],
                "side": held["side"],
                "entryPrice": held["entry"],
                # ⚠️ ccxt 的 bingx parse_position 把這個欄位**寫死成
                # None**,不管帳戶是不是雙向持倉。模擬器照做 ——
                # 讀它的程式碼必須壞掉,那正是它壞掉的地方。
                "hedged": None,
                "info": {
                    "positionSide": held["side"].upper(),
                    "positionAmt": str(held["contracts"]),
                },
            })
        return rows

    # ---------------- 部位帳 ----------------

    def _apply_fill(self, symbol, side, quantity, reduce_only):
        if not quantity:
            return

        side_value = OrderSide.parse(side, OrderSide.BUY)
        held = self._positions.get(symbol)

        if reduce_only:
            if held:
                held["contracts"] = max(
                    0.0, round(held["contracts"] - quantity, 10),
                )
                if not held["contracts"]:
                    self._positions.pop(symbol, None)
            return

        direction = "long" if side_value is OrderSide.BUY else "short"

        if held is None:
            self._positions[symbol] = {
                "contracts": quantity, "side": direction, "entry": self.price,
            }
            return

        if held["side"] == direction:
            held["contracts"] = round(held["contracts"] + quantity, 10)
        else:
            held["contracts"] = round(held["contracts"] - quantity, 10)
            if held["contracts"] <= 0:
                self._positions.pop(symbol, None)

    # ---------------- 給測試與腳本看的 ----------------

    def open_stop_orders(self, symbol=None):
        return [
            order for order in self._open_orders.values()
            if order.get("stopLossPrice") is not None
            and (symbol is None or order["symbol"] == symbol)
        ]

    def position_of(self, symbol):
        return self._positions.get(symbol)

    def names(self):
        return [name for name, _ in self.calls]


def build(failures=(), **kwargs):
    """給腳本用的簡短建構子。"""
    return SimulatedExchange(failures=failures, **kwargs)
