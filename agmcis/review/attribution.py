"""
績效歸因:把損益拆到策略、市況、出場原因、標的。

一個總體的「勝率 58%」沒有用。有用的是:

    「這 58% 全部來自盤整市,趨勢市裡是 41%。」
    「扣掉最好的三筆,期望值是負的。」

第二句尤其重要。少數幾筆極端獲利可以讓一個沒有優勢的策略看起來很好,
而那幾筆通常不會重演。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 低於這個筆數的分群不下結論
MIN_SAMPLE = 20

# 完全不下結論的下限
MIN_SAMPLE_HARD = 5


@dataclass
class Bucket:
    """一個分群的績效。"""
    key: str = ""
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0

    @property
    def win_rate(self):
        return (self.wins / self.trades * 100) if self.trades else 0.0

    @property
    def profit_factor(self):
        """
        沒有虧損單時回 None,**不是無限大**。
        無限大會讓排序把一個只有兩筆交易的分群排到第一。
        """
        if self.gross_loss <= 0:
            return None
        return self.gross_profit / self.gross_loss

    @property
    def expectancy(self):
        return (self.net_pnl / self.trades) if self.trades else 0.0

    @property
    def reliable(self):
        """這個分群的數字能不能拿來下結論。"""
        return self.trades >= MIN_SAMPLE

    def add(self, pnl):
        self.trades += 1
        self.net_pnl += pnl
        if pnl > 0:
            self.wins += 1
            self.gross_profit += pnl
        else:
            self.losses += 1
            self.gross_loss += abs(pnl)

    def to_dict(self):
        return {
            "key": self.key,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": (
                round(self.profit_factor, 3)
                if self.profit_factor is not None else None
            ),
            "expectancy": round(self.expectancy, 4),
            "net_pnl": round(self.net_pnl, 4),
            "reliable": self.reliable,
        }


def _pnl(trade):
    value = trade.get("pnl_usdt")
    return float(value) if value is not None else None


def closed_with_pnl(trades):
    """只取有損益數字的已平倉交易。"""
    return [
        trade for trade in trades
        if trade.get("status") == "CLOSED" and _pnl(trade) is not None
    ]


def group_by(trades, key_fn, label="key"):
    """
    依 key_fn 分群。key_fn 回傳 None 的交易會被**排除**,
    不會被歸進一個叫「未知」的桶子 —— 那個桶子的統計沒有意義,
    而且會讓人以為那是一個真實的分類。
    """
    buckets: Dict[str, Bucket] = {}
    skipped = 0

    for trade in closed_with_pnl(trades):
        key = key_fn(trade)
        if key is None:
            skipped += 1
            continue

        key = str(key)
        bucket = buckets.setdefault(key, Bucket(key=key))
        bucket.add(_pnl(trade))

    return buckets, skipped


def by_regime(trades):
    return group_by(trades, lambda t: t.get("market_regime"))


def by_strategy(trades):
    return group_by(trades, lambda t: t.get("strategy"))


def by_exit_reason(trades):
    return group_by(trades, lambda t: t.get("close_reason"))


def by_symbol(trades):
    return group_by(trades, lambda t: t.get("symbol"))


def by_direction(trades):
    return group_by(trades, lambda t: t.get("signal"))


@dataclass
class ConcentrationCheck:
    """
    績效有多依賴少數幾筆交易。

    扣掉最好的幾筆之後期望值就變負,代表這套系統其實沒有優勢 ——
    只是運氣好抓到幾根大的。那幾根通常不會重演。
    """
    total_trades: int = 0
    expectancy: float = 0.0
    expectancy_without_top3: Optional[float] = None
    top3_share_of_profit: Optional[float] = None
    depends_on_outliers: bool = False
    reliable: bool = False

    def to_dict(self):
        return dict(self.__dict__)


def concentration(trades):
    closed = closed_with_pnl(trades)
    check = ConcentrationCheck(total_trades=len(closed))

    if not closed:
        return check

    pnls = sorted((_pnl(t) for t in closed), reverse=True)
    check.expectancy = round(sum(pnls) / len(pnls), 4)
    check.reliable = len(pnls) >= MIN_SAMPLE

    if len(pnls) <= 3:
        return check

    rest = pnls[3:]
    check.expectancy_without_top3 = round(sum(rest) / len(rest), 4)

    gross_profit = sum(p for p in pnls if p > 0)
    if gross_profit > 0:
        top3_profit = sum(p for p in pnls[:3] if p > 0)
        check.top3_share_of_profit = round(top3_profit / gross_profit, 4)

    check.depends_on_outliers = (
        check.expectancy > 0 and check.expectancy_without_top3 <= 0
    )
    return check


@dataclass
class AttributionReport:
    total_trades: int = 0
    attributed_trades: int = 0
    buckets: Dict[str, List[Dict]] = field(default_factory=dict)
    skipped: Dict[str, int] = field(default_factory=dict)
    concentration: Optional[ConcentrationCheck] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "total_trades": self.total_trades,
            "attributed_trades": self.attributed_trades,
            "buckets": {k: list(v) for k, v in self.buckets.items()},
            "skipped": dict(self.skipped),
            "concentration": (
                self.concentration.to_dict() if self.concentration else None
            ),
            "warnings": list(self.warnings),
        }


DIMENSIONS = {
    "regime": by_regime,
    "strategy": by_strategy,
    "exit_reason": by_exit_reason,
    "symbol": by_symbol,
    "direction": by_direction,
}


def build(trades):
    report = AttributionReport()
    closed = closed_with_pnl(trades)
    report.total_trades = len(closed)

    for name, fn in DIMENSIONS.items():
        buckets, skipped = fn(trades)
        report.buckets[name] = [
            b.to_dict() for b in sorted(
                buckets.values(), key=lambda x: x.net_pnl, reverse=True,
            )
        ]
        report.skipped[name] = skipped

    report.attributed_trades = report.total_trades - report.skipped.get("regime", 0)
    report.concentration = concentration(trades)

    _add_warnings(report)
    return report


def _add_warnings(report):
    if report.total_trades < MIN_SAMPLE_HARD:
        report.warnings.append(
            f"只有 {report.total_trades} 筆已平倉交易,任何分群都不足以下結論。"
        )
        return

    if report.total_trades < MIN_SAMPLE:
        report.warnings.append(
            f"只有 {report.total_trades} 筆已平倉交易(門檻 {MIN_SAMPLE})。"
            f"下面的數字可以看,但不能當作結論。"
        )

    skipped = report.skipped.get("regime", 0)
    if skipped:
        report.warnings.append(
            f"{skipped} 筆交易沒有市況紀錄(Phase 15 之前開的倉),"
            f"已從市況歸因中排除。這些交易無法事後補回歸因。"
        )

    check = report.concentration
    if check and check.depends_on_outliers:
        report.warnings.append(
            f"扣掉最好的三筆之後期望值變成 {check.expectancy_without_top3:+.4f} ——"
            f"這套系統的績效依賴少數幾筆極端獲利,那不是優勢。"
        )

    for name, buckets in report.buckets.items():
        losing = [
            b for b in buckets
            if b["reliable"] and b["expectancy"] <= 0
        ]
        for bucket in losing:
            report.warnings.append(
                f"{name} = {bucket['key']} 的期望值是 {bucket['expectancy']:+.4f}"
                f"({bucket['trades']} 筆),這個分群長期在虧。"
            )
