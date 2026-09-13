"""
下單層:三道閘,以及一個會毀掉 Calmar 的設計陷阱。

═══ 那個陷阱 ═══
直覺會想:策略出場是 50 日均線,那就把停損掛在均線上。

回測的規則是「**收盤**跌破均線,隔日開盤出場」。掛單的規則是
「**盤中任何一刻**觸到就成交」。加密貨幣一天插針兩三次是常態,
把停損掛在均線上會在無數個「盤中破線、收盤拉回」的日子被掃出去。

那是另一條策略,而 Calmar 1.33 不是它的數字。

所以這裡的停損是**災難後備**,不是策略出場 —— 而它的位置是一條
Risk Limit,由執政官決定,程式不挑數字。有測試釘住這件事。
"""
import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from exchange.bingx import private, trade


CREDS = private.Credentials(key="k" * 20, secret="s" * 20)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.headers = {}
        self._body = body if body is not None else {"code": 0, "data": {}}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self.responses.pop(0) if self.responses else FakeResponse()


class TestTheLiveGate(unittest.TestCase):
    """第一道閘。"""

    def test_demo_is_allowed(self):
        trade.Trader(CREDS, mode="demo", session=FakeSession())

    def test_live_is_refused_while_live_enabled_is_false(self):
        """
        BINGX_ENV=live **不足以**下實單。環境變數是設定,
        LIVE_ENABLED 是原始碼常數(第 45 / 78 條)。
        """
        from portfolio.execution import LIVE_ENABLED
        self.assertFalse(LIVE_ENABLED, "LIVE_ENABLED 不該是 True")

        with self.assertRaises(trade.NotAllowed) as caught:
            trade.Trader(CREDS, mode="live", session=FakeSession())

        self.assertIn("LIVE_ENABLED", str(caught.exception))

    def test_the_gate_is_checked_again_at_submit_time(self):
        """
        建立到送出之間可能隔很久。只在建構子檢查一次不夠。
        """
        trader = trade.Trader(CREDS, mode="demo", session=FakeSession())
        plan = trade.plan_entry("BTC-USDT", 0.01, "s", "2026-09-13")

        with patch.object(private, "is_live", lambda mode=None: True):
            with self.assertRaises(trade.NotAllowed):
                trader.submit(plan, confirm=True)


class TestDryRunIsTheDefault(unittest.TestCase):
    """第二道閘。"""

    def test_submit_without_confirm_sends_nothing(self):
        session = FakeSession()
        trader = trade.Trader(CREDS, mode="demo", session=session)
        plan = trade.plan_entry("BTC-USDT", 0.01, "s", "2026-09-13")

        result = trader.submit(plan)

        self.assertTrue(result["dry_run"])
        self.assertEqual(session.calls, [], "乾跑不該碰網路")

    def test_dry_run_shows_the_exact_params(self):
        trader = trade.Trader(CREDS, mode="demo", session=FakeSession())
        plan = trade.plan_entry("BTC-USDT", 0.01, "s", "2026-09-13")

        params = trader.submit(plan)["params"]

        self.assertEqual(params["symbol"], "BTC-USDT")
        self.assertEqual(params["side"], "BUY")
        self.assertEqual(params["type"], "MARKET")
        self.assertIn("clientOrderID", params)

    def test_confirm_true_actually_sends(self):
        session = FakeSession()
        trader = trade.Trader(CREDS, mode="demo", session=session)
        plan = trade.plan_entry("BTC-USDT", 0.01, "s", "2026-09-13")

        trader.submit(plan, confirm=True)

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]["method"], "POST")


