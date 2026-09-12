"""
策略健康度、退化偵測與生命週期(Master Prompt 第七十三 ~ 七十六節)。

一個策略不會宣布自己壞掉。它會先變慢、再變差,然後某一天你回頭看
才發現最近三十筆已經吃掉前面一百筆的利潤。這個模組是為了讓那件事
在發生的當下被看見,而不是三個月後。

## 四件事

  **七十三 生命週期** RESEARCH -> VALIDATION -> PAPER -> APPROVED -> LIVE,
  以及 PAUSED / RETIRED。列舉本來就在 core/enums.py,但在這個模組出現
  以前沒有任何地方讀它 —— 一個沒有人讀的狀態欄位等於不存在。

  **七十四 策略 Kill Switch** 回撤超限自動 PAUSE。注意是 PAUSE 不是
  修改策略:第七十八節明講 AI 不得自己改策略。停掉是可逆的、
  不需要人工核可就能做的最強動作;改參數不是。

  **七十五 績效漂移** 近期 vs 歷史。這裡最容易寫錯的是「近期比較差
  就叫漂移」—— 任何策略的任何三十筆都有一半機率比歷史平均差。
  所以用標準誤檢定,不是比大小。

  **七十六 市況漂移** 策略的好成績是在哪一種市況拿到的。順勢策略在
  趨勢市賺錢、在盤整市虧錢是正常的;問題是市況已經轉成盤整,
  而系統還在用趨勢市的權重看它。

## 狀態存在檔案裡,不是資料庫裡

資料庫掛掉的時候,「所有策略看起來都是 LIVE」是錯誤的方向。
檔案讀得到、而且讀不到時預設是**不可交易**,這兩件事加起來
才是安全的失效模式。

(Kill Switch 用同樣的理由走檔案。兩者刻意一致。)
"""
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agmcis.core.enums import StrategyStatus

logger = logging.getLogger("agmcis.strategy.health")

STATUS_FILE = "data/strategy_status.json"
STATUS_AUDIT_FILE = "data/strategy_status_audit.log"

# 近期看幾筆。太小會被雜訊主導,太大會讓退化被歷史稀釋掉。
RECENT_WINDOW = 30

# 要做漂移判定,歷史至少要有這麼多筆。
MIN_BASELINE = 30

# 近期均值比歷史差幾個標準誤才算漂移。2 個標準誤 ≈ 95% 信賴區間。
DRIFT_SIGMA = 2.0

# 策略層級回撤超過這個百分比就自動 PAUSE。
MAX_STRATEGY_DRAWDOWN_PCT = 20.0

# 策略在某個市況下的交易數低於這個值,那個市況的統計不拿來下結論。
MIN_REGIME_SAMPLE = 10


# 哪些狀態可以參與投票。刻意分成兩組 ——
# 一個還在 PAPER 的策略可以在模擬盤跑,但不該碰真錢。
TRADEABLE_IN_PAPER = frozenset({
    StrategyStatus.PAPER, StrategyStatus.APPROVED, StrategyStatus.LIVE,
})
TRADEABLE_IN_LIVE = frozenset({StrategyStatus.APPROVED, StrategyStatus.LIVE})

# 沒有紀錄的策略預設是 PAPER:可以在模擬盤跑,不能碰真錢。
#
# 兩個方向都想過:預設 LIVE 等於讓一個沒驗證過的策略直接上實單;
# 預設 RESEARCH 等於全新安裝的系統什麼都不做,而那會讓人去關掉這個檢查。
DEFAULT_STATUS = StrategyStatus.PAPER


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------- 狀態儲存 ----------------

