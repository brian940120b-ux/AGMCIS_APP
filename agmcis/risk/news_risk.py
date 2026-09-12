"""
News Risk(Master Prompt 第五十一節)。

FOMC 公布前十分鐘的技術面看起來跟平常沒有兩樣:趨勢在、動能在、
量能在。所有指標都不知道三分鐘後會發生什麼。系統會照常開單,
然後在公布的瞬間被一根 K 棒穿過停損。

這一層做的是兩件不同的事:

  **已排程事件**(FOMC / CPI / NFP / 利率決議)—— 時間是已知的,
  所以做法是**時間窗封鎖**:公布前 N 分鐘到公布後 M 分鐘不開新倉。

  **未排程衝擊**(交易所被駭、倒閉、戰爭、監管突襲)—— 時間不可知,
  只能從新聞標題偵測。做法是關鍵字掃描,命中就停。

兩者的預設方向不同,原因在下面。

## 日曆過期的時候怎麼辦

這是這個模組唯一真正難的問題。

日曆是人維護的檔案。過期代表「不知道 FOMC 是不是十分鐘後」——
而「不知道」在這個系統的其他地方一律當成「不安全」。但如果照搬,
一個沒有人更新的檔案會讓系統永遠停止交易,那不是保守,那是故障。

所以這裡把兩種模式分開:

  * **模擬盤**:日曆過期只產生警告,不擋單。停掉一個模擬盤沒有意義。
  * **實單**:日曆過期由 LIVE SAFETY GATE 擋住(check_news_calendar)。
    真錢進場之前,「不知道今天有沒有 FOMC」不是可以接受的狀態。

關鍵字掃描沒有這個問題:新聞抓不到就是抓不到,那是另一回事,
而且不會因為沒有人維護檔案而靜靜地失效。

## 它不判斷方向

News Agent 判斷「這則新聞偏多還偏空」。這一層不判斷那個 ——
重大事件期間的問題不是方向猜錯,是波動大到停損沒有意義。
所以這裡只輸出「不要開」與「開小一點」,不輸出多空。
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("agmcis.risk.news_risk")

DEFAULT_CALENDAR_PATH = "config/news_calendar.json"

# 日曆超過這個天數沒更新就算過期。
CALENDAR_MAX_AGE_DAYS = 7

# 沒有指定時間窗時的預設值(分鐘)。
DEFAULT_BEFORE_MINUTES = 30
DEFAULT_AFTER_MINUTES = 30

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
NONE = "NONE"

_SEVERITY_ORDER = {NONE: 0, LOW: 1, MEDIUM: 2, HIGH: 3}

# 中等衝擊時把風險縮到多少。不是 0 —— 那是 HIGH 的工作。
MEDIUM_RISK_MULTIPLIER = 0.5

# 未排程衝擊的關鍵字。全部小寫比對。
#
# 這份清單一定不完整,而且會過時 —— 它是安全網不是護城河。
# 真正的防線是已排程事件的時間窗,那個是確定的。
SHOCK_PATTERNS = [
    (r"\bhack(ed|ing)?\b", "交易所或協議被駭"),
    (r"\bexploit(ed)?\b", "合約被利用"),
    (r"\bdrain(ed)?\b", "資金被抽乾"),
    (r"\binsolvency\b|\bbankrupt", "倒閉 / 資不抵債"),
    (r"halt(s|ed)? withdrawal", "暫停提領"),
    (r"suspend(s|ed)? (trading|withdrawal)", "暫停交易或提領"),
    (r"\bsec (sues|charges|lawsuit)", "監管訴訟"),
    (r"\bdelist(s|ed|ing)?\b", "下架"),
    (r"\bwar\b|\binvasion\b|\bmissile", "地緣衝突"),
    (r"\bflash crash\b", "閃崩"),
    (r"被駭|遭駭|駭客", "交易所或協議被駭"),
    (r"倒閉|破產|資不抵債", "倒閉 / 資不抵債"),
    (r"暫停提領|停止提現", "暫停提領"),
    (r"戰爭|開戰|飛彈", "地緣衝突"),
]


def _utc(value):
    """把各種時間表示轉成 aware UTC datetime。轉不動回 None。"""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class MacroEvent:
    name: str
    at: datetime
    impact: str = HIGH
    before_minutes: int = DEFAULT_BEFORE_MINUTES
    after_minutes: int = DEFAULT_AFTER_MINUTES

    def window(self):
        return (
            self.at - timedelta(minutes=self.before_minutes),
            self.at + timedelta(minutes=self.after_minutes),
        )

    def contains(self, moment):
        start, end = self.window()
        return start <= moment <= end

    def to_dict(self):
        start, end = self.window()
        return {
            "name": self.name,
            "at": self.at.isoformat(),
            "impact": self.impact,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }


@dataclass
class Calendar:
    events: List[MacroEvent] = field(default_factory=list)
    generated_at: Optional[datetime] = None
    source: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    def is_stale(self, now, max_age_days=CALENDAR_MAX_AGE_DAYS):
        """
        沒有 generated_at 一律算過期 —— 一個沒說自己什麼時候做的日曆,
        跟一個很舊的日曆一樣不可信。
        """
        if self.generated_at is None:
            return True
        return (now - self.generated_at) > timedelta(days=max_age_days)

    def active(self, now):
        return [event for event in self.events if event.contains(now)]


def load_calendar(path=None):
    """
    讀事件日曆。格式:

        {
          "generated_at": "2026-09-01T00:00:00Z",
          "events": [
            {"name": "FOMC", "at": "2026-09-17T18:00:00Z", "impact": "HIGH",
             "before_minutes": 60, "after_minutes": 60}
          ]
        }

    檔案不存在、壞掉、或某一筆時間解析不了,都**不會**拋例外 ——
    會回一個 errors 不為空的 Calendar。呼叫端看得到出了什麼事,
    而 is_stale() 會是 True(沒有 generated_at),所以實單那邊會被擋下。
    """
    target = Path(path or DEFAULT_CALENDAR_PATH)
    calendar = Calendar(source=str(target))

    if not target.exists():
        calendar.errors.append(f"事件日曆不存在:{target}")
        return calendar

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        calendar.errors.append(f"事件日曆讀取失敗:{type(exc).__name__}: {exc}")
        return calendar

    calendar.generated_at = _utc(payload.get("generated_at"))
    if calendar.generated_at is None:
        calendar.errors.append("事件日曆沒有可解析的 generated_at")

    for raw in payload.get("events") or []:
        moment = _utc(raw.get("at"))
        if moment is None:
            calendar.errors.append(
                f"事件 {raw.get('name', '?')} 的時間無法解析:{raw.get('at')!r}"
            )
            continue

        calendar.events.append(MacroEvent(
            name=str(raw.get("name") or "未命名事件"),
            at=moment,
            impact=str(raw.get("impact") or HIGH).upper(),
            before_minutes=int(raw.get("before_minutes") or DEFAULT_BEFORE_MINUTES),
            after_minutes=int(raw.get("after_minutes") or DEFAULT_AFTER_MINUTES),
        ))

    calendar.events.sort(key=lambda e: e.at)
    return calendar


def scan_headlines(headlines):
    """
    未排程衝擊的關鍵字掃描。回傳 [(標題, 說明), ...]。

    刻意只看標題:內文常常在複述歷史事件,「2022 年 FTX 倒閉」
    出現在一篇回顧文章裡不代表現在有事。
    """
    hits = []

    for item in headlines or []:
        title = item.get("title") if isinstance(item, dict) else str(item)
        if not title:
            continue

        lowered = title.lower()

        for pattern, label in SHOCK_PATTERNS:
            if re.search(pattern, lowered):
                hits.append((title, label))
                break

    return hits


@dataclass
class NewsRisk:
    level: str = NONE
    blocks_entry: bool = False
    risk_multiplier: float = 1.0
    events: List[dict] = field(default_factory=list)
    shocks: List[dict] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    degraded: bool = False
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "level": self.level,
            "blocks_entry": self.blocks_entry,
            "risk_multiplier": self.risk_multiplier,
            "events": list(self.events),
            "shocks": list(self.shocks),
            "reasons": list(self.reasons),
            "degraded": self.degraded,
            "warnings": list(self.warnings),
        }


def assess(now=None, calendar=None, headlines=None, calendar_path=None,
           max_age_days=CALENDAR_MAX_AGE_DAYS):
    """
    現在的消息面風險。

    回傳 NewsRisk。`blocks_entry=True` 代表不開新倉;
    `risk_multiplier < 1` 代表開小一點。兩者都**不**影響已開的部位 ——
    平倉永遠不該被消息面擋住。

    `degraded=True` 代表日曆不可信(不存在、壞掉、或過期)。
    模擬盤不因此停止交易;實單由 LIVE SAFETY GATE 擋。
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    risk = NewsRisk()

    if calendar is None:
        calendar = load_calendar(calendar_path)

    for error in calendar.errors:
        risk.warnings.append(error)

    if calendar.is_stale(moment, max_age_days=max_age_days):
        risk.degraded = True
        age = (
            "從來沒有更新過" if calendar.generated_at is None
            else f"已經 {(moment - calendar.generated_at).days} 天沒更新"
        )
        risk.warnings.append(
            f"事件日曆不可信({age})。現在無法判斷是否處於重大事件時間窗。"
        )

    # ---- 已排程事件 ----
    for event in calendar.active(moment):
        risk.events.append(event.to_dict())

        if event.impact == HIGH:
            risk.blocks_entry = True
            risk.level = HIGH
            risk.reasons.append(f"{event.name} 時間窗內(重大事件,不開新倉)")
        elif event.impact == MEDIUM:
            if _SEVERITY_ORDER[risk.level] < _SEVERITY_ORDER[MEDIUM]:
                risk.level = MEDIUM
            risk.risk_multiplier = min(risk.risk_multiplier, MEDIUM_RISK_MULTIPLIER)
            risk.reasons.append(f"{event.name} 時間窗內(中等事件,倉位減半)")
        else:
            if _SEVERITY_ORDER[risk.level] < _SEVERITY_ORDER[LOW]:
                risk.level = LOW
            risk.reasons.append(f"{event.name} 時間窗內(低度事件,僅記錄)")

    # ---- 未排程衝擊 ----
    for title, label in scan_headlines(headlines):
        risk.shocks.append({"title": title, "label": label})
        risk.blocks_entry = True
        risk.level = HIGH
        risk.reasons.append(f"偵測到{label}:{title[:80]}")

    if risk.blocks_entry:
        logger.warning(
            "News Risk | BLOCK | %s", " / ".join(risk.reasons),
        )

    return risk


