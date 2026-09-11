"""
績效歸因與自我檢討(Phase 15)。

這一層回答的是不舒服的問題:**哪些東西其實沒有貢獻?**
所以它很容易被做成一份只講好消息的報告 —— 而那比沒有報告更糟,
它會讓人對一個沒有優勢的系統產生信心。

測試的重點因此不是「算得對不對」,而是:
  * 樣本不夠時會不會說「不知道」
  * 虧錢的分群會不會被講出來
  * 隨機投票的 Agent 會不會被誤判成有貢獻
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.review import agent_scorecard, attribution, self_review


def trade(pnl, regime="BULL", signal="做多", votes=None, symbol="BTC/USDT",
          reason=None, strategy="multi_agent", status="CLOSED"):
    return {
        "status": status,
        "pnl_usdt": pnl,
        "market_regime": regime,
        "strategy": strategy,
        "symbol": symbol,
        "signal": signal,
        "close_reason": reason or ("自動止盈" if pnl > 0 else "自動止損"),
        "agent_votes": votes,
    }


def many(count, pnl, **kwargs):
    return [trade(pnl, **kwargs) for _ in range(count)]


class TestBucketMath(unittest.TestCase):

    def test_profit_factor_is_none_without_losses_not_infinity(self):
        """
        無限大會讓排序把一個只有兩筆交易的分群排到第一。
        """
        bucket = attribution.Bucket(key="x")
        bucket.add(10.0)
        bucket.add(5.0)

        self.assertIsNone(bucket.profit_factor)

    def test_a_thin_bucket_is_marked_unreliable(self):
        bucket = attribution.Bucket(key="x")
        for _ in range(3):
            bucket.add(1.0)

        self.assertFalse(bucket.reliable)

    def test_a_thick_bucket_is_reliable(self):
        bucket = attribution.Bucket(key="x")
        for _ in range(attribution.MIN_SAMPLE):
            bucket.add(1.0)

        self.assertTrue(bucket.reliable)


class TestGroupingExcludesRatherThanGuesses(unittest.TestCase):

    def test_trades_without_a_key_are_excluded_not_bucketed_as_unknown(self):
        """
        「未知」桶子的統計沒有意義,而且會讓人以為那是一個真實的分類。
        """
        trades = many(5, 10.0, regime="BULL") + many(3, 10.0, regime=None)
        buckets, skipped = attribution.by_regime(trades)

        self.assertEqual(set(buckets), {"BULL"})
        self.assertEqual(skipped, 3)

    def test_open_trades_are_not_counted(self):
        trades = many(5, 10.0) + many(4, 0.0, status="OPEN")

        self.assertEqual(len(attribution.closed_with_pnl(trades)), 5)

    def test_trades_without_pnl_are_not_counted(self):
        trades = many(3, 10.0)
        trades.append({"status": "CLOSED", "pnl_usdt": None, "symbol": "X"})

        self.assertEqual(len(attribution.closed_with_pnl(trades)), 3)


class TestConcentration(unittest.TestCase):
    """
    少數幾筆極端獲利可以讓一個沒有優勢的策略看起來很好,
    而那幾筆通常不會重演。
    """

    def test_a_system_carried_by_three_trades_is_flagged(self):
        trades = many(30, -1.0) + [trade(200.0), trade(180.0), trade(160.0)]
        check = attribution.concentration(trades)

        self.assertGreater(check.expectancy, 0)
        self.assertLessEqual(check.expectancy_without_top3, 0)
        self.assertTrue(check.depends_on_outliers)

    def test_a_broadly_profitable_system_is_not_flagged(self):
        check = attribution.concentration(many(40, 5.0))

        self.assertFalse(check.depends_on_outliers)

    def test_too_few_trades_cannot_be_assessed(self):
        check = attribution.concentration(many(3, 10.0))

        self.assertIsNone(check.expectancy_without_top3)
        self.assertFalse(check.reliable)


class TestAttributionWarnings(unittest.TestCase):

    def test_a_losing_bucket_with_enough_samples_is_called_out(self):
        trades = many(30, 10.0, regime="BULL") + many(30, -8.0, regime="RANGE")
        report = attribution.build(trades)

        self.assertTrue(any("RANGE" in w for w in report.warnings))

    def test_a_losing_bucket_that_is_too_thin_is_not_called_out(self):
        """
        樣本不夠時「這個分群在虧」跟「運氣不好」分不出來。
        """
        trades = many(40, 10.0, regime="BULL") + many(3, -8.0, regime="RANGE")
        report = attribution.build(trades)

        self.assertFalse(any("RANGE" in w for w in report.warnings))

    def test_missing_attribution_data_is_reported(self):
        trades = many(30, 10.0) + many(10, 10.0, regime=None)
        report = attribution.build(trades)

        self.assertTrue(any("沒有市況紀錄" in w for w in report.warnings))


class TestAgentScorecard(unittest.TestCase):

    def _informative(self, count=120, seed=7):
        """trend 的票真的有資訊;momentum 完全隨機。"""
        rng = random.Random(seed)
        trades = []

        for i in range(count):
            good = i % 2 == 0
            pnl = rng.uniform(5, 30) if good else rng.uniform(-25, -5)
            trades.append(trade(
                round(pnl, 2),
                regime="BULL" if good else "RANGE",
                votes={
                    "trend": "做多" if good else "做空",
                    "momentum": rng.choice(["做多", "做空"]),
                    "news": "棄權",
                },
            ))

        return trades

    def test_an_informative_agent_is_recognised(self):
        report = agent_scorecard.build(self._informative())
        trend = next(a for a in report.agents if a["agent"] == "trend")

        self.assertEqual(trend["verdict"], "CONTRIBUTING")
        self.assertGreater(trend["edge_sigmas"], agent_scorecard.MIN_SIGMA)

    def test_a_random_agent_is_not_mistaken_for_a_contributor(self):
        """
        隨機投票的 Agent 有一半的機會「同意時期望值比較高」——
        那完全是運氣。只看 edge > 0 會把它判成有貢獻。
        """
        report = agent_scorecard.build(self._informative())
        momentum = next(a for a in report.agents if a["agent"] == "momentum")

        self.assertEqual(momentum["verdict"], "UNKNOWN")
        self.assertLess(abs(momentum["edge_sigmas"]), agent_scorecard.MIN_SIGMA)

    def test_an_agent_that_never_speaks_is_marked_silent(self):
        report = agent_scorecard.build(self._informative())
        news = next(a for a in report.agents if a["agent"] == "news")

        self.assertEqual(news["verdict"], "SILENT")

    def test_an_inverted_agent_is_marked_no_edge(self):
        """票的方向是反的,比雜訊更糟。"""
        rng = random.Random(3)
        trades = []
        for i in range(120):
            good = i % 2 == 0
            pnl = rng.uniform(5, 30) if good else rng.uniform(-25, -5)
            trades.append(trade(
                round(pnl, 2),
                votes={"inverted": "做空" if good else "做多"},
            ))

        report = agent_scorecard.build(trades)
        inverted = next(a for a in report.agents if a["agent"] == "inverted")

        self.assertEqual(inverted["verdict"], "NO_EDGE")

    def test_trades_without_votes_are_excluded_and_counted(self):
        """
        補一個「未知」進去會讓每個 Agent 的樣本數看起來比實際多。
        """
        trades = self._informative(count=30) + many(10, 5.0, votes=None)
        report = agent_scorecard.build(trades)

        self.assertEqual(report.trades_without_votes, 10)
        self.assertEqual(report.trades_with_votes, 30)

    def test_a_small_sample_yields_unknown_not_a_verdict(self):
        report = agent_scorecard.build(self._informative(count=10))

        for agent in report.agents:
            with self.subTest(agent=agent["agent"]):
                self.assertIn(agent["verdict"], ("UNKNOWN", "SILENT"))

    def test_no_vote_data_at_all_is_stated_plainly(self):
        report = agent_scorecard.build(many(30, 5.0, votes=None))

        self.assertEqual(report.trades_with_votes, 0)
        self.assertTrue(any("補不回來" in w for w in report.warnings))


class TestSelfReviewVerdicts(unittest.TestCase):

    def test_too_few_trades_says_it_does_not_know(self):
        """「還不知道」是完全合法的結論。"""
        review = self_review.build(many(5, 10.0))

        self.assertEqual(review.verdict, self_review.NOT_ENOUGH_DATA)
        self.assertIn("還不知道", review.headline)

    def test_a_losing_system_is_called_losing(self):
        review = self_review.build(many(40, -3.0))

        self.assertEqual(review.verdict, self_review.LOSING)
        self.assertIn("虧", review.headline)

    def test_a_system_carried_by_outliers_is_called_fragile(self):
        trades = many(30, -1.0) + [trade(200.0), trade(180.0), trade(160.0)]
        review = self_review.build(trades)

        self.assertEqual(review.verdict, self_review.FRAGILE)

    def test_a_broadly_profitable_system_is_healthy(self):
        """驗證層也不能永遠說 NO —— 那樣同樣沒有資訊。"""
        rng = random.Random(11)
        trades = [trade(round(rng.uniform(1, 8), 2)) for _ in range(60)]
        review = self_review.build(trades)

        self.assertEqual(review.verdict, self_review.HEALTHY)

    def test_open_questions_are_always_listed(self):
        """
        看報告的人需要知道哪些結論還沒有依據,否則他會把沉默當成沒問題。
        """
        review = self_review.build(many(5, 10.0))

        self.assertTrue(review.questions_we_cannot_answer)

    def test_an_empty_history_does_not_crash(self):
        review = self_review.build([])

        self.assertEqual(review.verdict, self_review.NOT_ENOUGH_DATA)
        self.assertEqual(review.total_trades, 0)

    def test_the_report_survives_serialisation(self):
        review = self_review.build(many(40, 2.0))
        data = review.to_dict()

        self.assertIn("verdict", data)
        self.assertIn("attribution", data)
        self.assertIn("agents", data)


class TestNothingIsAutomaticallyDisabled(unittest.TestCase):
    """
    樣本小的時候「沒貢獻」跟「運氣不好」分不出來。
    自動關掉會讓系統在每一次小樣本的波動裡改變自己的組成 ——
    那是過擬合的另一種形式。
    """

    def test_the_scorecard_only_reports(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agmcis", "review", "agent_scorecard.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        for banned in ("set_registry", "remove", "disable", "registry.add"):
            with self.subTest(name=banned):
                self.assertNotIn(banned, source)

    def test_the_warning_says_adjustment_is_a_human_decision(self):
        rng = random.Random(3)
        trades = []
        for i in range(120):
            good = i % 2 == 0
            pnl = rng.uniform(5, 30) if good else rng.uniform(-25, -5)
            trades.append(trade(
                round(pnl, 2), votes={"inverted": "做空" if good else "做多"},
            ))

        report = agent_scorecard.build(trades)

        self.assertTrue(any("由人決定" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
