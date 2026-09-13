"""
限流器(第四十八節)。

═══ 最重要的一組是最後那個 class ═══
一個可以繞過的限流器,只是一個讓人放心的裝飾品。這個專案有
**十一個地方**直接對 BingX 發請求;只要有一個沒走限流器,
「限流」就只是一個名詞。

所以最後那組掃 AST,擋住新的繞道。它擋的不是今天的程式碼
(今天的已經改完了),是三個月後某人為了趕快看一個數字
而多加的那一行。

═══ 跨行程 ═══
daily / sentinel / dashboard / gauge 是四支獨立的 systemd 服務。
任何寫在單一行程記憶體裡的限流器,在這裡等於沒有限流器 ——
被限流的是我們這個 IP,不是某一支腳本。

所以有一條測試真的開子行程來驗。
"""
import ast
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import ratelimit


class Reset(unittest.TestCase):
    """每條測試都從一個滿的桶開始,否則彼此會互相影響。"""

    def setUp(self):
        ratelimit.LOCK.parent.mkdir(parents=True, exist_ok=True)
        ratelimit.LOCK.write_text(
            json.dumps(ratelimit.Bucket.fresh().to_dict()), encoding="utf-8")

    def tearDown(self):
        ratelimit.LOCK.unlink(missing_ok=True)


class TestTheBucketBehaves(Reset):

    def test_a_burst_passes_without_waiting(self):
        started = time.time()
        for _ in range(int(ratelimit.BURST)):
            ratelimit.acquire()
        self.assertLess(time.time() - started, 0.5)

    def test_past_the_burst_it_actually_slows_down(self):
        """
        這條要是綠得太快,代表限流器沒有在限流。
        """
        for _ in range(int(ratelimit.BURST)):
            ratelimit.acquire()

        started = time.time()
        for _ in range(3):
            ratelimit.acquire()
        elapsed = time.time() - started

        expected = 3 / ratelimit.RATE_PER_S
        self.assertGreater(elapsed, expected * 0.7,
                           "超過突發額度之後沒有慢下來")

    def test_waiting_too_long_raises_instead_of_hanging(self):
        """
        會無限期卡住的限流器,會讓記帳腳本掛在那裡而沒有人知道。
        超時是一個要被看見的事件(第九十四條)。
        """
        ratelimit.penalise(600.0, "測試")

        with self.assertRaises(ratelimit.WaitedTooLong):
            ratelimit.acquire(max_wait_s=0.3)


class TestItListensWhenTheExchangeSaysSlowDown(Reset):

    def test_429_empties_the_bucket_and_raises(self):
        class Headers:
            @staticmethod
            def get(_key):
                return "7"

        with self.assertRaises(ratelimit.RateLimited) as caught:
            ratelimit.check_response(429, Headers(), "/quote/ticker")

        self.assertEqual(caught.exception.retry_after_s, 7.0)
        self.assertTrue(ratelimit.state()["blocked"])

    def test_429_without_a_retry_after_still_backs_off(self):
        """
        「沒告訴我等多久」不等於「可以馬上再來」。
        退避 0 是把交易所的話當成沒說。
        """
        class Headers:
            @staticmethod
            def get(_key):
                return None

        with self.assertRaises(ratelimit.RateLimited) as caught:
            ratelimit.check_response(429, Headers(), "")

        self.assertGreater(caught.exception.retry_after_s, 0)
        self.assertTrue(ratelimit.state()["blocked"])

    def test_418_is_a_ban_and_says_so(self):
        with self.assertRaises(ratelimit.Banned):
            ratelimit.check_response(418, None, "/quote/ticker")

    def test_a_normal_response_does_nothing(self):
        ratelimit.check_response(200, None, "")
        self.assertFalse(ratelimit.state()["blocked"])

    def test_the_client_does_not_retry_a_429(self):
        """
        `bingx_client._get` 原本會對 429 重試三次、退避 0.5s/1s/2s。
        被限流的正確反應是慢下來,那個寫法是加速撞牆。
        """
        import inspect

        from market_data import bingx_client

        source = inspect.getsource(bingx_client.BingXClient._get)
        raise_at = source.index("raise\n")
        retry_at = source.index("requests.RequestException")
        self.assertLess(raise_at, retry_at,
                        "429 / 418 的 except 必須排在重試那條之前")