class TestTheBackstopLevelIsNotChosenByCode(unittest.TestCase):
    """
    ⚠️ 這一組守的是那個設計陷阱。

    2026-09-13 執政官裁定 25%,所以「預設必須是 None」那條退場了 ——
    但它守的東西沒有退場:**程式不准自己挑一條 Risk Limit**。
    改成守「有人決定過,而且說得出誰、何時、根據什麼」。

    一個沒有出處的數字,跟一個程式自己挑的數字,在事後是分不出來的。
    """

    def test_the_number_has_a_recorded_decision_behind_it(self):
        decision = trade.BACKSTOP_DECISION
        self.assertEqual(decision["value_pct"], trade.BACKSTOP_PCT,
                         "記錄裡的數字要跟實際生效的一致 —— "
                         "兩把尺是這個專案犯過十二次的錯")
        for field in ("decided_by", "decided_on", "evidence", "why"):
            self.assertTrue(str(decision.get(field) or "").strip(),
                            f"缺 {field} —— 沒有出處的數字不算決定")

    def test_the_decision_admits_what_it_does_not_cover(self):
        """
        **一條只寫好處的 Risk Limit 是危險的。**

        它會讓下一個看到的人以為這個數字沒有代價,而每一條
        Risk Limit 都有。
        """
        limits = trade.BACKSTOP_DECISION.get("known_limits") or ""
        self.assertTrue(limits.strip(), "缺 known_limits")
        self.assertIn("歷史沒有上限保證", limits,
                      "歷史最深值不是未來的上限,這句話要在")

    def test_the_decision_says_when_to_look_at_it_again(self):
        """槓桿或交易池一改,這個數字的依據就變了。"""
        self.assertTrue(
            str(trade.BACKSTOP_DECISION.get("revisit_when") or "").strip(),
            "缺 revisit_when —— 一條永遠不重新檢視的 Risk Limit "
            "會在依據早就變了之後繼續生效")

    def test_the_backstop_stays_clear_of_liquidation_at_the_leverage_cap(self):
        """
        **停損擺在強平之後等於沒有停損** —— 倉會先被強平。

        而強平距離是我方算的、偏樂觀的(未計維持保證金分層),
        所以不只要「比較近」,還要留餘裕。
        """
        from portfolio.account import MAINT_MARGIN_RATE
        from portfolio.paper import LEVERAGE_CAP

        liq_pct = (1.0 / LEVERAGE_CAP - MAINT_MARGIN_RATE) * 100.0
        self.assertLess(
            trade.BACKSTOP_PCT, liq_pct,
            f"{trade.BACKSTOP_PCT}% 的停損擺在 {LEVERAGE_CAP}× 的強平 "
            f"{liq_pct:.1f}% 之後 —— 停損永遠不會觸發")
        self.assertGreater(
            liq_pct - trade.BACKSTOP_PCT, 5.0,
            f"只剩 {liq_pct - trade.BACKSTOP_PCT:.1f} 個百分點的餘裕。"
            "強平距離是偏樂觀的估計,實際更近 —— 餘裕不夠")

    def test_asking_for_a_price_without_a_setting_still_raises(self):
        """
        哪天有人把 BACKSTOP_PCT 改回 None,行為要跟以前一樣:
        **「沒有人決定停損擺哪裡」不該被一個看起來合理的預設值蓋掉。**
        """
        original = trade.BACKSTOP_PCT
        trade.BACKSTOP_PCT = None
        try:
            with self.assertRaises(trade.NotAllowed) as caught:
                trade.backstop_price(100.0)
        finally:
            trade.BACKSTOP_PCT = original

        text = str(caught.exception)
        self.assertIn("還沒有設定", text)
        self.assertIn("最深的逆向走勢", text, "要說出需要什麼證據")

    def test_the_default_now_produces_the_decided_price(self):
        """不傳 pct 就該拿到執政官裁定的那個數字,不是別的。"""
        self.assertAlmostEqual(trade.backstop_price(100.0), 75.0)

    def test_an_explicit_pct_works(self):
        self.assertAlmostEqual(trade.backstop_price(100.0, pct=25.0), 75.0)

    def test_a_nonsense_pct_is_refused(self):
        for bad in (0, -5, 100, 150):
            with self.subTest(pct=bad):
                with self.assertRaises(ValueError):
                    trade.backstop_price(100.0, pct=bad)


class TestTheBackstopIsReduceOnly(unittest.TestCase):

    def test_a_backstop_is_reduce_only(self):
        """
        沒有 reduceOnly 的停損單,會在倉已經被平掉之後才觸發時
        **開出一個反向的新倉** —— 而那個倉沒有人在管。
        """
        plan = trade.plan_backstop("BTC-USDT", 0.01, entry=100.0,
                                   strategy="s", signal_day="2026-09-13",
                                   pct=25.0)

        self.assertTrue(plan.reduce_only)
        self.assertEqual(plan.params()["reduceOnly"], "true")

    def test_a_backstop_sells_a_long(self):
        plan = trade.plan_backstop("BTC-USDT", 0.01, 100.0, "s",
                                   "2026-09-13", pct=25.0)

        self.assertEqual(plan.side, trade.SELL)
        self.assertEqual(plan.position_side, trade.LONG)
        self.assertEqual(plan.order_type, trade.STOP_MARKET)

    def test_it_says_it_is_not_the_strategy_exit(self):
        plan = trade.plan_backstop("BTC-USDT", 0.01, 100.0, "s",
                                   "2026-09-13", pct=25.0)

        self.assertIn("不是策略出場", plan.reason)


