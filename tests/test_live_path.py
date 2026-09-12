"""
實單路徑的人工審視簽章(第七十八 / 一百零二節)。

這一組驗的是同一件事的三個面向:

  1. 沒簽 → 不通過
  2. 簽了但程式碼改過 → 不通過(簽的是那一份原始碼)
  3. 多了一個沒簽過的實單檔案 → 不通過

三個都不通過,而且**都是往關閉的方向倒**。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.safety import live_path


class LivePathTestCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, relative, text):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def found(self, *relatives):
        return [(r, "LiveBroker", "類別名稱含 live") for r in relatives]


class TestNothingToSign(LivePathTestCase):

    def test_no_live_code_needs_no_signature(self):
        ok, detail = live_path.verify([], {}, root=self.root)

        self.assertTrue(ok)
        self.assertIn("沒有實單程式碼", detail)


class TestSignatureIsRequired(LivePathTestCase):

    def test_live_code_without_a_signature_fails(self):
        self.write("a/live_broker.py", "class LiveBroker: pass\n")

        ok, detail = live_path.verify(
            self.found("a/live_broker.py"), {}, root=self.root,
        )

        self.assertFalse(ok)
        self.assertIn(live_path.REVIEW_KEY, detail)

    def test_an_empty_signature_is_the_same_as_none(self):
        """
        空的 dict 不是「簽了但沒有東西要簽」——
        有實單程式碼的時候它就是「沒簽」。
        """
        self.write("a/live_broker.py", "class LiveBroker: pass\n")

        ok, _ = live_path.verify(
            self.found("a/live_broker.py"),
            {live_path.REVIEW_KEY: {}},
            root=self.root,
        )

        self.assertFalse(ok)

    def test_a_signature_of_the_wrong_type_is_rejected(self):
        """
        字串、清單、True 都不是簽章。看不懂的輸入一律算沒通過。
        """
        self.write("a/live_broker.py", "class LiveBroker: pass\n")

        for value in ("yes", ["a/live_broker.py"], True, 1):
            with self.subTest(value=value):
                ok, _ = live_path.verify(
                    self.found("a/live_broker.py"),
                    {live_path.REVIEW_KEY: value},
                    root=self.root,
                )
                self.assertFalse(ok)


class TestSignatureBindsToTheSource(LivePathTestCase):

    def test_a_matching_signature_passes(self):
        self.write("a/live_broker.py", "class LiveBroker: pass\n")
        found = self.found("a/live_broker.py")

        ok, detail = live_path.verify(
            found,
            {live_path.REVIEW_KEY: live_path.build_signature(
                found, root=self.root,
            )},
            root=self.root,
        )

        self.assertTrue(ok, detail)

    def test_changing_one_character_invalidates_it(self):
        """
        簽的是**當時那一份原始碼**,不是「以後所有的版本」。
        這與第九十二節的設定指紋是同一條原則。
        """
        self.write("a/live_broker.py", "class LiveBroker: pass\n")
        found = self.found("a/live_broker.py")
        signature = live_path.build_signature(found, root=self.root)

        self.write("a/live_broker.py", "class LiveBroker: pass  #\n")

        ok, detail = live_path.verify(
            found, {live_path.REVIEW_KEY: signature}, root=self.root,
        )

        self.assertFalse(ok)
        self.assertIn("被改過", detail)

    def test_a_new_unsigned_file_invalidates_it(self):
        """
        簽了一個檔案,不等於簽了之後多出來的那一個。
        """
        self.write("a/live_broker.py", "class LiveBroker: pass\n")
        signed = live_path.build_signature(
            self.found("a/live_broker.py"), root=self.root,
        )

        self.write("a/live_orders.py", "class LiveSender: pass\n")

        ok, detail = live_path.verify(
            self.found("a/live_broker.py", "a/live_orders.py"),
            {live_path.REVIEW_KEY: signed},
            root=self.root,
        )

        self.assertFalse(ok)
        self.assertIn("a/live_orders.py", detail)

    def test_a_file_that_cannot_be_read_is_not_passed(self):
        """讀不到不等於沒問題。"""
        ok, detail = live_path.verify(
            self.found("a/gone.py"),
            {live_path.REVIEW_KEY: {"a/gone.py": "0" * 64}},
            root=self.root,
        )

        self.assertFalse(ok)
        self.assertIn("讀不到", detail)


class TestDigest(LivePathTestCase):

    def test_the_digest_is_the_sha256_of_the_bytes(self):
        """
        雜湊要是**位元組**的雜湊,不是解碼後的字串 ——
        換行方式或編碼被改掉也要看得出來。
        """
        path = self.write("a.py", "print('中文')\n")

        with open(path, "rb") as handle:
            expected = hashlib.sha256(handle.read()).hexdigest()

        self.assertEqual(live_path.digest(path), expected)


class TestTheRealRepository(unittest.TestCase):
    """
    掃描器與簽章器在**真的這個專案上**要接得起來。
    用假目錄測完卻在真專案上炸掉,是這一組要擋的事。
    """

    def test_the_scan_and_the_signature_agree(self):
        from agmcis.safety.live_gate import LiveGate

        found = LiveGate()._scan_live_broker_sources()
        signature = live_path.build_signature(found)

        self.assertEqual(
            sorted(signature), sorted({w for w, _n, _y in found}),
        )

        ok, detail = live_path.verify(
            found, {live_path.REVIEW_KEY: signature},
        )
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