class StatusStore:
    """
    策略狀態的持久化。純檔案,理由見模組開頭。

    讀不到檔案時回傳空的 dict,而呼叫端拿到空 dict 會套用 DEFAULT_STATUS ——
    也就是「可以在模擬盤跑、不能碰真錢」。檔案壞掉不會讓策略升級。
    """

    def __init__(self, path=None, audit_path=None):
        self.path = Path(path or STATUS_FILE)
        self.audit_path = Path(audit_path or STATUS_AUDIT_FILE)

    def load(self):
        if not self.path.exists():
            return {}

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception(
                "策略狀態檔讀取失敗 | %s | 全部策略退回預設狀態", self.path,
            )
            return {}

        if not isinstance(payload, dict):
            logger.error("策略狀態檔格式不對(不是物件)| %s", self.path)
            return {}

        return payload

    def get(self, name):
        raw = self.load().get(name)
        if not raw:
            return DEFAULT_STATUS
        return StrategyStatus.parse(raw.get("status"), DEFAULT_STATUS)

    def set(self, name, status, reason="", actor="system"):
        """
        改一個策略的狀態。每一次變更都寫稽核 ——
        策略被停掉而沒有人知道為什麼,跟沒有停掉一樣糟。
        """
        status = StrategyStatus.parse(status, None)
        if status is None:
            raise ValueError(f"無法辨識的策略狀態:{status!r}")

        payload = self.load()
        before = payload.get(name, {}).get("status")

        payload[name] = {
            "status": status.value,
            "reason": reason,
            "actor": actor,
            "changed_at": _utcnow().isoformat(),
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        self._audit(name, before, status.value, reason, actor)

        # 同一件事也寫進系統稽核(第六十五節)。檔案是權威來源
        # (資料庫掛掉時仍然讀得到),資料庫是給人查的 ——
        # 兩邊都寫,但只有檔案會被讀回來做判斷。
        try:
            from agmcis.review.decision_log import audit
            audit(
                "STRATEGY_STATUS_CHANGE", actor=actor, target=name,
                before=before, after=status.value, detail=reason,
            )
        except Exception:
            logger.exception("策略狀態的系統稽核寫入失敗 | %s", name)

        logger.warning(
            "策略狀態變更 | %s | %s -> %s | %s", name, before, status.value, reason,
        )
        return status

    def _audit(self, name, before, after, reason, actor):
        line = json.dumps({
            "at": _utcnow().isoformat(),
            "strategy": name,
            "from": before,
            "to": after,
            "reason": reason,
            "actor": actor,
        }, ensure_ascii=False)

        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            # 稽核寫不進去不該讓狀態變更失敗(那會讓一個該停的策略繼續跑),
            # 但一定要喊出來。
            logger.exception("策略狀態稽核寫入失敗 | %s", self.audit_path)

    def read_audit(self, limit=50):
        if not self.audit_path.exists():
            return []

        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            logger.exception("策略狀態稽核讀取失敗")
            return []

        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
        return entries


_store = None


def get_store():
    global _store
    if _store is None:
        _store = StatusStore()
    return _store


def set_store(store):
    global _store
    _store = store


def is_tradeable(name, mode="paper", store=None):
    """這個策略現在可不可以參與投票。"""
    status = (store or get_store()).get(name)
    allowed = TRADEABLE_IN_LIVE if str(mode).lower() == "live" else TRADEABLE_IN_PAPER
    return status in allowed


# ---------------- 績效漂移 ----------------

def _pnls(trades):
    values = []
    for trade in trades:
        if trade.get("status") != "CLOSED":
            continue
        value = trade.get("pnl_usdt")
        if value is None:
            continue
        values.append(float(value))
    return values


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _stdev(values):
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _win_rate(values):
    if not values:
        return 0.0
    return sum(1 for v in values if v > 0) / len(values) * 100.0


def _profit_factor(values):
    """沒有虧損單時回 None,不是無限大。"""
    gross_loss = sum(abs(v) for v in values if v <= 0)
    if gross_loss <= 0:
        return None
    return sum(v for v in values if v > 0) / gross_loss


def _max_drawdown_pct(values, starting=1000.0):
    """
    用策略自己的損益序列算回撤,基準是一個假想的起始資金。

    這不是帳戶回撤 —— 它衡量的是「這個策略單獨跑會有多痛」,
    所以不受其他策略的盈虧影響。
    """
    equity = starting
    peak = starting
    worst = 0.0

    for value in values:
        equity += value
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100.0)

    return worst


@dataclass
class DriftCheck:
    drifted: bool = False
    recent_trades: int = 0
    baseline_trades: int = 0
    recent_expectancy: float = 0.0
    baseline_expectancy: float = 0.0
    sigma_gap: Optional[float] = None
    detail: str = ""

    def to_dict(self):
        return {
            "drifted": self.drifted,
            "recent_trades": self.recent_trades,
            "baseline_trades": self.baseline_trades,
            "recent_expectancy": round(self.recent_expectancy, 4),
            "baseline_expectancy": round(self.baseline_expectancy, 4),
            "sigma_gap": round(self.sigma_gap, 3) if self.sigma_gap is not None else None,
            "detail": self.detail,
        }


