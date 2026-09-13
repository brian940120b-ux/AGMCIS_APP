"""
資金費歷史要涵蓋「今天可能會用到」的每一個幣。

═══ 2026-09-13 的事故 ═══
daily.py 只更新交易池那 7 個幣的資金費歷史,而當時還有一個跑動態
交易池的測試組。它挑進 1000PEPE-USDT —— 沒有人抓過那個幣的歷史。

    SpecMissing: 沒有 1000PEPE-USDT 的資金費率歷史 —— 不猜一個數字

那個例外**是對的**:用不完整的資料記帳會靜靜少收資金費、美化績效。
錯的是沒有人去抓那個幣的歷史。

結果:測試組整個 tick 死掉,連續 73.7 小時沒有記帳,而它帳上有 23 個
持倉。巡檢每十分鐘報一次,報了三天。

═══ 測試組退役之後,這一組還在防什麼 ═══
**「帳上握著的」不等於「交易池裡的」。**

一個幣被下架、或被移出交易池之後,倉不會瞬間消失 —— 它還要收資金費,
直到真的被平掉。只抓交易池會漏掉每一個**正在出場**的倉,
而那正是最不該漏的時候:出場那筆的成本算錯,直接錯在已實現損益上。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import daily


class Account:
    def __init__(self, symbols):
        self.positions = {s: object() for s in symbols}


class TestTheFundingListCoversEverythingInPlay(unittest.TestCase):

    def collect(self, held=(), load_raises=None):
        def fake_load(path):
            if load_raises:
                raise load_raises
            return Account(held)

        import portfolio.account as account_mod

        with patch.object(account_mod.Account, "load",
                          staticmethod(fake_load)):
            return daily.funding_symbols()

    def test_it_includes_the_main_seven(self):
        from portfolio.paper import SYMBOLS

        got = self.collect()

        for symbol in SYMBOLS:
            self.assertIn(symbol, got)

    def test_it_includes_a_coin_held_but_no_longer_in_the_pool(self):
        """
        ⚠️ 這是這一組真正的重點。

        一個幣被下架或移出交易池之後,倉不會瞬間消失 —— 它還要收
        資金費直到真的被平掉。只抓交易池會漏掉正在出場的那一筆,
        而那筆的成本算錯會直接錯在已實現損益上。
        """
        got = self.collect(held=["DELISTED-USDT"])

        self.assertIn("DELISTED-USDT", got)

    def test_the_list_has_no_duplicates(self):
        got = self.collect(held=["BTC-USDT"])

        self.assertEqual(len(got), len(set(got)))

    def test_an_unreadable_book_does_not_stop_the_refresh(self):
        """
        讀不到帳本時仍然要更新交易池的資金費。

        讓整個資金費更新停擺,會把一個「帳本暫時讀不到」升級成
        「明天記不了帳」。但它必須出聲(第九十四條)。
        """
        from portfolio.paper import SYMBOLS

        got = self.collect(load_raises=OSError("磁碟忙"))

        for symbol in SYMBOLS:
            self.assertIn(symbol, got)


class TestDailyActuallyUsesIt(unittest.TestCase):
    """
    一個沒有被呼叫的正確清單,跟沒有那個清單一樣。
    """

    def test_daily_refreshes_funding_with_the_union_not_just_main(self):
        """
        看 main() 的程式碼,不是看整支檔案 —— 檔案裡的說明文字
        會提到那個舊寫法,而那不是呼叫。
        """
        import inspect

        body = inspect.getsource(daily.main)

        self.assertIn("refresh_funding(wanted)", body,
                      "daily.py 必須用聯集清單去更新資金費")
        self.assertNotIn("refresh_funding(SYMBOLS)", body,
                         "只更新主城 7 幣就是那個 bug 本身")


if __name__ == "__main__":
    unittest.main()
