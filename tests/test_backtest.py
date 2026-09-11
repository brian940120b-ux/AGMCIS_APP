"""
回測引擎。

Phase 0 稽核在舊回測找到四種偏誤,這裡逐一鎖住修正結果。
最重要的是前兩個 —— 它們會讓回測系統性高估績效,
而一個高估勝率的回測比沒有回測更危險:它會讓人有信心把真錢
放進一個沒有優勢的策略。
"""
import unittest

from agmcis.backtest import metrics as metrics_module
from agmcis.backtest.costs import DEFAULT_COSTS, ZERO_COSTS, CostModel
from agmcis.backtest.engine import BacktestEngine
from agmcis.core.enums import Direction


def candle(i, open_, high, low, close, volume=100.0):
    return {"time": i * 3_600_000, "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


def flat_candles(count, price=100.0):
    return [candle(i, price, price, price, price) for i in range(count)]


class TestNoLookAhead(unittest.TestCase):
    """
    舊回測用第 i 根的收盤價產生訊號,又用同一根的收盤價成交。
    那等於偷看了未來 —— 實際交易時你不可能用剛剛才知道的收盤價成交。
    """

    def test_signal_function_never_sees_future_candles(self):
        """引擎保證 signal_fn 拿到的 history 不含未來資料。"""
        candles = flat_candles(100)
        seen = []

        def signal_fn(history, index):
            seen.append((len(history), index))
            return None

        BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)

        self.assertTrue(seen)
        for history_len, index in seen:
            self.assertEqual(
                history_len, index + 1,
                f"index {index} 拿到 {history_len} 根,應該只有 {index + 1} 根",
            )

    def test_entry_uses_the_next_candle_open_not_the_signal_candle_close(self):
        candles = flat_candles(70)
        # 訊號那根收盤 100,下一根開盤跳空到 120
        candles[65] = candle(65, 100, 100, 100, 100)
        candles[66] = candle(66, 120, 125, 119, 124)

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 80.0, "take_profit": 200.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_index, 66)
        self.assertAlmostEqual(trade.entry_price, 120.0, places=6)

    def test_a_gap_against_the_signal_is_not_hidden(self):
        """
        跳空不利時,誠實的回測會反映在進場價上。
        偷看未來的回測會用訊號那根的收盤價進場,假裝沒有跳空。
        """
        candles = flat_candles(70)
        candles[65] = candle(65, 100, 100, 100, 100)
        candles[66] = candle(66, 85, 86, 84, 85)   # 不利跳空

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 80.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)
        self.assertAlmostEqual(result.trades[0].entry_price, 85.0, places=6)


class TestIntrabarStopsUseHighLow(unittest.TestCase):
    """
    舊回測只比對收盤價,所以**盤中穿刺停損的 K 棒不會被判定停損**。
    這會系統性高估勝率 —— 那些實際上早就被掃出場的交易,
    在回測裡變成後來反彈獲利的贏家。
    """

    def _run_with_wick(self, wick_low, stop_loss=95.0):
        candles = flat_candles(70)
        candles[66] = candle(66, 100, 100, 100, 100)
        # 下一根:盤中跌破停損,但收盤又回到 100
        candles[67] = candle(67, 100, 101, wick_low, 100)
        candles[68] = candle(68, 100, 110, 100, 110)

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": stop_loss, "take_profit": 110.0}
            return None

        return BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)

    def test_a_wick_through_the_stop_counts_as_a_stop(self):
        result = self._run_with_wick(wick_low=90.0)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_reason, "停損")
        self.assertFalse(result.trades[0].is_win)

    def test_a_wick_that_does_not_reach_the_stop_does_not_exit(self):
        result = self._run_with_wick(wick_low=96.0)
        self.assertEqual(result.trades[0].exit_reason, "停利")

    def test_short_stop_uses_the_high(self):
        candles = flat_candles(70)
        candles[67] = candle(67, 100, 110, 99, 100)   # 上影線穿過停損

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做空", "stop_loss": 105.0, "take_profit": 90.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)
        self.assertEqual(result.trades[0].exit_reason, "停損")

    def test_stop_wins_when_both_are_touched_in_one_candle(self):
        """
        沒有逐筆資料就不知道誰先到。一律假設停損先到 ——
        寧可低估績效,也不要高估。
        """
        candles = flat_candles(70)
        candles[67] = candle(67, 100, 115, 90, 100)   # 同根同時觸及停損與停利

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 95.0, "take_profit": 110.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)
        self.assertEqual(result.trades[0].exit_reason, "停損")