class TestItWorksAcrossProcesses(Reset):
    """
    四支 systemd 服務共用的只有磁碟。
    """

    def test_a_second_process_sees_the_same_bucket(self):
        for _ in range(int(ratelimit.BURST)):
            ratelimit.acquire()

        # 這一個行程已經把桶抽乾了。另一個行程不該還有滿的額度。
        code = (
            "import json,sys,time;"
            f"sys.path.insert(0, {str(ROOT)!r});"
            "from core import ratelimit;"
            "t=time.time();ratelimit.acquire();"
            "print(time.time()-t)"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)

        waited = float(out.stdout.strip().splitlines()[-1])
        self.assertGreater(
            waited, 0.05,
            "另一個行程拿到令牌完全沒等 —— 限流器不是跨行程的,"
            "四支 systemd 服務會各衝各的")

    def test_a_penalty_from_one_process_stops_the_others(self):
        """被限流的是這個 IP,不是某一支腳本。"""
        code = (
            "import sys;"
            f"sys.path.insert(0, {str(ROOT)!r});"
            "from core import ratelimit;"
            "ratelimit.penalise(600.0, '子行程測試')"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)

        with self.assertRaises(ratelimit.WaitedTooLong):
            ratelimit.acquire(max_wait_s=0.3)


class TestNothingBypassesTheLimiter(unittest.TestCase):
    """
    ⚠️ 這是這個檔案裡最重要的一組。

    限流器擋不住「沒有走限流器的那一行」。這裡掃整個 repo 的 AST,
    找直接呼叫 `urllib.request.urlopen` 或 `requests.get` /
    `session.get` 的地方。
    """

    # 只有限流器自己可以直接發請求。
    ALLOWED_FILES = {"core/ratelimit.py"}

    BANNED = {
        ("urllib", "request", "urlopen"),
        ("requests", "get"),
        ("requests", "post"),
    }

    def raw_http_calls(self):
        found = []

        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d != "__pycache__"]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = Path(dirpath) / filename
                rel = path.relative_to(ROOT).as_posix()
                if rel in self.ALLOWED_FILES or rel.startswith("tests/"):
                    continue

                tree = ast.parse(path.read_text(encoding="utf-8"),
                                 filename=str(path))

                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    parts = self.dotted(node.func)
                    if parts is None:
                        continue
                    if tuple(parts) in self.BANNED:
                        found.append(f"{rel}:{node.lineno} {'.'.join(parts)}")
                    # `session.get(...)` / `self.session.get(...)`
                    elif len(parts) >= 2 and parts[-2:] == ["session", "get"]:
                        found.append(f"{rel}:{node.lineno} {'.'.join(parts)}")

        return sorted(found)

    @staticmethod
    def dotted(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return list(reversed(parts))
        return None

    def test_no_module_calls_http_directly(self):
        found = self.raw_http_calls()

        self.assertEqual(
            found, [],
            "這些地方直接發 HTTP,繞過了限流器:\n  "
            + "\n  ".join(found)
            + "\n\n改用 core.ratelimit.urlopen 或 core.ratelimit.requests_get。"
            "\n一個可以繞過的限流器,只是一個讓人放心的裝飾品。",
        )

    def test_the_scan_would_actually_catch_one(self):
        """
        掃描器自己要先證明它抓得到東西 —— 否則上面那條永遠是綠的,
        而綠的理由是它什麼都沒在看(教訓第 5 條)。
        """
        scratch = ROOT / "portfolio" / "_tmp_bypass.py"
        scratch.write_text(
            "import urllib.request\n"
            "def go():\n"
            "    return urllib.request.urlopen('https://example.com')\n",
            encoding="utf-8")
        try:
            found = self.raw_http_calls()
            self.assertTrue(
                any("_tmp_bypass.py" in f for f in found),
                f"掃描器沒抓到一個明顯的繞道:{found}")
        finally:
            scratch.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
