"""
Multi-Agent 層。

最重要的測試不是「共識算得對不對」,而是**架構鐵律有沒有被繞過**:
Agent 不得直接下單(Master Prompt 第三十二節)。
那條規則用 AST 掃描釘死,不是靠人記得。

其次是兩個反覆出現的教訓:
  * 棄權不是反對票(Phase 6:把棄權當反對,系統會永遠不交易)
  * 靜默失敗被禁止(第九十四節:壞掉的 Agent 必須看得見)
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.agents import builtin, consensus, registry
from agmcis.agents.base import AgentContext, AgentOpinion, BaseAgent, Vote
from agmcis.agents.supervisor import Supervisor
from agmcis.analysis.indicators import Indicators
from agmcis.analysis.regime import detect
from agmcis.core.enums import Direction

AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agmcis", "agents",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "agmcis.exchange", "agmcis.execution", "ccxt",
    "exchange_engine", "database_service", "db", "trade_service",
)

FORBIDDEN_CALLS = {
    "create_order", "cancel_order", "set_leverage", "set_margin_mode",
    "withdraw", "transfer", "close_position", "place_order",
}


def bull_indicators(**overrides):
    fields = dict(
        symbol="ETH/USDT", timeframe="1h", price=100.0,
        ema20=102.0, ema50=98.0, ema60=97.0, rsi=58.0,
        macd=0.5, macd_signal=0.2, macd_hist=0.3, adx=32.0, atr=1.2,
        bb_upper=106.0, bb_lower=94.0, bb_width_pct=12.0,
        volume=1500.0, volume_ma20=1000.0, data_ok=True,
    )
    fields.update(overrides)
    return Indicators(**fields)


def context_for(indicators, **overrides):
    fields = dict(
        symbol=indicators.symbol, timeframe="1h", indicators=indicators,
        regime=detect(indicators), price=indicators.price,
    )
    fields.update(overrides)
    return AgentContext(**fields)


class TestAgentsCannotTrade(unittest.TestCase):
    """
    第三十二節:AI / Agent 不得直接呼叫下單 API。

    這不是靠自律 —— 用 AST 掃描整個 agents 套件。
    """

    def _agent_modules(self):
        for name in sorted(os.listdir(AGENTS_DIR)):
            if name.endswith(".py"):
                path = os.path.join(AGENTS_DIR, name)
                with open(path, encoding="utf-8") as handle:
                    yield name, ast.parse(handle.read(), filename=path)

    def test_no_agent_module_imports_the_exchange_or_execution_layer(self):
        for name, tree in self._agent_modules():
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]

                for module in modules:
                    for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                        with self.subTest(module=name, imported=module):
                            self.assertFalse(
                                module == forbidden
                                or module.startswith(forbidden + "."),
                                f"{name} import 了 {module} —— "
                                f"Agent 層不得碰交易所或下單層",
                            )

    def test_no_agent_module_calls_an_order_function(self):
        for name, tree in self._agent_modules():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                func = node.func
                called = (
                    func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name)
                    else None
                )

                with self.subTest(module=name, call=called):
                    self.assertNotIn(called, FORBIDDEN_CALLS)

    def test_agent_context_carries_no_exchange_handle(self):
        """給不到的東西才是真的拿不到。"""
        context = AgentContext(symbol="BTC/USDT")
        forbidden = {"exchange", "adapter", "client", "session", "db", "connection"}

        self.assertEqual(forbidden & set(vars(context)), set())

    def test_agent_opinion_carries_no_size_or_leverage(self):
        """部位大小與槓桿是 Risk Engine 的職權,不是 Agent 的。"""
        opinion = AgentOpinion(agent="x")
        forbidden = {"size", "size_usdt", "leverage", "quantity", "margin"}

        self.assertEqual(forbidden & set(vars(opinion)), set())


class TestAbstainIsNotOpposition(unittest.TestCase):
    """Phase 6 的教訓:把棄權當反對票,系統會永遠不交易。"""

    def test_abstain_opposes_nobody(self):
        self.assertFalse(Vote.ABSTAIN.opposes(Vote.LONG))
        self.assertFalse(Vote.LONG.opposes(Vote.ABSTAIN))

    def test_only_two_opposite_directions_oppose(self):
        self.assertTrue(Vote.LONG.opposes(Vote.SHORT))
        self.assertFalse(Vote.LONG.opposes(Vote.LONG))
        self.assertFalse(Vote.WAIT.opposes(Vote.LONG))

    def test_abstaining_agents_do_not_dilute_the_winning_share(self):
        opinions = [
            AgentOpinion(agent="a", vote=Vote.LONG, confidence=80),
            AgentOpinion(agent="b", vote=Vote.LONG, confidence=80),
            AgentOpinion(agent="c", vote=Vote.ABSTAIN),
            AgentOpinion(agent="d", vote=Vote.ABSTAIN),
            AgentOpinion(agent="e", vote=Vote.ABSTAIN),
        ]

        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertIs(result.direction, Direction.LONG)
        self.assertIsNotNone(result.intent)

    def test_a_system_where_everyone_abstains_produces_no_trade(self):
        opinions = [AgentOpinion(agent=f"a{i}", vote=Vote.ABSTAIN) for i in range(5)]
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertIsNone(result.intent)
        self.assertIn("棄權", result.blocked_reason)


class TestConsensusRules(unittest.TestCase):

    def _long_votes(self, count, confidence=80):
        return [
            AgentOpinion(agent=f"a{i}", vote=Vote.LONG, confidence=confidence)
            for i in range(count)
        ]

    def test_a_single_directional_vote_is_not_enough(self):
        result = consensus.decide("ETH/USDT", self._long_votes(1), entry=100.0)

        self.assertIsNone(result.intent)
        self.assertIn("低於", result.blocked_reason)

    def test_a_split_vote_produces_no_trade(self):
        opinions = (
            self._long_votes(2)
            + [AgentOpinion(agent="s1", vote=Vote.SHORT, confidence=80),
               AgentOpinion(agent="s2", vote=Vote.SHORT, confidence=80)]
        )
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertIsNone(result.intent)
        self.assertIn("分歧", result.blocked_reason)

    def test_strong_wait_votes_veto_the_entry(self):
        opinions = self._long_votes(2) + [
            AgentOpinion(agent="vol", vote=Vote.WAIT, confidence=95, weight=2.0),
        ]
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertIsNone(result.intent)
        self.assertIn("觀望票", result.blocked_reason)

    def test_a_zero_confidence_wait_has_no_veto_power(self):
        """信心 0 的 WAIT 是「我沒有力道」,不該擋下高信心的共識。"""
        opinions = self._long_votes(3) + [
            AgentOpinion(agent="meh", vote=Vote.WAIT, confidence=0),
        ]
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertIsNotNone(result.intent)

    def test_the_exit_agent_never_votes_on_new_entries(self):
        """管理型 Agent 的職責是既有部位,不是要不要開新倉。"""
        opinions = self._long_votes(2) + [
            AgentOpinion(agent="exit", vote=Vote.WAIT, confidence=100, weight=5.0),
        ]
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertIsNotNone(result.intent)

    def test_the_intent_always_has_a_stop_even_when_no_agent_supplied_one(self):
        result = consensus.decide("ETH/USDT", self._long_votes(3), entry=100.0)

        self.assertIsNotNone(result.intent.stop_loss)
        self.assertLess(result.intent.stop_loss, result.intent.entry)

    def test_the_most_conservative_stop_wins(self):
        """
        調整只能讓部位更安全,不能更寬鬆 —— 這條規則從 Phase 4 沿用至今。
        做多時「較保守」= 離進場價較近 = 數字較大。
        """
        opinions = [
            AgentOpinion(agent="a", vote=Vote.LONG, confidence=80, stop_loss=90.0),
            AgentOpinion(agent="b", vote=Vote.LONG, confidence=80, stop_loss=97.0),
        ]
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertAlmostEqual(result.intent.stop_loss, 97.0, places=6)

    def test_the_most_conservative_target_wins_for_shorts(self):
        opinions = [
            AgentOpinion(agent="a", vote=Vote.SHORT, confidence=80,
                         stop_loss=103.0, take_profit=80.0),
            AgentOpinion(agent="b", vote=Vote.SHORT, confidence=80,
                         stop_loss=110.0, take_profit=95.0),
        ]
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertAlmostEqual(result.intent.take_profit, 95.0, places=6)
        self.assertAlmostEqual(result.intent.stop_loss, 103.0, places=6)

    def test_no_price_means_no_intent(self):
        result = consensus.decide("ETH/USDT", self._long_votes(3), entry=None)

        self.assertIsNone(result.intent)
        self.assertIn("現價", result.blocked_reason)

    def test_agent_votes_are_recorded_on_the_intent(self):
        result = consensus.decide("ETH/USDT", self._long_votes(3), entry=100.0)

        self.assertEqual(len(result.intent.agent_votes), 3)

    def test_errors_are_surfaced_not_swallowed(self):
        opinions = self._long_votes(3) + [
            AgentOpinion(agent="broken", error="ValueError: 壞了"),
        ]
        result = consensus.decide("ETH/USDT", opinions, entry=100.0)

        self.assertTrue(any("broken" in e for e in result.errors))


class TestBaseAgentBehaviour(unittest.TestCase):

    def test_a_crashing_agent_becomes_an_error_opinion_not_silence(self):
        """靜默失敗被禁止。壞掉的 Agent 必須看得見。"""
        class Exploding(BaseAgent):
            name = "exploding"

            def _analyse(self, context):
                raise RuntimeError("壞了")

        opinion = Exploding().analyse(context_for(bull_indicators()))

        self.assertIsNotNone(opinion.error)
        self.assertIn("壞了", opinion.error)
        self.assertIs(opinion.vote, Vote.ABSTAIN)

    def test_broken_data_makes_every_agent_abstain(self):
        """資料壞掉不等於市場中性 —— 不可以因此投 WAIT 或方向票。"""
        context = context_for(bull_indicators(data_ok=False))

        for agent_class in builtin.ALL_AGENT_CLASSES:
            with self.subTest(agent=agent_class.name):
                self.assertIs(agent_class().analyse(context).vote, Vote.ABSTAIN)

    def test_an_agent_returning_none_abstains_rather_than_crashing(self):
        class Silent(BaseAgent):
            name = "silent"

            def _analyse(self, context):
                return None

        self.assertIs(Silent().analyse(context_for(bull_indicators())).vote,
                      Vote.ABSTAIN)

    def test_position_agents_abstain_without_a_position(self):
        context = context_for(bull_indicators())

        self.assertIs(builtin.ExitAgent().analyse(context).vote, Vote.ABSTAIN)

    def test_every_agent_survives_a_context_with_almost_no_data(self):
        """
        缺資料是常態,不是例外。任何一個 Agent 因為缺欄位就拋例外,
        整個 Agent 群的可信度都會被 Supervisor 打掉。
        """
        sparse = Indicators(symbol="X/USDT", timeframe="1h", data_ok=True)
        context = AgentContext(symbol="X/USDT", indicators=sparse)

        for agent_class in builtin.ALL_AGENT_CLASSES:
            with self.subTest(agent=agent_class.name):
                opinion = agent_class().analyse(context)
                self.assertIsNone(opinion.error, opinion.error)


class TestIndividualAgents(unittest.TestCase):

    def test_trend_agent_waits_in_a_choppy_market(self):
        context = context_for(bull_indicators(adx=10.0))

        self.assertIs(builtin.TrendAgent().analyse(context).vote, Vote.WAIT)

    def test_mean_reversion_abstains_in_a_trending_market(self):
        """強趨勢裡逆勢接刀是虧損的主要來源之一。"""
        context = context_for(bull_indicators(adx=45.0, rsi=20.0))

        self.assertIs(builtin.MeanReversionAgent().analyse(context).vote,
                      Vote.ABSTAIN)

    def test_volatility_agent_waits_on_extreme_atr(self):
        context = context_for(bull_indicators(atr=8.0))
        opinion = builtin.VolatilityAgent().analyse(context)

        self.assertIs(opinion.vote, Vote.WAIT)
        self.assertGreater(opinion.confidence, 80)

    def test_volatility_agent_never_picks_a_direction(self):
        for atr in (0.1, 1.0, 3.5, 8.0):
            with self.subTest(atr=atr):
                opinion = builtin.VolatilityAgent().analyse(
                    context_for(bull_indicators(atr=atr)),
                )
                self.assertFalse(opinion.vote.is_directional)

    def test_funding_agent_fades_an_overcrowded_long_side(self):
        context = context_for(bull_indicators(), funding_rate=0.005)

        self.assertIs(builtin.FundingAgent().analyse(context).vote, Vote.SHORT)

    def test_funding_agent_abstains_without_data_rather_than_assuming_zero(self):
        context = context_for(bull_indicators(), funding_rate=None)

        self.assertIs(builtin.FundingAgent().analyse(context).vote, Vote.ABSTAIN)

    def test_btc_agent_abstains_on_btc_itself(self):
        indicators = bull_indicators(symbol="BTC/USDT")
        context = context_for(indicators, btc_indicators=indicators)

        self.assertIs(builtin.BtcCorrelationAgent().analyse(context).vote,
                      Vote.ABSTAIN)

    def test_position_risk_agent_waits_when_too_concentrated(self):
        context = context_for(
            bull_indicators(), correlated_symbols=["ETH/USDT", "SOL/USDT"],
        )
        opinion = builtin.PositionRiskAgent().analyse(context)

        self.assertIs(opinion.vote, Vote.WAIT)

    def test_exit_agent_warns_when_the_stop_is_close(self):
        context = context_for(
            bull_indicators(),
            position={"direction": "做多", "stop_loss": 99.5},
        )
        opinion = builtin.ExitAgent().analyse(context)

        self.assertIs(opinion.vote, Vote.WAIT)
        self.assertGreater(opinion.confidence, 50)

    def test_news_agent_abstains_without_data(self):
        self.assertIs(
            builtin.NewsAgent().analyse(context_for(bull_indicators())).vote,
            Vote.ABSTAIN,
        )


class TestSupervisorCanOnlyDowngrade(unittest.TestCase):

    def _actionable(self):
        opinions = [
            AgentOpinion(agent=f"a{i}", vote=Vote.LONG, confidence=80)
            for i in range(3)
        ]
        return consensus.decide("ETH/USDT", opinions, entry=100.0)

    def test_a_healthy_deliberation_is_not_touched(self):
        deliberation = self._actionable()
        report = Supervisor().review(deliberation)

        self.assertFalse(report.vetoed)
        self.assertIsNotNone(deliberation.intent)

    def test_too_many_errors_veto_the_trade(self):
        opinions = [
            AgentOpinion(agent="a", vote=Vote.LONG, confidence=80),
            AgentOpinion(agent="b", vote=Vote.LONG, confidence=80),
            AgentOpinion(agent="c", error="boom"),
            AgentOpinion(agent="d", error="boom"),
        ]
        deliberation = consensus.decide("ETH/USDT", opinions, entry=100.0)
        self.assertIsNotNone(deliberation.intent)     # 共識本身是成立的

        report = Supervisor().review(deliberation)

        self.assertTrue(report.vetoed)
        self.assertIsNone(deliberation.intent)
        self.assertIs(deliberation.direction, Direction.WAIT)

    def test_suspicious_unanimity_is_vetoed(self):
        """十二個獨立 Agent 不會完全同意。完全同意通常代表它們全壞了。"""
        opinions = [
            AgentOpinion(agent=f"a{i}", vote=Vote.LONG, confidence=80)
            for i in range(12)
        ]
        deliberation = consensus.decide("ETH/USDT", opinions, entry=100.0)
        report = Supervisor().review(deliberation)

        self.assertTrue(report.vetoed)
        self.assertIsNone(deliberation.intent)

    def test_the_supervisor_cannot_turn_a_wait_into_a_trade(self):
        """一個能把 WAIT 變成進場的監督者,就不是監督者。"""
        waiting = consensus.decide(
            "ETH/USDT",
            [AgentOpinion(agent="a", vote=Vote.WAIT, confidence=90)],
            entry=100.0,
        )
        self.assertIsNone(waiting.intent)

        Supervisor().review(waiting)

        self.assertIsNone(waiting.intent)
        self.assertIs(waiting.direction, Direction.WAIT)

    def test_a_stuck_agent_is_reported(self):
        supervisor = Supervisor()

        for _ in range(25):
            deliberation = self._actionable()
            deliberation.opinions.append(
                AgentOpinion(agent="stuck", vote=Vote.LONG, confidence=50),
            )
            report = supervisor.review(deliberation)

        self.assertTrue(any("stuck" in w for w in report.health_warnings))


class TestDeliberateEndToEnd(unittest.TestCase):
    """
    一個永遠不交易的系統,跟一個會崩潰的系統一樣是壞的(Phase 6 的原話)。
    所以這裡同時測「該進場時會進場」與「該擋時會擋」。
    """

    def _run(self, indicators, **context_kwargs):
        return registry.deliberate(
            context_for(indicators, **context_kwargs),
            registry=registry.AgentRegistry(),
            supervisor=Supervisor(),
        )

    def test_a_clean_bull_setup_produces_a_trade_intent(self):
        deliberation, report = self._run(bull_indicators())

        self.assertIsNotNone(deliberation.intent)
        self.assertIs(deliberation.direction, Direction.LONG)
        self.assertFalse(report.vetoed)

    def test_a_clean_bear_setup_produces_a_short(self):
        indicators = bull_indicators(
            ema20=98.0, ema50=102.0, macd_hist=-0.3, rsi=42.0,
        )
        deliberation, _ = self._run(indicators)

        self.assertIs(deliberation.direction, Direction.SHORT)
        self.assertIsNotNone(deliberation.intent)

    def test_extreme_volatility_blocks_everything(self):
        deliberation, _ = self._run(bull_indicators(atr=8.0))

        self.assertIsNone(deliberation.intent)

    def test_broken_data_blocks_everything(self):
        deliberation, _ = self._run(bull_indicators(data_ok=False))

        self.assertIsNone(deliberation.intent)
        self.assertIn("棄權", deliberation.blocked_reason)

    def test_the_intent_carries_no_size_or_leverage(self):
        """Risk Engine 才決定大小。Agent 層給出大小就是越權。"""
        deliberation, _ = self._run(bull_indicators())
        fields = set(vars(deliberation.intent))

        self.assertEqual(
            fields & {"size_usdt", "leverage", "quantity", "margin"}, set(),
        )

    def test_every_agent_is_represented_in_the_vote_record(self):
        deliberation, _ = self._run(bull_indicators())

        self.assertEqual(
            set(deliberation.votes), set(registry.AgentRegistry().names),
        )


class TestAgentPipelineWiring(unittest.TestCase):
    """
    資料層接到 Agent 層的那一段。

    這段刻意**不**放在 agmcis/agents/ 裡 —— Agent 套件不碰資料層,
    那條界線由上面的 AST 測試守著。
    """

    def setUp(self):
        from agmcis.signal import agent_pipeline
        self.pipeline = agent_pipeline

    def test_the_agents_package_itself_never_fetches_data(self):
        """取資料的程式碼在 signal 層,不在 agents 層。"""
        for name in sorted(os.listdir(AGENTS_DIR)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(AGENTS_DIR, name), encoding="utf-8") as handle:
                source = handle.read()
            with self.subTest(module=name):
                self.assertNotIn("get_ohlcv", source)
                self.assertNotIn("market_data", source)

    def test_bad_data_produces_a_context_every_agent_abstains_on(self):
        from unittest.mock import patch

        class Report:
            summary = "缺 K 棒"

        with patch("agmcis.data.market_data.get_ohlcv_checked",
                   return_value=(None, Report())):
            context = self.pipeline.build_context("ETH/USDT")

        self.assertFalse(context.data_ok)
        for agent_class in builtin.ALL_AGENT_CLASSES:
            with self.subTest(agent=agent_class.name):
                self.assertIs(agent_class().analyse(context).vote, Vote.ABSTAIN)

    def test_missing_funding_rate_stays_none_instead_of_becoming_zero(self):
        """
        用 0 填補缺失資料會讓「沒資料」看起來像「數值正常」。
        FundingAgent 看到 None 會棄權,看到 0 會說「費率正常」—— 完全不同的事。
        """
        import pandas as pd
        from unittest.mock import patch

        frame = pd.DataFrame({
            "timestamp": range(120), "open": [100.0] * 120, "high": [101.0] * 120,
            "low": [99.0] * 120, "close": [100.0] * 120, "volume": [10.0] * 120,
        })

        class Report:
            summary = "ok"

        with patch("agmcis.data.market_data.get_ohlcv_checked",
                   return_value=(frame, Report())), \
             patch("agmcis.data.market_data.get_price", return_value=100.0), \
             patch("agmcis.data.market_data.get_funding_rate", return_value=None):
            context = self.pipeline.build_context("ETH/USDT")

        self.assertIsNone(context.funding_rate)

    def test_a_symbol_that_blows_up_does_not_stop_the_whole_scan(self):
        from unittest.mock import patch

        def explode(symbol, **kwargs):
            if symbol == "BAD/USDT":
                raise RuntimeError("boom")
            return "ok", None

        with patch.object(self.pipeline, "analyse_symbol", side_effect=explode):
            results = self.pipeline.scan(["BAD/USDT", "GOOD/USDT"])

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], (None, None))

    def test_actionable_intents_skips_failed_and_waiting_symbols(self):
        class FakeDeliberation:
            def __init__(self, intent):
                self.intent = intent

            @property
            def is_actionable(self):
                return self.intent is not None

        results = [
            (None, None),
            (FakeDeliberation(None), None),
            (FakeDeliberation("INTENT"), None),
        ]

        self.assertEqual(self.pipeline.actionable_intents(results), ["INTENT"])


if __name__ == "__main__":
    unittest.main()
