"""
訂單流(第三十八節)—— 真的那一種。

## 這一節為什麼卡了那麼久

因為我在 OrderFlow 的說明裡寫過一句話:「BingX 的公開 API 也不提供
逐筆成交」。**那句話是錯的。** BingX 有公開的 Recent Trades 端點,
ccxt 也支援(`fetchTrades: True`),而且它從 `isBuyerMaker` 推出的
`side` 就是主動方 —— 那正是 volume delta 需要的東西。

一句沒有查證的話,讓一整節停在「部分做到」很久。

## 這一組驗什麼

  1. delta 算得對,而且缺資料時不會假裝算得出來。
  2. 兩條路(逐筆成交 / 訂單簿代理)的信心上限不一樣,而且不混用。
  3. 資料源分得開 —— 訂單簿是掛著的單,成交是已經發生的事。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import Direction
from agmcis.strategy.extra import OrderFlow


def flow(delta_ratio=0.5, trades=100, unusable=0):
    return {
        "symbol": "BTC/USDT", "trades": trades, "unusable_trades": unusable,
        "delta_ratio": delta_ratio, "delta": 10.0,
        "buy_volume": 30.0, "sell_volume": 20.0,
    }


def indicators(atr=100.0, price=50000.0, volume_ratio=1.5):
    fake = MagicMock()
    fake.atr = atr
    fake.price = price
    fake.close = price
    fake.volume_ratio = volume_ratio
    return fake


class TestTheAdapterComputesDelta(unittest.TestCase):
    """
    ccxt 對 BingX 的公開成交:isBuyerMaker=True 代表買方是掛單方,
    所以主動方是賣方 -> side="sell"。side 就是主動方。
    """

    def adapter(self, trades):
        from agmcis.exchange.bingx.adapter import BingXAdapter

        exchange = MagicMock()
        exchange.fetch_trades.return_value = trades
        built = BingXAdapter(exchange_factory=lambda *a, **kw: exchange)
        built._markets_loaded = True
        return built

    def test_delta_is_taker_buys_minus_taker_sells(self):
        result = self.adapter([
            {"side": "buy", "amount": 3.0, "timestamp": 1},
            {"side": "sell", "amount": 1.0, "timestamp": 2},
        ]).get_trade_flow("BTC/USDT")

        self.assertEqual(result["buy_volume"], 3.0)
        self.assertEqual(result["sell_volume"], 1.0)
        self.assertEqual(result["delta"], 2.0)
        self.assertAlmostEqual(result["delta_ratio"], 0.5)

    def test_a_trade_with_no_side_is_not_guessed(self):
        """猜一邊會直接造出一個不存在的失衡。"""
        result = self.adapter([
            {"side": "buy", "amount": 1.0, "timestamp": 1},
            {"side": None, "amount": 99.0, "timestamp": 2},
        ]).get_trade_flow("BTC/USDT")

        self.assertEqual(result["trades"], 1)
        self.assertEqual(result["unusable_trades"], 1)
        self.assertEqual(result["delta"], 1.0)

    def test_a_trade_with_no_amount_is_not_counted_as_zero(self):
        """
        數量缺值當成 0,會讓 delta 看起來比實際上平衡(第九十四節)。
        """
        result = self.adapter([
            {"side": "buy", "amount": 1.0, "timestamp": 1},
            {"side": "sell", "amount": None, "timestamp": 2},
        ]).get_trade_flow("BTC/USDT")

        self.assertEqual(result["unusable_trades"], 1)
        self.assertEqual(result["sell_volume"], 0.0)
        self.assertEqual(result["delta_ratio"], 1.0)

    def test_no_usable_trades_returns_none_not_a_zero_delta(self):
        """
        「一筆都讀不到」不是「主動買賣剛好平衡」。
        後者是一個確定的判斷,前者不是。
        """
        result = self.adapter([
            {"side": None, "amount": None},
        ]).get_trade_flow("BTC/USDT")

        self.assertIsNone(result)

    def test_an_unavailable_endpoint_returns_none(self):
        from agmcis.exchange.bingx.adapter import BingXAdapter

        exchange = MagicMock()
        exchange.fetch_trades.return_value = []
        built = BingXAdapter(exchange_factory=lambda *a, **kw: exchange)
        built._markets_loaded = True

        self.assertIsNone(built.get_trade_flow("BTC/USDT"))


class TestTheStrategyPrefersRealTrades(unittest.TestCase):

    def setUp(self):
        self.strategy = OrderFlow()

    def test_a_strong_delta_produces_a_verdict(self):
        verdict = self.strategy.evaluate(
            indicators(), regime=None, trade_flow=flow(delta_ratio=0.6),
        )

        self.assertIs(verdict.direction, Direction.LONG)
        self.assertTrue(any("逐筆成交" in r for r in verdict.reasons))

    def test_a_negative_delta_is_short(self):
        verdict = self.strategy.evaluate(
            indicators(), regime=None, trade_flow=flow(delta_ratio=-0.6),
        )

        self.assertIs(verdict.direction, Direction.SHORT)

    def test_real_trades_beat_the_order_book_proxy(self):
        """
        兩個資料源說相反的話時,撤不掉的那個贏。
        """
        book = {"imbalance": -0.9, "spread_pct": 0.01}

        verdict = self.strategy.evaluate(
            indicators(), regime=None, order_book=book,
            trade_flow=flow(delta_ratio=0.6),
        )

        self.assertIs(verdict.direction, Direction.LONG)

    def test_a_thin_sample_falls_back_to_the_proxy(self):
        """
        十筆成交的 delta 是噪音。樣本不足是「這條路走不通」,
        不是「這個資料說不要交易」—— 所以退回代理,不是 WAIT。
        """
        book = {"imbalance": 0.8, "spread_pct": 0.01}

        verdict = self.strategy.evaluate(
            indicators(), regime=None, order_book=book,
            trade_flow=flow(delta_ratio=0.9, trades=5),
        )

        self.assertIs(verdict.direction, Direction.LONG)
        self.assertTrue(any("代理指標" in r for r in verdict.reasons))

    def test_a_weak_but_measured_delta_does_not_fall_back(self):
        """
        delta 明確但不夠強,是**這個資料源說不夠強**。

        退回代理會讓一個信心上限更高的訊號,去覆蓋一個更可靠的
        資料源說出來的「不夠」。那個方向是錯的。
        """
        book = {"imbalance": 0.9, "spread_pct": 0.01}

        verdict = self.strategy.evaluate(
            indicators(), regime=None, order_book=book,
            trade_flow=flow(delta_ratio=0.05, trades=200),
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_the_proxy_is_labelled_as_a_proxy(self):
        book = {"imbalance": 0.8, "spread_pct": 0.01}

        verdict = self.strategy.evaluate(
            indicators(), regime=None, order_book=book, trade_flow=None,
        )

        self.assertTrue(any("代理指標" in r for r in verdict.reasons))

    def test_neither_source_means_wait(self):
        verdict = self.strategy.evaluate(
            indicators(), regime=None, order_book=None, trade_flow=None,
        )

        self.assertIs(verdict.direction, Direction.WAIT)


class TestTheTwoSourcesAreNotEquivalent(unittest.TestCase):
    """
    掛單可以撤,成交撤不掉。兩者的信心上限不該一樣。
    """

    def test_real_trades_can_score_higher_than_the_proxy_ceiling(self):
        strategy = OrderFlow()

        real = strategy.evaluate(
            indicators(), regime=None, trade_flow=flow(delta_ratio=0.95),
        )
        proxy = strategy.evaluate(
            indicators(), regime=None,
            order_book={"imbalance": 0.95, "spread_pct": 0.01},
        )

        self.assertGreater(real.confidence, proxy.confidence)
        self.assertLessEqual(proxy.confidence, 65.0)
        self.assertLessEqual(real.confidence, 80.0)

    def test_the_strategy_declares_it_needs_trade_flow(self):
        """
        沒宣告的話註冊表不會把資料傳給它,而它會安靜地永遠走代理。
        """
        self.assertTrue(OrderFlow.needs_trade_flow)

    def test_only_strategies_that_ask_for_it_receive_it(self):
        """
        另外十個策略的簽名不必為了一個資料源全部改一輪。
        """
        from agmcis.strategy.registry import StrategyRegistry

        received = {}

        class Asks:
            name = "asks"
            needs_trade_flow = True

            def evaluate(self, ind, reg, candles=None, order_book=None,
                         trade_flow=None):
                received["asks"] = trade_flow
                return OrderFlow().wait("測試")

            def wait(self, *reasons):
                return OrderFlow().wait(*reasons)

        class DoesNot:
            name = "does_not"

            def evaluate(self, ind, reg, candles=None, order_book=None):
                received["does_not"] = "沒有多收到參數"
                return OrderFlow().wait("測試")

            def wait(self, *reasons):
                return OrderFlow().wait(*reasons)

        registry = StrategyRegistry(strategies=[Asks(), DoesNot()])
        registry.evaluate_all(
            indicators(), regime=None, trade_flow=flow(),
        )

        self.assertIsNotNone(received["asks"])
        self.assertEqual(received["does_not"], "沒有多收到參數")


if __name__ == "__main__":
    unittest.main()
