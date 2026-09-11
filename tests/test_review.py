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
          reason=None, strategy="multi_agent", status="CLOSED", month=None):
    return {
        "status": status,
        "pnl_usdt": pnl,
        "market_regime": regime,
        "strategy": strategy,
        "symbol": symbol,
        "signal": signal,
        "close_reason": reason or ("自動止盈" if pnl > 0 else "自動止損"),
        "agent_votes": votes,
        "closed_at": f"{month or '2026-06'}-15 10:00:00",
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


class TestTimeTrend(unittest.TestCase):
    """
    一個月比一個月差,通常代表優勢正在消失 —— 可能是市場結構變了,
    也可能是它從一開始就沒有優勢,前面只是運氣。兩種都需要停下來看。
    """

    def _months(self, *pairs):
        trades = []
        for month, pnl in pairs:
            trades.extend(many(30, pnl, month=month))
        return trades

    def test_a_declining_system_is_detected(self):
        check = attribution.trend(
            self._months(("2026-06", 10.0), ("2026-07", 8.0), ("2026-08", 0.5)),
        )

        self.assertTrue(check.declining)
        self.assertEqual(len(check.months), 3)

    def test_a_steady_system_is_not_flagged(self):
        check = attribution.trend(
            self._months(("2026-06", 5.0), ("2026-07", 5.0), ("2026-08", 5.0)),
        )

        self.assertFalse(check.declining)

    def test_an_improving_system_is_not_flagged(self):
        check = attribution.trend(
            self._months(("2026-06", 2.0), ("2026-07", 5.0), ("2026-08", 9.0)),
        )

        self.assertFalse(check.declining)

    def test_too_few_months_cannot_show_a_trend(self):
        """
        兩個點連得出一條線,但那條線沒有意義。
        """
        check = attribution.trend(
            self._months(("2026-07", 10.0), ("2026-08", 1.0)),
        )

        self.assertFalse(check.declining)
        self.assertFalse(check.reliable)

    def test_a_previously_losing_system_is_not_called_declining(self):
        """先前就在虧的系統,「衰退」不是正確的描述。"""
        check = attribution.trend(
            self._months(("2026-06", -5.0), ("2026-07", -5.0), ("2026-08", -8.0)),
        )

        self.assertFalse(check.declining)

    def test_trades_without_a_timestamp_are_excluded(self):
        trades = many(30, 5.0, month="2026-06")
        for item in trades[:10]:
            item["closed_at"] = None
            item["opened_at"] = None

        buckets, skipped = attribution.by_month(trades)

        self.assertEqual(skipped, 10)

    def test_a_malformed_timestamp_is_excluded_not_guessed(self):
        trades = many(5, 5.0)
        for item in trades:
            item["closed_at"] = "不是日期"
            item["opened_at"] = None

        buckets, skipped = attribution.by_month(trades)

        self.assertEqual(buckets, {})
        self.assertEqual(skipped, 5)

    def test_decay_makes_the_verdict_fragile_not_healthy(self):
        """
        整體期望值還是正的,但最近一個月掉得很明顯 ——
        那跟「穩定獲利」是兩件事。
        """
        review = self_review.build(
            self._months(("2026-06", 10.0), ("2026-07", 9.0), ("2026-08", 0.5)),
        )

        self.assertEqual(review.verdict, self_review.FRAGILE)
        self.assertIn("衰退", review.headline)

    def test_month_is_one_of_the_attribution_dimensions(self):
        report = attribution.build(
            self._months(("2026-06", 5.0), ("2026-07", 5.0)),
        )

        self.assertIn("month", report.buckets)


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
        self.assertGreater(trend["edge_sigmas"], trend["min_sigma"])

    def test_a_random_agent_is_not_mistaken_for_a_contributor(self):
        """
        隨機投票的 Agent 有一半的機會「同意時期望值比較高」——
        那完全是運氣。只看 edge > 0 會把它判成有貢獻。
        """
        report = agent_scorecard.build(self._informative())
        momentum = next(a for a in report.agents if a["agent"] == "momentum")

        self.assertEqual(momentum["verdict"], "UNKNOWN")
        self.assertLess(abs(momentum["edge_sigmas"]), momentum["min_sigma"])

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


class TestMultipleComparisonCorrection(unittest.TestCase):
    """
    單獨看一個 Agent 時 2 個標準誤代表「純屬巧合的機率約 5%」。
    但我們同時看十二個 —— 至少一個假陽性的機率是 1 - 0.95^12 ≈ 46%。

    也就是說:一套**全部由隨機投票組成**的 Agent 群,
    有將近一半的機會會產生「至少一個有貢獻的 Agent」。
    那個結論毫無意義,但它看起來跟真的一模一樣。
    """

    def test_a_single_agent_uses_the_base_threshold(self):
        self.assertEqual(
            agent_scorecard.min_sigma_for(1), agent_scorecard.BASE_MIN_SIGMA,
        )

    def test_more_agents_means_a_higher_bar(self):
        thresholds = [agent_scorecard.min_sigma_for(n) for n in (1, 3, 12, 30)]

        self.assertEqual(thresholds, sorted(thresholds))
        self.assertGreater(thresholds[-1], thresholds[0])

    def test_the_twelve_agent_threshold_is_meaningfully_higher(self):
        self.assertGreater(
            agent_scorecard.min_sigma_for(12),
            agent_scorecard.BASE_MIN_SIGMA + 0.5,
        )

    def test_an_absurd_count_is_capped_not_unbounded(self):
        self.assertEqual(
            agent_scorecard.min_sigma_for(10000), agent_scorecard.MAX_SIGMA,
        )

    def test_a_whole_panel_of_random_agents_produces_no_contributors(self):
        """
        這是整個校正存在的理由。十二個純隨機的 Agent,
        不校正時很可能會冒出一兩個「有貢獻」的。
        """
        rng = random.Random(5)
        trades = []

        for i in range(200):
            pnl = rng.uniform(-20, 20)
            votes = {
                f"random{n}": rng.choice(["做多", "做空"]) for n in range(12)
            }
            trades.append(trade(round(pnl, 2), votes=votes))

        report = agent_scorecard.build(trades)
        contributors = [a for a in report.agents if a["verdict"] == "CONTRIBUTING"]

        self.assertEqual(contributors, [], contributors)

    def test_the_correction_is_explained_in_the_warnings(self):
        rng = random.Random(9)
        trades = []
        for i in range(120):
            good = i % 2 == 0
            pnl = rng.uniform(5, 30) if good else rng.uniform(-25, -5)
            trades.append(trade(round(pnl, 2), votes={
                "a": "做多" if good else "做空",
                "b": rng.choice(["做多", "做空"]),
                "c": rng.choice(["做多", "做空"]),
            }))

        report = agent_scorecard.build(trades)

        self.assertTrue(any("Bonferroni" in w for w in report.warnings))

    def test_a_strong_signal_still_gets_through_the_higher_bar(self):
        """
        校正是保守的,但不能保守到連真的訊號都認不出來。
        """
        rng = random.Random(7)
        trades = []
        for i in range(200):
            good = i % 2 == 0
            pnl = rng.uniform(10, 30) if good else rng.uniform(-30, -10)
            votes = {"trend": "做多" if good else "做空"}
            votes.update({
                f"noise{n}": rng.choice(["做多", "做空"]) for n in range(11)
            })
            trades.append(trade(round(pnl, 2), votes=votes))

        report = agent_scorecard.build(trades)
        trend = next(a for a in report.agents if a["agent"] == "trend")

        self.assertEqual(trend["verdict"], "CONTRIBUTING")


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