class TestIdempotency(unittest.TestCase):
    """第十六條。"""

    def test_the_same_decision_produces_the_same_id(self):
        a = trade.client_order_id("ma50", "BTC-USDT", "2026-09-13", "entry")
        b = trade.client_order_id("ma50", "BTC-USDT", "2026-09-13", "entry")

        self.assertEqual(a, b)

    def test_different_purposes_differ(self):
        entry = trade.client_order_id("ma50", "BTC-USDT", "2026-09-13",
                                      "entry")
        stop = trade.client_order_id("ma50", "BTC-USDT", "2026-09-13",
                                     "backstop")

        self.assertNotEqual(entry, stop)

    def test_different_days_differ(self):
        self.assertNotEqual(
            trade.client_order_id("ma50", "BTC-USDT", "2026-09-13", "entry"),
            trade.client_order_id("ma50", "BTC-USDT", "2026-09-14", "entry"))

    def test_the_id_is_short_enough_for_the_exchange(self):
        """
        把欄位串起來會超過交易所的長度限制,所以用雜湊。
        """
        long_name = "50日均線之上才持有 + 波動目標 27.0%"
        got = trade.client_order_id(long_name, "1000PEPE-USDT",
                                    "2026-09-13", "backstop")

        self.assertLessEqual(len(got), 32)
        self.assertTrue(got.startswith("agm"))


class TestAFailedSendIsUnknownNotFailed(unittest.TestCase):

    def test_a_network_error_says_the_state_is_unknown(self):
        """
        **這是實盤最貴的一種誤判。**

        送出失敗不代表交易所沒收到。當成「失敗」然後重送,
        就會有兩個倉。
        """
        class Boom:
            def request(self, *a, **kw):
                raise ConnectionError("斷線")

        trader = trade.Trader(CREDS, mode="demo", session=Boom())
        plan = trade.plan_entry("BTC-USDT", 0.01, "s", "2026-09-13")

        with self.assertRaises(trade.OrderFailed) as caught:
            trader.submit(plan, confirm=True)

        text = str(caught.exception)
        self.assertIn("狀態未知", text)
        self.assertIn("不要盲目重送", text)
        self.assertIn(plan.client_id, text, "要給查證用的 id")

    def test_no_signature_leaks_into_the_error(self):
        class Boom:
            def request(self, *a, **kw):
                raise ConnectionError("斷線")

        trader = trade.Trader(CREDS, mode="demo", session=Boom())
        plan = trade.plan_entry("BTC-USDT", 0.01, "s", "2026-09-13")

        with self.assertRaises(trade.OrderFailed) as caught:
            trader.submit(plan, confirm=True)

        self.assertNotIn(CREDS.secret, str(caught.exception))
        self.assertNotIn("signature=", str(caught.exception))


class TestProtectionMustBeOnTheExchange(unittest.TestCase):
    """
    第十八條。**帳本裡有停損欄位,在實盤不代表任何事。**
    """

    def trader(self, orders):
        return trade.Trader(CREDS, mode="demo", session=FakeSession([
            FakeResponse(body={"code": 0, "data": orders})]))

    def test_a_reduce_only_stop_counts_as_protection(self):
        t = self.trader([{"type": "STOP_MARKET", "reduceOnly": True}])
        self.assertTrue(t.has_protection("BTC-USDT"))

    def test_a_stop_without_reduce_only_does_not_count(self):
        """
        它不是保護,是一個會在倉不見之後開出反向新倉的陷阱。
        """
        t = self.trader([{"type": "STOP_MARKET", "reduceOnly": False}])
        self.assertFalse(t.has_protection("BTC-USDT"))

    def test_a_plain_limit_order_does_not_count(self):
        t = self.trader([{"type": "LIMIT", "reduceOnly": True}])
        self.assertFalse(t.has_protection("BTC-USDT"))

    def test_no_orders_means_no_protection(self):
        self.assertFalse(self.trader([]).has_protection("BTC-USDT"))

    def test_an_unreadable_position_size_counts_as_having_a_position(self):
        """
        讀不懂數量就當成沒倉,會讓一個看不懂的部位安靜地失去保護。
        """
        class NoProtection:
            @staticmethod
            def has_protection(_symbol):
                return False

        got = trade.unprotected(
            [{"symbol": "BTC-USDT", "positionAmt": "看不懂"}], NoProtection())

        self.assertEqual(got, ["BTC-USDT"])

    def test_a_flat_position_is_not_reported(self):
        class NoProtection:
            @staticmethod
            def has_protection(_symbol):
                return False

        got = trade.unprotected(
            [{"symbol": "BTC-USDT", "positionAmt": "0"}], NoProtection())

        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()


