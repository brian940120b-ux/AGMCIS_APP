"""
設定變更稽核。

問題:改了 `MAX_LEVERAGE` 之後,沒有任何紀錄說它什麼時候變的、從多少變成多少。
風控參數是這個系統裡最容易被「先放寬一下試試看」的東西,
而放寬之後常常沒有人記得改回來。

## 這個模組做得到與做不到的事

**做得到**:記錄「什麼時候變的、從多少變成多少」。

**做不到**:記錄「是誰改的」。設定來自環境變數,環境變數沒有作者。
要有「誰」就必須把設定搬進資料庫並加上身分,那是另一個重構。
這裡不假裝做得到 —— 稽核紀錄裡不會出現一個猜出來的使用者名稱。

## 為什麼是快照比對而不是攔截寫入

設定在模組載入時就固定了。沒有「設定被修改」這個事件可以攔 ——
改的是 `.env` 然後重啟。所以只能靠比對:啟動時拍一張快照,
跟上次的比一比。

代價是:兩次快照之間改過又改回來,看不到。那個限制寫在這裡,
不是靠使用者自己發現。
"""
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from agmcis.config import settings

logger = logging.getLogger("agmcis.config.audit")

SNAPSHOT_FILE = os.getenv("CONFIG_SNAPSHOT_FILE", "config_snapshot.json")
AUDIT_FILE = os.getenv("CONFIG_AUDIT_FILE", "config_audit.log")

# 只看會影響風險的設定。全部都看會讓真正重要的變更被淹沒在雜訊裡。
WATCHED = (
    "TRADING_MODE",
    "AUTO_TRADING_ENABLED",
    "MAX_LEVERAGE",
    "MAX_RISK_PER_TRADE_PCT",
    "MAX_DRAWDOWN_PCT",
    "MAX_DAILY_LOSS_USDT",
    "MAX_TOTAL_OPEN_LOSS_USDT",
    "MAX_OPEN_POSITIONS",
    "MAX_TRADES_PER_DAY",
    "MAX_CONSECUTIVE_LOSSES",
    "MAX_EXPOSURE_PCT",
    "PAPER_MAKER_FEE",
    "PAPER_TAKER_FEE",
    "PAPER_SLIPPAGE_PCT",
    "PAPER_SPREAD_PCT",
    "PAPER_FUNDING_RATE_8H",
    "PAPER_MAINTENANCE_MARGIN_RATIO",
    "POSITION_MONITOR_USE_INTRABAR",
    "EXCHANGE_SHARED_RATE_LIMIT",
    "EXCHANGE_RATE_LIMIT_CALLS",
)

# 往這個方向變 = 風險變大。這些變更會被標成 RISK_INCREASED。
HIGHER_IS_RISKIER = {
    "MAX_LEVERAGE",
    "MAX_RISK_PER_TRADE_PCT",
    "MAX_DRAWDOWN_PCT",
    "MAX_DAILY_LOSS_USDT",
    "MAX_TOTAL_OPEN_LOSS_USDT",
    "MAX_OPEN_POSITIONS",
    "MAX_TRADES_PER_DAY",
    "MAX_CONSECUTIVE_LOSSES",
    "MAX_EXPOSURE_PCT",
}

# 調低成本假設 = 模擬績效變好看 = 一種自欺。
LOWER_IS_RISKIER = {
    "PAPER_MAKER_FEE",
    "PAPER_TAKER_FEE",
    "PAPER_SLIPPAGE_PCT",
    "PAPER_SPREAD_PCT",
    "PAPER_FUNDING_RATE_8H",
    "PAPER_MAINTENANCE_MARGIN_RATIO",
}

RISK_INCREASED = "RISK_INCREASED"
RISK_DECREASED = "RISK_DECREASED"
CHANGED = "CHANGED"


@dataclass
class Change:
    key: str
    old: object = None
    new: object = None
    kind: str = CHANGED

    @property
    def is_risk_increase(self):
        return self.kind == RISK_INCREASED

    def to_dict(self):
        return {"key": self.key, "old": self.old, "new": self.new, "kind": self.kind}

    def describe(self):
        arrow = {
            RISK_INCREASED: "風險變大",
            RISK_DECREASED: "風險變小",
            CHANGED: "已變更",
        }[self.kind]
        return f"{self.key}:{self.old} → {self.new}({arrow})"


