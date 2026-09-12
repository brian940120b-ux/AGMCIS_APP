"""
/health(Master Prompt 第六十六節)。

這個端點與 /api/system_health 有一個關鍵差異:**它不需要 API 金鑰。**
健康檢查是給外部監控用的 —— uptime 監測、負載平衡器、systemd 看門狗
都不會帶金鑰。一個需要金鑰才打得到的健康端點等於沒有健康端點。

所以這組測試有兩個重點:
  一、真的不需要金鑰(而且不會因此洩漏任何東西)。
  二、自己壞掉的時候回報不健康,不是回報健康。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import health


def all_ok():
    """把每一項探針都換成「正常」。"""
    return {
        "_database": lambda: True,
        "_bingx": lambda: True,
        "_market_data": lambda: True,
        "_risk_engine": lambda: True,
        "_trading_engine": lambda: True,
        "_agent_engine": lambda: True,
        "_scheduler": lambda: True,
    }


def build(**overrides):
    probes = all_ok()
    probes.update(overrides)

    patches = [patch.object(health, name, probe) for name, probe in probes.items()]
    for p in patches:
        p.start()
    try:
        return health.build_health()
    finally:
        for p in patches:
            p.stop()


class TestTheHappyPath(unittest.TestCase):

    def test_everything_up_is_healthy(self):
        payload = build()

        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["failed"], [])

    def test_every_component_the_master_prompt_asks_for_is_present(self):
        components = build()["components"]

        for name in ("api", "database", "bingx", "market_data",
                     "websocket", "trading_engine", "risk_engine",
                     "agent_engine"):
            self.assertIn(name, components)

    def test_the_trading_mode_is_reported(self):
        """從外面最常需要確認的一件事,而且它不是秘密。"""
        self.assertIn(build()["trading_mode"], ("paper", "test", "live", "manual"))


class TestFailuresDefaultToUnhealthy(unittest.TestCase):
    """
    一個在自己壞掉時回報健康的健康檢查,比沒有健康檢查更糟 ——
    它會讓監控系統安靜下來。
    """

    def test_a_probe_that_raises_counts_as_error(self):
        def explode():
            raise RuntimeError("連不上")

        payload = build(_database=explode)

        self.assertEqual(payload["components"]["database"], "error")

    def test_a_probe_that_returns_false_counts_as_error(self):
        payload = build(_bingx=lambda: False)

        self.assertEqual(payload["components"]["bingx"], "error")

    def test_a_database_failure_is_unhealthy_not_degraded(self):
        """資料庫掛掉就不能交易。那不是降級,是壞掉。"""
        payload = build(_database=lambda: False)

        self.assertEqual(payload["status"], "unhealthy")
        self.assertIn("database", payload["critical_failed"])

    def test_a_market_data_failure_is_only_degraded(self):
        """
        行情拿不到時系統不會下單(資料品質閘門會擋),
        但既有部位的停損仍然有效。那是降級,不是全壞。
        """
        payload = build(_market_data=lambda: False)

        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["critical_failed"], [])

    def test_a_scheduler_failure_is_degraded(self):
        payload = build(_scheduler=lambda: False)

        self.assertEqual(payload["status"], "degraded")
        self.assertIn("scheduler", payload["failed"])

    def test_the_risk_engine_is_critical(self):
        payload = build(_risk_engine=lambda: False)

        self.assertEqual(payload["status"], "unhealthy")


class TestTheSchedulerFreshnessCheck(unittest.TestCase):
    """
    程序還在但工作卡住的情況,用「程序在不在」看不出來。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="agmcis-test-health-")
        self.path = os.path.join(self.dir, "scheduler_status.json")

    def _write(self, payload):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def _check(self):
        from agmcis.config import settings
        with patch.object(settings, "SCHEDULER_STATUS_FILE", self.path):
            return health._scheduler()

    def test_a_fresh_status_file_is_healthy(self):
        self._write({"updated_at_epoch": time.time()})
        self.assertTrue(self._check())

    def test_a_stale_status_file_is_not(self):
        self._write({"updated_at_epoch": time.time() - 3600})
        self.assertFalse(self._check())

    def test_a_missing_file_is_not_healthy(self):
        self.assertFalse(self._check())

    def test_a_file_without_a_readable_timestamp_is_not_healthy(self):
        """說不出是新是舊,一律算不健康。"""
        self._write({"jobs": []})
        self.assertFalse(self._check())


class TestTheWebsocketIsDisabledNotBroken(unittest.TestCase):

    def test_an_absent_websocket_does_not_count_as_a_failure(self):
        """
        WebSocket 目前只用於 Dashboard 推播,不是行情來源。
        回 error 會讓監控以為有東西壞了。
        """
        payload = build()

        self.assertNotIn("websocket", payload["failed"])
        self.assertIn(payload["components"]["websocket"], ("ok", "disabled"))


