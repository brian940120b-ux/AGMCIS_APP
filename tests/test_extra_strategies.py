"""
第三十八節要求的其餘五個策略。

每一個都在找**不同種類的機會**,所以在不同市況出手。這組測試最在意的
是「它們在不該出手的時候真的不出手」—— 一個什麼市況都出手的策略,
與其說是策略,不如說是雜訊產生器。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.analysis.indicators import Indicators
from agmcis.analysis.regime import MarketRegime, Regime, Volatility
from agmcis.core.enums import Direction
from agmcis.strategy.extra import (
    MacdCross,
    MarketStructure,
    RsiReversion,
    VolatilityBreakout,
    VwapReversion,
)


def indicators(**overrides):
    base = dict(
        symbol="BTC/USDT", timeframe="1h", price=100.0,
        ema20=101.0, ema50=100.0, ema60=99.0, rsi=50.0,
        macd=1.0, macd_signal=0.5, macd_hist=0.5, adx=15.0, atr=2.0,
        bb_upper=104.0, bb_lower=96.0, volume=1000.0, volume_ma20=900.0,
    )
    base.update(overrides)
    result = Indicators(**base)
    if result.bb_upper and result.bb_lower and result.price:
        result.bb_width_pct = (
            (result.bb_upper - result.bb_lower) / result.price * 100
        )
    return result


def regime(kind=Regime.RANGE, volatility=Volatility.NORMAL):
    return MarketRegime(regime=kind, volatility=volatility)


def candles(n=40, rising=True, volume=100.0):
    rows = []
    base = 100.0
    for i in range(n):
        step = i if rising else -i
        wave = 2.0 if i % 4 in (1, 2) else 0.0
        close = base + step + wave
        rows.append({
            "time": i, "open": close, "high": close + 1.0,
            "low": close - 2.0, "close": close, "volume": volume,
        })
    return rows


class TestRsiReversion(unittest.TestCase):
    """
    「RSI 30 以下買」在趨勢市是災難:強勢下跌可以讓 RSI 在 20 附近
    待上好幾天,每一根都是「超賣」,而每一次進場都是接刀。
    """

    def test_it_refuses_to_trade_in_a_trend(self):
        verdict = RsiReversion().evaluate(
            indicators(rsi=20.0), regime(Regime.STRONG_BEAR),
        )

        self.assertIs(verdict.direction, Direction.WAIT)
        self.assertIn("不在", verdict.reasons[0])

    def test_oversold_in_a_range_is_a_long(self):
        verdict = RsiReversion().evaluate(
            indicators(rsi=25.0, price=95.0), regime(Regime.RANGE),
        )

        self.assertIs(verdict.direction, Direction.LONG)

    def test_overbought_in_a_range_is_a_short(self):
        verdict = RsiReversion().evaluate(
            indicators(rsi=75.0, price=105.0), regime(Regime.RANGE),
        )

        self.assertIs(verdict.direction, Direction.SHORT)

    def test_a_neutral_rsi_waits(self):
        verdict = RsiReversion().evaluate(indicators(rsi=50.0), regime())

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_bollinger_confirmation_raises_confidence(self):
        """兩個獨立的超賣訊號比一個可靠。"""
        confirmed = RsiReversion().evaluate(
            indicators(rsi=25.0, price=95.0), regime(),
        )
        unconfirmed = RsiReversion().evaluate(
            indicators(rsi=25.0, price=99.0), regime(),
        )

        self.assertGreater(confirmed.confidence, unconfirmed.confidence)

    def test_a_high_adx_lowers_confidence(self):
        """反轉策略最怕趨勢還在延伸。"""
        calm = RsiReversion().evaluate(
            indicators(rsi=25.0, price=95.0, adx=15.0), regime(),
        )
        trending = RsiReversion().evaluate(
            indicators(rsi=25.0, price=95.0, adx=35.0), regime(),
        )

        self.assertLess(trending.confidence, calm.confidence)

    def test_the_stop_is_on_the_right_side(self):
        verdict = RsiReversion().evaluate(
            indicators(rsi=25.0, price=95.0), regime(),
        )

        self.assertLess(verdict.stop_loss, 95.0)
        self.assertGreater(verdict.take_profit, 95.0)


class TestMacdCross(unittest.TestCase):

    def test_a_fresh_positive_histogram_is_a_long(self):
        verdict = MacdCross().evaluate(
            indicators(macd_hist=0.1, ema20=101.0, ema50=100.0),
            regime(Regime.BULL),
        )

        self.assertIs(verdict.direction, Direction.LONG)

    def test_a_stale_histogram_waits(self):
        """
        只看「MACD 在訊號線上方」會在整段趨勢裡一直發出訊號。
        柱狀體已經很大代表轉折走了一半。
        """
        verdict = MacdCross().evaluate(
            indicators(macd_hist=5.0, ema20=101.0, ema50=100.0),
            regime(Regime.BULL),
        )

        self.assertIs(verdict.direction, Direction.WAIT)
        self.assertIn("走過一段", verdict.reasons[0])

    def test_a_histogram_against_the_ema_waits(self):
        verdict = MacdCross().evaluate(
            indicators(macd_hist=0.1, ema20=99.0, ema50=100.0),
            regime(Regime.BEAR),
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_it_does_not_trade_in_a_range(self):
        verdict = MacdCross().evaluate(
            indicators(macd_hist=0.1), regime(Regime.RANGE),
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_a_zero_histogram_waits(self):
        verdict = MacdCross().evaluate(
            indicators(macd_hist=0.0), regime(Regime.BULL),
        )

        self.assertIs(verdict.direction, Direction.WAIT)


class TestVwapReversion(unittest.TestCase):

    def test_no_candles_means_wait(self):
        """
        用指標湊一個近似的 VWAP 會產生一個名字叫 VWAP 但其實不是的訊號。
        """
        verdict = VwapReversion().evaluate(indicators(), regime(), candles=None)

        self.assertIs(verdict.direction, Direction.WAIT)
        self.assertIn("VWAP", verdict.reasons[0])

    def test_price_above_vwap_is_a_short(self):
        from agmcis.analysis.structure import vwap

        rows = candles(40)
        price = vwap(rows) * 1.10       # 明確在 VWAP 上方

        verdict = VwapReversion().evaluate(
            indicators(price=price), regime(), candles=rows,
        )

        self.assertIs(verdict.direction, Direction.SHORT)

    def test_price_below_vwap_is_a_long(self):
        from agmcis.analysis.structure import vwap

        rows = candles(40)
        price = vwap(rows) * 0.90

        verdict = VwapReversion().evaluate(
            indicators(price=price), regime(), candles=rows,
        )

        self.assertIs(verdict.direction, Direction.LONG)

    def test_a_small_deviation_waits(self):
        rows = candles(40)
        from agmcis.analysis.structure import vwap

        verdict = VwapReversion().evaluate(
            indicators(price=vwap(rows)), regime(), candles=rows,
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_no_volume_means_wait(self):
        rows = candles(40, volume=0.0)
        verdict = VwapReversion().evaluate(
            indicators(price=500.0), regime(), candles=rows,
        )

        self.assertIs(verdict.direction, Direction.WAIT)
        self.assertIn("算不出 VWAP", verdict.reasons[0])

    def test_it_does_not_trade_in_a_trend(self):
        verdict = VwapReversion().evaluate(
            indicators(price=500.0), regime(Regime.STRONG_BULL),
            candles=candles(40),
        )

        self.assertIs(verdict.direction, Direction.WAIT)


class TestVolatilityBreakout(unittest.TestCase):

    def _squeezed(self, price):
        return indicators(price=price, bb_upper=100.5, bb_lower=99.5)

    def test_a_wide_channel_waits(self):
        verdict = VolatilityBreakout().evaluate(
            indicators(price=110.0, bb_upper=104.0, bb_lower=96.0), regime(),
        )

        self.assertIs(verdict.direction, Direction.WAIT)
        self.assertIn("未達壓縮標準", verdict.reasons[0])

    def test_a_squeeze_with_an_upside_break_is_a_long(self):
        verdict = VolatilityBreakout().evaluate(self._squeezed(101.0), regime())

        self.assertIs(verdict.direction, Direction.LONG)

    def test_a_squeeze_with_a_downside_break_is_a_short(self):
        verdict = VolatilityBreakout().evaluate(self._squeezed(99.0), regime())

        self.assertIs(verdict.direction, Direction.SHORT)

    def test_a_squeeze_without_a_break_waits(self):
        verdict = VolatilityBreakout().evaluate(self._squeezed(100.0), regime())

        self.assertIs(verdict.direction, Direction.WAIT)
        self.assertIn("等突破方向", verdict.reasons[0])

    def test_low_volume_lowers_confidence(self):
        """沒有量的突破常常是假的。"""
        strong = self._squeezed(101.0)
        strong.volume, strong.volume_ma20 = 2000.0, 1000.0

        weak = self._squeezed(101.0)
        weak.volume, weak.volume_ma20 = 500.0, 1000.0

        self.assertGreater(
            VolatilityBreakout().evaluate(strong, regime()).confidence,
            VolatilityBreakout().evaluate(weak, regime()).confidence,
        )

    def test_it_trades_in_any_regime(self):
        """壓縮本身就是市況的一部分,突破的方向決定接下來是什麼市況。"""
        for kind in (Regime.RANGE, Regime.BULL, Regime.STRONG_BEAR):
            verdict = VolatilityBreakout().evaluate(
                self._squeezed(101.0), regime(kind),
            )
            self.assertIs(verdict.direction, Direction.LONG, kind)


class TestMarketStructure(unittest.TestCase):

    def test_no_candles_means_wait(self):
        verdict = MarketStructure().evaluate(
            indicators(), regime(Regime.BULL), candles=None,
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_an_uptrend_structure_is_a_long(self):
        verdict = MarketStructure().evaluate(
            indicators(price=140.0), regime(Regime.BULL), candles=candles(40),
        )

        self.assertIs(verdict.direction, Direction.LONG)

    def test_the_stop_goes_outside_the_structure_not_at_a_fixed_atr(self):
        """
        這是這個策略的重點:被打到代表結構破了,不是「跌了 2 個 ATR」。
        """
        verdict = MarketStructure().evaluate(
            indicators(price=140.0, atr=2.0), regime(Regime.BULL),
            candles=candles(40),
        )

        self.assertIsNotNone(verdict.stop_loss)
        self.assertTrue(
            any("結構支撐" in r for r in verdict.reasons), verdict.reasons,
        )
        # ATR 停損會是 140 - 4 = 136;結構停損應該不同
        self.assertNotAlmostEqual(verdict.stop_loss, 136.0, places=2)

    def test_it_does_not_trade_in_a_range(self):
        verdict = MarketStructure().evaluate(
            indicators(), regime(Regime.RANGE), candles=candles(40),
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_too_few_candles_waits(self):
        verdict = MarketStructure().evaluate(
            indicators(), regime(Regime.BULL), candles=candles(8),
        )

        self.assertIs(verdict.direction, Direction.WAIT)


class TestTheyAreAllRegistered(unittest.TestCase):

    def test_the_registry_has_all_nine_strategies(self):
        from agmcis.strategy.registry import StrategyRegistry

        names = StrategyRegistry().names
        for name in ("rsi_reversion", "macd_cross", "vwap_reversion",
                     "volatility_breakout", "market_structure"):
            self.assertIn(name, names)

    def test_they_default_to_paper_not_live(self):
        """
        沒有經過 OOS 與 Walk Forward 驗證的策略可以在模擬盤跑,
        不能碰真錢(第七十三節)。
        """
        import tempfile

        from agmcis.strategy.health import StatusStore, is_tradeable

        folder = tempfile.mkdtemp(prefix="agmcis-test-extra-")
        store = StatusStore(
            path=os.path.join(folder, "s.json"),
            audit_path=os.path.join(folder, "a.log"),
        )

        for name in ("rsi_reversion", "market_structure"):
            self.assertTrue(is_tradeable(name, mode="paper", store=store))
            self.assertFalse(is_tradeable(name, mode="live", store=store))

    def test_a_strategy_that_needs_candles_says_so(self):
        self.assertTrue(VwapReversion.needs_candles)
        self.assertTrue(MarketStructure.needs_candles)
        self.assertFalse(RsiReversion.needs_candles)


if __name__ == "__main__":
    unittest.main()


class TestFlowAgents(unittest.TestCase):
    """
    資金流 / 情緒 / 總體事件 Agent。它們共同的弱點是資料最容易缺,
    而缺的時候最容易被誤讀成中性 —— 0 在這些欄位裡的意思分別是
    「多空平衡」「市場中性」「沒有事件」,那是三個很有信心的判斷。
    """

    def _context(self, **overrides):
        from agmcis.agents.base import AgentContext

        base = dict(
            symbol="BTC/USDT", indicators=indicators(),
            regime=regime(), price=100.0,
        )
        base.update(overrides)
        return AgentContext(**base)

    # ---- OrderBookAgent ----

    def _book(self, imbalance=0.0, spread_pct=0.02):
        return {"imbalance": imbalance, "spread_pct": spread_pct,
                "best_bid": 99.9, "best_ask": 100.1}

    def test_no_order_book_means_abstain_not_neutral(self):
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import OrderBookAgent

        opinion = OrderBookAgent().analyse(self._context(order_book=None))

        self.assertIs(opinion.vote, Vote.ABSTAIN)

    def test_a_strong_bid_imbalance_is_a_long(self):
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import OrderBookAgent

        opinion = OrderBookAgent().analyse(
            self._context(order_book=self._book(imbalance=0.5)),
        )

        self.assertIs(opinion.vote, Vote.LONG)

    def test_a_mild_imbalance_abstains(self):
        """一個 55/45 的訂單簿沒有任何資訊。"""
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import OrderBookAgent

        opinion = OrderBookAgent().analyse(
            self._context(order_book=self._book(imbalance=0.1)),
        )

        self.assertIs(opinion.vote, Vote.ABSTAIN)

    def test_a_wide_spread_beats_any_imbalance(self):
        """
        再漂亮的失衡,在一個寬價差的市場裡也不值得進場 ——
        進出各吃一次,優勢會被吃光。
        """
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import OrderBookAgent

        opinion = OrderBookAgent().analyse(
            self._context(order_book=self._book(imbalance=0.9, spread_pct=0.5)),
        )

        self.assertIs(opinion.vote, Vote.WAIT)

    def test_its_confidence_is_capped_because_orders_can_be_pulled(self):
        from agmcis.agents.flow import OrderBookAgent

        opinion = OrderBookAgent().analyse(
            self._context(order_book=self._book(imbalance=0.99)),
        )

        self.assertLessEqual(opinion.confidence, 70.0)

    def test_its_weight_is_low(self):
        from agmcis.agents.flow import OrderBookAgent

        self.assertLess(OrderBookAgent.weight, 1.0)

    # ---- SentimentAgent ----

    def test_no_sentiment_means_abstain(self):
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import SentimentAgent

        opinion = SentimentAgent().analyse(self._context(sentiment_score=None))

        self.assertIs(opinion.vote, Vote.ABSTAIN)

    def test_extreme_greed_says_wait_not_short(self):
        """
        極端情緒是反指標,但「太貪婪了所以要跌」是最貴的一種判斷 ——
        所以它只說「不要在這裡追多」,不說「做空」。
        """
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import SentimentAgent

        opinion = SentimentAgent().analyse(self._context(sentiment_score=85.0))

        self.assertIs(opinion.vote, Vote.WAIT)

    def test_extreme_fear_also_says_wait(self):
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import SentimentAgent

        opinion = SentimentAgent().analyse(self._context(sentiment_score=-85.0))

        self.assertIs(opinion.vote, Vote.WAIT)

    def test_neutral_sentiment_abstains(self):
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import SentimentAgent

        opinion = SentimentAgent().analyse(self._context(sentiment_score=10.0))

        self.assertIs(opinion.vote, Vote.ABSTAIN)

    # ---- MacroAgent ----

    def test_macro_only_ever_says_wait(self):
        """
        FOMC 前十分鐘的問題不是方向猜錯,是波動大到停損沒有意義 ——
        那是一個「不要進場」的理由,不是「往哪邊」的理由。
        """
        import inspect

        from agmcis.agents.flow import MacroAgent

        source = inspect.getsource(MacroAgent)
        self.assertNotIn("Vote.LONG", source)
        self.assertNotIn("Vote.SHORT", source)

    def test_a_blackout_makes_macro_vote_wait_loudly(self):
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import MacroAgent
        from agmcis.risk.news_risk import NewsRisk

        risk = NewsRisk(
            blocks_entry=True, level="HIGH", reasons=["FOMC 時間窗內"],
        )
        opinion = MacroAgent().analyse(self._context(news_risk=risk))

        self.assertIs(opinion.vote, Vote.WAIT)
        self.assertGreater(opinion.confidence, 90)

    def test_a_quiet_calendar_abstains(self):
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import MacroAgent
        from agmcis.risk.news_risk import NewsRisk

        opinion = MacroAgent().analyse(self._context(news_risk=NewsRisk()))

        self.assertIs(opinion.vote, Vote.ABSTAIN)

    def test_a_stale_calendar_abstains_rather_than_blocking(self):
        """
        日曆過期在模擬盤只是警告。實單那一側由 LIVE GATE 擋。
        """
        from agmcis.agents.base import Vote
        from agmcis.agents.flow import MacroAgent
        from agmcis.risk.news_risk import NewsRisk

        opinion = MacroAgent().analyse(
            self._context(news_risk=NewsRisk(degraded=True)),
        )

        self.assertIs(opinion.vote, Vote.ABSTAIN)


class TestEnrichmentCanBeTurnedOff(unittest.TestCase):
    """
    每個標的多三次外部呼叫。rate limit 吃緊時要能關掉,
    而關掉之後對應的 Agent 必須**棄權**,不是拿到中性值。
    """

    def test_turning_it_off_gives_none_not_zero(self):
        from unittest.mock import patch

        from agmcis.config import settings
        from agmcis.signal import agent_pipeline

        with patch.object(settings, "AGENT_ENRICHMENT_ENABLED", False):
            payload = agent_pipeline.enrich("BTC/USDT")

        self.assertEqual(set(payload.values()), {None})

    def test_a_failing_source_does_not_break_the_context(self):
        from unittest.mock import patch

        from agmcis.signal import agent_pipeline

        with patch("agmcis.data.market_data.get_order_book",
                   side_effect=RuntimeError("掛了")), \
             patch.object(agent_pipeline, "logger"):
            payload = agent_pipeline.enrich("BTC/USDT")

        self.assertIsNone(payload["order_book"])


class TestOrderFlow(unittest.TestCase):
    """
    真正的 order flow 需要逐筆成交。這裡做的是訂單簿失衡 + 量能確認,
    是一個粗糙的代理 —— 所以它的門檻高、信心上限低,
    而且要求另一個獨立訊號確認。
    """

    def _book(self, imbalance=0.5, spread_pct=0.02):
        return {"imbalance": imbalance, "spread_pct": spread_pct}

    def _indicators(self, volume_ratio=1.5):
        result = indicators()
        result.volume = 1000.0 * volume_ratio
        result.volume_ma20 = 1000.0
        return result

    def test_no_order_book_means_wait(self):
        from agmcis.strategy.extra import OrderFlow

        verdict = OrderFlow().evaluate(indicators(), regime(), order_book=None)

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_a_strong_imbalance_with_volume_is_a_long(self):
        from agmcis.strategy.extra import OrderFlow

        verdict = OrderFlow().evaluate(
            self._indicators(), regime(), order_book=self._book(0.6),
        )

        self.assertIs(verdict.direction, Direction.LONG)

    def test_an_imbalance_without_volume_is_treated_as_a_spoof(self):
        """
        沒有成交量支撐的訂單簿失衡,更可能是掛單牆而不是真的買盤 ——
        而掛單牆會在價格接近時消失。
        """
        from agmcis.strategy.extra import OrderFlow

        verdict = OrderFlow().evaluate(
            self._indicators(volume_ratio=0.6), regime(),
            order_book=self._book(0.9),
        )

        self.assertIs(verdict.direction, Direction.WAIT)
        self.assertIn("掛單牆", verdict.reasons[0])

    def test_a_wide_spread_blocks_it(self):
        from agmcis.strategy.extra import OrderFlow

        verdict = OrderFlow().evaluate(
            self._indicators(), regime(), order_book=self._book(0.9, spread_pct=0.5),
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_a_moderate_imbalance_waits(self):
        from agmcis.strategy.extra import OrderFlow

        verdict = OrderFlow().evaluate(
            self._indicators(), regime(), order_book=self._book(0.2),
        )

        self.assertIs(verdict.direction, Direction.WAIT)

    def test_its_confidence_is_capped(self):
        from agmcis.strategy.extra import OrderFlow

        verdict = OrderFlow().evaluate(
            self._indicators(), regime(), order_book=self._book(0.99),
        )

        self.assertLessEqual(verdict.confidence, 65.0)

    def test_it_declares_that_it_needs_an_order_book(self):
        from agmcis.strategy.extra import OrderFlow

        self.assertTrue(OrderFlow.needs_order_book)

    def test_it_is_registered(self):
        from agmcis.strategy.registry import StrategyRegistry

        self.assertIn("order_flow", StrategyRegistry().names)
