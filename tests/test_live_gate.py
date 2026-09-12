"""
LIVE SAFETY GATE(Phase 17)。

這個閘門的目的是**阻止**事情發生,所以測試的方向跟其他模組相反:
重點不是「條件滿足時會不會開」,而是「條件不滿足時會不會漏開」。

三類測試:
  1. 每一項條件單獨缺少時,閘門都必須關著
  2. 檢查本身壞掉時算「沒通過」,不是「跳過」
  3. 人工確認無法被繞過
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.safety import live_gate as lg


def audit_path():
    handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    handle.close()
    return handle.name


def confirmation(phrase=None, hours_ago=0.0, notional=20.0, **overrides):
    data = {
        "phrase": phrase if phrase is not None else lg.REQUIRED_PHRASE,
        "signed_at": (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).isoformat(),
        "approved_notional_usdt": notional,
    }
    data.update(overrides)

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8",
    )
    json.dump(data, handle, ensure_ascii=False)
    handle.close()
    return handle.name


def _fresh_calendar():
    """一份剛維護過的事件日曆。過期的日曆會擋住實單(第五十一節)。"""
    from datetime import datetime, timezone

    from agmcis.risk.news_risk import Calendar

    return Calendar(generated_at=datetime.now(timezone.utc), events=[])


def passing_providers(**overrides):
    providers = {
        "preflight": lambda: [],
        "paper_record": lambda: {
            "closed_trades": lg.MIN_PAPER_TRADES,
            "days": lg.MIN_PAPER_DAYS,
        },
        "self_review": lambda: {"verdict": "HEALTHY", "headline": "沒問題"},
        "strategy_validation": lambda: {
            "all_results": [
                {"verdict": "PASS", "strategy": "LIVE_PIPELINE",
                 "is_live_pipeline": True},
            ],
        },
        "calibration": lambda: {
            "calibrated": True, "stale": False, "testnet": False,
            "captured_at": "2026-09-01T00:00:00+00:00",
        },
        "reconciliation": lambda: {
            "naked_count": 0, "unresolved_count": 0,
            "reconciliation_critical": 0, "alerts": [],
        },
        "kill_switch": lambda: {"can_close_positions": True, "armed": False},
        "news_calendar": _fresh_calendar,
    }
    providers.update(overrides)
    return providers


class GateTestCase(unittest.TestCase):

    def gate(self, providers=None, confirmation_file=None):
        audit = audit_path()
        self.addCleanup(os.unlink, audit)

        return lg.LiveGate(
            confirmation_file=confirmation_file or "/nonexistent.json",
            audit_file=audit,
            providers=providers if providers is not None else passing_providers(),
        )

    def open_gate(self, **overrides):
        path = confirmation()
        self.addCleanup(os.unlink, path)

        return self.gate(
            providers=passing_providers(**overrides), confirmation_file=path,
        ).evaluate()


class TestTheGateOpensOnlyWhenEverythingIsReady(GateTestCase):

    def test_all_conditions_met_opens_the_gate(self):
        """閘門也要能開 —— 一個永遠關著的閘門等於沒有做完。"""
        result = self.open_gate()

        self.assertTrue(result.open, [c.name for c in result.failed])
        self.assertEqual(result.approved_notional, 20.0)

    def test_a_missing_confirmation_keeps_it_shut(self):
        result = self.gate().evaluate()

        self.assertFalse(result.open)
        self.assertIn("人工確認", [c.name for c in result.failed])

    def test_failing_preflight_keeps_it_shut(self):
        result = self.open_gate(preflight=lambda: ["資料庫密碼"])

        self.assertFalse(result.open)

    def test_too_few_paper_trades_keeps_it_shut(self):
        result = self.open_gate(
            paper_record=lambda: {"closed_trades": 10, "days": 60},
        )

        self.assertFalse(result.open)

    def test_enough_trades_over_too_few_days_keeps_it_shut(self):
        """
        筆數夠但只跑了兩天,代表沒有經歷過不同的市況。
        """
        result = self.open_gate(
            paper_record=lambda: {"closed_trades": 500, "days": 2},
        )

        self.assertFalse(result.open)

    def test_a_fragile_self_review_keeps_it_shut(self):
        """
        績效依賴少數幾筆極端獲利的系統,用真錢跑只是把運氣放大。
        """
        result = self.open_gate(
            self_review=lambda: {"verdict": "FRAGILE", "headline": "靠三筆"},
        )

        self.assertFalse(result.open)

    def test_not_enough_data_keeps_it_shut(self):
        result = self.open_gate(
            self_review=lambda: {"verdict": "NOT_ENOUGH_DATA", "headline": "太少"},
        )

        self.assertFalse(result.open)

    def test_no_validated_strategy_keeps_it_shut(self):
        result = self.open_gate(
            strategy_validation=lambda: {
                "all_results": [
                    {"verdict": "REJECT", "is_live_pipeline": True},
                    {"verdict": "MARGINAL", "is_live_pipeline": True},
                ],
            },
        )

        self.assertFalse(result.open)

    def test_only_legacy_strategies_passing_keeps_it_shut(self):
        """
        系統裡還留著幾個舊的模組式策略,它們會一起被驗證,但 live 不用它們。
        **驗證一組永遠不會下單的策略,等於沒有驗證。**
        """
        result = self.open_gate(
            strategy_validation=lambda: {
                "all_results": [
                    {"verdict": "PASS", "strategy": "EMA_RSI_MACD_PRO",
                     "is_live_pipeline": False},
                    {"verdict": "REJECT", "strategy": "LIVE_PIPELINE",
                     "is_live_pipeline": True, "blockers": ["OOS 只有 12 筆"]},
                ],
            },
        )

        self.assertFalse(result.open)
        detail = next(c.detail for c in result.failed if c.name == "策略驗證")
        self.assertIn("live 訊號管線", detail)

    def test_a_report_without_the_live_pipeline_keeps_it_shut(self):
        """
        舊格式的報告(或驗證根本沒跑到 live 管線)不能算通過。
        """
        result = self.open_gate(
            strategy_validation=lambda: {
                "all_results": [{"verdict": "PASS", "strategy": "EMA_RSI_MACD_PRO"}],
            },
        )

        self.assertFalse(result.open)
        detail = next(c.detail for c in result.failed if c.name == "策略驗證")
        self.assertIn("沒有 live 訊號管線", detail)

    def test_a_passing_live_pipeline_is_enough(self):
        check = self.gate(
            providers=passing_providers(),
        ).check_strategy_validation()

        self.assertTrue(check.passed)
        self.assertIn("live 訊號管線", check.detail)

    def test_uncalibrated_specs_keep_it_shut(self):
        """
        用猜的維持保證金率算出來的強平價,在真實行情裡不會準 ——
        而它準不準決定的是會不會爆倉。
        """
        result = self.open_gate(calibration=lambda: {"calibrated": False})

        self.assertFalse(result.open)

    def test_testnet_specs_keep_it_shut(self):
        result = self.open_gate(calibration=lambda: {
            "calibrated": True, "stale": False, "testnet": True,
        })

        self.assertFalse(result.open)

    def test_a_naked_position_keeps_it_shut(self):
        result = self.open_gate(reconciliation=lambda: {
            "naked_count": 1, "unresolved_count": 0,
            "reconciliation_critical": 0, "alerts": [],
        })

        self.assertFalse(result.open)

    def test_a_kill_switch_that_cannot_close_keeps_it_shut(self):
        result = self.open_gate(kill_switch=lambda: {
            "can_close_positions": False, "armed": False,
        })

        self.assertFalse(result.open)

    def test_an_armed_kill_switch_keeps_it_shut(self):
        result = self.open_gate(kill_switch=lambda: {
            "can_close_positions": True, "armed": True,
        })

        self.assertFalse(result.open)


class TestItFailsClosed(GateTestCase):
    """一個在自己壞掉時預設放行的安全閘門不是安全閘門。"""

    def test_a_provider_that_raises_counts_as_failed(self):
        def explode():
            raise RuntimeError("壞了")

        result = self.open_gate(self_review=explode)

        self.assertFalse(result.open)

    def test_a_missing_provider_counts_as_failed(self):
        result = self.gate(providers={}).evaluate()

        self.assertFalse(result.open)
        self.assertGreaterEqual(len(result.failed), 7)

    def test_unreadable_reconciliation_data_is_not_treated_as_clean(self):
        """
        第一版我只看 summary.get("naked_count"),資料庫連不上時
        每個計數都是 None,這一項就通過了。讀不到不等於沒問題。
        """
        result = self.open_gate(reconciliation=lambda: {"error": "資料庫掛了"})

        self.assertFalse(result.open)
        detail = next(
            c.detail for c in result.failed if c.name == "對帳狀態"
        )
        self.assertIn("讀不到資料不等於沒問題", detail)

    def test_a_critical_alert_anywhere_keeps_it_shut(self):
        result = self.open_gate(reconciliation=lambda: {
            "naked_count": 0, "unresolved_count": 0,
            "reconciliation_critical": 0,
            "alerts": [{"level": "critical", "message": "讀不到訂單"}],
        })

        self.assertFalse(result.open)


class TestHumanConfirmationCannotBeBypassed(GateTestCase):

    def _with(self, **kwargs):
        path = confirmation(**kwargs)
        self.addCleanup(os.unlink, path)
        return self.gate(confirmation_file=path).check_confirmation()

    def test_the_phrase_must_match_exactly(self):
        check = self._with(phrase="我確認")

        self.assertFalse(check.passed)
        self.assertIn("確認句不符", check.detail)

    def test_an_expired_confirmation_is_rejected(self):
        """「上個月批准過」不等於「現在批准」。"""
        check = self._with(hours_ago=lg.CONFIRMATION_VALID_HOURS + 1)

        self.assertFalse(check.passed)
        self.assertIn("過期", check.detail)

    def test_a_future_dated_confirmation_is_rejected(self):
        check = self._with(hours_ago=-5)

        self.assertFalse(check.passed)

    def test_the_approved_amount_is_required(self):
        """「批准過一次」不等於「批准所有金額」。"""
        path = confirmation()
        with open(path, encoding="utf-8") as handle:
            data = json.loads(handle.read())
        del data["approved_notional_usdt"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        self.addCleanup(os.unlink, path)

        check = self.gate(confirmation_file=path).check_confirmation()

        self.assertFalse(check.passed)

    def test_an_oversized_amount_is_rejected(self):
        """第一次用真錢跑的規模應該小到虧光也不影響任何事。"""
        check = self._with(notional=lg.MAX_INITIAL_NOTIONAL_USDT + 1)

        self.assertFalse(check.passed)

    def test_a_malformed_file_is_rejected(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        handle.write("這不是 JSON")
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        check = self.gate(confirmation_file=handle.name).check_confirmation()

        self.assertFalse(check.passed)

    def test_the_confirmation_file_is_gitignored(self):
        """
        它是「這個人在這個時間點批准了這個金額」的憑證。
        提交進 git 等於把那個決定變成永久的、對所有人生效的。
        """
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".gitignore",
        )
        with open(path, encoding="utf-8") as handle:
            self.assertIn("live_gate_confirmation.json", handle.read())


class TestLiveTradingIsStillImpossible(GateTestCase):
    """
    閘門開啟不代表系統會下實單。這是刻意的:
    閘門是實單的前提,不是開關。
    """

    def test_there_is_no_live_broker(self):
        check = self.gate().check_live_broker_absent()

        self.assertTrue(check.passed)

    def test_an_open_gate_still_has_no_live_broker(self):
        result = self.open_gate()
        live_check = next(c for c in result.checks if c.name == "實單路徑")

        self.assertTrue(result.open)
        self.assertTrue(live_check.passed)
        self.assertIn("還不會下實單", live_check.detail)

    def test_a_live_broker_appearing_would_close_the_gate(self):
        from agmcis.execution import broker as broker_module

        with patch.object(broker_module, "LiveBroker", object, create=True):
            check = self.gate().check_live_broker_absent()

        self.assertFalse(check.passed)
        self.assertTrue(check.blocking)


class TestAudit(GateTestCase):

    def test_every_evaluation_is_recorded_including_failures(self):
        """
        只記錄通過的評估會讓稽核紀錄變成一份成功史。
        事後要回答「當時為什麼會放行」,需要看到它前面失敗了幾次。
        """
        audit = audit_path()
        self.addCleanup(os.unlink, audit)

        gate = lg.LiveGate(
            confirmation_file="/nonexistent.json",
            audit_file=audit,
            providers=passing_providers(),
        )
        gate.evaluate()
        gate.evaluate()

        with open(audit, encoding="utf-8") as handle:
            lines = [l for l in handle.read().strip().split("\n") if l]

        self.assertEqual(len(lines), 2)
        self.assertFalse(json.loads(lines[0])["open"])
        self.assertIn("人工確認", json.loads(lines[0])["failed"])


class TestIsLiveAllowed(GateTestCase):

    def test_it_is_false_by_default(self):
        audit = audit_path()
        self.addCleanup(os.unlink, audit)

        gate = lg.LiveGate(
            confirmation_file="/nonexistent.json",
            audit_file=audit, providers={},
        )

        self.assertFalse(lg.is_live_allowed(gate))


if __name__ == "__main__":
    unittest.main()
