"""
第五十一 / 三十節:事件日曆過期是一個**安靜的**失效。

日曆超過七天沒更新 → news_risk 回 degraded → Macro Agent 棄權 →
LIVE SAFETY GATE 擋實單。三個後果都是對的,但在那之前的每一天,
系統看起來都很正常:Macro Agent 棄權不會亮紅燈,而
「沒有事件」與「不知道有沒有事件」在投票畫面上長得一樣。

這一組測試盯的是那件事現在會主動說話。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from agmcis.risk import calendar_watch, news_risk


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset():
    calendar_watch.reset()
    yield
    calendar_watch.reset()


def write_calendar(tmp_path, generated_at=None, events=None, raw=None):
    target = tmp_path / "news_calendar.json"

    if raw is not None:
        target.write_text(raw, encoding="utf-8")
        return str(target)

    payload = {"events": events if events is not None else []}
    if generated_at is not None:
        payload["generated_at"] = generated_at.isoformat()

    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(target)


def an_event(when, name="FOMC 利率決議", impact="HIGH"):
    return {"name": name, "at": when.isoformat(), "impact": impact}


# ---------------- 狀態判定 ----------------

def test_a_fresh_calendar_is_ok(tmp_path):
    path = write_calendar(
        tmp_path, generated_at=NOW - timedelta(days=1),
        events=[an_event(NOW + timedelta(days=3))],
    )

    state = calendar_watch.inspect_calendar(path=path, now=NOW)
    assert state["status"] == calendar_watch.OK
    assert state["events"] == 1


def test_a_missing_calendar_is_not_the_same_as_a_broken_one(tmp_path):
    """
    還沒做與做壞了,下一步的動作完全不同。
    """
    missing = calendar_watch.inspect_calendar(
        path=str(tmp_path / "nope.json"), now=NOW)
    assert missing["status"] == calendar_watch.MISSING

    broken = calendar_watch.inspect_calendar(
        path=write_calendar(tmp_path, raw="{ this is not json"), now=NOW)
    assert broken["status"] == calendar_watch.BROKEN


def test_an_expired_calendar_is_stale(tmp_path):
    path = write_calendar(
        tmp_path,
        generated_at=NOW - timedelta(days=news_risk.CALENDAR_MAX_AGE_DAYS + 1),
    )

    state = calendar_watch.inspect_calendar(path=path, now=NOW)
    assert state["status"] == calendar_watch.STALE
    assert "沒有在保護任何東西" in state["detail"]


def test_it_warns_before_the_calendar_expires(tmp_path):
    """
    過期的當下,系統已經在「不知道有沒有 FOMC」的狀態下跑了。
    要在還來得及的時候講。
    """
    age = news_risk.CALENDAR_MAX_AGE_DAYS - calendar_watch.WARN_BEFORE_DAYS
    path = write_calendar(tmp_path, generated_at=NOW - timedelta(days=age))

    state = calendar_watch.inspect_calendar(path=path, now=NOW)
    assert state["status"] == calendar_watch.EXPIRING


def test_a_calendar_with_no_timestamp_is_not_trusted(tmp_path):
    """一個沒說自己什麼時候做的日曆不可信。"""
    path = write_calendar(tmp_path, generated_at=None)

    state = calendar_watch.inspect_calendar(path=path, now=NOW)
    assert state["status"] in calendar_watch.NOT_PROTECTING


def test_events_that_fail_to_parse_downgrade_a_fresh_calendar(tmp_path):
    """
    日曆還算數,但那幾筆不見了 —— 而不見的可能正是 FOMC。
    """
    path = write_calendar(
        tmp_path, generated_at=NOW - timedelta(days=1),
        events=[{"name": "壞掉的", "at": "not-a-date", "impact": "HIGH"}],
    )

    state = calendar_watch.inspect_calendar(path=path, now=NOW)
    assert state["status"] == calendar_watch.EXPIRING
    assert state["errors"]


def test_an_empty_but_fresh_calendar_is_ok_and_says_so(tmp_path):
    """
    空的日曆會通過所有檢查。這一條記錄那個事實 ——
    inspect 不會假裝它有問題,但 upcoming 是空的,而腳本會提醒。
    """
    path = write_calendar(tmp_path, generated_at=NOW - timedelta(hours=1))

    state = calendar_watch.inspect_calendar(path=path, now=NOW)
    assert state["status"] == calendar_watch.OK
    assert state["upcoming"] == []


def test_upcoming_only_lists_events_ahead_of_now(tmp_path):
    path = write_calendar(
        tmp_path, generated_at=NOW,
        events=[
            an_event(NOW - timedelta(days=2), name="過去的"),
            an_event(NOW + timedelta(days=1), name="明天"),
            an_event(NOW + timedelta(days=90), name="太遠"),
        ],
    )

    state = calendar_watch.inspect_calendar(path=path, now=NOW)
    assert [e["name"] for e in state["upcoming"]] == ["明天"]


# ---------------- 通知 ----------------

def test_only_a_change_of_state_notifies(tmp_path):
    """
    一份過期三個月的日曆每小時發一次 Telegram,只會讓人把那個通知
    靜音 —— 而那正好讓下一個真正的警報也被靜音。
    """
    path = write_calendar(
        tmp_path,
        generated_at=NOW - timedelta(days=news_risk.CALENDAR_MAX_AGE_DAYS + 1),
    )

    sent = []
    for _ in range(3):
        calendar_watch.run_calendar_watch(
            path=path, now=NOW, notify=sent.append)

    assert len(sent) == 1


def test_recovery_notifies_too(tmp_path):
    """只講壞消息的話,使用者不知道自己修好了沒有。"""
    stale = write_calendar(
        tmp_path,
        generated_at=NOW - timedelta(days=news_risk.CALENDAR_MAX_AGE_DAYS + 1),
    )

    sent = []
    calendar_watch.run_calendar_watch(path=stale, now=NOW, notify=sent.append)

    fresh = write_calendar(tmp_path, generated_at=NOW - timedelta(hours=1))
    calendar_watch.run_calendar_watch(path=fresh, now=NOW, notify=sent.append)

    assert len(sent) == 2
    assert "OK" in sent[1]


def test_a_healthy_calendar_does_not_notify_on_the_first_check(tmp_path):
    """開機時一切正常,不需要通知任何人。"""
    path = write_calendar(tmp_path, generated_at=NOW - timedelta(hours=1))

    sent = []
    calendar_watch.run_calendar_watch(path=path, now=NOW, notify=sent.append)

    assert sent == []


def test_the_alert_says_what_is_actually_at_risk(tmp_path):
    path = write_calendar(
        tmp_path,
        generated_at=NOW - timedelta(days=news_risk.CALENDAR_MAX_AGE_DAYS + 1),
    )

    sent = []
    calendar_watch.run_calendar_watch(path=path, now=NOW, notify=sent.append)

    assert "FOMC" in sent[0] or "沒有在保護" in sent[0]


def test_a_failing_notifier_does_not_break_the_job(tmp_path, caplog):
    def boom(_):
        raise RuntimeError("telegram down")

    path = write_calendar(tmp_path, generated_at=None)

    with caplog.at_level(logging.WARNING):
        state = calendar_watch.run_calendar_watch(path=path, now=NOW, notify=boom)

    assert state["status"] in calendar_watch.NOT_PROTECTING
    assert "NOTIFY_FAILED" in caplog.text


def test_it_never_invents_dates():
    """
    FOMC 的日期是公布的,不是算出來的;CPI 與 NFP 會因為假日與
    日光節約時間移動。一份**看起來新、內容是猜的**日曆會通過所有檢查,
    然後在真正的 FOMC 當天讓系統照常開倉。

    用 AST 檢查**程式碼裡**有沒有日期字面值 —— docstring 裡的
    使用範例不算,那是在教人怎麼用。
    """
    import ast
    import re
    from pathlib import Path

    date_like = re.compile(r"\d{4}-\d{2}-\d{2}")

    for name in ("agmcis/risk/calendar_watch.py", "scripts/calendar.py"):
        source = Path(name).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # docstring 節點的位置,用來排除
        docstrings = {
            id(node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }

        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings
                    and date_like.search(node.value)):
                raise AssertionError(
                    f"{name}:{node.lineno} 有一個日期字面值 "
                    f"{node.value!r} —— 日期只能從官方來源複製進日曆檔"
                )

        # 也不該用日期推算套件生「每月第一個星期五」這種東西
        for forbidden in ("relativedelta", "rrule", "calendar.monthcalendar"):
            assert forbidden not in source, f"{name} 不該推算日期({forbidden})"


# ---------------- /health ----------------

def test_health_reports_the_calendar(tmp_path):
    from unittest.mock import patch

    from api import health

    with patch.object(calendar_watch, "inspect_calendar",
                      return_value={"status": calendar_watch.STALE}):
        assert health._news_calendar() == health.ERROR

    with patch.object(calendar_watch, "inspect_calendar",
                      return_value={"status": calendar_watch.EXPIRING}):
        assert health._news_calendar() == health.DEGRADED

    with patch.object(calendar_watch, "inspect_calendar",
                      return_value={"status": calendar_watch.OK}):
        assert health._news_calendar() == health.OK


def test_a_degraded_component_downgrades_the_page_but_is_not_a_failure():
    """
    degraded 不算 failed(監控不該為它半夜叫人),但整體狀態要降級 ——
    一個回 healthy 的系統沒有人會去看細節。
    """
    from unittest.mock import patch

    from api import health
    from tests.test_health import build

    payload = build(_news_calendar=lambda: health.DEGRADED)

    assert payload["status"] == "degraded"
    assert payload["failed"] == []
    assert payload["degraded"] == ["news_calendar"]


def test_the_calendar_is_not_a_critical_component():
    """
    日曆過期不代表不能交易,代表少一層保護。
    把它列為 critical 會讓 /health 回 503,而負載平衡器會把
    一個還能正常交易的系統拿掉。
    """
    from api import health

    assert "news_calendar" not in health.CRITICAL_COMPONENTS


# ---------------- 維護腳本 ----------------

def _script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "calendar_script", "scripts/calendar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adding_an_event_restamps_the_timestamp(tmp_path):
    """
    手改 JSON 最容易忘記改 generated_at —— 改了事件卻沒改時間戳,
    系統仍然當它過期。
    """
    script = _script()
    path = tmp_path / "cal.json"

    payload = {"events": []}
    assert script.add_event(
        payload, "FOMC", "2099-01-01T19:00Z", "HIGH") is None

    stamp = script._save(str(path), payload)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["generated_at"] == stamp
    assert len(saved["events"]) == 1


def test_a_past_event_is_refused():
    script = _script()
    problem = script.add_event({"events": []}, "舊的", "2000-01-01T00:00Z", "HIGH")

    assert problem is not None
    assert "已經過去" in problem


def test_a_bad_impact_level_is_refused():
    script = _script()
    problem = script.add_event(
        {"events": []}, "X", "2099-01-01T00:00Z", "VERY_HIGH")

    assert problem is not None
    assert "HIGH" in problem


def test_an_unparseable_time_is_refused():
    script = _script()
    problem = script.add_event({"events": []}, "X", "下週三", "HIGH")

    assert problem is not None
    assert "解析" in problem


def test_duplicates_are_refused():
    script = _script()
    payload = {"events": []}

    assert script.add_event(payload, "FOMC", "2099-01-01T19:00Z", "HIGH") is None
    assert script.add_event(payload, "FOMC", "2099-01-01T19:00Z", "HIGH") is not None
    assert len(payload["events"]) == 1


def test_prune_keeps_events_it_cannot_parse():
    """
    解析不了的是一個要人去看的問題。清掉等於把問題藏起來。
    """
    script = _script()
    payload = {"events": [
        {"name": "過去", "at": "2000-01-01T00:00:00+00:00"},
        {"name": "壞掉", "at": "???"},
        {"name": "未來", "at": "2099-01-01T00:00:00+00:00"},
    ]}

    removed = script.prune(payload, now=NOW)

    assert removed == 1
    assert [e["name"] for e in payload["events"]] == ["壞掉", "未來"]


def test_events_stay_sorted():
    script = _script()
    payload = {"events": []}

    script.add_event(payload, "晚", "2099-06-01T00:00Z", "HIGH")
    script.add_event(payload, "早", "2099-01-01T00:00Z", "HIGH")

    assert [e["name"] for e in payload["events"]] == ["早", "晚"]
