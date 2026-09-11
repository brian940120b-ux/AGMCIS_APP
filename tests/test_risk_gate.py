"""
風控 hard gate。

Phase 0.5 修掉的核心問題:auto_trader 是生產環境每 60 秒執行、
且有 HTTP 端點可觸發的開倉路徑,卻完全沒有呼叫風控。
這裡鎖住「風控擋下時絕不開倉」以及「槓桿不得超過上限」。
"""
import unittest
from unittest.mock import patch

import auto_trader
import risk_control
import risk_limits


def blocked_status(*blockers):
    return {
        "system_status": "LIMITED",
        "allow_new_trade": False,
        "emergency_stop": False,
        "blockers": list(blockers),
    }


def open_status():
    return {
        "system_status": "ACTIVE",
        "allow_new_trade": True,
        "emergency_stop": False,
        "blockers": [],
    }


def candidate(signal="🟢 Buy", symbol="BTC/USDT"):
    """
    掃描層的候選。Phase 9 之後它只決定「要看哪些標的」——
    要不要進場、停損放哪裡,由 Agent 共識決定。
    """
    return {
        "symbol": symbol,
        "trade_signal": signal,
        "confidence": 96,
        "entry_price": 100.0,
        "stoploss": 97.0,
        "takeprofit": 106.0,
        "mtf_score": 3,
        "mtf_status": "STRONG_BULLISH",
        "data_ok": True,
        "indicators": {"atr": 1.0, "price": 100.0},
    }


def deliberation_for(symbol="BTC/USDT", direction="做多", entry=100.0,
                     stop_loss=97.0, take_profit=106.0, blocked_reason=None):
    """
    假的 Agent 共識結果。

    Phase 9 之後 auto_trader 的 TradeIntent 來自 Agent 共識,
    所以這裡 patch 的接縫是 agent_pipeline,不再是掃描結果的欄位。
    """
    from agmcis.agents.consensus import Deliberation
    from agmcis.core.models import TradeIntent
    from agmcis.core.enums import Direction

    if blocked_reason is not None:
        return Deliberation(symbol=symbol, blocked_reason=blocked_reason)

    intent = TradeIntent(
        symbol=symbol, market_type="perpetual", direction=direction,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit,
        confidence=90.0, strategy="multi_agent",
    )
    return Deliberation(
        symbol=symbol, direction=Direction.parse(direction),
        confidence=90.0, intent=intent,
    )


def agent_returns(*deliberations):
    """
    依呼叫順序回傳這些共識結果;用完就重複最後一個。
    回傳 (deliberation, None) —— 第二個是 SupervisorReport,這裡用不到。
    """
    queue = list(deliberations)

    def fake(symbol, **kwargs):
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        return result, None

    return fake


class TestAssertCanOpen(unittest.TestCase):

    def test_blocks_and_reports_reason(self):
        with patch.object(risk_control, "get_risk_control_status",
                          return_value=blocked_status("MAX_DAILY_LOSS", "MAX_DRAWDOWN")), \
             patch.object(risk_control, "logger"):
            allowed, reason, _ = risk_control.assert_can_open("BTC/USDT")

        self.assertFalse(allowed)
        self.assertIn("MAX_DAILY_LOSS", reason)
        self.assertIn("MAX_DRAWDOWN", reason)

    def test_allows_when_clear(self):
        with patch.object(risk_control, "get_risk_control_status", return_value=open_status()), \
             patch.object(risk_control, "logger"):
            allowed, reason, _ = risk_control.assert_can_open("BTC/USDT")

        self.assertTrue(allowed)
        self.assertIsNone(reason)


class TestCapLeverage(unittest.TestCase):

    def test_caps_at_configured_max(self):
        self.assertEqual(risk_control.cap_leverage(99), risk_limits.MAX_LEVERAGE)

    def test_leaves_lower_leverage_alone(self):
        self.assertEqual(risk_control.cap_leverage(2), 2.0)

    def test_floors_at_one(self):
        self.assertEqual(risk_control.cap_leverage(0), 1.0)
        self.assertEqual(risk_control.cap_leverage(-5), 1.0)

    def test_handles_garbage_input(self):
        self.assertEqual(risk_control.cap_leverage(None), 1.0)
        self.assertEqual(risk_control.cap_leverage("abc"), 1.0)


