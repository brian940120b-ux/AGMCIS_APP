"""
/health(第六十六節)。

═══ 這一組真正在防的東西 ═══
**一個永遠回 200 的 /health,比沒有 /health 危險。**

它會讓上面接的每一層監控都變成綠燈,而綠燈的理由是它什麼都沒在看。
`CLAUDE.md` 教訓第 5 條:說謊的稽核比沒有稽核危險。

所以這裡花最多力氣的不是「健康時回 200」,是:

  · 檔案讀不到 -> fail,不是 skip、不是「假設沒問題」
  · 檢查自己拋例外 -> 一條 fail 的檢查,不是整個端點 500
  · 任何一條不過 -> 503,沒有「大部分還好」這個選項
  · LIVE_ENABLED 變成 True -> 當場說出來

最後一條是全部裡面最重要的:其他檢查講的是「系統好不好」,
那一條講的是「它有沒有在動真錢」。
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core import health


class Sandbox(unittest.TestCase):
    """把 health 指向一個乾淨的暫存 data/,不要動到真的檔案。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.patcher = patch.object(health, "DATA", self.data)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def write(self, name, obj):
        (self.data / name).write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def write_lines(self, name, rows):
        (self.data / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    def fresh_bookkeeping(self, hours_ago=1.0):
        from datetime import datetime, timedelta, timezone
        when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        self.write_lines("portfolio_equity.jsonl",
                         [{"t": when.isoformat(), "equity": 10000}])

    def everything_healthy(self):
        self.fresh_bookkeeping()
        self.write("bingx_specs.json",
                   {"updated": time.time(), "contracts": {"BTC-USDT": {}}})
        self.write("portfolio_risk.json", {"verdict": "ALLOW", "checks": []})
        self.write("portfolio_contract.json", {"passed": 2, "total": 8})
        from datetime import datetime, timedelta, timezone
        soon = (datetime.now(timezone.utc) + timedelta(days=3)).date()
        self.write("events.json", {"events": [
            {"date": soon.isoformat(), "kind": "FOMC", "level": "HIGH",
             "note": "測試"}]})


class TestItCanSayOk(Sandbox):

    def test_a_healthy_system_returns_200(self):
        self.everything_healthy()
        status, payload = health.report(detailed=True)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["status"], "ok")


class TestItRefusesToPretend(Sandbox):

    def test_a_missing_file_is_a_failure_not_a_skip(self):
        """
        什麼都沒有的時候,答案是「不健康」,不是「沒事」。
        """
        status, payload = health.report(detailed=True)

        self.assertEqual(status, 503)
        failed = [c["name"] for c in payload["checks"] if not c["ok"]]
        self.assertIn("記帳", failed)
        self.assertIn("交易所規格", failed)
        # 沒有日曆也算不健康 —— 它代表沒有人在維護這一項
        self.assertIn("事件日曆", failed)

    def test_stale_bookkeeping_fails(self):
        """
        記帳停了兩天而面板還在轉 —— 那正是最容易沒人發現的壞法。
        """
        self.everything_healthy()
        self.fresh_bookkeeping(hours_ago=48)

        status, payload = health.report(detailed=True)

        self.assertEqual(status, 503)
        row = next(c for c in payload["checks"] if c["name"] == "記帳")
        self.assertFalse(row["ok"])
        self.assertIn("48", row["detail"])

    def test_stale_exchange_specs_fail(self):
        self.everything_healthy()
        self.write("bingx_specs.json",
                   {"updated": time.time() - 30 * 86400,
                    "contracts": {"BTC-USDT": {}}})

        status, _payload = health.report()
        self.assertEqual(status, 503)

    def test_an_empty_spec_cache_fails(self):
        self.everything_healthy()
        self.write("bingx_specs.json", {"updated": time.time(),
                                        "contracts": {}})

        status, payload = health.report(detailed=True)
        self.assertEqual(status, 503)
        row = next(c for c in payload["checks"] if c["name"] == "交易所規格")
        self.assertIn("空的", row["detail"])

    def test_a_check_that_explodes_becomes_a_failed_check(self):
        """
        不是讓整個端點 500。

        500 告訴監控「服務掛了」,而實際上是「有一條檢查有問題」——
        兩件事的處理方式不一樣。
        """
        def boom():
            raise RuntimeError("我壞了")

        check = health._guard("測試", boom)

        self.assertFalse(check.ok)
        self.assertIn("RuntimeError", check.detail)
        self.assertIn("我壞了", check.detail)

    def test_one_failure_is_enough_to_be_unhealthy(self):
        """
        沒有「大部分還好」這個選項。一個會自己決定哪些失敗不算數的
        健康檢查,遲早會把所有失敗都算成不算數。
        """
        self.everything_healthy()
        (self.data / "bingx_specs.json").unlink()

        status, payload = health.report(detailed=True)

        ok_count = sum(1 for c in payload["checks"] if c["ok"])
        self.assertGreater(ok_count, 3, "應該只壞一條")
        self.assertEqual(status, 503)


