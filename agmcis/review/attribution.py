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


def _month_of(trade):
    """
    交易所屬的月份。用**平倉時間**,不是開倉時間 ——
    損益是在平倉那一刻實現的。

    時間格式不對就回 None(那筆會被排除),不硬解析成某個日期。
    """
    stamp = trade.get("closed_at") or trade.get("opened_at")
    if not stamp:
        return None

    text = str(stamp)
    # "2026-09-11 12:00:00" 與 ISO 格式都取前 7 碼
    if len(text) >= 7 and text[4] == "-":
        return text[:7]
    return None


def by_month(trades):
    return group_by(trades, _month_of)


@dataclass
class TrendCheck:
    """
    績效有沒有在衰退。

    一個月比一個月差,通常代表策略的優勢正在消失 ——
    可能是市場結構變了,也可能是它從一開始就沒有優勢,
    前面只是運氣。兩種都需要停下來看。
    """
    months: List[str] = field(default_factory=list)
    expectancies: List[float] = field(default_factory=list)
    recent_expectancy: Optional[float] = None
    earlier_expectancy: Optional[float] = None
    declining: bool = False
    reliable: bool = False

    def to_dict(self):
        return dict(self.__dict__)


# 至少要有這麼多個月才談得上趨勢
MIN_MONTHS_FOR_TREND = 3


def trend(trades):
    """
    比較「最近一個月」與「更早的月份」。

    刻意不做線性迴歸之類的東西 —— 交易月數通常個位數,
    在那麼少的點上擬合一條線,斜率幾乎完全由雜訊決定。
    """
    buckets, _ = by_month(trades)
    check = TrendCheck()

    if not buckets:
        return check

    months = sorted(buckets)
    check.months = months
    check.expectancies = [round(buckets[m].expectancy, 4) for m in months]

    if len(months) < MIN_MONTHS_FOR_TREND:
        return check

    check.reliable = all(buckets[m].trades >= MIN_SAMPLE_HARD for m in months)

    recent_bucket = buckets[months[-1]]
    earlier = [buckets[m] for m in months[:-1]]

    earlier_trades = sum(b.trades for b in earlier)
    if earlier_trades == 0:
        return check

    check.recent_expectancy = round(recent_bucket.expectancy, 4)
    check.earlier_expectancy = round(
        sum(b.net_pnl for b in earlier) / earlier_trades, 4,
    )

    check.declining = (
        check.earlier_expectancy > 0
        and check.recent_expectancy < check.earlier_expectancy * 0.5
    )

    return check


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
    trend: Optional[TrendCheck] = None
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
            "trend": self.trend.to_dict() if self.trend else None,
            "warnings": list(self.warnings),
        }


DIMENSIONS = {
    "month": by_month,
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
    report.trend = trend(trades)

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

    decay = report.trend
    if decay and decay.declining:
        report.warnings.append(
            f"最近一個月的期望值 {decay.recent_expectancy:+.4f} 只有先前 "
            f"{decay.earlier_expectancy:+.4f} 的一半不到 —— 績效正在衰退。"
            f"可能是市場結構變了,也可能是它從一開始就沒有優勢、前面只是運氣。"
        )

    if decay and decay.months and len(decay.months) < MIN_MONTHS_FOR_TREND:
        report.warnings.append(
            f"只有 {len(decay.months)} 個月的資料(門檻 {MIN_MONTHS_FOR_TREND}),"
            f"還看不出績效趨勢。"
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
