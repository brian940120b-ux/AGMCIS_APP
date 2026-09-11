"""
持倉評估(Dashboard 的 AI Decision Center)。

Phase 9 之前這是系統裡**第三套**獨立評分,權重跟訊號管線不一樣、
也跟風控不一樣 —— 同一個部位在三個地方會得到三種結論。
現在它跑的是同一套 Agent 群,只是多帶了部位資訊。

這一層只呈現意見,不平倉。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_decision_service
from agmcis.agents.base import AgentOpinion, Vote
from agmcis.agents.consensus import Deliberation
from agmcis.core.enums import Direction


def deliberation(direction=Direction.LONG, confidence=80.0, opinions=None,
                 blocked_reason=None):
    return Deliberation(
        symbol="BTC/USDT", direction=direction, confidence=confidence,
        opinions=opinions or [
            AgentOpinion(agent="trend", vote=Vote.LONG, confidence=80),
            AgentOpinion(agent="momentum", vote=Vote.LONG, confidence=70),
        ],
        blocked_reason=blocked_reason,
    )


def evaluate(position, result):
    with patch.object(ai_decision_service.agent_pipeline, "analyse_symbol",
                      return_value=(result, None)):
        return ai_decision_service._evaluate_position(position)


LONG_POSITION = {"symbol": "BTC/USDT", "signal": "做多", "entry_price": 100.0,
                 "stoploss": 97.0, "takeprofit": 106.0, "leverage": 3}


class TestItUsesTheSameAgentsAsTheRestOfTheSystem(unittest.TestCase):

    def test_the_module_has_no_scoring_formula_of_its_own(self):
        """
        舊版自己一套 score_rsi / score_trend / score_macd / score_atr。
        那些函式如果復活,同一個部位又會有兩種結論。
        """
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ai_decision_service.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        for banned in ("def score_rsi", "def score_trend", "def score_macd",
                       "def score_atr", "def score_risk"):
            with self.subTest(func=banned):
                self.assertNotIn(banned, source)

    def test_it_goes_through_the_agent_pipeline(self):
        with patch.object(ai_decision_service.agent_pipeline, "analyse_symbol",
                          return_value=(deliberation(), None)) as analyse:
            ai_decision_service._evaluate_position(LONG_POSITION)

        analyse.assert_called_once()
        self.assertEqual(analyse.call_args.kwargs["position"], LONG_POSITION)


class TestSignalTranslation(unittest.TestCase):

    def test_agreement_with_the_position_is_a_hold(self):
        result = evaluate(LONG_POSITION, deliberation(Direction.LONG))

        self.assertEqual(result["trade_signal"], ai_decision_service.HOLD_ALIGNED)

    def test_consensus_flipping_against_the_position_suggests_reducing(self):
        result = evaluate(LONG_POSITION, deliberation(Direction.SHORT))

        self.assertEqual(result["trade_signal"], ai_decision_service.REDUCE)
        self.assertIn("相反", result["reason"])

    def test_a_near_stop_warning_outranks_a_bullish_consensus(self):
        """風險先於機會。停損快到了比「共識還是看多」重要。"""
        opinions = [
            AgentOpinion(agent="trend", vote=Vote.LONG, confidence=95),
            AgentOpinion(agent="exit", vote=Vote.WAIT, confidence=90,
                         reasons=["距離停損只剩 0.4%"]),
        ]
        result = evaluate(
            LONG_POSITION, deliberation(Direction.LONG, opinions=opinions),
        )

        self.assertEqual(result["trade_signal"],
                         ai_decision_service.STOPLOSS_WARNING)

    def test_a_mild_exit_concern_is_only_a_reduce(self):
        opinions = [
            AgentOpinion(agent="trend", vote=Vote.LONG, confidence=95),
            AgentOpinion(agent="exit", vote=Vote.WAIT, confidence=70,
                         reasons=["RSI 超買"]),
        ]
        result = evaluate(
            LONG_POSITION, deliberation(Direction.LONG, opinions=opinions),
        )

        self.assertEqual(result["trade_signal"], ai_decision_service.REDUCE)

    def test_all_agents_abstaining_produces_no_data_not_a_neutral_hold(self):
        """分不出「市場中性」與「資料壞掉」是 Phase 0 稽核的老問題。"""
        opinions = [AgentOpinion(agent=f"a{i}") for i in range(4)]
        result = evaluate(LONG_POSITION, deliberation(opinions=opinions))

        self.assertEqual(result["trade_signal"], ai_decision_service.NO_DATA)
        self.assertIsNone(result["confidence"])

    def test_a_blocked_consensus_is_a_neutral_hold_with_the_reason(self):
        result = evaluate(
            LONG_POSITION,
            deliberation(Direction.WAIT, blocked_reason="觀望票佔 50% 權重"),
        )

        self.assertEqual(result["trade_signal"], ai_decision_service.HOLD_NEUTRAL)
        self.assertIn("觀望票", result["reason"])


class TestItNeverTrades(unittest.TestCase):
    """這一層只呈現意見。真正的出場動作要等 Phase 12。"""

    def test_the_module_does_not_import_anything_that_can_close_a_position(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ai_decision_service.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        for banned in ("close_trade", "create_paper_trade", "create_order",
                       "exchange_engine"):
            with self.subTest(name=banned):
                self.assertNotIn(banned, source)


class TestFailuresAreVisible(unittest.TestCase):

    def test_one_broken_position_does_not_blank_the_whole_panel(self):
        positions = [LONG_POSITION, {"symbol": "ETH/USDT", "signal": "做空"}]

        def analyse(symbol, **kwargs):
            if symbol == "BTC/USDT":
                raise RuntimeError("boom")
            return deliberation(Direction.SHORT), None

        with patch.object(ai_decision_service, "get_open_trades",
                          return_value=positions), \
             patch.object(ai_decision_service.agent_pipeline, "analyse_symbol",
                          side_effect=analyse), \
             patch.object(ai_decision_service, "logger"):
            results = ai_decision_service.get_ai_decisions()

        self.assertEqual(len(results), 2)
        broken = [r for r in results if r["symbol"] == "BTC/USDT"][0]
        self.assertEqual(broken["trade_signal"], ai_decision_service.NO_DATA)
        self.assertTrue(broken["errors"])

    def test_agent_errors_are_reported_not_hidden(self):
        opinions = [
            AgentOpinion(agent="trend", vote=Vote.LONG, confidence=80),
            AgentOpinion(agent="broken", error="ValueError: 壞了"),
        ]
        result = evaluate(
            LONG_POSITION, deliberation(Direction.LONG, opinions=opinions),
        )

        self.assertTrue(any("broken" in e for e in result["errors"]))

    def test_no_positions_produces_an_empty_list_not_an_error(self):
        with patch.object(ai_decision_service, "get_open_trades", return_value=[]), \
             patch.object(ai_decision_service, "logger"):
            self.assertEqual(ai_decision_service.get_ai_decisions(), [])


if __name__ == "__main__":
    unittest.main()
