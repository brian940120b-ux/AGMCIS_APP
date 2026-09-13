"""
資金費歷史要涵蓋「今天可能會用到」的每一個幣。

═══ 2026-09-10 的事故 ═══
daily.py 只更新主城 7 個幣的資金費歷史,而測試組用的是**動態交易池**。
那天池子挑進 1000PEPE-USDT —— 沒有人抓過它的歷史。

    SpecMissing: 沒有 1000PEPE-USDT 的資金費率歷史 —— 不猜一個數字

那個例外**是對的**:用不完整的資料記帳會靜靜少收資金費、美化績效。
錯的是沒有人去抓那個幣的歷史。

結果:測試組整個 tick 死掉,連續 73.7 小時沒有記帳,而它帳上有 23 個
持倉。巡檢有報,而且報了三天。

═══ 這一組驗什麼 ═══
清單是三個來源的聯集,少任何一個都會重演同一件事。
最容易被漏掉的是第三個:**已經掉出池子、但帳上還握著的幣**
—— 只抓池子會漏掉正在出場的倉。
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

    def collect(self, main_held=(), trial_held=(), screened=(),
                screen_raises=None):
        loaded = {}

        def fake_load(path):
            name = Path(path).name
            if name == "trial_account.json":
                return Account(trial_held)
            return Account(main_held)

        def fake_screen():
            if screen_raises:
                raise screen_raises
            return list(screened)

        import portfolio.account as account_mod
        import portfolio.trial as trial_mod

        with patch.object(account_mod.Account, "load", staticmethod(fake_load)), \
                patch.object(trial_mod, "screen", fake_screen):
            return daily.funding_symbols()

    def test_it_includes_the_main_seven(self):
        from portfolio.paper import SYMBOLS

        got = self.collect()

        for symbol in SYMBOLS:
            self.assertIn(symbol, got)

    def test_it_includes_todays_dynamic_pool(self):
        """
        **這是那個 bug 本身。** 明天要買的東西,今天就要有資料。
        """
        got = self.collect(screened=["1000PEPE-USDT", "ZEC-USDT"])

        self.assertIn("1000PEPE-USDT", got)
        self.assertIn("ZEC-USDT", got)

    def test_it_includes_coins_held_but_no_longer_in_the_pool(self):
        """
        ⚠️ 最容易漏的一條。

        一個幣掉出池子之後,倉不會瞬間消失 —— 它還要收資金費,
        直到真的被平掉。只抓池子會漏掉每一個正在出場的倉。
        """
        got = self.collect(trial_held=["OLD-USDT"], screened=["NEW-USDT"])

        self.assertIn("OLD-USDT", got)
        self.assertIn("NEW-USDT", got)

    def test_it_includes_what_the_main_book_holds(self):
        got = self.collect(main_held=["DELISTED-USDT"])

        self.assertIn("DELISTED-USDT", got)

    def test_the_list_has_no_duplicates(self):
        got = self.collect(main_held=["BTC-USDT"], trial_held=["BTC-USDT"],
                           screened=["BTC-USDT"])

        self.assertEqual(len(got), len(set(got)))

    def test_a_failing_screen_does_not_stop_the_refresh(self):
        """
        篩不出來時仍然要更新主城與持倉的資金費。

        讓整個資金費更新停擺,會把一個「測試組的池子篩不出來」
        升級成「主城明天也記不了帳」。
        """
        from portfolio.paper import SYMBOLS

        got = self.collect(main_held=["HELD-USDT"],
                           screen_raises=RuntimeError("交易所掛了"))

        self.assertIn("HELD-USDT", got)
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