class TestShortsAreSupported(unittest.TestCase):
    """舊回測只做多。"""

    def test_short_profits_when_price_falls(self):
        candles = flat_candles(70)
        candles[67] = candle(67, 100, 100, 88, 89)

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做空", "stop_loss": 105.0, "take_profit": 90.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)
        trade = result.trades[0]

        self.assertIs(trade.direction, Direction.SHORT)
        self.assertEqual(trade.exit_reason, "停利")
        self.assertGreater(trade.net_pnl, 0)

    def test_short_loses_when_price_rises(self):
        candles = flat_candles(70)
        candles[67] = candle(67, 100, 106, 100, 105)

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做空", "stop_loss": 105.0, "take_profit": 90.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)
        self.assertLess(result.trades[0].net_pnl, 0)


class TestCostsAreCharged(unittest.TestCase):
    """舊回測完全沒有成本。對一天進出好幾次的策略,手續費就能吃掉所有優勢。"""

    def _round_trip(self, costs):
        candles = flat_candles(70)
        candles[67] = candle(67, 100, 110, 100, 110)

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 95.0, "take_profit": 110.0}
            return None

        return BacktestEngine(costs=costs).run(candles, signal_fn, warmup=60)

    def test_costs_reduce_the_result(self):
        free = self._round_trip(ZERO_COSTS).trades[0]
        charged = self._round_trip(DEFAULT_COSTS).trades[0]

        self.assertEqual(free.fees, 0.0)
        self.assertGreater(charged.fees, 0.0)
        self.assertLess(charged.net_pnl, free.net_pnl)

    def test_entry_slippage_is_adverse_for_both_directions(self):
        costs = CostModel(slippage_pct=0.01, spread_pct=0.0, taker_fee=0.0)

        self.assertGreater(costs.entry_price(100, True), 100)    # 買進吃虧
        self.assertLess(costs.entry_price(100, False), 100)      # 賣出吃虧
        self.assertLess(costs.exit_price(100, True), 100)        # 平多吃虧
        self.assertGreater(costs.exit_price(100, False), 100)    # 平空吃虧

    def test_funding_is_paid_by_longs_and_received_by_shorts(self):
        costs = CostModel(funding_rate_8h=0.001)

        self.assertGreater(costs.funding_cost(10000, 8, True), 0)
        self.assertLess(costs.funding_cost(10000, 8, False), 0)

    def test_funding_accrues_with_holding_time(self):
        costs = CostModel(funding_rate_8h=0.001)
        short_hold = costs.funding_cost(10000, 8, True)
        long_hold = costs.funding_cost(10000, 80, True)

        self.assertAlmostEqual(long_hold, short_hold * 10, places=6)

    def test_round_trip_cost_is_reported(self):
        """用來判斷策略的平均獲利有沒有大到足以覆蓋成本。"""
        self.assertGreater(DEFAULT_COSTS.round_trip_cost_pct(), 0)


