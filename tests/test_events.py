"""
事件日曆(第五十一條)。

═══ 這一組的重心 ═══
不是「查得到事件」,是**「查不到的時候說了什麼」**。

  「沒有載入日曆」  = 我不知道
  「今天沒有事件」  = 一個確定的判斷

這兩句話混為一談,就是這一節唯一真正的失敗模式:一個空的日曆
會永遠回「沒事」,而它看起來一直在工作。教訓第 5 條。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio import events


class Calendar(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows):
        self.path.write_text(json.dumps({"events": rows}, ensure_ascii=False),
                             encoding="utf-8")


class TestItReadsACalendar(Calendar):

    def test_events_come_back_sorted(self):
        self.write([
            {"date": "2026-12-10", "kind": "CPI", "level": "HIGH"},
            {"date": "2026-11-05", "kind": "FOMC", "level": "HIGH",
             "note": "利率決議"},
        ])

        got = events.load(self.path)

        self.assertEqual([e.on.isoformat() for e in got],
                         ["2026-11-05", "2026-12-10"])
        self.assertEqual(got[0].note, "利率決議")

    def test_upcoming_respects_the_window(self):
        today = date(2026, 9, 13)
        self.write([
            {"date": "2026-09-14", "kind": "CPI", "level": "HIGH"},
            {"date": "2026-10-30", "kind": "FOMC", "level": "HIGH"},
        ])

        got = events.upcoming(today, within_days=14, path=self.path)

        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].kind, "CPI")

    def test_a_past_event_is_not_upcoming(self):
        today = date(2026, 9, 13)
        self.write([{"date": "2026-09-01", "kind": "CPI", "level": "HIGH"}])

        self.assertEqual(events.upcoming(today, path=self.path), [])


class TestItRefusesToSayNothingHappened(Calendar):
    """⚠️ 這一組是整個檔案的重點。"""

    def test_a_missing_calendar_raises_rather_than_returning_empty(self):
        """
        回空清單會讓呼叫端寫出 `if not events(): "今天沒事"` ——
        而那句話是錯的。
        """
        with self.assertRaises(events.CalendarMissing):
            events.load(self.path)

    def test_an_empty_calendar_is_also_missing(self):
        self.path.write_text('{"events": []}', encoding="utf-8")

        with self.assertRaises(events.CalendarMissing):
            events.load(self.path)

    def test_a_broken_calendar_raises_instead_of_being_ignored(self):
        self.path.write_text("{ not json", encoding="utf-8")

        with self.assertRaises(events.CalendarMissing):
            events.load(self.path)

    def test_a_stale_calendar_is_detected(self):
        """
        停在半年前的日曆,查詢起來永遠回「沒有事件」——
        最安靜的一種壞掉:它看起來一直在工作。
        """
        today = date(2026, 9, 13)
        self.write([{"date": "2026-01-01", "kind": "FOMC", "level": "HIGH"}])

        with self.assertRaises(events.CalendarStale):
            events.check_freshness(today, self.path)

    def test_status_distinguishes_three_different_answers(self):
        today = date(2026, 9, 13)

        # 一、不知道
        st = events.status(today, self.path)
        self.assertFalse(st["loaded"])
        self.assertEqual(st["today"], [])

        # 二、在看一份沒人維護的
        self.write([{"date": "2026-01-01", "kind": "FOMC", "level": "HIGH"}])
        st = events.status(today, self.path)
        self.assertTrue(st["loaded"])
        self.assertTrue(st["stale"])

        # 三、知道,而答案是「今天有事」
        self.write([{"date": "2026-09-13", "kind": "FOMC", "level": "HIGH"},
                    {"date": "2026-09-20", "kind": "CPI", "level": "HIGH"}])
        st = events.status(today, self.path)
        self.assertTrue(st["loaded"])
        self.assertFalse(st["stale"])
        self.assertEqual(len(st["today"]), 1)
        self.assertEqual(len(st["upcoming"]), 1)


class TestBadDataIsRejectedNotAbsorbed(Calendar):

    def test_a_misspelled_kind_raises(self):
        """
        打錯的類別會變成一個沒有人在看的分類。
        """
        self.write([{"date": "2026-09-13", "kind": "FOMCC", "level": "HIGH"}])

        with self.assertRaises(ValueError) as caught:
            events.load(self.path)
        self.assertIn("FOMCC", str(caught.exception))

    def test_a_bad_date_raises(self):
        self.write([{"date": "下週三", "kind": "FOMC", "level": "HIGH"}])

        with self.assertRaises(ValueError):
            events.load(self.path)

    def test_an_unknown_level_raises(self):
        self.write([{"date": "2026-09-13", "kind": "FOMC", "level": "極高"}])

        with self.assertRaises(ValueError):
            events.load(self.path)


class TestItDoesNotChangeTrading(unittest.TestCase):
    """
    第一批的界線:看得見,不閃避。

    擋掉 FOMC 當天的進場會改變進場日期,而那是不同的報酬序列 ——
    Calmar 1.33 立刻不算數。那屬於要重新回測的那一批。
    """

    def test_the_risk_engine_does_not_import_the_calendar(self):
        import inspect

        from portfolio import risk

        self.assertNotIn("events", inspect.getsource(risk),
                         "風控引擎不該讀事件日曆 —— 那會改變交易決策")

    def test_the_order_builder_does_not_import_the_calendar(self):
        import inspect

        from portfolio import orders

        self.assertNotIn("import events", inspect.getsource(orders))
        self.assertNotIn("from portfolio import events",
                         inspect.getsource(orders))

    def test_the_ledger_records_unknown_and_none_differently(self):
        """
        帳本裡 None 代表「不知道」,[] 代表「確定沒有」。
        把「沒查」寫成「沒有」,是把空白偽裝成結論。
        """
        from unittest.mock import patch

        from portfolio import events as ev
        from portfolio.paper import _events_on

        # 有日曆、當天沒事 -> [](確定沒有)
        self.assertEqual(_events_on(date(2026, 9, 13)), [])

        # 有日曆、當天有事 -> 那一筆
        got = _events_on(date(2026, 9, 16))
        self.assertTrue(got)
        self.assertEqual(got[0]["kind"], "FOMC")

        # 日曆整個不見 -> None(不知道),**不是** []
        missing = Path("/nonexistent/events.json")
        with patch.object(ev, "SHIPPED", missing), \
                patch.object(ev, "OVERRIDE", missing):
            self.assertIsNone(_events_on(date(2026, 9, 13)))


class TestTheShippedCalendarIsReal(unittest.TestCase):
    """
    版控裡那份是真的要拿來用的,不是範例。
    """

    def test_it_parses_with_the_real_loader(self):
        got = events.load(events.SHIPPED)
        self.assertTrue(got)

    def test_it_records_where_the_dates_came_from(self):
        """
        一份沒有註明來源的日曆,下一個人沒有辦法查證,
        也不知道什麼時候該更新。
        """
        raw = json.loads(events.SHIPPED.read_text(encoding="utf-8"))
        source = raw.get("_source") or {}

        self.assertIn("查證日期", source)
        self.assertIn("federalreserve.gov", json.dumps(source))
        self.assertIn("bls.gov", json.dumps(source))

    def test_it_is_not_already_stale(self):
        """
        一份已經過期的日曆會永遠回「沒有事件」。
        這條紅了就是該更新版控裡那份了。
        """
        try:
            events.check_freshness(path=events.SHIPPED)
        except events.CalendarStale as e:
            self.fail(f"{e}\n\n去 federalreserve.gov / bls.gov 抓新的日期,"
                      "更新 docs/events.json。")

    def test_a_local_override_wins_when_present(self):
        self.assertEqual(events.source(), events.SHIPPED)

    def test_no_guessed_2027_cpi_dates(self):
        """
        2026-09-13 查證時 BLS 還沒發布 2027 的 CPI 時間表。

        「通常是第二週的週二到週五」不是資料,是推測。推測出來的
        日期會讓人以為有在看,而那正是這一節最該避免的事。
        這條擋住之後有人「順手補齊」。
        """
        guessed = [e for e in events.load(events.SHIPPED)
                   if e.kind == "CPI" and e.on.year >= 2027]

        self.assertEqual(
            [e.on.isoformat() for e in guessed], [],
            "2027 的 CPI 日期只能從 BLS 公布的時間表來 —— "
            "不可以用發布規律推測。確認是官方來源就把這條測試更新掉。")


if __name__ == "__main__":
    unittest.main()
