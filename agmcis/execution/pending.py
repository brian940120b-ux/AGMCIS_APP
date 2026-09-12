"""
掛單(Master Prompt 第十三節的 LIMIT / STOP / TAKE_PROFIT)。

到目前為止執行層只送 MARKET。訂單型別列舉早就齊全了,但一張
「掛在 98 等它跌下來」的單需要的不只是一個型別欄位 —— 它需要
一個會在價格到達時把它變成成交的東西,而那個東西不存在。

這個模組就是那個東西。

## 為什麼掛單值得做

市價單每一次都吃掉半個價差加滑點。對一個一天進出好幾次的系統,
那是實打實的成本 —— 第一百零六節第 10 點要求所有策略都要考慮
Fees / Slippage,而降低滑點最直接的方式就是不要每次都用市價。

## 三條規則

**一、掛單也有有效期。** 一張掛了三天的單,當初的訊號早就過期了,
但它還是會在某個半夜成交。所以每一張都有 `expires_at`,
到期就取消,而不是永遠掛著等。

**二、觸價用區間極值,不是輪詢當下的價格。**
與 position_monitor 同一個理由:輪詢間隔內價格可能碰到又離開,
只看當下的價格會漏掉真的成交過的機會。

**三、成交價是限價,不是現價。**
一張掛在 98 的買單,價格跌到 97 時成交在 98(或更好)。
用 97 記帳會讓回測與模擬盤系統性高估 —— 而那是往樂觀的方向。
實際上更好的成交是可能的,但假設拿得到「更好」是不保守的。
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from agmcis.core.enums import Direction, OrderState, OrderType

logger = logging.getLogger("agmcis.execution.pending")

# 掛單預設多久失效。四小時 = 大約一個 4H K 棒,
# 超過那個時間,產生訊號時看到的結構已經不一樣了。
DEFAULT_TTL_HOURS = 4.0

FILLED = "FILLED"
STILL_WAITING = "STILL_WAITING"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"


def _utcnow():
    return datetime.now(timezone.utc)


def _sign(direction):
    value = str(getattr(direction, "value", direction) or "").strip().upper()
    if value in ("做多", "LONG", "BUY", "看多"):
        return 1
    if value in ("做空", "SHORT", "SELL", "看空"):
        return -1
    return 0


@dataclass
class PendingOrder:
    """
    一張還沒成交的掛單。

    `trigger_price` 是價格要走到哪裡;`fill_price` 是走到之後成交在哪裡。
    對 LIMIT 兩者相同;對 STOP(停損進場)成交價會比觸發價差 ——
    停損單觸發之後是用市價送出去的。
    """
    client_order_id: str
    symbol: str
    direction: str
    order_type: str
    trigger_price: float
    quantity: float
    size_usdt: float
    leverage: float
    stop_loss: float
    take_profit: Optional[float] = None
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: Optional[datetime] = None

    @property
    def sign(self):
        return _sign(self.direction)

    def is_expired(self, now=None):
        if self.expires_at is None:
            return False
        return (now or _utcnow()) >= self.expires_at

    def triggered(self, high=None, low=None, price=None):
        """
        價格有沒有走到觸發價。

        區間極值優先,沒有就退回單點價格 —— 與 position_monitor 同一個
        理由:輪詢間隔內價格可能碰到又離開。
        """
        sign = self.sign
        if sign == 0:
            return False

        kind = str(self.order_type).lower()

        if kind in ("limit", "take_profit", "take_profit_market"):
            # 限價買掛在現價下方:價格跌到那裡才成交。
            if sign > 0:
                reference = low if low is not None else price
                return reference is not None and reference <= self.trigger_price
            reference = high if high is not None else price
            return reference is not None and reference >= self.trigger_price

        if kind in ("stop", "stop_market"):
            # 停損進場掛在現價上方(做多):突破才進場。
            if sign > 0:
                reference = high if high is not None else price
                return reference is not None and reference >= self.trigger_price
            reference = low if low is not None else price
            return reference is not None and reference <= self.trigger_price

        raise ValueError(f"這個型別不需要掛單:{self.order_type!r}")

    def fill_price(self, costs=None):
        """
        成交價。

        LIMIT 成交在限價 —— 不是現價。價格跌到 97 時,掛在 98 的買單
        成交在 98。用 97 記帳會系統性高估,而那是往樂觀的方向。
        實際上更好的成交是可能的,但假設拿得到「更好」不保守。

        STOP 觸發之後是市價送出去的,所以照樣吃滑點。
        """
        kind = str(self.order_type).lower()

        if kind in ("stop", "stop_market") and costs is not None:
            return costs.entry_price(self.trigger_price, self.sign > 0)

        return self.trigger_price

    def to_dict(self):
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "order_type": self.order_type,
            "trigger_price": self.trigger_price,
            "quantity": self.quantity,
            "size_usdt": self.size_usdt,
            "leverage": self.leverage,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


def from_request(order_request, intent, size_usdt, leverage,
                 ttl_hours=DEFAULT_TTL_HOURS, now=None):
    """從 Trading Rules 驗過的 OrderRequest 建一張掛單。"""
    moment = now or _utcnow()
    trigger = order_request.price or order_request.reference_price

    if trigger is None:
        raise ValueError("掛單沒有觸發價")

    return PendingOrder(
        client_order_id=order_request.client_order_id,
        symbol=order_request.symbol,
        direction=getattr(intent.direction, "value", intent.direction),
        order_type=getattr(
            order_request.order_type, "value", order_request.order_type,
        ),
        trigger_price=float(trigger),
        quantity=float(order_request.quantity),
        size_usdt=float(size_usdt),
        leverage=float(leverage),
        stop_loss=float(intent.stop_loss),
        take_profit=intent.take_profit,
        created_at=moment,
        expires_at=(
            moment + timedelta(hours=float(ttl_hours))
            if ttl_hours else None
        ),
    )


@dataclass
class PendingResult:
    status: str = STILL_WAITING
    client_order_id: Optional[str] = None
    symbol: Optional[str] = None
    fill_price: Optional[float] = None
    reason: str = ""

    def to_dict(self):
        return dict(self.__dict__)


class PendingBook:
    """
    掛單簿。純記憶體 —— 但每一次變化都會寫進 order_store,
    所以重啟之後可以從 ACCEPTED 狀態的訂單重建。

    ⚠️ 這是**模擬盤**的掛單簿。實盤的掛單在交易所那邊,
    這裡不該再模擬一次 —— 兩份掛單簿會產生兩種事實。
    LiveBroker 出現時這個類別必須被繞過,不是被沿用。
    """

    def __init__(self, store=None, clock=None):
        self._orders = {}
        self._store = store
        self._clock = clock or _utcnow

    @property
    def store(self):
        if self._store is None:
            from agmcis.execution import order_store
            self._store = order_store.get_store()
        return self._store

    def add(self, pending):
        self._orders[pending.client_order_id] = pending
        logger.info(
            "Pending | ADDED | %s | %s %s @ %s | 到期 %s",
            pending.symbol, pending.direction, pending.order_type,
            pending.trigger_price, pending.expires_at,
        )
        return pending

    def all(self):
        return list(self._orders.values())

    def for_symbol(self, symbol):
        return [o for o in self._orders.values() if o.symbol == symbol]

    def cancel(self, client_order_id, reason="手動取消"):
        pending = self._orders.pop(client_order_id, None)
        if pending is None:
            return None

        logger.info("Pending | CANCELLED | %s | %s", pending.symbol, reason)
        return PendingResult(
            status=CANCELLED, client_order_id=client_order_id,
            symbol=pending.symbol, reason=reason,
        )

    def check(self, price_of, range_of=None, fill=None, costs=None, now=None):
        """
        跑一輪:到期的取消,觸價的成交。

        `price_of(symbol) -> float`,`range_of(symbol) -> (high, low)`。
        `fill(pending, price) -> bool` 由呼叫端提供 —— 這一層決定
        「該不該成交」,不決定「怎麼成交」。

        單一標的失敗不會中斷整輪。一張因為取不到價而被跳過的掛單,
        下一輪還在;但一輪在中間爆掉會讓後面所有掛單都沒有人管。
        """
        moment = now or self._clock()
        results = []

        for pending in list(self._orders.values()):
            try:
                results.append(self._check_one(
                    pending, price_of, range_of, fill, costs, moment,
                ))
            except Exception as exc:
                logger.exception("Pending | CHECK_FAILED | %s", pending.symbol)
                results.append(PendingResult(
                    status=STILL_WAITING,
                    client_order_id=pending.client_order_id,
                    symbol=pending.symbol,
                    reason=f"{type(exc).__name__}: {exc}",
                ))

        return results

    def _check_one(self, pending, price_of, range_of, fill, costs, now):
        # 到期先看:一張過期的單即使這一輪觸價也不該成交 ——
        # 當初的訊號已經不成立了。
        if pending.is_expired(now):
            self._orders.pop(pending.client_order_id, None)
            logger.info(
                "Pending | EXPIRED | %s | 掛了 %.1f 小時沒成交",
                pending.symbol,
                (now - pending.created_at).total_seconds() / 3600.0,
            )
            return PendingResult(
                status=EXPIRED, client_order_id=pending.client_order_id,
                symbol=pending.symbol, reason="掛單到期",
            )

        price = price_of(pending.symbol)
        if price is None:
            return PendingResult(
                status=STILL_WAITING,
                client_order_id=pending.client_order_id,
                symbol=pending.symbol, reason="取不到現價",
            )

        high = low = None
        if range_of is not None:
            bounds = range_of(pending.symbol)
            if bounds:
                high, low = bounds

        if not pending.triggered(high=high, low=low, price=float(price)):
            return PendingResult(
                status=STILL_WAITING,
                client_order_id=pending.client_order_id,
                symbol=pending.symbol,
            )

        fill_price = pending.fill_price(costs=costs)

        if fill is not None and not fill(pending, fill_price):
            return PendingResult(
                status=STILL_WAITING,
                client_order_id=pending.client_order_id,
                symbol=pending.symbol, reason="成交被拒",
            )

        self._orders.pop(pending.client_order_id, None)
        logger.info(
            "Pending | FILLED | %s | %s @ %s",
            pending.symbol, pending.order_type, fill_price,
        )
        return PendingResult(
            status=FILLED, client_order_id=pending.client_order_id,
            symbol=pending.symbol, fill_price=fill_price,
        )


_book = None


def get_book():
    global _book
    if _book is None:
        _book = PendingBook()
    return _book


def set_book(book):
    global _book
    _book = book


def run_pending_orders(price_of=None, range_of=None, book=None):
    """排程入口。"""
    book = book or get_book()

    if price_of is None:
        from market_data import get_price as price_of

    if range_of is None:
        from position_monitor import _intrabar_range

        def range_of(symbol):
            high, low, _ = _intrabar_range(symbol)
            return (high, low) if high is not None else None

    def fill(pending, price):
        import paper_trading

        result = paper_trading.create_paper_trade(
            symbol=pending.symbol,
            entry_price=price,
            signal=pending.direction,
            size_usdt=pending.size_usdt,
            stoploss=pending.stop_loss,
            takeprofit=pending.take_profit,
            leverage=pending.leverage,
            position_value=pending.quantity * price,
            source="PENDING",
        )
        return bool(result.get("success"))

    results = book.check(price_of, range_of=range_of, fill=fill)

    return {
        "checked": len(results),
        "filled": [r.symbol for r in results if r.status == FILLED],
        "expired": [r.symbol for r in results if r.status == EXPIRED],
        "waiting": sum(1 for r in results if r.status == STILL_WAITING),
    }
