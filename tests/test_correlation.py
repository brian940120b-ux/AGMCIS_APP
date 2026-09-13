"""
相關性引擎(第六十條)。

═══ 這一組要證明的事 ═══
「總曝險 49%」這個數字,在七個幣一起跌的時候,不代表分散。
這一組驗的是:那件事被算出來了,而且算錯的時候會說算不出來,
不會給一個好看的假答案。

最重要的兩條是最後兩條:

  · 資料不夠時**不可以**回「不相關」。那是所有錯誤裡最貴的一種 ——
    它剛好在最危險的時候給出最樂觀的答案。
  · 報酬歷史真的有從 plan() 走到 Risk Engine。一條永遠收不到資料的
    檢查,就是一顆沒有作用的旋鈕(教訓第 4 條)。
"""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portfolio.correlation import (MIN_OVERLAP, concentration, daily_returns,
                                   matrix)


class Bar:
    def __init__(self, c):
        self.c = c


def walk(days, seed, shared=None, share=0.0):
    """
    造一段價格。share=1.0 代表完全跟著 shared 走(相關性 1),
    share=0 代表完全自己走。
    """
    rng = random.Random(seed)
    price, out = 100.0, []
    for i in range(days):
        own = rng.gauss(0, 0.02)
        step = own if shared is None else share * shared[i] + (1 - share) * own
        price *= math.exp(step)
        out.append(price)
    return out


def index_of(series: dict):
    """{幣: [價格]} -> plan() 用的那種 idx。"""
    return {s: {d: Bar(p) for d, p in enumerate(prices)}
            for s, prices in series.items()}


class TestTheMathIsTheStandardOne(unittest.TestCase):

    def test_perfectly_correlated_positions_count_as_one(self):
        """
        七個 7% 的倉,相關性 1 -> 等效曝險 49%、有效檔數 1。

        這正是這一節存在的理由:帳面七檔,實際上是一個 49% 的倉。
        """
        days = 300
        shared = [random.Random(9).gauss(0, 0.02) for _ in range(days)]
        prices = walk(days, 1, shared, share=1.0)
        idx = index_of({f"C{i}-USDT": prices for i in range(7)})
        dates = list(range(days))

        returns = daily_returns(idx, list(idx), dates, days - 1, days)
        c = concentration({s: 0.07 for s in idx}, returns)

        self.assertAlmostEqual(c.effective_positions, 1.0, places=6)
        self.assertAlmostEqual(c.equivalent_exposure_pct, 49.0, places=4)
        self.assertAlmostEqual(c.total_exposure_pct, 49.0, places=4)

    def test_independent_positions_count_as_many(self):
        """完全不相關 -> 有效檔數接近檔數,等效曝險遠低於帳面。"""
        days = 800
        idx = index_of({f"C{i}-USDT": walk(days, 100 + i) for i in range(7)})
        dates = list(range(days))

        returns = daily_returns(idx, list(idx), dates, days - 1, days)
        c = concentration({s: 0.07 for s in idx}, returns)

        self.assertGreater(c.effective_positions, 5.5)
        self.assertLess(c.equivalent_exposure_pct, 25.0)
        self.assertAlmostEqual(c.total_exposure_pct, 49.0, places=4)

    def test_a_single_position_is_its_own_equivalent(self):
        c = concentration({"BTC-USDT": 0.2}, {})
        self.assertEqual(c.effective_positions, 1.0)
        self.assertAlmostEqual(c.equivalent_exposure_pct, 20.0)

    def test_no_positions_is_not_an_error(self):
        c = concentration({}, {})
        self.assertFalse(c.measured)
        self.assertEqual(c.total_exposure_pct, 0.0)