class TestPositionSideMustNotBeGuessed(unittest.TestCase):
    """
    單向持倉模式下 positionSide 必須是 BOTH,雙向必須是 LONG/SHORT。
    猜錯會被拒單,而錯誤訊息通常只說「參數錯誤」。
    """

    def test_hedge_mode_uses_long(self):
        self.assertEqual(trade.side_for_mode("hedge"), trade.LONG)

    def test_one_way_mode_uses_both(self):
        self.assertEqual(trade.side_for_mode("one_way"), trade.BOTH)

    def test_unknown_mode_raises_rather_than_defaulting(self):
        """
        **這是重點。** 預設成任何一邊都是猜,而猜錯在實盤可能是
        「該平的倉沒平掉」。
        """
        with self.assertRaises(trade.NotAllowed) as caught:
            trade.side_for_mode(None)

        text = str(caught.exception)
        self.assertIn("BOTH", text)
        self.assertIn("LONG", text)

    def test_the_probe_returns_none_when_it_cannot_tell(self):
        session = FakeSession([FakeResponse(status=404)])
        reader = private.ReadOnlyClient(CREDS, mode="demo", session=session)

        self.assertIsNone(reader.position_mode())

    def test_the_probe_reads_dual_side_position(self):
        for raw, expected in ((True, "hedge"), (False, "one_way"),
                              ("true", "hedge"), ("false", "one_way")):
            with self.subTest(raw=raw):
                session = FakeSession([FakeResponse(
                    body={"code": 0, "data": {"dualSidePosition": raw}})])
                reader = private.ReadOnlyClient(CREDS, mode="demo",
                                                session=session)
                self.assertEqual(reader.position_mode(), expected)


class TestMinimumSizeIsCheckedNearTheSend(unittest.TestCase):
    """
    2026-09-09 的事故:七個持倉的數量全部不符精度,真的送出去會被
    直接拒單 —— 而紙上完全看不出來。

    orders.py 已經檢查過,但那是「策略產生訂單」那條路。
    任何繞過 build_orders() 直接組單的地方都沒有人檢查。
    """

    def fake_specs(self, min_qty=0.001, min_notional=5.0):
        from unittest.mock import patch
        from portfolio import specs
        return (patch.object(specs, "min_qty", lambda s: min_qty),
                patch.object(specs, "min_notional", lambda s: min_notional))

    def test_a_size_above_both_floors_passes(self):
        a, b = self.fake_specs()
        with a, b:
            trade.check_size("BTC-USDT", 0.01, 80000.0)

    def test_below_the_minimum_quantity_is_refused(self):
        a, b = self.fake_specs(min_qty=0.01)
        with a, b:
            with self.assertRaises(trade.NotAllowed) as caught:
                trade.check_size("BTC-USDT", 0.001, 80000.0)
        self.assertIn("最小量", str(caught.exception))

    def test_below_the_minimum_notional_is_refused(self):
        a, b = self.fake_specs(min_qty=0.00001, min_notional=100.0)
        with a, b:
            with self.assertRaises(trade.NotAllowed) as caught:
                trade.check_size("BTC-USDT", 0.0001, 80000.0)
        self.assertIn("最小名目", str(caught.exception))


class TestARejectionExplainsItself(unittest.TestCase):
    """
    2026-09-13 第一次送 Demo 單拿到 code=100004。

    那其實是**好消息** —— 簽章、路徑、參數結構全過了,只卡在最後
    一道權限閘。但訊息長得像失敗,下一個人會以為是參數寫錯。
    """

    def reject(self, code, msg="nope"):
        session = FakeSession([FakeResponse(body={"code": code, "msg": msg})])
        trader = trade.Trader(CREDS, mode="demo", session=session)
        plan = trade.plan_entry("BTC-USDT", 0.01, "s", "2026-09-13")
        with self.assertRaises(trade.OrderFailed) as caught:
            trader.submit(plan, confirm=True)
        return str(caught.exception)

    def test_a_permission_error_says_it_is_not_a_parameter_problem(self):
        text = self.reject(100004)

        self.assertIn("權限", text)
        self.assertIn("不是參數問題", text)

    def test_it_warns_that_demo_and_live_share_the_key(self):
        """
        給交易權限之前必須知道這件事。
        """
        text = self.reject(100004)

        self.assertIn("共用同一把金鑰", text)
        self.assertIn("IP 白名單", text)
        self.assertIn("提款權限永遠 OFF", text)

    def test_it_offers_the_route_that_needs_no_new_permission(self):
        text = self.reject(100004)

        self.assertIn("手動開一個最小的倉", text)

    def test_the_exchange_message_is_still_shown(self):
        text = self.reject(100004, msg="the real reason")

        self.assertIn("the real reason", text)

    def test_an_unknown_code_adds_no_invented_hint(self):
        text = self.reject(999999)

        self.assertIn("999999", text)
        self.assertNotIn("權限", text)
