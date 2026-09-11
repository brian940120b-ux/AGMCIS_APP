"""
組合風險與相關性(Master Prompt 第五十九 / 六十節)。

同時做多 BTC、ETH、SOL,每一筆的單筆風險都是 1%,帳面上是三個獨立的 1%。
實際上是一個 3% 的大型加密貨幣 Beta 曝險。這一組測試在測那件事有沒有被看見。

最重要的幾個測試不是「算得對不對」,是**不知道的時候往哪邊倒**:
相關係數算不出來時必須當成相關,不能當成不相關。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.risk import correlation, portfolio


def series(values):
    """把報酬率序列還原成收盤價序列,方便寫測試資料。"""
    price = 100.0
    closes = [price]
    for value in values:
        price *= math.exp(value)
        closes.append(price)
    return closes


def wave(n, amplitude=0.01, phase=0.0, step=0.7):
    return [amplitude * math.sin(phase + i * step) for i in range(n)]


def leg(symbol, direction="做多", notional=3000.0, entry=100.0, stop=97.0):
    return {
        "symbol": symbol, "direction": direction,
        "notional_usdt": notional, "entry": entry, "stop_loss": stop,
    }


class TestReturns(unittest.TestCase):

    def test_prices_become_log_returns(self):
        values = correlation.returns([100.0, 110.0, 121.0])

        self.assertEqual(len(values), 2)
        self.assertAlmostEqual(values[0], values[1], places=9,
                               msg="兩段同樣是 +10%,對數報酬率必須相同")

    def test_a_bad_price_breaks_the_chain_rather_than_inventing_a_return(self):
        """
        0 或負數是資料錯誤,不是行情。跳過它但也不能把它前後兩根
        接起來算成一段報酬 —— 那一段從來沒有發生過。
        """
        values = correlation.returns([100.0, 110.0, 0.0, 200.0, 220.0])

        self.assertEqual(len(values), 2)

    def test_unparseable_values_are_skipped(self):
        values = correlation.returns([100.0, "x", None, 110.0, 121.0])
        self.assertEqual(len(values), 1)


class TestPearson(unittest.TestCase):

    def test_a_series_correlates_perfectly_with_itself(self):
        values = wave(100)
        self.assertAlmostEqual(correlation.pearson(values, values), 1.0, places=9)

    def test_an_inverted_series_correlates_perfectly_negatively(self):
        values = wave(100)
        inverted = [-v for v in values]
        self.assertAlmostEqual(correlation.pearson(values, inverted), -1.0, places=9)

    def test_a_flat_series_has_no_correlation_not_zero_correlation(self):
        """
        變異數為 0 時相關係數沒有定義。回 0 會被讀成「已知不相關」,
        而那會讓一個部位從相關群裡消失。
        """
        self.assertIsNone(correlation.pearson(wave(100), [0.0] * 100))

    def test_series_of_different_lengths_are_aligned_at_the_end(self):
        """最新一根必須是同一個時間。對齊開頭會把兩條序列錯開。"""
        values = wave(100)
        self.assertAlmostEqual(
            correlation.pearson(values, values[-40:]), 1.0, places=9,
        )

    def test_too_few_points_gives_nothing(self):
        self.assertIsNone(correlation.pearson([0.01], [0.01]))


class TestBeta(unittest.TestCase):

    def test_a_symbol_that_moves_twice_as_much_has_beta_two(self):
        benchmark = wave(100)
        doubled = [v * 2 for v in benchmark]

        self.assertAlmostEqual(correlation.beta(doubled, benchmark), 2.0, places=9)

    def test_a_flat_benchmark_gives_no_beta(self):
        self.assertIsNone(correlation.beta(wave(100), [0.0] * 100))


class TestMatrix(unittest.TestCase):

    def test_correlated_symbols_are_recognised(self):
        base = wave(120)
        matrix = correlation.build({
            "BTC/USDT": series(base),
            "ETH/USDT": series([v * 1.2 for v in base]),
        })

        self.assertTrue(matrix.is_correlated("BTC/USDT", "ETH/USDT"))
        self.assertGreater(matrix.get("BTC/USDT", "ETH/USDT"), 0.9)

    def test_an_unknown_pair_returns_none_not_zero(self):
        matrix = correlation.build({"BTC/USDT": series(wave(120))})

        self.assertIsNone(matrix.get("BTC/USDT", "DOGE/USDT"))
        self.assertIsNone(matrix.is_correlated("BTC/USDT", "DOGE/USDT"))

    def test_a_symbol_with_too_few_candles_is_excluded_and_reported(self):
        """
        20 根算出來的 0.9 可以純粹是運氣。樣本不足時不給值,
        而且必須在 warnings 裡說出來 —— 悄悄不給值跟給錯值一樣糟。
        """
        matrix = correlation.build({
            "BTC/USDT": series(wave(120)),
            "ETH/USDT": series(wave(120)),
            "NEW/USDT": series(wave(10)),
        })

        self.assertNotIn("NEW/USDT", matrix.symbols)
        self.assertIsNone(matrix.get("NEW/USDT", "BTC/USDT"))
        self.assertTrue(any("NEW/USDT" in w for w in matrix.warnings))

    def test_a_symbol_correlates_with_itself(self):
        matrix = correlation.build({})
        self.assertEqual(matrix.get("BTC/USDT", "BTC/USDT"), 1.0)

    def test_beta_is_computed_against_the_benchmark(self):
        base = wave(120)
        matrix = correlation.build(
            {
                "BTC/USDT": series(base),
                "ALT/USDT": series([v * 3 for v in base]),
            },
            benchmark="BTC/USDT",
        )

        self.assertAlmostEqual(matrix.betas["ALT/USDT"], 3.0, places=6)

    def test_fetch_failures_do_not_empty_the_whole_matrix(self):
        """
        一檔抓不到就讓整個矩陣變空,等於把「不知道」擴散到所有組合。
        """
        import pandas as pd

        base = wave(120)
        frames = {
            "BTC/USDT": pd.DataFrame({"close": series(base)}),
            "ETH/USDT": pd.DataFrame({"close": series([v * 1.1 for v in base])}),
        }

        def fetch(symbol):
            if symbol == "BROKEN/USDT":
                raise RuntimeError("timeout")
            return frames.get(symbol)

        matrix = correlation.build_from_market(
            ["BTC/USDT", "ETH/USDT", "BROKEN/USDT"],
            benchmark="BTC/USDT", fetch=fetch,
        )

        self.assertIsNotNone(matrix.get("BTC/USDT", "ETH/USDT"))
        self.assertIsNone(matrix.get("BROKEN/USDT", "BTC/USDT"))
        self.assertTrue(any("BROKEN" in w for w in matrix.warnings))


class TestTheCacheDoesNotHideFailures(unittest.TestCase):

    def setUp(self):
        correlation.clear_cache()

    tearDown = setUp

    def test_a_build_that_raises_becomes_an_empty_matrix_not_an_exception(self):
        def explode(symbol):
            raise RuntimeError("交易所掛了")

        matrix = correlation.get_matrix(
            ["BTC/USDT", "ETH/USDT"], fetch=explode, now=0.0,
        )

        self.assertIsNone(matrix.get("BTC/USDT", "ETH/USDT"))
        self.assertTrue(matrix.warnings)

    def test_a_different_symbol_set_is_not_served_from_cache(self):
        calls = []

        def fetch(symbol):
            calls.append(symbol)
            return None

        correlation.get_matrix(["A", "B"], fetch=fetch, now=0.0)
        first = len(calls)
        correlation.get_matrix(["A", "C"], fetch=fetch, now=0.0)

        self.assertGreater(len(calls), first)

    def test_the_same_symbol_set_is_served_from_cache_until_it_expires(self):
        calls = []

        def fetch(symbol):
            calls.append(symbol)
            return None

        correlation.get_matrix(["A", "B"], fetch=fetch, now=0.0, ttl=100)
        cached = len(calls)

        correlation.get_matrix(["A", "B"], fetch=fetch, now=50.0, ttl=100)
        self.assertEqual(len(calls), cached)

        correlation.get_matrix(["A", "B"], fetch=fetch, now=200.0, ttl=100)
        self.assertGreater(len(calls), cached)


class TestUnknownCorrelationIsTreatedAsCorrelated(unittest.TestCase):
    """
    這一組是這一層存在的理由。相關性未知就假設不相關,
    等於在資料最少的時候發出最寬鬆的額度。
    """

    def test_no_matrix_at_all_puts_every_same_side_position_in_one_cluster(self):
        report = portfolio.assess(
            candidate=leg("SOL/USDT"),
            open_positions=[leg("BTC/USDT"), leg("ETH/USDT")],
            equity=10000.0,
            matrix=None,
            max_cluster_risk_pct=3.0,
        )

        self.assertEqual(
            report.cluster_symbols, ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        )
        self.assertEqual(sorted(set(report.assumed_correlated)),
                         ["BTC/USDT", "ETH/USDT"])

    def test_a_pair_missing_from_the_matrix_is_assumed_correlated(self):
        matrix = correlation.build({
            "BTC/USDT": series(wave(120)),
            "ETH/USDT": series(wave(120, phase=1.0)),
        })

        report = portfolio.assess(
            candidate=leg("NEW/USDT"),
            open_positions=[leg("BTC/USDT")],
            equity=10000.0,
            matrix=matrix,
            max_cluster_risk_pct=3.0,
        )

        self.assertIn("BTC/USDT", report.cluster_symbols)
        self.assertIn("BTC/USDT", report.assumed_correlated)

    def test_the_assumption_is_stated_in_the_warnings(self):
        report = portfolio.assess(
            candidate=leg("SOL/USDT"),
            open_positions=[leg("BTC/USDT")],
            equity=10000.0, matrix=None, max_cluster_risk_pct=3.0,
        )

        self.assertTrue(any("當成相關" in w for w in report.warnings))

    def test_a_position_with_an_unreadable_direction_is_counted_conservatively(self):
        report = portfolio.assess(
            candidate=leg("SOL/USDT"),
            open_positions=[leg("BTC/USDT", direction="???")],
            equity=10000.0, matrix=None, max_cluster_risk_pct=3.0,
        )

        self.assertIn("BTC/USDT", report.cluster_symbols)


class TestClusterRisk(unittest.TestCase):

    def test_three_correlated_longs_are_one_bet(self):
        """
        每筆 1% 風險,三筆 = 3%。這正是第五十九節在講的事。
        """
        report = portfolio.assess(
            candidate=leg("SOL/USDT"),
            open_positions=[leg("BTC/USDT"), leg("ETH/USDT")],
            equity=10000.0, matrix=None, max_cluster_risk_pct=2.5,
        )

        self.assertAlmostEqual(report.cluster_risk_pct, 2.7, places=1)
        self.assertIn("MAX_CORRELATED_RISK", report.blockers)

    def test_the_same_three_positions_pass_under_a_looser_limit(self):
        report = portfolio.assess(
            candidate=leg("SOL/USDT"),
            open_positions=[leg("BTC/USDT"), leg("ETH/USDT")],
            equity=10000.0, matrix=None, max_cluster_risk_pct=5.0,
        )

        self.assertTrue(report.allowed)

    def test_uncorrelated_positions_do_not_join_the_cluster(self):
        base = wave(200)
        matrix = correlation.build({
            "BTC/USDT": series(base),
            # 用不同的頻率,產生近乎無關的序列
            "ODD/USDT": series(wave(200, step=0.13, phase=2.0)),
        })
        self.assertFalse(matrix.is_correlated("BTC/USDT", "ODD/USDT"))

        report = portfolio.assess(
            candidate=leg("BTC/USDT"),
            open_positions=[leg("ODD/USDT")],
            equity=10000.0, matrix=matrix, max_cluster_risk_pct=1.5,
        )

        self.assertEqual(report.cluster_symbols, ["BTC/USDT"])
        self.assertTrue(report.allowed)

    def test_an_opposite_side_position_is_not_netted_off(self):
        """
        做多 BTC + 做空 ETH 的方向性曝險確實比較低,但兩邊都是槓桿部位、
        兩邊都會被追繳保證金,而且爆倉的時候「對沖」的那一邊不會保護你。
        給抵銷還會產生漏洞:想繞過上限開一個反向部位就好。
        """
        report = portfolio.assess(
            candidate=leg("BTC/USDT", direction="做多"),
            open_positions=[leg("ETH/USDT", direction="做空")],
            equity=10000.0, matrix=None, max_cluster_risk_pct=3.0,
        )

        self.assertEqual(report.cluster_symbols, ["BTC/USDT"])
        self.assertGreater(report.cluster_risk_pct, 0)

    def test_a_negatively_correlated_pair_flips_which_side_is_the_same_bet(self):
        base = wave(200)
        matrix = correlation.build({
            "AAA/USDT": series(base),
            "BBB/USDT": series([-v for v in base]),
        })
        self.assertLess(matrix.get("AAA/USDT", "BBB/USDT"), -0.9)

        # 兩檔反向走,所以「一多一空」才是同一個賭注
        report = portfolio.assess(
            candidate=leg("AAA/USDT", direction="做多"),
            open_positions=[leg("BBB/USDT", direction="做空")],
            equity=10000.0, matrix=matrix, max_cluster_risk_pct=3.0,
        )
        self.assertEqual(report.cluster_symbols, ["AAA/USDT", "BBB/USDT"])

        same_side = portfolio.assess(
            candidate=leg("AAA/USDT", direction="做多"),
            open_positions=[leg("BBB/USDT", direction="做多")],
            equity=10000.0, matrix=matrix, max_cluster_risk_pct=3.0,
        )
        self.assertEqual(same_side.cluster_symbols, ["AAA/USDT"])


class TestRiskThatCannotBeComputed(unittest.TestCase):

    def test_a_leg_without_a_stop_does_not_vanish_from_the_cluster(self):
        """
        算不出風險就當成 0,會讓一個沒有停損的部位在組合風險裡消失 ——
        而那是風險最大的那一種部位。
        """
        no_stop = leg("BTC/USDT")
        no_stop["stop_loss"] = None

        report = portfolio.assess(
            candidate=leg("SOL/USDT"),
            open_positions=[no_stop],
            equity=10000.0, matrix=None, max_cluster_risk_pct=3.0,
            assumed_risk_pct=1.0,
        )

        self.assertIn("BTC/USDT", report.assumed_risk_symbols)
        self.assertAlmostEqual(report.cluster_risk_pct, 1.0 + 0.9, places=6)
        self.assertTrue(any("估計" in w for w in report.warnings))


class TestSymbolExposure(unittest.TestCase):

    def test_stacking_the_same_symbol_hits_the_per_symbol_limit(self):
        report = portfolio.assess(
            candidate=leg("BTC/USDT", notional=3000.0),
            open_positions=[leg("BTC/USDT", notional=3000.0)],
            equity=10000.0, matrix=None, max_symbol_pct=50.0,
        )

        self.assertIn("MAX_SYMBOL_EXPOSURE", report.blockers)
        self.assertAlmostEqual(report.symbol_pct["BTC/USDT"], 60.0, places=6)

    def test_the_notional_is_derived_from_size_and_leverage_when_missing(self):
        report = portfolio.assess(
            candidate={"symbol": "BTC/USDT", "direction": "做多",
                       "size_usdt": 1000.0, "leverage": 3.0,
                       "entry": 100.0, "stoploss": 97.0},
            open_positions=[],
            equity=10000.0, matrix=None, max_symbol_pct=50.0,
        )

        self.assertAlmostEqual(report.symbol_pct["BTC/USDT"], 30.0, places=6)

    def test_zero_equity_blocks_instead_of_dividing_by_zero(self):
        report = portfolio.assess(
            candidate=leg("BTC/USDT"), open_positions=[], equity=0.0,
        )

        self.assertFalse(report.allowed)
        self.assertIn("NO_EQUITY", report.blockers)


if __name__ == "__main__":
    unittest.main()