class TestAutoTraderRespectsRiskGate(unittest.TestCase):
    """
    Phase 9 之後 auto_trader 走完整鏈路:
        scan -> Agent 共識 -> TradeIntent -> Risk Engine -> 開倉

    倉位不再是固定 1000 USDT,由風控依停損距離反推。
    Agent 層只產生意圖,風控才決定大小與槓桿 —— 這裡鎖住的要求始終不變:
    **風控擋下就不開。**
    """

    def setUp(self):
        patch.object(auto_trader, "logger").start()
        patch.object(auto_trader, "notify_open_trade").start()
        self.addCleanup(patch.stopall)

    def _decision(self, intent, approved=True, size=500.0, leverage=3.0,
                  reason=None, blockers=None):
        from agmcis.core.models import RiskDecision
        return RiskDecision(
            intent=intent, approved=approved,
            size_usdt=size if approved else None,
            leverage=leverage if approved else None,
            reason=reason, blockers=blockers or [],
        )

    def test_does_not_scan_or_open_when_account_gate_blocks(self):
        """帳戶層級被擋時連掃描都不該做 —— 掃描要打交易所 API。"""
        with patch.object(auto_trader, "assert_can_open",
                          return_value=(False, "MAX_DAILY_LOSS",
                                        blocked_status("MAX_DAILY_LOSS"))), \
             patch.object(auto_trader, "scan_market") as scan, \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "BLOCKED_BY_RISK")
        self.assertEqual(result["reason"], "MAX_DAILY_LOSS")
        scan.assert_not_called()
        create.assert_not_called()

    def test_opens_when_risk_allows(self):
        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[candidate()]), \
             patch.object(auto_trader.agent_pipeline, "analyse_symbol",
                          side_effect=agent_returns(deliberation_for())), \
             patch.object(auto_trader, "evaluate_intent",
                          side_effect=lambda i, **k: self._decision(i)), \
             patch.object(auto_trader, "create_paper_trade",
                          return_value={"success": True, "message": "ok"}) as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "OPENED")
        create.assert_called_once()

    def test_position_size_comes_from_risk_engine_not_a_fixed_number(self):
        """這是 Phase 5 的重點:倉位由風控算,不再是寫死的 1000 USDT。"""
        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[candidate()]), \
             patch.object(auto_trader.agent_pipeline, "analyse_symbol",
                          side_effect=agent_returns(deliberation_for())), \
             patch.object(auto_trader, "evaluate_intent",
                          side_effect=lambda i, **k: self._decision(i, size=137.5, leverage=4.0)), \
             patch.object(auto_trader, "create_paper_trade",
                          return_value={"success": True, "message": "ok"}) as create:
            auto_trader.run_auto_trader()

        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["size_usdt"], 137.5)
        self.assertEqual(kwargs["leverage"], 4.0)

    def test_per_intent_rejection_tries_the_next_candidate(self):
        """單一標的被拒(例如名目太小)不該讓整輪停下來。"""
        first, second = candidate(symbol="AAA/USDT"), candidate(symbol="BBB/USDT")

        def evaluate(intent, **kwargs):
            if intent.symbol == "AAA/USDT":
                return self._decision(intent, approved=False,
                                      reason="名目太小", blockers=["SIZING_REJECTED"])
            return self._decision(intent)

        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[first, second]), \
             patch.object(auto_trader.agent_pipeline, "analyse_symbol",
                          side_effect=agent_returns(
                              deliberation_for(symbol="AAA/USDT"),
                              deliberation_for(symbol="BBB/USDT"))), \
             patch.object(auto_trader, "evaluate_intent", side_effect=evaluate), \
             patch.object(auto_trader, "create_paper_trade",
                          return_value={"success": True, "message": "ok"}) as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "OPENED")
        self.assertEqual(result["symbol"], "BBB/USDT")

    def test_account_level_block_mid_loop_stops_everything(self):
        """帳戶層級的封鎖對所有標的都一樣,不該繼續試下一檔。"""
        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market",
                          return_value=[candidate("🟢 Buy", "AAA/USDT"),
                                        candidate("🟢 Buy", "BBB/USDT")]), \
             patch.object(auto_trader.agent_pipeline, "analyse_symbol",
                          side_effect=agent_returns(
                              deliberation_for(symbol="AAA/USDT"),
                              deliberation_for(symbol="BBB/USDT"))), \
             patch.object(auto_trader, "evaluate_intent",
                          side_effect=lambda i, **k: self._decision(
                              i, approved=False, reason="MAX_DRAWDOWN",
                              blockers=["MAX_DRAWDOWN"])), \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "BLOCKED_BY_RISK")
        create.assert_not_called()

    def test_skips_candidates_with_bad_data(self):
        bad = candidate()
        bad["data_ok"] = False

        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "scan_market", return_value=[bad]), \
             patch.object(auto_trader, "evaluate_intent") as evaluate, \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "NO_TRADE_SIGNAL")
        evaluate.assert_not_called()
        create.assert_not_called()

    def test_no_agent_intent_means_no_risk_call_and_no_trade(self):
        """
        Agent 共識結論是觀望時,連風控都不必呼叫。

        Phase 9 之前這裡測的是「掃描結果沒有停損就進不到風控」。
        現在停損由 TradeIntent 在建構時強制(見 tests/test_agents.py),
        auto_trader 這一層要保證的是:**沒有 intent 就沒有交易。**
        """
        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[candidate()]), \
             patch.object(auto_trader.agent_pipeline, "analyse_symbol",
                          side_effect=agent_returns(
                              deliberation_for(blocked_reason="觀望票過半"))), \
             patch.object(auto_trader, "evaluate_intent") as evaluate, \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "NO_TRADE_SIGNAL")
        evaluate.assert_not_called()
        create.assert_not_called()

    def test_an_agent_layer_crash_skips_the_symbol_instead_of_trading_blind(self):
        """Agent 層炸掉時絕不能退回「用掃描結果直接開倉」。"""
        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[candidate()]), \
             patch.object(auto_trader.agent_pipeline, "analyse_symbol",
                          side_effect=RuntimeError("agent 壞了")), \
             patch.object(auto_trader, "evaluate_intent") as evaluate, \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "NO_TRADE_SIGNAL")
        evaluate.assert_not_called()
        create.assert_not_called()

    def test_short_signal_becomes_short_order(self):
        short = candidate(signal="🔴 Sell")
        short["stoploss"] = 103.0
        short["takeprofit"] = 94.0

        with patch.object(auto_trader, "assert_can_open",
                          return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[short]), \
             patch.object(auto_trader.agent_pipeline, "analyse_symbol",
                          side_effect=agent_returns(deliberation_for(
                              direction="做空", stop_loss=103.0, take_profit=94.0))), \
             patch.object(auto_trader, "evaluate_intent",
                          side_effect=lambda i, **k: self._decision(i)), \
             patch.object(auto_trader, "create_paper_trade",
                          return_value={"success": True, "message": "ok"}) as create:
            auto_trader.run_auto_trader()

        self.assertEqual(create.call_args.kwargs["signal"], "做空")
