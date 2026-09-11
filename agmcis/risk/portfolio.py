"""
Portfolio Risk(Master Prompt 第五十九節)。

Risk Engine 的帳戶層閘門看的是**總和**:總曝險、總部位數、總虧損。
它看不見的是**結構** —— 五個部位分散在五檔,和五個部位其實是同一個
方向的加密貨幣 Beta,在它眼裡是一樣的。

這一層補上那個結構:

    每檔名目曝險上限   MAX_SYMBOL_EXPOSURE_PCT
    相關群風險上限     MAX_CORRELATED_RISK_PCT

## 為什麼相關群管的是「風險」不是「名目」

名目價值是停損距離的倒數:同樣 1% 的單筆風險,停損放 3% 的部位名目是
權益的 33%,停損放 1% 的部位名目是 100%。用名目管相關群,等於懲罰
停損放得近的部位 —— 而那些是比較好的部位。

相關群真正的問題是**一起停損的時候一共虧多少**。所以這裡加的是
每一腿的風險金額(名目 × 停損距離),上限直接拿來跟
MAX_RISK_PER_TRADE_PCT 比較:3% 的相關群上限 = 「最多同時押三個
1% 的同方向賭注」。這個數字看得懂,名目百分比看不懂。

名目還是有算,但只用在單檔上限(那是槓桿與集中度的守門員)與報表。

## 為什麼不給對沖抵銷

做多 BTC + 做空 ETH,方向性曝險確實比兩邊都做多低。這裡**還是**
把它們算成兩筆各自的曝險,不相減。理由不是懶,是:

  * 相關係數是平時的統計量。連環爆倉的時候 BTC 跌 8%、ETH 跌 12%,
    「對沖」的那一邊不但沒有保護,還在虧。
  * 兩邊都是槓桿部位,兩邊都會被追繳保證金。相減之後看起來很小的
    淨曝險,對應的是兩份完整的維持保證金需求。
  * 給抵銷會產生一個可以被利用的漏洞:想繞過曝險上限的時候,
    開一個反向部位就好。

所以相關群裡**只把同向的腿相加**。反向的腿不加也不減 ——
它自己會被「每檔曝險上限」管到。

## 不知道相關係數的時候

一律當成相關。這是這一層唯一合理的預設值:相關性未知就假設不相關,
等於在資料最少的時候給最寬鬆的額度。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agmcis.risk.correlation import DEFAULT_THRESHOLD, CorrelationMatrix

logger = logging.getLogger("agmcis.risk.portfolio")


def _sign(direction):
    """做多 +1,做空 -1,其他 0。接受列舉或字串。"""
    value = getattr(direction, "value", direction)
    text = str(value).strip().upper()

    if text in ("做多", "LONG", "BUY", "看多"):
        return 1
    if text in ("做空", "SHORT", "SELL", "看空"):
        return -1
    return 0


@dataclass
class Leg:
    """一個部位在曝險計算裡的樣子。名目價值,不是保證金。"""
    symbol: str
    direction: str
    notional_usdt: float
    entry: Optional[float] = None
    stop_loss: Optional[float] = None

    @property
    def sign(self):
        return _sign(self.direction)

    @property
    def risk_usdt(self):
        """
        這一腿觸及停損時會虧掉多少。算不出來回 None ——
        **不是回 0**,那會讓一個沒有停損的部位在組合風險裡消失。
        """
        if not self.entry or self.stop_loss is None:
            return None
        try:
            entry = float(self.entry)
            stop = float(self.stop_loss)
        except (TypeError, ValueError):
            return None
        if entry <= 0:
            return None
        return abs(self.notional_usdt) * abs(entry - stop) / entry


@dataclass
class ExposureReport:
    equity: float = 0.0
    symbol_pct: Dict[str, float] = field(default_factory=dict)
    cluster_symbols: List[str] = field(default_factory=list)
    cluster_notional_pct: float = 0.0
    cluster_risk_pct: float = 0.0
    assumed_risk_symbols: List[str] = field(default_factory=list)
    assumed_correlated: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def allowed(self):
        return not self.blockers

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "equity": self.equity,
            "symbol_pct": {k: round(v, 4) for k, v in self.symbol_pct.items()},
            "cluster_symbols": list(self.cluster_symbols),
            "cluster_notional_pct": round(self.cluster_notional_pct, 4),
            "cluster_risk_pct": round(self.cluster_risk_pct, 4),
            "assumed_risk_symbols": list(self.assumed_risk_symbols),
            "assumed_correlated": list(self.assumed_correlated),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def _as_leg(item):
    if isinstance(item, Leg):
        return item

    symbol = item.get("symbol")
    direction = item.get("direction") or item.get("signal")

    notional = item.get("notional_usdt")
    if notional is None:
        notional = item.get("position_value")
    if notional is None:
        size = item.get("size_usdt") or 0.0
        leverage = item.get("leverage") or 1.0
        notional = float(size) * float(leverage)

    return Leg(
        symbol=symbol,
        direction=direction,
        notional_usdt=float(notional or 0.0),
        entry=item.get("entry") or item.get("entry_price"),
        stop_loss=item.get("stop_loss") if item.get("stop_loss") is not None
                  else item.get("stoploss"),
    )


def assess(candidate, open_positions, equity, matrix=None,
           max_symbol_pct=None, max_cluster_risk_pct=None,
           threshold=DEFAULT_THRESHOLD, assumed_risk_pct=1.0):
    """
    候選部位加進去之後,組合會長什麼樣子。

    `candidate` 是 Leg 或 dict(要加的那一筆);`open_positions` 是現有部位。
    `matrix` 是 CorrelationMatrix,**None 代表完全不知道相關性** ——
    那會讓所有同向部位都被當成同一群。

    `assumed_risk_pct` 是算不出某一腿風險時(沒有停損價或進場價)採用的
    替代值,單位是權益百分比。它是**假設**,會進 warnings。

    回傳 ExposureReport。`allowed=False` 時 blockers 說明是哪一條。
    """
    report = ExposureReport(equity=float(equity or 0.0))

    if report.equity <= 0:
        report.blockers.append("NO_EQUITY")
        report.warnings.append("權益為 0 或未知,算不出任何曝險比例")
        return report

    new_leg = _as_leg(candidate)
    existing = [_as_leg(p) for p in (open_positions or [])]

    # ---- 每檔曝險 ----
    per_symbol = {}
    for leg in existing + [new_leg]:
        per_symbol[leg.symbol] = per_symbol.get(leg.symbol, 0.0) + abs(leg.notional_usdt)

    report.symbol_pct = {
        symbol: value / report.equity * 100.0 for symbol, value in per_symbol.items()
    }

    if max_symbol_pct is not None:
        candidate_pct = report.symbol_pct.get(new_leg.symbol, 0.0)
        if candidate_pct > max_symbol_pct:
            report.blockers.append("MAX_SYMBOL_EXPOSURE")
            report.warnings.append(
                f"{new_leg.symbol} 曝險 {candidate_pct:.1f}% 超過上限 {max_symbol_pct}%"
            )

    # ---- 相關群曝險 ----
    if new_leg.sign == 0:
        report.warnings.append(
            f"{new_leg.symbol} 方向不明({new_leg.direction}),不做相關群計算"
        )
        return report

    cluster = [new_leg]

    for leg in existing:
        if leg.symbol == new_leg.symbol:
            cluster.append(leg)
            continue

        if leg.sign == 0:
            report.warnings.append(f"{leg.symbol} 方向不明,保守計入相關群")
            cluster.append(leg)
            report.assumed_correlated.append(leg.symbol)
            continue

        related = (
            None if matrix is None
            else matrix.is_correlated(leg.symbol, new_leg.symbol, threshold)
        )

        if related is None:
            # 不知道 = 當成相關。見模組開頭。
            report.assumed_correlated.append(leg.symbol)
            related = True

        if not related:
            continue

        correlation = 1.0 if matrix is None else (
            matrix.get(leg.symbol, new_leg.symbol) or 1.0
        )

        # 相關係數為負代表「反向走」,所以反向的腿才是同一個賭注。
        same_bet = (leg.sign == new_leg.sign) if correlation >= 0 else (leg.sign != new_leg.sign)

        if same_bet:
            cluster.append(leg)

    report.cluster_symbols = sorted({leg.symbol for leg in cluster})
    report.cluster_notional_pct = (
        sum(abs(leg.notional_usdt) for leg in cluster) / report.equity * 100.0
    )

    fallback_risk = report.equity * float(assumed_risk_pct) / 100.0
    cluster_risk = 0.0

    for leg in cluster:
        risk = leg.risk_usdt
        if risk is None:
            risk = fallback_risk
            report.assumed_risk_symbols.append(leg.symbol)
        cluster_risk += risk

    report.cluster_risk_pct = cluster_risk / report.equity * 100.0

    if report.assumed_risk_symbols:
        report.warnings.append(
            f"以下標的算不出停損距離,風險以 {assumed_risk_pct}% 權益估計:"
            f"{', '.join(sorted(set(report.assumed_risk_symbols)))}"
        )

    if (max_cluster_risk_pct is not None
            and report.cluster_risk_pct > max_cluster_risk_pct):
        report.blockers.append("MAX_CORRELATED_RISK")
        report.warnings.append(
            f"相關群 {'+'.join(report.cluster_symbols)} 一起停損會虧 "
            f"{report.cluster_risk_pct:.2f}% 權益,超過上限 {max_cluster_risk_pct}%"
        )

    if report.assumed_correlated:
        report.warnings.append(
            f"以下標的因為算不出相關係數而被當成相關:"
            f"{', '.join(sorted(set(report.assumed_correlated)))}"
        )

    return report
