"""
BingX WebSocket 行情(Master Prompt 第四十九節)。

容器連不到 BingX,所以這裡用假的 exchange 驅動 —— 而那其實是
比較好的測試方式:真實連線測不出「斷線之後會怎樣」,而那正是
這一層唯一真正危險的地方。

**最重要的一條:過期的價格等於沒有價格。**
斷線時繼續回傳最後一次收到的價格,是這一層最危險的失敗模式 ——
那個價格看起來完全正常,而系統會用它算停損、算強平、算損益。
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.exchange.bingx import stream as stream_module


class FakeExchange:
    """
    可以指定「推幾次之後開始斷線」的假交易所。
    """

    def __init__(self, tickers=None, fail_after=None, error=None):
        self.tickers = tickers or {
            "BTC/USDT": {"last": 65000.0, "bid": 64999.0, "ask": 65001.0},
        }
        self.fail_after = fail_after
        self.error = error or ConnectionError("斷線")
        self.calls = 0
        self.closed = False

    async def watch_tickers(self, symbols):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise self.error
        return self.tickers

    async def close(self):
        self.closed = True


def stream(exchange, **kwargs):
    kwargs.setdefault("max_reconnects", 2)
    kwargs.setdefault("backoff_seconds", 0.001)
    return stream_module.MarketStream(
        ["BTC/USDT"], exchange_factory=lambda: exchange, **kwargs
    )


def run_once(target, timeout=2.0):
    """啟動、等它收到東西、停掉。"""
    target.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if target.status.updates > 0 or target.status.gave_up:
            break
        time.sleep(0.01)
    target.stop(timeout=timeout)
    return target


class TestStaleQuotesAreNotQuotes(unittest.TestCase):
    """這一組是這個模組存在的理由。"""

    def test_a_fresh_quote_is_returned(self):
        target = stream_module.MarketStream(["BTC/USDT"])
        target._quotes["BTC/USDT"] = stream_module.Quote(
            symbol="BTC/USDT", price=65000.0, received_at=time.time(),
        )

        self.assertEqual(target.get_price("BTC/USDT"), 65000.0)

    def test_a_stale_quote_is_not(self):
        """
        斷線時繼續回傳最後一次收到的價格,是這一層最危險的失敗模式。
        """
        target = stream_module.MarketStream(["BTC/USDT"], max_age_seconds=5.0)
        target._quotes["BTC/USDT"] = stream_module.Quote(
            symbol="BTC/USDT", price=65000.0, received_at=time.time() - 60,
        )

        self.assertIsNone(target.get_price("BTC/USDT"))

    def test_an_unknown_symbol_gives_none(self):
        self.assertIsNone(
            stream_module.MarketStream(["BTC/USDT"]).get_price("ETH/USDT")
        )

    def test_the_snapshot_shows_stale_quotes_with_their_age(self):
        """診斷時需要看到「它多舊」,不是看到「它不存在」。"""
        target = stream_module.MarketStream(["BTC/USDT"], max_age_seconds=5.0)
        target._quotes["BTC/USDT"] = stream_module.Quote(
            symbol="BTC/USDT", price=65000.0, received_at=time.time() - 60,
        )

        snapshot = target.snapshot()
        self.assertFalse(snapshot["BTC/USDT"]["fresh"])
        self.assertGreater(snapshot["BTC/USDT"]["age_seconds"], 50)


class TestTheLoop(unittest.TestCase):

    def test_it_records_prices(self):
        exchange = FakeExchange()
        target = run_once(stream(exchange))

        self.assertGreater(target.status.updates, 0)
        self.assertEqual(target.get_price("BTC/USDT"), 65000.0)

    def test_a_ticker_without_a_price_is_skipped(self):
        exchange = FakeExchange(tickers={"BTC/USDT": {"bid": 1.0}})
        target = stream(exchange)
        target._record("BTC/USDT", {"bid": 1.0})

        self.assertIsNone(target.get_price("BTC/USDT"))

    def test_it_closes_the_exchange_on_stop(self):
        exchange = FakeExchange()
        run_once(stream(exchange))

        deadline = time.time() + 2.0
        while time.time() < deadline and not exchange.closed:
            time.sleep(0.01)

        self.assertTrue(exchange.closed)


class TestReconnectIsBounded(unittest.TestCase):
    """
    第四十八節:禁止無限重試。一個安靜地永遠重連的背景執行緒,
    看起來跟正常運作一模一樣。
    """

    def test_it_gives_up_after_the_limit(self):
        exchange = FakeExchange(fail_after=0)

        with patch.object(stream_module, "logger"):
            target = run_once(stream(exchange, max_reconnects=2), timeout=3.0)

        self.assertTrue(target.status.gave_up)
        self.assertLessEqual(target.status.reconnects, 4)

    def test_giving_up_is_visible_in_health(self):
        from api import health

        target = stream_module.MarketStream(["BTC/USDT"])
        target.status.running = True
        target.status.gave_up = True

        with patch.object(stream_module, "_stream", target):
            self.assertEqual(health._websocket(), "error")

    def test_a_stream_that_was_never_started_is_disabled_not_broken(self):
        from api import health

        with patch.object(stream_module, "_stream", None):
            self.assertEqual(health._websocket(), "disabled")

    def test_a_connected_stream_is_ok(self):
        from api import health

        target = stream_module.MarketStream(["BTC/USDT"])
        target.status.running = True
        target.status.connected = True

        with patch.object(stream_module, "_stream", target):
            self.assertEqual(health._websocket(), "ok")


class TestPriceLookupPrefersTheStream(unittest.TestCase):

    def setUp(self):
        stream_module.set_stream(None)
        self.addCleanup(stream_module.set_stream, None)

    def test_a_fresh_stream_price_wins(self):
        from agmcis.data import market_data

        target = stream_module.MarketStream(["BTC/USDT"])
        target._quotes["BTC/USDT"] = stream_module.Quote(
            symbol="BTC/USDT", price=65000.0, received_at=time.time(),
        )
        stream_module.set_stream(target)

        with patch.object(market_data, "get_ticker") as rest:
            price = market_data.get_price("BTC/USDT")

        self.assertEqual(price, 65000.0)
        rest.assert_not_called()

    def test_a_stale_stream_falls_back_to_rest(self):
        from agmcis.data import market_data

        target = stream_module.MarketStream(["BTC/USDT"], max_age_seconds=1.0)
        target._quotes["BTC/USDT"] = stream_module.Quote(
            symbol="BTC/USDT", price=65000.0, received_at=time.time() - 60,
        )
        stream_module.set_stream(target)

        with patch.object(market_data, "get_ticker",
                          return_value={"price": 64000.0}):
            self.assertEqual(market_data.get_price("BTC/USDT"), 64000.0)

    def test_no_stream_falls_back_to_rest(self):
        from agmcis.data import market_data

        with patch.object(market_data, "get_ticker",
                          return_value={"price": 64000.0}):
            self.assertEqual(market_data.get_price("BTC/USDT"), 64000.0)

    def test_a_broken_stream_does_not_break_price_lookup(self):
        """串流那一層出問題不該讓取價失敗 —— REST 還在。"""
        from agmcis.data import market_data

        class Broken:
            def get_price(self, symbol):
                raise RuntimeError("壞了")

        stream_module.set_stream(Broken())

        with patch.object(market_data, "get_ticker",
                          return_value={"price": 64000.0}), \
             patch.object(market_data, "logger"):
            self.assertEqual(market_data.get_price("BTC/USDT"), 64000.0)


class TestStartupIsOptOut(unittest.TestCase):

    def setUp(self):
        stream_module.set_stream(None)
        self.addCleanup(stream_module.set_stream, None)

    def test_it_is_off_by_default(self):
        """
        它是一條新的、還沒在這個部署上跑過的路徑,而取價是停損判斷
        的依據。預設關閉。
        """
        from agmcis.config import settings

        self.assertFalse(settings.WEBSOCKET_ENABLED)

    def test_starting_it_while_disabled_does_nothing(self):
        from agmcis.config import settings

        with patch.object(settings, "WEBSOCKET_ENABLED", False):
            self.assertIsNone(stream_module.start_stream(["BTC/USDT"]))

    def test_starting_twice_does_not_open_a_second_connection(self):
        """第二條連線會讓交易所的連線數上限提早用完。"""
        from agmcis.config import settings

        target = stream_module.MarketStream(["BTC/USDT"])
        target.status.running = True
        stream_module.set_stream(target)

        with patch.object(settings, "WEBSOCKET_ENABLED", True):
            self.assertIs(stream_module.start_stream(["BTC/USDT"]), target)

    def test_the_public_stream_carries_no_credentials(self):
        """
        一個能收到帳戶事件的連線,離一個能送出訂單的連線只差幾行程式碼。
        """
        import inspect

        source = inspect.getsource(stream_module._default_exchange)
        self.assertNotIn("apiKey", source)
        self.assertNotIn("secret", source)


if __name__ == "__main__":
    unittest.main()
