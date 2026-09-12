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
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.safety import live_gate as lg
from agmcis.safety import live_path


def audit_path():
    handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    handle.close()
    return handle.name


def confirmation(phrase=None, hours_ago=0.0, notional=20.0, **overrides):
    """
    一份完整的確認檔。

    第九十二節的七項確認與設定指紋一併帶上 —— 少任何一項閘門都會關,
    而那正是 test_confirmations 那一組在驗的事。
    """
    from agmcis.safety import live_confirm

    data = {
        "phrase": phrase if phrase is not None else lg.REQUIRED_PHRASE,
        "signed_at": (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).isoformat(),
        "approved_notional_usdt": notional,
        "confirmations": {name: True for name in live_confirm.REQUIRED_ITEMS},
        "settings_fingerprint": live_confirm.fingerprint(),
        # 實單原始碼的簽章。系統裡有 LiveBroker 的時候少了它閘門會關,
        # 而那正是 TestLiveTradingIsStillImpossible 那一組在驗的事。
        live_path.REVIEW_KEY: live_path.build_signature(
            lg.LiveGate()._scan_live_broker_sources()
        ),
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

    @staticmethod
    def as_production():
        """
        實單閘門要求 APP_ENV=production(第八十一節)。
        測試環境是 development,所以要開閘門的測試必須明講這件事。
        """
        from unittest.mock import patch

        from agmcis.config import settings

        return patch.object(settings, "APP_ENV", settings.ENV_PRODUCTION)

    def open_gate(self, **overrides):
        # 閘門要求 APP_ENV=production(第八十一節)。測試環境是
        # development,所以要驗「閘門能開」就必須明講這件事 ——
        # 那本身也是一個在記錄「實單只能在正式環境跑」的斷言。
        #
        # 確認檔也要在同一個 context 裡建立:設定指紋含 APP_ENV,
        # 而在 development 簽的名不該在 production 生效。
        with self.as_production():
            path = confirmation()
            self.addCleanup(os.unlink, path)

            return self.gate(
                providers=passing_providers(**overrides),
                confirmation_file=path,
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

    def test_unsigned_live_code_closes_the_gate(self):
        """
        沒有簽章時,實單程式碼的存在本身就把閘門關上。
        `self.gate()` 用的是一個不存在的確認檔。
        """
        check = self.gate().check_live_broker_absent()
        found = lg.LiveGate()._scan_live_broker_sources()

        if not found:
            self.assertTrue(check.passed)
            self.assertIn("沒有 LiveBroker", check.detail)
            return

        self.assertFalse(check.passed)
        self.assertIn(live_path.REVIEW_KEY, check.detail)

    def test_an_open_gate_has_the_live_path_signed(self):
        """
        閘門開著的時候,實單路徑要嘛不存在,要嘛已經被人逐檔簽過。
        沒有第三種。
        """
        result = self.open_gate()
        live_check = next(c for c in result.checks if c.name == "實單路徑")

        self.assertTrue(result.open)
        self.assertTrue(live_check.passed)
        self.assertTrue(
            "沒有 LiveBroker" in live_check.detail
            or "人工審視並簽章" in live_check.detail,
            live_check.detail,
        )

    def scan(self, filename, source):
        """把一段程式碼放進一個假的 execution 套件,讓閘門去掃它。"""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        with open(os.path.join(directory, filename), "w", encoding="utf-8") as handle:
            handle.write(source)

        gate = self.gate()
        with patch.object(lg.LiveGate, "LIVE_PATH_ROOTS", (directory,)):
            return gate.check_live_broker_absent()

    def test_a_live_broker_appearing_would_close_the_gate(self):
        check = self.scan("broker.py", "class LiveBroker:\n    pass\n")

        self.assertFalse(check.passed)
        self.assertTrue(check.blocking)

    def test_a_live_broker_in_another_file_is_also_caught(self):
        """
        這是一個真的洞的回歸測試。

        第一版只 import agmcis/execution/broker.py 然後看 dir() ——
        所以 agmcis/execution/live_broker.py 裡的 LiveBroker 完全看不到。
        我實際建過那個檔案,閘門照樣回報「沒有 LiveBroker」。
        """
        check = self.scan(
            "live_broker.py",
            "class LiveBroker:\n    is_live = True\n",
        )

        self.assertFalse(check.passed)
        self.assertIn("live_broker.py", check.detail)

    def test_a_class_that_hides_the_word_live_is_still_caught(self):
        """
        名字裡沒有 live 但自稱 is_live = True 的類別一樣要抓到。
        改個名字就能繞過的檢查等於沒有檢查。
        """
        check = self.scan(
            "real_orders.py",
            "class OrderSender:\n    is_live = True\n",
        )

        self.assertFalse(check.passed)
        self.assertIn("real_orders.py", check.detail)

    def test_a_paper_broker_does_not_trip_it(self):
        check = self.scan(
            "paper.py",
            "class PaperBroker:\n    is_live = False\n",
        )

        self.assertTrue(check.passed)

    def test_it_reads_the_source_instead_of_importing_it(self):
        """
        一個還沒被審視過的實單模組不該被執行才對。
        所以掃描讀原始碼 —— 就算模組 import 會炸,它照樣被看見。
        """
        check = self.scan(
            "explodes.py",
            "raise RuntimeError('這個模組 import 就會炸')\n\n"
            "class LiveBroker:\n    pass\n",
        )

        self.assertFalse(check.passed)

    def test_a_directory_it_cannot_scan_counts_as_not_passed(self):
        """「掃不動」不等於「沒有」。"""
        gate = self.gate()
        with patch.object(
            lg.LiveGate, "LIVE_PATH_ROOTS", ("/nonexistent-execution-package",)
        ):
            check = gate.check_live_broker_absent()

        self.assertFalse(check.passed)
        self.assertTrue(check.blocking)

    def test_it_scans_the_whole_execution_package(self):
        """設定本身要對:掃的是整個套件,不是單一檔案。"""
        for relative in lg.LiveGate.LIVE_PATH_ROOTS:
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                relative,
            )
            self.assertTrue(os.path.isdir(path), relative)


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


class TestTheEnvironmentGate(GateTestCase):
    """
    一台標成 development 的機器送真實訂單,代表有人把設定搬錯了 ——
    而那通常也代表資料庫、API 金鑰或風控參數有一項是錯的。
    """

    def test_development_cannot_open_the_gate(self):
        from agmcis.config import settings

        with patch.object(settings, "APP_ENV", settings.ENV_DEVELOPMENT):
            check = self.gate().check_environment()

        self.assertFalse(check.passed)
        self.assertIn("development", check.detail)

    def test_staging_cannot_open_the_gate_either(self):
        """staging 的設定與 production 相同,但帳戶不同。"""
        from agmcis.config import settings

        with patch.object(settings, "APP_ENV", settings.ENV_STAGING):
            self.assertFalse(self.gate().check_environment().passed)

    def test_production_passes(self):
        with self.as_production():
            check = self.gate().check_environment()

        self.assertTrue(check.passed)

    def test_an_unrecognised_env_name_falls_back_to_development(self):
        """
        當成 production 會讓一個打錯字的環境變數解鎖正式環境的行為;
        拋例外會讓系統在可以繼續跑的情況下起不來。
        """
        from agmcis.config import settings

        self.assertEqual(
            settings._normalise_env("prodution"), settings.ENV_DEVELOPMENT,
        )
        self.assertEqual(
            settings._normalise_env(None), settings.ENV_DEVELOPMENT,
        )

    def test_legacy_names_still_work(self):
        """"dev" 一直是預設值,直接改掉會讓現有部署變成未知環境。"""
        from agmcis.config import settings

        self.assertEqual(settings._normalise_env("dev"), settings.ENV_DEVELOPMENT)
        self.assertEqual(settings._normalise_env("prod"), settings.ENV_PRODUCTION)