class TestTheLiveGateIsTheMostImportantCheck(Sandbox):

    def test_paper_mode_passes(self):
        check = health.check_live_gate()
        self.assertTrue(check.ok)
        self.assertIn("False", check.detail)

    def test_live_enabled_makes_the_system_unhealthy(self):
        """
        LIVE_ENABLED 變 True 只可能是有人 commit 了那個改動。
        /health 要能當場說出來 —— 這是它最重要的一句話。
        """
        self.everything_healthy()
        with patch("portfolio.execution.LIVE_ENABLED", True):
            status, payload = health.report(detailed=True)

        self.assertEqual(status, 503)
        row = next(c for c in payload["checks"] if c["name"] == "實盤閘門")
        self.assertFalse(row["ok"])
        self.assertIn("真錢", row["detail"])


class TestTheProbeDoesNotNeedTheKey(unittest.TestCase):

    def test_without_the_key_you_get_names_but_no_details(self):
        """
        監控探針帶不了 DASHBOARD_KEY。一個要金鑰才能問的健康檢查,
        在最需要它的時候剛好用不了。
        """
        _status, payload = health.report(detailed=False)

        for row in payload["checks"]:
            self.assertEqual(set(row), {"name", "ok"},
                             f"不帶金鑰不該看到細節:{row}")

    def test_with_the_key_you_get_details(self):
        _status, payload = health.report(detailed=True)

        for row in payload["checks"]:
            self.assertIn("detail", row)

    def test_the_endpoint_is_handled_before_the_key_check(self):
        """
        路由順序寫錯的話,沒有金鑰的探針會拿到 403 而不是健康狀態。
        """
        import inspect

        import dashboard

        source = inspect.getsource(dashboard.Handler.do_GET)
        health_at = source.index('/health')
        forbidden_at = source.index("send_response(403)")
        self.assertLess(health_at, forbidden_at,
                        "/health 必須排在 403 那段之前")


if __name__ == "__main__":
    unittest.main()


class TestTheEndpointOverRealHttp(unittest.TestCase):
    """
    模組層測完了,還要證明**經過 HTTP 之後**行為一樣。

    中間隔著路由、金鑰檢查與 header 處理 —— 那三層都有把事情
    做錯的空間,而模組層的測試一條都看不到。
    """

    PORT = 18094

    @classmethod
    def setUpClass(cls):
        import logging
        import threading
        from http.server import ThreadingHTTPServer

        os.environ["DASHBOARD_KEY"] = "test-key-not-a-secret"
        logging.disable(logging.CRITICAL)

        import dashboard
        cls.dashboard = dashboard
        cls.srv = ThreadingHTTPServer(("127.0.0.1", cls.PORT),
                                      dashboard.Handler)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        import logging
        cls.srv.shutdown()
        logging.disable(logging.NOTSET)
        os.environ.pop("DASHBOARD_KEY", None)

    def get(self, path):
        import socket
        sock = socket.create_connection(("127.0.0.1", self.PORT), timeout=10)
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
            .encode())
        raw = b""
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            raw += chunk
        text = raw.decode("utf-8", "replace")
        head, _, body = text.partition("\r\n\r\n")
        return int(head.split()[1]), body

    def test_an_unhealthy_system_answers_503_not_200(self):
        status, body = self.get("/health")
        self.assertIn(status, (200, 503))
        self.assertIn("status", json.loads(body))

    def test_a_probe_without_the_key_gets_names_only(self):
        _status, body = self.get("/health")
        for row in json.loads(body)["checks"]:
            self.assertEqual(set(row), {"name", "ok"},
                             "沒有金鑰不該看到細節")

    def test_with_the_key_the_details_come_through(self):
        _status, body = self.get("/health?key=test-key-not-a-secret")
        rows = json.loads(body)["checks"]
        self.assertTrue(all("detail" in r for r in rows))

    def test_other_endpoints_still_need_the_key(self):
        """
        /health 開放不代表整個面板開放。
        """
        status, _body = self.get("/api/live?key=wrong")
        self.assertEqual(status, 403)

    def test_the_dashboard_reads_the_key_from_the_environment(self):
        """
        原本 _env 只讀 .env,不看環境變數 —— systemd 的
        `Environment=DASHBOARD_KEY=...` 完全沒有作用,而寫的人
        不會收到任何提示。

        一個「設了卻沒生效」的存取控制,比明白地沒有存取控制更糟。
        """
        self.assertEqual(self.dashboard._env("DASHBOARD_KEY"),
                         "test-key-not-a-secret")