@dataclass
class AuditResult:
    changes: List[Change] = field(default_factory=list)
    first_run: bool = False
    snapshot: Dict = field(default_factory=dict)

    @property
    def risk_increases(self):
        return [c for c in self.changes if c.is_risk_increase]

    def to_dict(self):
        return {
            "first_run": self.first_run,
            "change_count": len(self.changes),
            "risk_increase_count": len(self.risk_increases),
            "changes": [c.to_dict() for c in self.changes],
        }


def snapshot():
    """目前的設定值。取不到的鍵留 None,不跳過 —— 跳過會讓它看起來沒被監控。"""
    return {key: getattr(settings, key, None) for key in WATCHED}


def _classify(key, old, new):
    if key not in HIGHER_IS_RISKIER and key not in LOWER_IS_RISKIER:
        return CHANGED

    try:
        old_value, new_value = float(old), float(new)
    except (TypeError, ValueError):
        return CHANGED

    if key in HIGHER_IS_RISKIER:
        return RISK_INCREASED if new_value > old_value else RISK_DECREASED

    return RISK_INCREASED if new_value < old_value else RISK_DECREASED


def compare(previous, current):
    changes = []

    for key in WATCHED:
        old = previous.get(key)
        new = current.get(key)

        if old == new:
            continue

        changes.append(Change(key=key, old=old, new=new,
                              kind=_classify(key, old, new)))

    # 上一份快照裡有、現在不在監控清單裡的鍵。
    # 這代表監控清單被改過,本身就值得記一筆。
    for key in previous:
        if key not in current:
            changes.append(Change(key=key, old=previous[key], new=None,
                                  kind=CHANGED))

    return changes


class ConfigAuditor:

    def __init__(self, snapshot_file=None, audit_file=None, now=None):
        self.snapshot_file = Path(snapshot_file or SNAPSHOT_FILE)
        self.audit_file = Path(audit_file or AUDIT_FILE)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _load_previous(self):
        if not self.snapshot_file.exists():
            return None

        try:
            return json.loads(self.snapshot_file.read_text(encoding="utf-8"))
        except Exception as exc:
            # 快照壞掉不可以當成「沒有快照」——
            # 那會讓這一次的所有變更被當成首次執行而消失。
            logger.error("設定快照讀取失敗,本次無法比對:%s", exc)
            raise

    def _save(self, current):
        payload = {"captured_at": self._now().isoformat(), "settings": current}
        self.snapshot_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _audit(self, result):
        if not result.changes:
            return

        record = {
            "at": self._now().isoformat(),
            **result.to_dict(),
        }

        try:
            with open(self.audit_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            logger.error("設定稽核紀錄寫入失敗:%s", exc)

    def run(self):
        current = snapshot()

        try:
            previous = self._load_previous()
        except Exception:
            # 讀不到就不寫新的快照 —— 覆蓋掉會讓問題消失
            return AuditResult(snapshot=current)

        if previous is None:
            result = AuditResult(first_run=True, snapshot=current)
            self._save(current)
            logger.info("設定稽核 | 首次執行,已建立基準快照")
            return result

        result = AuditResult(
            changes=compare(previous.get("settings", {}), current),
            snapshot=current,
        )

        for change in result.changes:
            if change.is_risk_increase:
                logger.warning("設定稽核 | %s", change.describe())
            else:
                logger.info("設定稽核 | %s", change.describe())

        self._audit(result)
        self._save(current)
        return result

    def read_audit(self, limit=50):
        if not self.audit_file.exists():
            return []

        lines = self.audit_file.read_text(encoding="utf-8").strip().split("\n")
        records = []
        for line in lines[-limit:]:
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"raw": line})
        return records


_auditor = None


def get_auditor():
    global _auditor
    if _auditor is None:
        _auditor = ConfigAuditor()
    return _auditor


def run_config_audit():
    """排程器的入口。"""
    return get_auditor().run().to_dict()