def detect_drift(values, window=RECENT_WINDOW, min_baseline=MIN_BASELINE,
                 sigma=DRIFT_SIGMA):
    """
    近期表現有沒有顯著地比歷史差。

    **不是比大小。** 任何策略的任何一段近期都有大約一半機率比歷史平均差,
    用「近期 < 歷史」當標準,等於每兩次檢查就宣告一次漂移。

    做法是把近期均值當成從歷史分佈抽樣的結果,看它離歷史均值有幾個
    標準誤。標準誤用**歷史**的標準差除以近期樣本數的平方根 ——
    問的是「如果策略沒有變,近期跑成這樣的機率有多低」。
    """
    check = DriftCheck()

    if len(values) <= window:
        check.detail = f"總共只有 {len(values)} 筆,不足以切出近期與歷史"
        return check

    recent = values[-window:]
    baseline = values[:-window]

    check.recent_trades = len(recent)
    check.baseline_trades = len(baseline)
    check.recent_expectancy = _mean(recent)
    check.baseline_expectancy = _mean(baseline)

    if len(baseline) < min_baseline:
        check.detail = (
            f"歷史只有 {len(baseline)} 筆(需要 {min_baseline}),"
            f"不足以當成比較基準"
        )
        return check

    deviation = _stdev(baseline)
    if deviation <= 0:
        check.detail = "歷史損益沒有變異,算不出標準誤"
        return check

    standard_error = deviation / math.sqrt(len(recent))
    gap = (check.recent_expectancy - check.baseline_expectancy) / standard_error
    check.sigma_gap = gap

    if gap <= -sigma:
        check.drifted = True
        check.detail = (
            f"近期 {len(recent)} 筆的每筆期望值 {check.recent_expectancy:+.2f},"
            f"比歷史的 {check.baseline_expectancy:+.2f} 低 {abs(gap):.1f} 個標準誤"
        )
    else:
        check.detail = (
            f"近期與歷史的差距 {gap:+.1f} 個標準誤,在雜訊範圍內"
        )

    return check


# ---------------- 市況漂移 ----------------

@dataclass
class RegimeFit:
    current_regime: Optional[str] = None
    mismatch: bool = False
    best_regime: Optional[str] = None
    current_expectancy: Optional[float] = None
    best_expectancy: Optional[float] = None
    samples: Dict[str, int] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self):
        return {
            "current_regime": self.current_regime,
            "mismatch": self.mismatch,
            "best_regime": self.best_regime,
            "current_expectancy": (
                round(self.current_expectancy, 4)
                if self.current_expectancy is not None else None
            ),
            "best_expectancy": (
                round(self.best_expectancy, 4)
                if self.best_expectancy is not None else None
            ),
            "samples": dict(self.samples),
            "detail": self.detail,
        }


def regime_fit(trades, current_regime, min_sample=MIN_REGIME_SAMPLE):
    """
    這個策略的好成績是在哪一種市況拿到的,而現在是哪一種市況。

    順勢策略在趨勢市賺、在盤整市虧是正常的 —— 那不是策略壞掉。
    問題是市況已經轉成盤整,而系統還在用趨勢市的權重看它。

    樣本不足的市況**不列入比較**。三筆交易的「期望值」不是期望值。
    """
    fit = RegimeFit(current_regime=current_regime)

    buckets = {}
    for trade in trades:
        if trade.get("status") != "CLOSED":
            continue
        value = trade.get("pnl_usdt")
        regime = trade.get("market_regime")
        if value is None or not regime:
            continue
        buckets.setdefault(str(regime), []).append(float(value))

    fit.samples = {name: len(values) for name, values in buckets.items()}

    usable = {
        name: values for name, values in buckets.items()
        if len(values) >= min_sample
    }

    if not usable:
        fit.detail = f"沒有任何市況累積到 {min_sample} 筆,無法比較"
        return fit

    expectancies = {name: _mean(values) for name, values in usable.items()}
    fit.best_regime = max(expectancies, key=expectancies.get)
    fit.best_expectancy = expectancies[fit.best_regime]

    if current_regime is None:
        fit.detail = "不知道目前市況,無法判斷是否錯配"
        return fit

    if current_regime not in usable:
        fit.detail = (
            f"目前市況 {current_regime} 只有 "
            f"{fit.samples.get(current_regime, 0)} 筆紀錄,不足以判斷"
        )
        return fit

    fit.current_expectancy = expectancies[current_regime]

    if fit.current_expectancy < 0 <= fit.best_expectancy:
        fit.mismatch = True
        fit.detail = (
            f"這個策略在 {fit.best_regime} 的每筆期望值 "
            f"{fit.best_expectancy:+.2f},在目前的 {current_regime} 是 "
            f"{fit.current_expectancy:+.2f} —— 市況已經不適合它"
        )
    else:
        fit.detail = (
            f"目前市況 {current_regime} 的每筆期望值 {fit.current_expectancy:+.2f}"
        )

    return fit


# ---------------- 綜合健康度 ----------------

VERDICT_HEALTHY = "HEALTHY"
VERDICT_WATCH = "WATCH"
VERDICT_DRIFT = "STRATEGY_DRIFT"
VERDICT_PAUSE = "SHOULD_PAUSE"
VERDICT_UNKNOWN = "INSUFFICIENT_DATA"