# ---------------- 生產環境入口 ----------------
#
# 日曆是讀檔,很便宜;新聞標題是 RSS,很貴。每次下單前都重抓 RSS
# 會把下單延遲綁在三個外部網站的可用性上 —— 而那三個網站掛掉的時候,
# 最不該發生的事就是系統跟著停下來。
#
# 所以標題有快取,而且抓不到就當作沒有標題(只失去未排程衝擊的偵測,
# 已排程事件的時間窗不受影響)。

HEADLINE_TTL_SECONDS = 600

_HEADLINES = {"items": [], "expires_at": 0.0}


def _headlines(now, ttl=HEADLINE_TTL_SECONDS, fetch=None):
    if _HEADLINES["expires_at"] > now:
        return _HEADLINES["items"]

    try:
        if fetch is None:
            from news_center import get_crypto_news as fetch
        items = list(fetch() or [])
    except Exception as exc:
        logger.warning("News Risk | HEADLINES_FAILED | %s", exc)
        items = []

    _HEADLINES.update({"items": items, "expires_at": now + ttl})
    return items


def current(calendar_path=None, fetch_headlines=None, clock=None):
    """
    現在的消息面風險。Risk Engine 的生產環境呼叫點。

    任何一段失敗都不會拋例外 —— 回傳的 NewsRisk 會帶著 warnings,
    而 degraded 會讓 LIVE SAFETY GATE 在實單那一側擋下來。
    """
    import time

    moment = datetime.now(timezone.utc)
    return assess(
        now=moment,
        calendar_path=calendar_path,
        headlines=_headlines(
            (clock or time.time)(), fetch=fetch_headlines,
        ),
    )


def clear_headline_cache():
    _HEADLINES.update({"items": [], "expires_at": 0.0})
