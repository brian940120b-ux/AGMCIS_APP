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

    def setUp(self):
        patch.object(auto_trader, "logger").start()
        patch.object(auto_trader, "notify_open_trade").start()
        self.addCleanup(patch.stopall)

    def test_does_not_scan_or_open_when_risk_blocks(self):
        with patch.object(auto_trader, "assert_can_open",
                          return_value=(False, "MAX_DAILY_LOSS", blocked_status("MAX_DAILY_LOSS"))), \
             patch.object(auto_trader, "scan_market") as scan, \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "BLOCKED_BY_RISK")
        self.assertEqual(result["reason"], "MAX_DAILY_LOSS")
        scan.assert_not_called()
        create.assert_not_called()

    def test_opens_when_risk_allows(self):
        with patch.object(auto_trader, "assert_can_open", return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trades", return_value=[]), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[candidate()]), \
             patch.object(auto_trader, "create_paper_trade",
                          return_value={"success": True, "message": "ok"}) as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "OPENED")
        create.assert_called_once()

    def test_leverage_passed_to_order_never_exceeds_cap(self):
        with patch.object(auto_trader, "assert_can_open", return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trades", return_value=[]), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[candidate()]), \
             patch.object(auto_trader, "calculate_leverage", return_value=25), \
             patch.object(auto_trader, "create_paper_trade",
                          return_value={"success": True, "message": "ok"}) as create:
            auto_trader.run_auto_trader()

        self.assertLessEqual(create.call_args.kwargs["leverage"], risk_limits.MAX_LEVERAGE)

    def test_skips_candidates_with_bad_data(self):
        bad = candidate()
        bad["data_ok"] = False

        with patch.object(auto_trader, "assert_can_open", return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trades", return_value=[]), \
             patch.object(auto_trader, "scan_market", return_value=[bad]), \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "NO_TRADE_SIGNAL")
        create.assert_not_called()

    def test_skips_candidates_without_stop_loss(self):
        no_sl = candidate()
        no_sl["stoploss"] = None

        with patch.object(auto_trader, "assert_can_open", return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trades", return_value=[]), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[no_sl]), \
             patch.object(auto_trader, "create_paper_trade") as create:
            result = auto_trader.run_auto_trader()

        self.assertEqual(result["status"], "NO_TRADE_SIGNAL")
        create.assert_not_called()

    def test_short_signal_becomes_short_order(self):
        short = candidate(signal="🔴 Sell")
        short["stoploss"] = 103.0
        short["takeprofit"] = 94.0

        with patch.object(auto_trader, "assert_can_open", return_value=(True, None, open_status())), \
             patch.object(auto_trader, "get_open_trades", return_value=[]), \
             patch.object(auto_trader, "get_open_trade", return_value=None), \
             patch.object(auto_trader, "scan_market", return_value=[short]), \
             patch.object(auto_trader, "create_paper_trade",
                          return_value={"success": True, "message": "ok"}) as create:
            auto_trader.run_auto_trader()

        self.assertEqual(create.call_args.kwargs["signal"], "做空")