class TestItIsReachableWithoutAKey(unittest.TestCase):

    def _client(self):
        from fastapi.testclient import TestClient

        import main
        return TestClient(main.app)

    def test_health_answers_without_an_api_key(self):
        response = self._client().get("/health")

        self.assertIn(response.status_code, (200, 503))
        self.assertIn("status", response.json())

    def test_the_protected_endpoints_still_need_one(self):
        """
        前提檢查:沒有這一條,上面那個測試也可能只是因為金鑰驗證整個沒開。

        401(金鑰錯)與 503(系統沒設定金鑰)都算擋住了;
        200 才是問題。
        """
        response = self._client().get("/api/system_health")

        self.assertIn(response.status_code, (401, 503))

    def test_it_leaks_no_error_details(self):
        """
        不需要金鑰就代表任何人都看得到。錯誤訊息裡常常有主機名稱、
        連線字串、API 回應內容 —— 那些一項都不能出現。
        """
        def explode():
            raise RuntimeError(
                "could not connect to server at db.internal:5432 user=agmcis"
            )

        with patch.object(health, "_database", explode), \
             patch.object(health, "logger"):
            body = json.dumps(health.build_health(), ensure_ascii=False)

        self.assertNotIn("db.internal", body)
        self.assertNotIn("5432", body)
        self.assertNotIn("agmcis", body)

    def test_an_unhealthy_system_returns_503(self):
        """
        監控系統看的是狀態碼,不是 JSON 內容。
        一個永遠回 200 的健康端點對負載平衡器而言等於永遠健康。
        """
        with patch.object(health, "build_health",
                          return_value={"status": "unhealthy", "components": {}}):
            response = self._client().get("/health")

        self.assertEqual(response.status_code, 503)

    def test_a_degraded_system_still_returns_200(self):
        with patch.object(health, "build_health",
                          return_value={"status": "degraded", "components": {}}):
            response = self._client().get("/health")

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()


class TestAuditCoversTheRestOfSectionSixtyFive(unittest.TestCase):
    """
    第六十五節列了 Login / API Change / Mode Change / Strategy Change /
    Risk Change / Order / Cancel / Close / Emergency。

    這組測試確認前四項真的會產生稽核 —— 不是「有一個稽核函式」,
    是「那條路徑上真的呼叫了它」。
    """

    def test_login_is_audited(self):
        import web_auth

        written = []
        with patch("agmcis.review.decision_log.audit",
                   lambda *a, **k: written.append((a, k))):
            web_auth.redirect_with_session("/", "secret-key", request=None)

        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][0][0], "LOGIN")

    def test_the_login_audit_never_carries_the_key(self):
        """
        第十節:API Key 不進 Database。「只記前四碼」的折衷在這裡
        沒有價值,卻讓金鑰有了第二個存在的地方。
        """
        import web_auth

        written = []
        with patch("agmcis.review.decision_log.audit",
                   lambda *a, **k: written.append((a, k))):
            web_auth.redirect_with_session("/", "super-secret-key", request=None)

        blob = json.dumps(written, default=str)
        self.assertNotIn("super-secret-key", blob)
        self.assertNotIn("super", blob)

    def test_a_failing_audit_does_not_block_login(self):
        """這一層是觀測,不是門禁。"""
        import web_auth

        def explode(*args, **kwargs):
            raise RuntimeError("資料庫掛了")

        with patch("agmcis.review.decision_log.audit", explode), \
             patch.object(web_auth, "logger"):
            response = web_auth.redirect_with_session("/", "k", request=None)

        self.assertEqual(response.status_code, 303)

    def test_pausing_the_system_is_audited(self):
        import system_control

        written = []
        with patch("agmcis.review.decision_log.audit",
                   lambda *a, **k: written.append((a, k))), \
             patch.object(system_control.Path, "write_text", lambda *a: None), \
             patch.object(system_control, "get_mode", lambda: "RUNNING"):
            system_control.pause_system(actor="tester", reason="測試")

        self.assertEqual(written[0][0][0], "SYSTEM_PAUSE")
        self.assertEqual(written[0][1]["after"], "PAUSED")

    def test_resuming_is_audited_too(self):
        """
        被暫停的系統被誰、在什麼時候恢復,是事後檢討最需要知道的一件事。
        """
        import system_control

        written = []
        with patch("agmcis.review.decision_log.audit",
                   lambda *a, **k: written.append((a, k))), \
             patch.object(system_control.Path, "write_text", lambda *a: None), \
             patch.object(system_control, "get_mode", lambda: "PAUSED"):
            system_control.resume_system(actor="tester")

        self.assertEqual(written[0][0][0], "SYSTEM_RESUME")

    def test_an_unreadable_mode_file_counts_as_paused(self):
        """
        反過來(讀不到就當成執行中)會讓一次檔案系統問題變成
        「系統在沒有人知道的情況下繼續交易」。
        """
        import system_control

        with patch.object(system_control.Path, "exists", lambda self: True), \
             patch.object(system_control.Path, "read_text",
                          lambda self: (_ for _ in ()).throw(OSError("壞了"))), \
             patch.object(system_control, "logger"):
            self.assertEqual(system_control.get_mode(), "PAUSED")

    def test_a_strategy_status_change_is_audited(self):
        import tempfile as tf

        from agmcis.core.enums import StrategyStatus
        from agmcis.strategy.health import StatusStore

        folder = tf.mkdtemp(prefix="agmcis-test-audit-")
        store = StatusStore(
            path=os.path.join(folder, "s.json"),
            audit_path=os.path.join(folder, "a.log"),
        )

        written = []
        with patch("agmcis.review.decision_log.audit",
                   lambda *a, **k: written.append((a, k))):
            store.set("trend", StrategyStatus.PAUSED, reason="回撤超限")

        self.assertEqual(written[0][0][0], "STRATEGY_STATUS_CHANGE")
        self.assertEqual(written[0][1]["after"], "paused")