class TestItRefusesToGuess(unittest.TestCase):

    def test_a_thin_sample_returns_none_not_zero_correlation(self):
        """
        **這是這一組最重要的一條。**

        資料不夠的時候回「不相關」,等於在最危險的時候給最樂觀的答案。
        """
        days = MIN_OVERLAP - 5
        idx = index_of({f"C{i}-USDT": walk(days, 200 + i) for i in range(3)})
        dates = list(range(days))

        returns = daily_returns(idx, list(idx), dates, days - 1, days)
        c = concentration({s: 0.1 for s in idx}, returns)

        self.assertFalse(c.measured)
        self.assertIsNone(c.effective_positions)
        self.assertIsNone(c.equivalent_exposure_pct)
        self.assertIn("共同觀測", c.reason)
        # 帳面曝險還是算得出來 —— 那個不需要歷史
        self.assertAlmostEqual(c.total_exposure_pct, 30.0)

    def test_one_coin_without_history_stops_the_whole_matrix(self):
        """
        半殘的矩陣沒辦法算二次式,而「跳過那一對」等於偷偷
        假設它不相關。
        """
        days = 300
        series = {f"C{i}-USDT": walk(days, 300 + i) for i in range(3)}
        series["NEW-USDT"] = walk(10, 999)          # 剛上市,只有十天
        idx = index_of(series)
        dates = list(range(days))

        returns = daily_returns(idx, list(series), dates, days - 1, days)
        c = concentration({s: 0.1 for s in series}, returns)

        self.assertFalse(c.measured)
        self.assertIn("NEW", c.reason)
        self.assertIn("NEW-USDT", c.unmeasured)

    def test_a_flat_series_has_no_correlation_rather_than_zero(self):
        """一整段完全沒動,相關性是無定義,不是 0。"""
        days = 300
        series = {"A-USDT": walk(days, 7), "B-USDT": [100.0] * days}
        idx = index_of(series)
        dates = list(range(days))

        returns = daily_returns(idx, list(series), dates, days - 1, days)
        R, _obs, _un = matrix(returns, list(series))

        self.assertIsNone(R)


class TestItCannotSeeTheFuture(unittest.TestCase):
    """第三十四條。相關性也是一個訊號,同樣不准偷看。"""

    def test_returns_stop_at_the_decision_day(self):
        days = 200
        idx = index_of({"A-USDT": walk(days, 11)})
        dates = list(range(days))

        cut = 100
        returns = daily_returns(idx, ["A-USDT"], dates, cut, days)

        self.assertTrue(returns["A-USDT"])
        self.assertLessEqual(max(returns["A-USDT"]), dates[cut])

    def test_a_gap_in_the_data_breaks_the_series_instead_of_spanning_it(self):
        """
        缺一天就跨過去硬算,那個「日報酬」其實是兩天以上的變化 ——
        會低估波動,也扭曲相關性。
        """
        idx = {"A-USDT": {0: Bar(100.0), 1: Bar(101.0), 3: Bar(150.0),
                          4: Bar(151.0)}}
        dates = [0, 1, 2, 3, 4]

        returns = daily_returns(idx, ["A-USDT"], dates, 4, 10)

        # 第 3 天沒有前一天可比 -> 不該產生那個 50% 的假報酬
        self.assertNotIn(3, returns["A-USDT"])
        self.assertIn(1, returns["A-USDT"])
        self.assertIn(4, returns["A-USDT"])


