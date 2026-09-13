"""
SIMULATED LIVE:讓實單路徑整條跑起來,但一毛錢都不動。

    python scripts/simulate_live.py

## 這支腳本存在的理由

`LiveBroker` 寫好了,但它沒有接進 Execution Engine,而且在真的 BingX
上一次都沒有跑過。中間那一段 —— 「這條路徑接得起來嗎」—— 既不需要
真錢,也不需要網路,只需要一個行為像 BingX 的東西。

那個東西是 `agmcis/exchange/simulated.py`。它回傳的每一個欄位都照
ccxt 4.5.78 的 bingx 真正會產生的形狀,包括那些坑:

    * 停損單的 type 是 "market"(parse_order_type 洗掉了 "stop")
    * 部分成交的 status 是 "open"
    * 部位的 hedged 永遠是 None

## 它驗什麼、不驗什麼

**驗**:交易所這樣回應的時候,系統會做什麼。

**不驗**:策略賺不賺錢。這裡沒有行情、沒有撮合 —— 那是回測與
模擬盤的事。一個回測好看的策略跟一條走得通的實單路徑是兩件事,
混在一起會讓人以為驗過了其實沒有。

**更不代表可以下實單。** 模擬器永遠比真實交易所仁慈:它不會限流、
不會有時鐘偏移、不會在半夜改 API。能不能下實單由 LIVE SAFETY GATE
決定,而它需要人的簽章。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.core.enums import OrderSide, OrderType, PositionSide  # noqa: E402
from agmcis.exchange.simulated import Failure, SimulatedExchange  # noqa: E402
from agmcis.execution import emergency  # noqa: E402
from agmcis.execution.live_broker import LiveBroker  # noqa: E402

RULE = "=" * 70
SYMBOL = "BTC-USDT"
CAP = 1_000_000.0


class Intent:
    def __init__(self, symbol=SYMBOL):
        self.symbol = symbol


class Request:
    def __init__(self, quantity=0.01, position_side=PositionSide.LONG):
        self.client_order_id = "sim-cid-1"
        self.side = OrderSide.BUY
        self.position_side = position_side
        self.order_type = OrderType.MARKET
        self.quantity = quantity
        self.price = None
        self.reference_price = 50000.0

    @property
    def notional(self):
        return self.quantity * self.reference_price


def broker(exchange):
    return LiveBroker(adapter=exchange, notional_cap=CAP)


# ---------------- 情境 ----------------
#
# 每一個情境回傳 (通過?, 說明)。
# 「通過」的意思一律是「往安全的方向倒」,不是「成功下單」。


def scenario_happy_path(writer):
    exchange = SimulatedExchange()
    live = broker(exchange)

    fill = live.submit_entry(Request(), Intent(), 100.0, 5)
    if not fill.is_filled:
        return False, f"正常路徑竟然沒成交:{fill.reason}"

    if not live.ensure_stop_loss(SYMBOL, 49000.0):
        return False, "停損掛不上去"

    if not live.has_protection(SYMBOL):
        return False, "停損掛上了但查不到"

    closed = live.close_position(SYMBOL, reason="SIM")
    if not closed.ok:
        return False, f"平不掉:{closed.reason}"

    if exchange.position_of(SYMBOL) is not None:
        return False, "平倉之後交易所還有部位"

    if exchange.open_stop_orders(SYMBOL):
        return False, "平倉之後停損單還掛著 —— 它會變成反向開倉"

    return True, "開倉 → 掛停損 → 確認 → 平倉,而且平倉前撤掉了停損"


def scenario_stop_rejected(writer):
    """
    第十八節:停損掛不上去的時候,不可以留一個沒有保護的部位。
    """
    exchange = SimulatedExchange(failures={Failure.STOP_REJECTED})
    live = broker(exchange)
    live.submit_entry(Request(), Intent(), 100.0, 5)

    outcome = emergency.protect(
        live, SYMBOL, 49000.0, retries=2,
        close_position=None, pause_file=None,
        disable_orders=False, notifier=lambda *a, **k: None,
    )

    if outcome.status != "CLOSED":
        return False, f"停損掛不上卻沒有平倉,結果是 {outcome.status}"

    if exchange.position_of(SYMBOL) is not None:
        return False, "宣稱平倉了但交易所還有部位"

    return True, "重試失敗 → 縮倉 → 還是不行 → 平倉。沒有留下裸倉"


def scenario_stop_vanishes(writer):
    """
    交易所收下停損、回 200,然後自己把它撤掉。

    `ensure_stop_loss()` 回 True 只代表請求被接受 —— 呼叫端必須
    再用 `has_protection()` 確認一次。這個情境就是在驗那句話。
    """
    exchange = SimulatedExchange(failures={Failure.STOP_VANISHES})
    live = broker(exchange)
    live.submit_entry(Request(), Intent(), 100.0, 5)

    accepted = live.ensure_stop_loss(SYMBOL, 49000.0)
    protected = live.has_protection(SYMBOL)

    if not accepted:
        return False, "這個情境裡掛單請求應該被接受"

    if protected:
        return False, "停損已經被交易所撤掉了,卻回報有保護"

    return True, "掛單回 True 但 has_protection() 說沒有 —— 兩件事分得開"


def scenario_partial_fill(writer):
    exchange = SimulatedExchange(failures={Failure.PARTIAL_FILL})
    live = broker(exchange)

    fill = live.submit_entry(Request(quantity=0.01), Intent(), 100.0, 5)

    if not fill.is_filled:
        return False, "部分成交也是成交"

    if not fill.is_partial():
        return False, (
            f"沒有認出部分成交(送 {fill.requested_quantity} "
            f"成交 {fill.filled_quantity})"
        )

    return True, (
        f"送出 {fill.requested_quantity} 成交 {fill.filled_quantity},"
        f"認出是部分成交"
    )


def scenario_submit_disconnects(writer):
    """
    送單之後連線斷掉。交易所**可能已經收到了**。

    LiveBroker 必須讓例外往上跑 —— Execution Engine 會標 UNKNOWN
    交給對帳。在這裡吞掉它並回 ok=False,等於宣稱「沒有送出去」。
    """
    exchange = SimulatedExchange(failures={Failure.SUBMIT_RAISES})
    live = broker(exchange)

    try:
        live.submit_entry(Request(), Intent(), 100.0, 5)
        return False, "連線中斷被吞掉了,呼叫端會以為沒送出去"
    except Exception:
        pass

    if exchange.position_of(SYMBOL) is None:
        return False, "情境設定錯了:這裡交易所應該已經有部位"

    return True, "例外往上拋,而交易所那邊確實已經有部位了"


def scenario_reconcile_after_disconnect(writer):
    """
    接續上一個情境:對帳要找得到那張單。

    這條路走的是「列舉未結與歷史再比對 clientOrderId」,而不是拿
    client id 去查交易所的 orderId 欄位。
    """
    exchange = SimulatedExchange(failures={Failure.SUBMIT_RAISES})
    live = broker(exchange)

    try:
        live.submit_entry(Request(), Intent(), 100.0, 5)
    except Exception:
        pass

    found = live.fetch_order("sim-cid-1", SYMBOL)

    if found is None:
        return False, "對帳查不到那張單,會被標成 REJECTED"

    if found["state"] != "filled":
        return False, f"狀態判成 {found['state']},應該是 filled"

    return True, "對帳靠 clientOrderId 在歷史裡找到了它,判定 filled"


def scenario_query_failure_is_not_absence(writer):
    exchange = SimulatedExchange(failures={Failure.OPEN_ORDERS_FAIL})
    live = broker(exchange)

    try:
        live.fetch_order("sim-cid-1", SYMBOL)
        return False, "查詢失敗卻回答了 —— 那會被當成「交易所沒有這張單」"
    except Exception:
        return True, "查詢失敗拋例外,對帳會記成「狀態不明,不可重送」"


def scenario_hedge_mode(writer):
    """
    雙向持倉的出場單一定要帶 positionSide,不帶會被拒;
    帶錯會變成反向開倉。

    判斷依據不能是部位裡的 hedged —— ccxt 把它寫死成 None,
    而模擬器照做,所以讀它的程式碼在這裡一定會壞。
    """
    exchange = SimulatedExchange(hedged=True)
    live = broker(exchange)
    live.submit_entry(Request(), Intent(), 100.0, 5)
    live.ensure_stop_loss(SYMBOL, 49000.0)

    stops = [
        kwargs for name, kwargs in exchange.calls
        if name == "create_order" and kwargs.get("reduce_only")
    ]

    if not stops:
        return False, "沒有送出停損單"

    params = stops[-1].get("params") or {}
    if params.get("positionSide") != "LONG":
        return False, f"雙向持倉的停損沒帶 positionSide:{params}"

    return True, "雙向持倉的出場單帶了 positionSide=LONG"


def scenario_one_way_mode(writer):
    exchange = SimulatedExchange(hedged=False)
    live = broker(exchange)
    live.submit_entry(Request(), Intent(), 100.0, 5)
    live.ensure_stop_loss(SYMBOL, 49000.0)

    stops = [
        kwargs for name, kwargs in exchange.calls
        if name == "create_order" and kwargs.get("reduce_only")
    ]
    params = stops[-1].get("params") or {}

    if "positionSide" in params:
        return False, "單向持倉卻帶了 positionSide,交易所會拒絕"

    return True, "單向持倉的出場單不帶 positionSide"


def scenario_unknown_position_mode(writer):
    exchange = SimulatedExchange(
        hedged=True, failures={Failure.POSITION_MODE_FAILS},
    )
    live = broker(exchange)
    live.submit_entry(Request(), Intent(), 100.0, 5)
    live.ensure_stop_loss(SYMBOL, 49000.0)

    stops = [
        kwargs for name, kwargs in exchange.calls
        if name == "create_order" and kwargs.get("reduce_only")
    ]
    params = stops[-1].get("params") or {}

    if "positionSide" in params:
        return False, "查不到持倉模式卻還是帶了 positionSide"

    return True, "查不到持倉模式時不帶 —— 被拒絕比開反向單好"


def scenario_cannot_close(writer):
    """
    最壞的情況:救不回來又平不掉。它必須喊出來,不可以安靜失敗。
    """
    exchange = SimulatedExchange(failures={
        Failure.STOP_REJECTED, Failure.CLOSE_DOES_NOT_FILL,
    })
    live = broker(exchange)
    live.submit_entry(Request(), Intent(), 100.0, 5)

    outcome = emergency.protect(
        live, SYMBOL, 49000.0, retries=1,
        close_position=None, pause_file=None,
        disable_orders=False, notifier=lambda *a, **k: None,
    )

    if outcome.status != "STUCK":
        return False, f"平不掉卻回報 {outcome.status}"

    if not outcome.reason:
        return False, "STUCK 但沒有說原因"

    return True, f"回報 STUCK 並說明原因:{outcome.reason}"


SCENARIOS = (
    ("正常路徑", scenario_happy_path),
    ("停損被拒 → 緊急保護", scenario_stop_rejected),
    ("停損掛上又被撤掉", scenario_stop_vanishes),
    ("部分成交", scenario_partial_fill),
    ("送單後連線中斷", scenario_submit_disconnects),
    ("中斷之後對帳找得到", scenario_reconcile_after_disconnect),
    ("查詢失敗不等於不存在", scenario_query_failure_is_not_absence),
    ("雙向持倉", scenario_hedge_mode),
    ("單向持倉", scenario_one_way_mode),
    ("持倉模式查不到", scenario_unknown_position_mode),
    ("救不回來又平不掉", scenario_cannot_close),
)


def run(writer=print):
    writer(RULE)
    writer("AGMCIS — SIMULATED LIVE:實單路徑整條跑一次(不動真錢)")
    writer(RULE)
    writer("")

    passed = 0
    for title, scenario in SCENARIOS:
        try:
            ok, detail = scenario(writer)
        except Exception as exc:
            ok, detail = False, f"情境本身炸了:{type(exc).__name__}: {exc}"

        writer(f"  {'✓' if ok else '⛔'} {title}")
        writer(f"       {detail}")
        passed += 1 if ok else 0

    writer("")
    writer(RULE)

    if passed == len(SCENARIOS):
        writer(f"{passed}/{len(SCENARIOS)} 個情境都往安全的方向倒。")
        writer("")
        writer("這**不代表可以下實單**。模擬器永遠比真實交易所仁慈:")
        writer("它不會限流、不會有時鐘偏移、不會在半夜改 API。")
        writer("能不能下實單由 LIVE SAFETY GATE 決定,而它需要人的簽章。")
        return True, f"{passed}/{len(SCENARIOS)}"

    writer(f"⛔ {len(SCENARIOS) - passed} 個情境沒通過({passed}/{len(SCENARIOS)})。")
    return False, f"{passed}/{len(SCENARIOS)}"


def main():
    ok, _ = run()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
