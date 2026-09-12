"""
事件日曆的保鮮監控(Master Prompt 第五十一 / 三十節)。

## 這個模組修的是一個安靜的失效

第五十一節的事件日曆是一份**人工維護**的檔案。`Calendar.is_stale()`
會在超過七天沒更新時回 True,而那件事的後果是:

  * Risk Engine 的 `news_risk` 回傳 `degraded=True`
  * Macro Agent 從「有沒有事件」變成「不知道有沒有事件」而棄權
  * LIVE SAFETY GATE 在實單那一側擋下來

三個後果都是對的。問題在於**沒有人會知道它過期了**,直到有一天
想開實單、被閘門擋住、才回頭發現日曆停在三個月前。

在那之前的每一天,系統看起來都很正常 —— Macro Agent 棄權不會亮紅燈,
而「沒有事件」與「不知道有沒有事件」在投票畫面上長得很像。

這個模組把那件事變成一個會主動說話的東西。

## 為什麼要提前警告,不是過期才說

日曆過期的當下,系統已經在「不知道有沒有 FOMC」的狀態下跑了。
要在**還來得及**的時候講,所以有一個 `WARN_BEFORE_DAYS` 的提前量。

## 它不會自己去抓日曆

FOMC 的日期是公布的,不是算出來的;CPI 與 NFP 的規則會因為假日
與日光節約時間而移動。從模型記憶裡生一份日期出來,比沒有日曆更糟 ——
一份**看起來新、內容是猜的**日曆會通過所有檢查,然後在真正的
FOMC 當天讓系統照常開倉。

第五節的原則(不要靠模型記憶猜 API)在這裡一樣適用。
"""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("agmcis.risk.calendar_watch")

# 還剩幾天到期就開始提醒。日曆有效期是七天,提前兩天講還來得及維護。
WARN_BEFORE_DAYS = 2

OK = "OK"
EXPIRING = "EXPIRING"
STALE = "STALE"
MISSING = "MISSING"
BROKEN = "BROKEN"

# 這幾個狀態代表消息面那一層現在**沒有在保護任何東西**。
NOT_PROTECTING = (STALE, MISSING, BROKEN)


def inspect_calendar(path=None, now=None):
    """
    日曆現在的狀態。純讀取,不寫任何東西 —— 測試可以直接比對。

    回傳 dict,一定有 `status` 與 `detail`。
    """
    from agmcis.risk import news_risk

    now = now or datetime.now(timezone.utc)
    calendar = news_risk.load_calendar(path)

    payload = {
        "status": OK,
        "detail": "",
        "source": calendar.source,
        "events": len(calendar.events),
        "errors": list(calendar.errors),
        "generated_at": (
            calendar.generated_at.isoformat() if calendar.generated_at else None
        ),
        "age_days": None,
        "upcoming": _upcoming(calendar, now),
    }

    if calendar.errors and not calendar.generated_at:
        # 檔案不存在與檔案壞掉要分開 —— 前者是還沒做,後者是做壞了,
        # 而兩者的下一步動作完全不同。
        missing = any("不存在" in error for error in calendar.errors)
        payload["status"] = MISSING if missing else BROKEN
        payload["detail"] = "; ".join(calendar.errors)
        return payload

    age = now - calendar.generated_at
    payload["age_days"] = round(age.total_seconds() / 86400, 2)

    if calendar.is_stale(now):
        payload["status"] = STALE
        payload["detail"] = (
            f"日曆已 {payload['age_days']:.1f} 天沒更新"
            f"(有效期 {news_risk.CALENDAR_MAX_AGE_DAYS} 天)。"
            f"消息面風險這一層現在沒有在保護任何東西。"
        )
        return payload

    remaining = timedelta(days=news_risk.CALENDAR_MAX_AGE_DAYS) - age
    if remaining <= timedelta(days=WARN_BEFORE_DAYS):
        payload["status"] = EXPIRING
        payload["detail"] = (
            f"日曆再 {remaining.total_seconds() / 86400:.1f} 天就過期。"
            f"請更新 config/news_calendar.json,或跑 "
            f"python scripts/calendar.py --touch。"
        )
        return payload

    if calendar.errors:
        # 有 generated_at 但某幾筆事件解析失敗 —— 日曆還算數,
        # 但那幾筆不見了,而不見的可能正是 FOMC。
        payload["status"] = EXPIRING
        payload["detail"] = "部分事件無法解析:" + "; ".join(calendar.errors[:3])
        return payload

    payload["detail"] = (
        f"{len(calendar.events)} 筆事件,{payload['age_days']:.1f} 天前更新。"
    )
    return payload