class TestTheRiskEngineActuallyUsesIt(unittest.TestCase):

    def account(self, weights, equity=10000.0):
        class Position:
            def __init__(self, amt, price):
                self.position_amt, self.avg_price = amt, price

            def liq_price(self):
                return None

        class Account:
            def __init__(self):
                self.peak_equity = equity
                self.positions = {
                    s: Position(w * equity / 100.0, 100.0)
                    for s, w in weights.items()}

            def equity(self, marks):
                return equity

            def exposure(self, marks):
                return sum(weights.values())

            def weights(self, marks):
                return dict(weights)

        return Account()

    def test_the_check_reports_the_number_even_without_a_limit(self):
        """
        門檻還沒設,不代表不量。量到的數字每天都要看得見 ——
        否則執政官要拿什麼決定門檻?
        """
        from portfolio.risk import ALLOW, RiskEngine

        days = 300
        shared = [random.Random(4).gauss(0, 0.02) for _ in range(days)]
        prices = walk(days, 2, shared, share=1.0)
        idx = index_of({f"C{i}-USDT": prices for i in range(7)})
        dates = list(range(days))
        returns = daily_returns(idx, list(idx), dates, days - 1, days)

        account = self.account({s: 0.07 for s in idx})
        rd = RiskEngine().evaluate(account, [], {}, [], returns=returns)

        check = next(c for c in rd.checks if c.name == "相關性集中度")
        self.assertEqual(check.verdict, ALLOW)
        self.assertIn("等效單一標的曝險 49.0%", check.detail)
        self.assertIn("有效 1.0 檔", check.detail)
        self.assertIn("尚未設定上限", check.detail)

    def test_a_set_limit_actually_rejects(self):
        """設了門檻它就是硬閘,和其他門檻一樣。"""
        from portfolio.risk import REJECT, RiskEngine, RiskLimits

        days = 300
        shared = [random.Random(5).gauss(0, 0.02) for _ in range(days)]
        prices = walk(days, 3, shared, share=1.0)
        idx = index_of({f"C{i}-USDT": prices for i in range(7)})
        dates = list(range(days))
        returns = daily_returns(idx, list(idx), dates, days - 1, days)

        limits = RiskLimits(max_equivalent_exposure_pct=25.0)
        account = self.account({s: 0.07 for s in idx})
        rd = RiskEngine(limits).evaluate(account, [], {}, [], returns=returns)

        self.assertEqual(rd.verdict, REJECT)

    def test_a_set_limit_with_unmeasurable_data_rejects(self):
        """
        設計原則五:檢查失敗時拒絕,不是放行。
        有門檻卻量不到,不能當成通過。
        """
        from portfolio.risk import REJECT, RiskEngine, RiskLimits

        limits = RiskLimits(max_equivalent_exposure_pct=25.0)
        account = self.account({"A-USDT": 0.2, "B-USDT": 0.2})
        rd = RiskEngine(limits).evaluate(account, [], {}, [], returns={})

        self.assertEqual(rd.verdict, REJECT)

    def test_no_returns_means_the_check_does_not_appear(self):
        """
        沒有歷史可用時不要生一條假的檢查。「沒有這條檢查」
        比「一條說不出所以然的檢查」誠實。
        """
        from portfolio.risk import RiskEngine

        account = self.account({"A-USDT": 0.2})
        rd = RiskEngine().evaluate(account, [], {}, [], returns=None)

        self.assertNotIn("相關性集中度", [c.name for c in rd.checks])

    def test_plan_hands_the_returns_to_the_gate(self):
        """
        **一條收不到資料的檢查就是一顆沒作用的旋鈕。**

        這裡不 mock Risk Engine —— 直接確認 paper.tick 真的把
        plan() 算出來的 returns 傳進 evaluate。
        """
        import inspect

        from portfolio import paper

        source = inspect.getsource(paper)
        self.assertIn("correlation.daily_returns", source,
                      "plan() 沒有算報酬歷史")
        self.assertIn('returns=p.get("returns")', source,
                      "tick() 沒有把報酬歷史交給 Risk Engine")


if __name__ == "__main__":
    unittest.main()


class TestTheNumberReachesTheDashboard(unittest.TestCase):
    """
    量到卻沒人看得到的數字,等於沒量。

    尤其這一條的門檻**還沒設定** —— 執政官要拿面板上的數字決定門檻。
    如果它只存在於 log,那個決定永遠不會發生。
    """

    def setUp(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "scripts"))
        self.path = root / "data" / "portfolio_risk.json"
        self.existed = self.path.exists()
        self.backup = self.path.read_bytes() if self.existed else None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.existed:
            self.path.write_bytes(self.backup)
        else:
            self.path.unlink(missing_ok=True)

    def write(self, concentration):
        import json
        self.path.write_text(json.dumps(
            {"verdict": "ALLOW", "rejected_symbols": [], "checks": [],
             "concentration": concentration}), encoding="utf-8")

    def render(self):
        import dashboard
        return dashboard.block_correlation()

    def test_a_measured_concentration_shows_both_numbers(self):
        self.write({"effective_positions": 1.24,
                    "equivalent_exposure_pct": 44.2,
                    "total_exposure_pct": 49.0, "positions": 7,
                    "observations": 300, "unmeasured": [], "reason": None})

        html = self.render()

        self.assertIn("44.2", html)          # 等效單一標的曝險
        self.assertIn("49.0", html)          # 帳面總曝險 —— 兩個都要在
        self.assertIn("1.2", html)           # 有效檔數
        self.assertIn("尚未設定", html)       # 門檻狀態不能藏起來

    def test_an_unmeasurable_concentration_says_so(self):
        """
        算不出來的時候顯示「—」,不是顯示 0、也不是把卡片藏起來。
        藏起來會讓人以為今天沒有這個風險。
        """
        self.write({"effective_positions": None,
                    "equivalent_exposure_pct": None,
                    "total_exposure_pct": 49.0, "positions": 7,
                    "observations": 12, "unmeasured": ["NEW-USDT"],
                    "reason": "共同觀測 12 天(至少要 60 天)"})

        html = self.render()

        self.assertIn("算不出來", html)
        self.assertIn("共同觀測 12 天", html)
        self.assertNotIn("0.0% 等效", html)
