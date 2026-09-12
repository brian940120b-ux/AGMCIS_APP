"""
News Risk(Master Prompt 第五十一節)。

FOMC 公布前十分鐘的技術面跟平常沒有兩樣:趨勢在、動能在、量能在。
所有指標都不知道三分鐘後會發生什麼。這一層做的是讓系統知道。

這組測試最在意兩件事:
  一、封鎖只擋開倉,**永遠不擋平倉**。
  二、日曆不可信的時候,模擬盤與實單的處理必須不一樣 ——
      而且「不一樣」要是刻意的,不是漏掉的。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.models import TradeIntent
from agmcis.risk import news_risk
from agmcis.risk.engine import AccountState, RiskEngine

NOW = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)

TMP = tempfile.mkdtemp(prefix="agmcis-test-news-")


def event(name="FOMC", minutes_from_now=0, impact=news_risk.HIGH, **kwargs):
    return news_risk.MacroEvent(
        name=name,
        at=NOW + timedelta(minutes=minutes_from_now),
        impact=impact,
        **kwargs,
    )


def calendar(events=None, age_days=0, generated_at=...):
    return news_risk.Calendar(
        events=list(events or []),
        generated_at=(NOW - timedelta(days=age_days))
                     if generated_at is ... else generated_at,
    )


def intent(**overrides):
    base = dict(symbol="BTC/USDT", market_type="perpetual", direction="做多",
                entry=65000.0, stop_loss=63050.0, take_profit=69000.0)
    base.update(overrides)
    return TradeIntent(**base)


LIMITS = {
    "MAX_RISK_PER_TRADE_PCT": 1.0, "MAX_DRAWDOWN_PCT": 15, "MAX_EXPOSURE_PCT": 80,
    "MAX_OPEN_POSITIONS": 5, "MAX_LEVERAGE": 5, "MAX_DAILY_LOSS_USDT": 300,
    "MAX_TOTAL_OPEN_LOSS_USDT": -300, "MAX_CONSECUTIVE_LOSSES": 4,
    "MAX_TRADES_PER_DAY": 10, "MIN_PROFIT_FACTOR": 0.8,
    "MAX_WEEKLY_LOSS_USDT": 900, "AUTO_TRADING_ENABLED": True,
    "MAX_SYMBOL_EXPOSURE_PCT": 50, "MAX_CORRELATED_RISK_PCT": 3.0,
    "EMERGENCY_STOP_FILE": "/nonexistent/emergency.stop",
    "TRADING_PAUSE_FILE": "/nonexistent/trading_pause.flag",
}


def engine():
    return RiskEngine(limits=dict(LIMITS), mode="paper")


def state(**overrides):
    base = dict(equity=10000.0, available_balance=10000.0, profit_factor=1.5)
    base.update(overrides)
    return AccountState(**base)


class TestTheBlackoutWindow(unittest.TestCase):

    def test_a_high_impact_event_blocks_entry(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([event()]))

        self.assertTrue(risk.blocks_entry)
        self.assertEqual(risk.level, news_risk.HIGH)

    def test_the_window_opens_before_the_announcement(self):
        """
        公布**之後**才開始擋沒有意義 —— 傷害在那一根 K 棒就發生了。
        """
        risk = news_risk.assess(
            now=NOW, calendar=calendar([event(minutes_from_now=20)]),
        )

        self.assertTrue(risk.blocks_entry)

    def test_the_window_stays_open_after_the_announcement(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([event(minutes_from_now=-20)]),
        )

        self.assertTrue(risk.blocks_entry)

    def test_outside_the_window_nothing_happens(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([event(minutes_from_now=600)]),
        )

        self.assertFalse(risk.blocks_entry)
        self.assertEqual(risk.level, news_risk.NONE)
        self.assertEqual(risk.risk_multiplier, 1.0)

    def test_the_window_size_is_per_event(self):
        """FOMC 的餘波比 PPI 長。用同一個窗寬對兩者都不對。"""
        wide = event(minutes_from_now=-70, after_minutes=90)
        narrow = event(minutes_from_now=-70, after_minutes=30)

        self.assertTrue(
            news_risk.assess(now=NOW, calendar=calendar([wide])).blocks_entry
        )
        self.assertFalse(
            news_risk.assess(now=NOW, calendar=calendar([narrow])).blocks_entry
        )

    def test_an_empty_calendar_blocks_nothing(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([]))

        self.assertFalse(risk.blocks_entry)
        self.assertFalse(risk.degraded)


class TestSeverity(unittest.TestCase):

    def test_a_medium_event_halves_the_risk_instead_of_blocking(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([event(impact=news_risk.MEDIUM)]),
        )

        self.assertFalse(risk.blocks_entry)
        self.assertEqual(risk.risk_multiplier, news_risk.MEDIUM_RISK_MULTIPLIER)
        self.assertEqual(risk.level, news_risk.MEDIUM)

    def test_a_low_event_only_records(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([event(impact=news_risk.LOW)]),
        )

        self.assertFalse(risk.blocks_entry)
        self.assertEqual(risk.risk_multiplier, 1.0)
        self.assertTrue(risk.reasons)

    def test_the_strictest_active_event_wins(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([
            event(name="PPI", impact=news_risk.MEDIUM),
            event(name="FOMC", impact=news_risk.HIGH),
        ]))

        self.assertTrue(risk.blocks_entry)
        self.assertEqual(risk.level, news_risk.HIGH)

    def test_two_medium_events_do_not_stack_below_half(self):
        """減半是減半。疊成 0.25 會變成一個沒有人設計過的數字。"""
        risk = news_risk.assess(now=NOW, calendar=calendar([
            event(name="A", impact=news_risk.MEDIUM),
            event(name="B", impact=news_risk.MEDIUM),
        ]))

        self.assertEqual(risk.risk_multiplier, news_risk.MEDIUM_RISK_MULTIPLIER)


class TestUnscheduledShocks(unittest.TestCase):

    def test_a_hack_headline_blocks_entry(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([]),
            headlines=[{"title": "Major exchange hacked, $200M drained"}],
        )

        self.assertTrue(risk.blocks_entry)
        self.assertEqual(risk.level, news_risk.HIGH)
        self.assertTrue(risk.shocks)

    def test_chinese_headlines_are_scanned_too(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([]),
            headlines=[{"title": "某交易所遭駭,暫停提領"}],
        )

        self.assertTrue(risk.blocks_entry)

    def test_ordinary_headlines_do_not_block(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([]), headlines=[
            {"title": "Bitcoin rises 3% as ETF inflows continue"},
            {"title": "分析師看好第四季行情"},
        ])

        self.assertFalse(risk.blocks_entry)

    def test_plain_strings_are_accepted_as_headlines(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([]),
            headlines=["Exchange halts withdrawals amid insolvency rumours"],
        )

        self.assertTrue(risk.blocks_entry)

    def test_an_empty_headline_is_skipped_rather_than_crashing(self):
        risk = news_risk.assess(
            now=NOW, calendar=calendar([]),
            headlines=[{"title": ""}, {}, {"title": None}],
        )

        self.assertFalse(risk.blocks_entry)

    def test_each_headline_is_reported_once(self):
        """同一個標題命中兩個關鍵字不該變成兩個事件。"""
        risk = news_risk.assess(
            now=NOW, calendar=calendar([]),
            headlines=[{"title": "Exchange hacked and drained after exploit"}],
        )

        self.assertEqual(len(risk.shocks), 1)


class TestAStaleCalendarIsVisibleButNotFatal(unittest.TestCase):
    """
    日曆過期代表「不知道 FOMC 是不是十分鐘後」。照系統其他地方的慣例
    「不知道」該當成「不安全」,但照搬會讓一個沒人更新的檔案永遠停掉
    交易 —— 那不是保守,那是故障。所以模擬盤只警告,實單由 LIVE GATE 擋。
    """

    def test_an_old_calendar_is_degraded_but_does_not_block(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([], age_days=30))

        self.assertTrue(risk.degraded)
        self.assertFalse(risk.blocks_entry)
        self.assertTrue(risk.warnings)

    def test_a_calendar_without_a_timestamp_is_stale(self):
        """沒說自己什麼時候做的日曆,跟一個很舊的日曆一樣不可信。"""
        risk = news_risk.assess(
            now=NOW, calendar=calendar([], generated_at=None),
        )

        self.assertTrue(risk.degraded)

    def test_a_fresh_calendar_is_not_degraded(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([], age_days=1))

        self.assertFalse(risk.degraded)

    def test_a_stale_calendar_still_honours_the_events_it_does_have(self):
        """
        過期不等於作廢。裡面那筆 FOMC 還是真的 FOMC ——
        只是可能有別的事件沒被記進來。
        """
        risk = news_risk.assess(
            now=NOW, calendar=calendar([event()], age_days=30),
        )

        self.assertTrue(risk.blocks_entry)
        self.assertTrue(risk.degraded)

    def test_the_live_gate_refuses_a_stale_calendar(self):
        from agmcis.safety.live_gate import LiveGate

        gate = LiveGate(
            confirmation_file="/nonexistent.json",
            audit_file=os.path.join(TMP, "audit.log"),
            now=lambda: NOW,
            providers={"news_calendar": lambda: calendar([], age_days=30)},
        )

        check = gate.check_news_calendar()
        self.assertFalse(check.passed)
        self.assertIn("30 天", check.detail)

    def test_the_live_gate_accepts_a_fresh_calendar(self):
        from agmcis.safety.live_gate import LiveGate

        gate = LiveGate(
            confirmation_file="/nonexistent.json",
            audit_file=os.path.join(TMP, "audit.log"),
            now=lambda: NOW,
            providers={
                "news_calendar": lambda: calendar([event(minutes_from_now=9999)]),
            },
        )

        check = gate.check_news_calendar()
        self.assertTrue(check.passed, check.detail)

    def test_a_provider_that_raises_fails_the_gate(self):
        from agmcis.safety.live_gate import LiveGate

        def explode():
            raise RuntimeError("檔案系統掛了")

        gate = LiveGate(
            confirmation_file="/nonexistent.json",
            audit_file=os.path.join(TMP, "audit.log"),
            now=lambda: NOW,
            providers={"news_calendar": explode},
        )

        self.assertFalse(gate.check_news_calendar().passed)


class TestLoadingTheCalendarNeverRaises(unittest.TestCase):
    """
    讀檔失敗不能變成例外往上炸 —— 那會讓一次設定錯誤停掉整個排程。
    失敗要變成「errors 不為空 + 沒有 generated_at」,也就是過期。
    """

    def _write(self, name, text):
        path = os.path.join(TMP, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_a_missing_file_is_reported_not_raised(self):
        loaded = news_risk.load_calendar(os.path.join(TMP, "nope.json"))

        self.assertEqual(loaded.events, [])
        self.assertTrue(loaded.errors)
        self.assertTrue(loaded.is_stale(NOW))

    def test_broken_json_is_reported_not_raised(self):
        path = self._write("broken.json", "{ not json")
        loaded = news_risk.load_calendar(path)

        self.assertTrue(loaded.errors)
        self.assertTrue(loaded.is_stale(NOW))

    def test_one_unparseable_event_does_not_discard_the_others(self):
        path = self._write("mixed.json", json.dumps({
            "generated_at": "2026-09-16T00:00:00Z",
            "events": [
                {"name": "好的", "at": "2026-09-17T18:00:00Z"},
                {"name": "壞的", "at": "下午六點"},
            ],
        }))
        loaded = news_risk.load_calendar(path)

        self.assertEqual([e.name for e in loaded.events], ["好的"])
        self.assertTrue(any("壞的" in e for e in loaded.errors))

    def test_a_valid_file_loads_with_its_windows(self):
        path = self._write("good.json", json.dumps({
            "generated_at": "2026-09-16T00:00:00Z",
            "events": [{
                "name": "FOMC", "at": "2026-09-17T18:00:00Z",
                "impact": "HIGH", "before_minutes": 60, "after_minutes": 90,
            }],
        }))
        loaded = news_risk.load_calendar(path)

        self.assertFalse(loaded.is_stale(NOW))
        self.assertTrue(loaded.events[0].contains(NOW + timedelta(minutes=80)))
        self.assertFalse(loaded.events[0].contains(NOW + timedelta(minutes=100)))

    def test_the_shipped_example_file_actually_parses(self):
        """
        範例檔是使用者唯一的格式說明。它自己不能是壞的。
        """
        loaded = news_risk.load_calendar("config/news_calendar.example.json")

        self.assertEqual(loaded.errors, [])
        self.assertTrue(loaded.events)


class TestTheRiskEngineHonoursIt(unittest.TestCase):

    def test_a_blackout_rejects_the_trade(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([event()]))
        decision = engine().evaluate(
            intent(), state(), atr=500, mtf_score=3, news=risk,
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.blockers, ["NEWS_BLACKOUT"])
        self.assertIn("FOMC", decision.reason)

    def test_a_medium_event_halves_the_position(self):
        quiet = engine().evaluate(intent(), state(), atr=500, mtf_score=3)
        risk = news_risk.assess(
            now=NOW, calendar=calendar([event(impact=news_risk.MEDIUM)]),
        )
        reduced = engine().evaluate(
            intent(), state(), atr=500, mtf_score=3, news=risk,
        )

        self.assertTrue(reduced.approved, reduced.reason)
        self.assertAlmostEqual(reduced.notional, quiet.notional / 2, places=4)

    def test_no_news_object_changes_nothing(self):
        """
        消息面是選填的。沒傳等於沒有這一層,不是等於「已知安全」——
        差別在 LIVE GATE 那邊要求日曆必須新。
        """
        with_none = engine().evaluate(intent(), state(), atr=500, mtf_score=3)
        self.assertTrue(with_none.approved, with_none.reason)

    def test_an_approved_trade_carries_the_news_warnings(self):
        risk = news_risk.assess(now=NOW, calendar=calendar([], age_days=30))
        decision = engine().evaluate(
            intent(), state(), atr=500, mtf_score=3, news=risk,
        )

        self.assertTrue(decision.approved, decision.reason)
        self.assertTrue(any("日曆" in w for w in decision.warnings))

    def test_closing_a_position_is_never_blocked_by_news(self):
        """
        重大事件期間最需要能出場。封鎖只掛在開倉的路徑上 ——
        這個測試確認平倉根本不經過這一層。
        """
        import inspect

        from agmcis.execution.engine import ExecutionEngine

        source = inspect.getsource(ExecutionEngine.close)
        self.assertNotIn("news", source)


if __name__ == "__main__":
    unittest.main()