def _upcoming(calendar, now, limit=3, horizon_days=14):
    """接下來兩週的事件。給人看的 —— 讓「有沒有在維護」一眼看得出來。"""
    horizon = now + timedelta(days=horizon_days)

    rows = sorted(
        (event for event in calendar.events if now <= event.at <= horizon),
        key=lambda event: event.at,
    )

    return [
        {
            "name": event.name,
            "at": event.at.isoformat(),
            "impact": event.impact,
        }
        for event in rows[:limit]
    ]


# 上一次回報的狀態。只在**轉態**時發通知 —— 一份過期三個月的日曆
# 每小時發一次 Telegram,只會讓人把那個通知靜音,而那正好讓下一個
# 真正的警報也被靜音。
_last_status = None


def reset():
    """測試用。"""
    global _last_status
    _last_status = None


def run_calendar_watch(path=None, now=None, notify=None):
    """
    排程工作。回傳 inspect_calendar() 的結果,並在狀態改變時通知。
    """
    global _last_status

    payload = inspect_calendar(path=path, now=now)
    status = payload["status"]

    if status == _last_status:
        return payload

    previous, _last_status = _last_status, status

    level = "ERROR" if status in NOT_PROTECTING else (
        "WARNING" if status == EXPIRING else "INFO"
    )

    if status in NOT_PROTECTING:
        logger.error("Calendar | %s | %s", status, payload["detail"])
    elif status == EXPIRING:
        logger.warning("Calendar | %s | %s", status, payload["detail"])
    else:
        logger.info("Calendar | %s | %s", status, payload["detail"])

    _record_event(status, level, payload, previous)

    # 恢復也要通知。只講壞消息的話,使用者不知道自己修好了沒有。
    if status != OK or previous in NOT_PROTECTING:
        _notify(status, payload, notify)

    return payload


def _record_event(status, level, payload, previous):
    try:
        from database_service import insert_system_event
        insert_system_event(
            "NEWS_CALENDAR_STATUS", severity=level, source="calendar_watch",
            detail=payload["detail"] or status,
            payload={"status": status, "previous": previous,
                     "age_days": payload["age_days"],
                     "events": payload["events"]},
        )
    except Exception as exc:
        # 事件表可能還沒 migrate。不影響監控本身,但不能安靜。
        logger.warning(
            "Calendar | EVENT_WRITE_FAILED | %s: %s", type(exc).__name__, exc,
        )


ICON = {OK: "🟢", EXPIRING: "🟡", STALE: "🔴", MISSING: "🔴", BROKEN: "🔴"}


def _notify(status, payload, notify=None):
    if notify is None:
        from notifier import send_telegram as notify

    lines = [f"{ICON.get(status, '⚪')} 事件日曆 {status}", payload["detail"]]

    if status in NOT_PROTECTING:
        lines.append(
            "⚠️ 消息面風險這一層現在沒有在保護任何東西 —— "
            "FOMC / CPI 當天系統會照常開倉。"
        )

    try:
        notify("\n".join(line for line in lines if line))
    except Exception as exc:
        logger.warning("Calendar | NOTIFY_FAILED | %s: %s", type(exc).__name__, exc)