@dataclass
class StrategyHealth:
    name: str
    status: str = DEFAULT_STATUS.value
    verdict: str = VERDICT_UNKNOWN
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: Optional[float] = None
    expectancy: float = 0.0
    max_drawdown_pct: float = 0.0
    drift: DriftCheck = field(default_factory=DriftCheck)
    regime: RegimeFit = field(default_factory=RegimeFit)
    reasons: List[str] = field(default_factory=list)
    should_pause: bool = False

    def to_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "verdict": self.verdict,
            "trades": self.trades,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": (
                round(self.profit_factor, 3)
                if self.profit_factor is not None else None
            ),
            "expectancy": round(self.expectancy, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "drift": self.drift.to_dict(),
            "regime": self.regime.to_dict(),
            "reasons": list(self.reasons),
            "should_pause": self.should_pause,
        }


def evaluate(name, trades, current_regime=None, store=None,
             max_drawdown_pct=MAX_STRATEGY_DRAWDOWN_PCT, **kwargs):
    """
    一個策略的健康度。**不會**自己改狀態 —— 那是 apply() 的事。

    把判斷與行動分開,是為了讓 Dashboard 可以看健康度而不會有副作用。
    """
    store = store or get_store()
    health = StrategyHealth(name=name, status=store.get(name).value)

    values = _pnls(trades)
    health.trades = len(values)

    if not values:
        health.reasons.append("沒有已平倉的交易,無法判斷")
        return health

    health.win_rate = _win_rate(values)
    health.profit_factor = _profit_factor(values)
    health.expectancy = _mean(values)
    health.max_drawdown_pct = _max_drawdown_pct(values)

    health.drift = detect_drift(values, **kwargs)
    health.regime = regime_fit(trades, current_regime)

    # ---- 第七十四節:回撤超限自動停 ----
    if health.max_drawdown_pct >= max_drawdown_pct:
        health.should_pause = True
        health.verdict = VERDICT_PAUSE
        health.reasons.append(
            f"策略回撤 {health.max_drawdown_pct:.1f}% 超過上限 {max_drawdown_pct}%"
        )
        return health

    # ---- 第七十五節:績效漂移 ----
    if health.drift.drifted:
        health.verdict = VERDICT_DRIFT
        health.reasons.append(health.drift.detail)
        # 漂移不自動停 —— 它是「去看一下」,不是「已經確定壞了」。
        # 自動停一個只是運氣差的策略,會把系統停成一片空白。
        return health

    # ---- 第七十六節:市況錯配 ----
    if health.regime.mismatch:
        health.verdict = VERDICT_WATCH
        health.reasons.append(health.regime.detail)
        return health

    if health.trades < MIN_BASELINE:
        health.verdict = VERDICT_UNKNOWN
        health.reasons.append(
            f"只有 {health.trades} 筆交易,還不足以判斷健康度"
        )
        return health

    health.verdict = VERDICT_HEALTHY
    health.reasons.append(
        f"{health.trades} 筆、勝率 {health.win_rate:.1f}%、"
        f"每筆期望值 {health.expectancy:+.2f}"
    )
    return health


def evaluate_all(trades, names=None, current_regime=None, store=None, **kwargs):
    """所有策略的健康度。trades 是全部交易,這裡自己分群。"""
    by_name = {}
    for trade in trades or []:
        key = trade.get("strategy")
        if not key:
            continue
        by_name.setdefault(str(key), []).append(trade)

    wanted = list(names) if names is not None else sorted(by_name)

    return [
        evaluate(name, by_name.get(name, []),
                 current_regime=current_regime, store=store, **kwargs)
        for name in wanted
    ]


def apply(reports, store=None, actor="drift_monitor"):
    """
    把 should_pause 的策略真的停掉。

    只做 PAUSE,不做別的:第七十八節明講 AI 不得自己改策略。
    停掉是可逆的、不需要人工核可就能做的最強動作;改參數不是。

    已經是 PAUSED / RETIRED 的不重複處理 —— 否則每一輪都會寫一筆稽核。
    """
    store = store or get_store()
    paused = []

    for report in reports:
        if not report.should_pause:
            continue

        current = store.get(report.name)
        if current in (StrategyStatus.PAUSED, StrategyStatus.RETIRED):
            continue

        store.set(
            report.name, StrategyStatus.PAUSED,
            reason=" / ".join(report.reasons) or "策略回撤超限",
            actor=actor,
        )
        paused.append(report.name)

    return paused


def run_drift_monitor(trades=None, current_regime=None, store=None):
    """排程入口:評估所有策略,停掉該停的,回傳摘要。"""
    if trades is None:
        from database_service import get_closed_trades
        trades = get_closed_trades()

    reports = evaluate_all(trades, current_regime=current_regime, store=store)
    paused = apply(reports, store=store)

    return {
        "checked": len(reports),
        "paused": paused,
        "drifted": [r.name for r in reports if r.verdict == VERDICT_DRIFT],
        "watch": [r.name for r in reports if r.verdict == VERDICT_WATCH],
        "reports": [r.to_dict() for r in reports],
    }
