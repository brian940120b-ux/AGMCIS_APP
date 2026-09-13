"""
事件日曆 —— Master Prompt 第五十一條 · 2026-09-13

═══ 這一節在這套系統裡的形狀 ═══
規章想的是「重大新聞前後不要開新倉」。那假設的是一套**進出頻繁**
的系統:今天不進,明天再進,成本很低。

這條策略回測 3.3 年約**每月換手一次**。FOMC 當天它多半什麼都不做,
而它持有的倉會原封不動地穿過那個事件 —— 躲不掉,也不打算躲。

所以這一支現在做的是**看得見**,不是**閃避**:

  · 事件要出現在面板與 /health 上,讓人知道今天是什麼日子
  · 記帳當下把「這一天有沒有事件」寫進帳本,之後回頭看得出來
  · **不改變任何交易決策**

═══ 為什麼不現在就擋單 ═══
擋掉 FOMC 當天的進場,會改變進場日期,而進場日期改變就是不同的
報酬序列 —— Calmar 1.33 立刻不算數。那屬於「動到策略、要重新回測」
的那一批,不能夾在一次補洞裡偷偷做掉(第三十四條)。

要做的話正確順序是:先在回測裡加上這條規則,看它對 3.3 年的
Calmar / 回撤做了什麼,再決定要不要上線。

═══ 日期從哪裡來 —— 這裡要非常誠實 ═══
**這個檔案不內建任何日期。**

FOMC 的年度時間表是聯準會公布的,CPI 是勞工統計局公布的。
我沒有辦法在這個環境連到那些來源,而**憑記憶寫下 2026 年的 FOMC
日期,然後讓一個風險系統相信它**,是這整份規章裡最不該做的事
(第五條的精神:不要靠模型記憶猜外部事實)。

一個內容是錯的日曆,比沒有日曆危險得多:它會讓人以為有在看。

所以:日期放在 `data/events.json`,由人或由之後寫的抓取器填。
格式見 `docs/events.example.json`。

═══ 沒有日曆時說什麼 ═══
說「日曆沒有載入」,**不說「今天沒有事件」**。

那兩句話差很多。前者是「我不知道」,後者是一個確定的判斷 ——
而這裡不知道。這是整支檔案唯一真正重要的設計決定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CALENDAR = BASE / "data" / "events.json"

# 已知的事件類別。用固定清單而不是自由字串 —— 打錯字的類別會安靜地
# 變成一個沒有人在看的分類。
KINDS = {
    "FOMC": "聯準會利率決議",
    "CPI": "美國消費者物價指數",
    "NFP": "美國非農就業",
    "PCE": "美國個人消費支出物價",
    "UNLOCK": "代幣解鎖",
    "HALVING": "減半",
    "OTHER": "其他",
}

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LEVELS = (HIGH, MEDIUM)


class CalendarMissing(Exception):
    """沒有日曆可讀。**這不等於今天沒有事件。**"""


class CalendarStale(Exception):
    """日曆裡最後一筆已經過去了 —— 沒有人在維護它。"""


@dataclass(frozen=True)
class Event:
    on: date
    kind: str
    level: str
    note: str

    def label(self) -> str:
        return f"{KINDS.get(self.kind, self.kind)}"

    def to_dict(self) -> dict:
        return {"date": self.on.isoformat(), "kind": self.kind,
                "level": self.level, "note": self.note}


def _parse(row, where: str) -> Event:
    try:
        on = date.fromisoformat(str(row["date"]))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"{where}:date 不是合法日期 —— {row!r}") from e

    kind = str(row.get("kind") or "OTHER").upper()
    if kind not in KINDS:
        # 打錯的類別不要默默收下。它會變成一個沒有人在看的分類。
        raise ValueError(
            f"{where}:不認得的事件類別 {kind!r},可用:{sorted(KINDS)}")

    level = str(row.get("level") or MEDIUM).upper()
    if level not in LEVELS:
        raise ValueError(
            f"{where}:不認得的等級 {level!r},可用:{list(LEVELS)}")

    return Event(on=on, kind=kind, level=level,
                 note=str(row.get("note") or ""))


def load(path: Path | None = None) -> list:
    """
    讀日曆。讀不到就拋 CalendarMissing —— **不回空清單**。

    回空清單會讓呼叫端寫出 `if not events(): "今天沒事"`,
    而那句話是錯的:我們不知道今天有沒有事。
    """
    path = path or CALENDAR
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CalendarMissing(
            f"事件日曆不存在({path.name})。這代表**沒有在看**,"
            "不代表沒有事件。格式見 docs/events.example.json") from e
    except (OSError, json.JSONDecodeError) as e:
        raise CalendarMissing(f"事件日曆讀不懂({path.name}):{e}") from e

    rows = raw.get("events") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not rows:
        raise CalendarMissing(f"事件日曆是空的({path.name})")

    events = [_parse(row, f"{path.name} 第 {i + 1} 筆")
              for i, row in enumerate(rows)]
    events.sort(key=lambda e: (e.on, e.kind))
    return events


def upcoming(today: date | None = None, within_days: int = 14,
             path: Path | None = None) -> list:
    """今天(含)之後 within_days 天內的事件。"""
    today = today or datetime.now(timezone.utc).date()
    return [e for e in load(path)
            if 0 <= (e.on - today).days <= within_days]


def on_day(day: date, path: Path | None = None) -> list:
    return [e for e in load(path) if e.on == day]


def check_freshness(today: date | None = None,
                    path: Path | None = None) -> None:
    """
    日曆裡最後一筆已經過去 -> 拋 CalendarStale。

    一份停在半年前的日曆,查詢起來永遠回「沒有事件」——
    那是最安靜的一種壞掉:它看起來一直在工作。
    """
    today = today or datetime.now(timezone.utc).date()
    events = load(path)
    last = max(e.on for e in events)
    if last < today:
        raise CalendarStale(
            f"事件日曆最後一筆是 {last.isoformat()},已經過去了 —— "
            "沒有人在維護它。查詢它只會永遠回「沒有事件」。")


def status(today: date | None = None, path: Path | None = None) -> dict:
    """
    給面板與 /health 用的一句話。

    三種答案彼此不同,而且**不可以混為一談**:
      · loaded=False        我不知道(沒有日曆)
      · stale=True          我在看一份沒人維護的日曆(等於不知道)
      · loaded=True 且新鮮   我知道,而答案是這些
    """
    today = today or datetime.now(timezone.utc).date()
    try:
        events = load(path)
    except CalendarMissing as e:
        return {"loaded": False, "stale": None, "reason": str(e),
                "today": [], "upcoming": []}

    last = max(e.on for e in events)
    stale = last < today
    return {
        "loaded": True,
        "stale": stale,
        "reason": (f"最後一筆 {last.isoformat()} 已經過去,沒有人在維護"
                   if stale else None),
        "today": [e.to_dict() for e in events if e.on == today],
        "upcoming": [e.to_dict() for e in events
                     if 0 < (e.on - today).days <= 14],
    }