class TestLiquidation(unittest.TestCase):
    """舊回測沒有強平概念,所以高槓桿的災難完全看不到。"""

    def test_a_normal_stop_is_not_mislabelled_as_a_liquidation(self):
        """
        停損 99、強平 91 的部位在一根跌到 85 的 K 棒裡,
        價格是**先經過 99 才到 91**,所以那是正常停損不是爆倉。

        我第一版無條件先檢查強平,會把這種情況回報成強制平倉 ——
        那會誤導對槓桿設定的判斷。
        """
        candles = flat_candles(70)
        candles[67] = candle(67, 100, 100, 85, 86)

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 99.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS, max_leverage=10.0).run(
            candles, signal_fn, warmup=60,
        )
        trade = result.trades[0]

        self.assertEqual(trade.exit_reason, "停損")
        self.assertAlmostEqual(trade.exit_price, 99.0, places=6)

    def test_liquidation_wins_when_it_is_closer_than_the_stop(self):
        """
        強平真的先發生,只有在它比停損更接近進場價的時候 ——
        也就是槓桿相對停損距離開太高。
        引擎會自己壓低槓桿避免這件事,所以這裡直接建構該情境來驗證判定邏輯。
        """
        from agmcis.backtest.engine import BacktestTrade

        engine = BacktestEngine(costs=ZERO_COSTS)
        position = BacktestTrade(
            symbol="X", direction=Direction.LONG, entry_index=0, entry_time=0,
            entry_price=100.0, size_usdt=1000.0, leverage=20.0, quantity=200.0,
            stop_loss=80.0,          # 停損很遠
        )
        # 20x 的強平價約在 95.5,比停損 80 更接近進場價
        exited = engine._check_exit(position, candle(1, 100, 100, 90, 91), 1)

        self.assertTrue(exited)
        self.assertEqual(position.exit_reason, "強制平倉")

    def test_engine_caps_leverage_so_liquidation_stays_beyond_the_stop(self):
        """正常路徑下引擎會壓低槓桿,讓停損先於強平觸發。"""
        candles = flat_candles(70)
        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 40.0}   # 停損 60%
            return None

        result = BacktestEngine(costs=ZERO_COSTS, max_leverage=10.0).run(
            candles, signal_fn, warmup=60,
        )
        trade = result.trades[0]
        liquidation = trade.entry_price * (1 - 0.9 / trade.leverage)

        self.assertLess(liquidation, trade.stop_loss,
                        "強平價應該比停損更遠離進場價")

    def test_a_stop_so_wide_that_even_1x_liquidates_first_is_rejected(self):
        """
        停損距離 >= 90% 時,連 1x 都會先被強平。
        風控不會放行這種部位,回測也不該假裝做得到 —— 直接不開倉。
        """
        candles = flat_candles(70)
        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 1.0}   # 停損 99%
            return None

        result = BacktestEngine(costs=ZERO_COSTS, max_leverage=10.0).run(
            candles, signal_fn, warmup=60,
        )

        self.assertEqual(result.trades, [])
        self.assertEqual(result.skipped_signals, 1)

    def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop(self):
        """
        開盤就已經跌破停損時,不可能還在停損價成交。
        假設成交在停損價會系統性高估績效,那正是舊回測的老毛病。
        """
        candles = flat_candles(70)
        candles[67] = candle(67, 80, 81, 78, 79)   # 開盤直接跳空到 80

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 95.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS, max_leverage=1.0).run(
            candles, signal_fn, warmup=60,
        )
        trade = result.trades[0]

        self.assertAlmostEqual(trade.exit_price, 80.0, places=6)
        self.assertLess(trade.exit_price, trade.stop_loss)

    def test_loss_never_exceeds_the_margin(self):
        """閃崩穿過強平價時,虧損被限制在保證金以內 —— 強平就是為了這件事。"""
        candles = flat_candles(70)
        candles[67] = candle(67, 100, 100, 1, 2)   # 盤中崩到剩 1

        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 95.0}   # 停損 5%
            return None

        result = BacktestEngine(costs=ZERO_COSTS, max_leverage=10.0).run(
            candles, signal_fn, warmup=60,
        )
        trade = result.trades[0]

        self.assertGreaterEqual(trade.net_pnl, -trade.size_usdt)


class TestPositionSizingMatchesRiskEngine(unittest.TestCase):

    def test_risk_per_trade_is_respected(self):
        candles = flat_candles(70)
        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 98.0}   # 停損 2%
            return None

        engine = BacktestEngine(
            costs=ZERO_COSTS, start_balance=10000, risk_per_trade_pct=1.0,
        )
        result = engine.run(candles, signal_fn, warmup=60)
        trade = result.trades[0]

        # 名目 × 停損距離 應該約等於 1% 的權益
        risk = trade.notional * abs(trade.entry_price - trade.stop_loss) / trade.entry_price
        self.assertAlmostEqual(risk, 100.0, places=2)

    def test_wrong_side_stop_is_rejected(self):
        candles = flat_candles(70)
        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 110.0}   # 做多卻放在上方
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)

        self.assertEqual(result.trades, [])
        self.assertEqual(result.skipped_signals, 1)

    def test_signal_without_a_stop_is_rejected(self):
        candles = flat_candles(70)
        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多"}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)
        self.assertEqual(result.trades, [])


class TestMetrics(unittest.TestCase):

    def _result_with(self, pnls):
        from agmcis.backtest.engine import BacktestResult, BacktestTrade

        trades = []
        balance = 10000.0
        curve = [balance]

        for i, pnl in enumerate(pnls):
            trade = BacktestTrade(
                symbol="X", direction=Direction.LONG, entry_index=i, entry_time=i,
                entry_price=100.0, size_usdt=1000.0, leverage=1.0, quantity=10.0,
                stop_loss=98.0, take_profit=104.0,
            )
            trade.exit_index = i + 1
            trade.exit_time = i + 1
            trade.exit_price = 100 + pnl / 10
            trade.exit_reason = "停利" if pnl > 0 else "停損"
            trade.net_pnl = pnl
            trades.append(trade)
            balance += pnl
            curve.append(balance)

        return BacktestResult(
            trades=trades, equity_curve=curve,
            start_balance=10000.0, end_balance=balance, bars=len(pnls) * 10,
        )

    def test_expectancy_exposes_a_high_win_rate_loser(self):
        """
        勝率 80% 但每次賺 1、虧 10 —— 這是穩定虧錢的策略。
        只看勝率會以為它很好。
        """
        pnls = [1, 1, 1, 1, -10] * 4
        m = metrics_module.compute(self._result_with(pnls))

        self.assertEqual(m.win_rate, 80.0)
        self.assertLess(m.expectancy_usdt, 0)
        self.assertTrue(
            any("Expectancy 非正值" in w for w in m.warnings),
            m.warnings,
        )

    def test_profit_factor(self):
        m = metrics_module.compute(self._result_with([100, 100, -50]))
        self.assertAlmostEqual(m.profit_factor, 4.0)

    def test_no_losses_is_reported_as_unevaluable_not_infinite(self):
        m = metrics_module.compute(self._result_with([100, 50]))
        self.assertIsNone(m.profit_factor)
        self.assertTrue(any("無法評估" in w for w in m.warnings))

    def test_max_drawdown(self):
        m = metrics_module.compute(self._result_with([1000, -2000, 500]))
        self.assertGreater(m.max_drawdown_pct, 0)

    def test_small_sample_is_flagged(self):
        m = metrics_module.compute(self._result_with([10, -5]))
        self.assertTrue(any("樣本太小" in w for w in m.warnings))

    def test_empty_result_does_not_crash(self):
        from agmcis.backtest.engine import BacktestResult

        m = metrics_module.compute(BacktestResult(start_balance=10000, end_balance=10000))
        self.assertEqual(m.total_trades, 0)
        self.assertTrue(any("沒有任何交易" in w for w in m.warnings))

    def test_sharpe_and_sortino_computed(self):
        m = metrics_module.compute(self._result_with([100, -50, 80, -30, 120, -40]))
        self.assertIsNotNone(m.sharpe)
        self.assertIsNotNone(m.sortino)

    def test_costs_are_summarised(self):
        result = self._result_with([100, -50])
        for trade in result.trades:
            trade.fees = 5.0
            trade.funding = 1.0

        m = metrics_module.compute(result)
        self.assertAlmostEqual(m.total_fees, 10.0)
        self.assertAlmostEqual(m.total_funding, 2.0)
        self.assertGreater(m.cost_drag_pct, 0)


class TestEngineGuards(unittest.TestCase):

    def test_insufficient_candles_warns_instead_of_crashing(self):
        result = BacktestEngine(costs=ZERO_COSTS).run(
            flat_candles(10), lambda h, i: None, warmup=60,
        )
        self.assertEqual(result.trades, [])
        self.assertTrue(any("無法回測" in w for w in result.warnings))

    def test_open_position_is_closed_at_the_end(self):
        """否則未實現損益會被忽略,總報酬會失真。"""
        candles = flat_candles(70)
        fired = {"done": False}

        def signal_fn(history, index):
            if index == 65 and not fired["done"]:
                fired["done"] = True
                return {"direction": "做多", "stop_loss": 50.0, "take_profit": 500.0}
            return None

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_reason, "回測結束")
        self.assertFalse(result.trades[0].is_open)

    def test_accepts_raw_ccxt_rows(self):
        rows = [[i * 3_600_000, 100, 100, 100, 100, 10] for i in range(70)]
        result = BacktestEngine(costs=ZERO_COSTS).run(rows, lambda h, i: None, warmup=60)
        self.assertEqual(result.bars, 70)

    def test_no_second_position_while_one_is_open(self):
        """
        這個引擎一次只持有一個部位。持倉期間不會再問策略 ——
        問了也不能用。加倉與反手要等多部位版本。
        """
        candles = flat_candles(80)

        def signal_fn(history, index):
            return {"direction": "做多", "stop_loss": 50.0, "take_profit": 500.0}

        result = BacktestEngine(costs=ZERO_COSTS).run(candles, signal_fn, warmup=60)

        self.assertEqual(len(result.trades), 1)
        self.assertGreater(result.signals_while_in_position, 0)
