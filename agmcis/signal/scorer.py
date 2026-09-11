"""
單一 0-100 評分(Master Prompt 第 27 條)。

原本系統有兩套評分:

    scanner_service.calculate_scanner_confidence()   基準 50,趨勢 ±25、MACD ±20、RSI ±20
    strategy.analyze_symbol()                        從 0 累加,EMA 15+20、RSI 15、MACD 20、量 15、ADX 15

同一檔標的在兩套裡會得到完全不同的分數,而 Dashboard 顯示 A、下單依據 B。
現在只有一份。

⚠️ 權重是**起始值,不是回測結果**。Master Prompt 第 27 條明確說
   「實際權重由 Quant Research Agent 研究,不要固定認為這一定最佳」。
   Phase 8 的 Walk Forward 之後才會知道哪些權重真的有效。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agmcis.core.enums import Direction

# 各項目的滿分。加總 100。
WEIGHTS = {
    "strategy_consensus": 30,   # 策略共識(最重要:多個獨立方法同意)
    "trend_alignment": 15,      # 趨勢與方向一致
    "momentum": 15,             # 動能
    "volume": 10,               # 量能
    "market_regime": 15,        # 市況支持
    "volatility": 10,           # 波動度適中
    "risk_reward": 5,           # 風報比
}

# 這些項目缺資料時給的分數。給 0 而不是給一半 ——
# 沒有資料不等於「中性」,那是 Phase 0 稽核抓到的根本問題。
MISSING = 0.0


@dataclass
class ScoreBreakdown:
    total: float = 0.0
    components: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    @property
    def max_possible(self):
        return sum(WEIGHTS.values())

    @property
    def coverage_pct(self):
        """有多少比例的評分項目拿得到資料。太低的話分數不可信。"""
        available = self.max_possible - sum(WEIGHTS[k] for k in self.missing)
        return available / self.max_possible * 100 if self.max_possible else 0

    def to_dict(self):
        return {
            "total": round(self.total, 2),
            "components": {k: round(v, 2) for k, v in self.components.items()},
            "weights": dict(WEIGHTS),
            "coverage_pct": round(self.coverage_pct, 1),
            "missing": list(self.missing),
            "notes": list(self.notes),
        }


def _scaled(weight, ratio):
    return weight * max(0.0, min(1.0, ratio))


def score(consensus, indicators, regime, risk_reward=None):
    """
    把共識、指標與市況算成一個 0-100 分。

    回傳 ScoreBreakdown —— 刻意保留每一項的得分,
    這樣 Dashboard 可以回答「為什麼是 87 分」而不只是顯示 87。
    """
    breakdown = ScoreBreakdown()
    direction = consensus.direction

    # ---- 1. 策略共識 ----
    if consensus.agreeing:
        total_strategies = len(consensus.agreeing) + len(consensus.waiting) + len(consensus.opposing)
        agreement_ratio = len(consensus.agreeing) / max(1, total_strategies)
        confidence_ratio = consensus.confidence / 100.0
        value = _scaled(
            WEIGHTS["strategy_consensus"],
            agreement_ratio * 0.5 + confidence_ratio * 0.5,
        )
        breakdown.notes.append(
            f"{len(consensus.agreeing)}/{total_strategies} 個策略同向,"
            f"平均信心 {consensus.confidence:.1f}"
        )
    else:
        value = MISSING
        breakdown.missing.append("strategy_consensus")
    breakdown.components["strategy_consensus"] = value

    # ---- 2. 趨勢一致 ----
    if indicators.trend == "UNKNOWN":
        breakdown.components["trend_alignment"] = MISSING
        breakdown.missing.append("trend_alignment")
    else:
        aligned = (
            (direction is Direction.LONG and indicators.trend == "BULLISH")
            or (direction is Direction.SHORT and indicators.trend == "BEARISH")
        )
        breakdown.components["trend_alignment"] = (
            WEIGHTS["trend_alignment"] if aligned else 0.0
        )
        breakdown.notes.append(
            f"趨勢 {indicators.trend} 與方向{'一致' if aligned else '不一致'}"
        )

    # ---- 3. 動能 ----
    hist = indicators.macd_hist
    if hist is None:
        breakdown.components["momentum"] = MISSING
        breakdown.missing.append("momentum")
    else:
        favourable = (
            (direction is Direction.LONG and hist > 0)
            or (direction is Direction.SHORT and hist < 0)
        )
        breakdown.components["momentum"] = WEIGHTS["momentum"] if favourable else 0.0

    # ---- 4. 量能 ----
    volume_ratio = indicators.volume_ratio
    if volume_ratio is None:
        breakdown.components["volume"] = MISSING
        breakdown.missing.append("volume")
    else:
        # 1.0 倍給一半,2.0 倍以上給滿分
        breakdown.components["volume"] = _scaled(
            WEIGHTS["volume"], (volume_ratio - 0.5) / 1.5
        )
        breakdown.notes.append(f"量能 {volume_ratio:.2f} 倍於均量")

    # ---- 5. 市況 ----
    if regime.regime.value == "UNKNOWN":
        breakdown.components["market_regime"] = MISSING
        breakdown.missing.append("market_regime")
    elif regime.favours(direction.value):
        breakdown.components["market_regime"] = WEIGHTS["market_regime"]
        breakdown.notes.append(f"市況 {regime.regime.value} 支持這個方向")
    elif regime.regime.value == "RANGE":
        breakdown.components["market_regime"] = WEIGHTS["market_regime"] * 0.4
        breakdown.notes.append("盤整市,市況不特別支持任一方向")
    else:
        breakdown.components["market_regime"] = 0.0
        breakdown.notes.append(f"市況 {regime.regime.value} 與方向相反")

    # ---- 6. 波動度:適中最好,太低沒行情,太高停損容易被掃 ----
    atr_pct = indicators.atr_pct
    if atr_pct is None:
        breakdown.components["volatility"] = MISSING
        breakdown.missing.append("volatility")
    else:
        if atr_pct >= 5.0:
            value, note = 0.0, f"ATR {atr_pct:.2f}% 極端波動"
        elif atr_pct >= 3.0:
            value, note = WEIGHTS["volatility"] * 0.3, f"ATR {atr_pct:.2f}% 偏高"
        elif atr_pct >= 0.5:
            value, note = WEIGHTS["volatility"], f"ATR {atr_pct:.2f}% 適中"
        else:
            value, note = WEIGHTS["volatility"] * 0.5, f"ATR {atr_pct:.2f}% 過低,行情不足"
        breakdown.components["volatility"] = value
        breakdown.notes.append(note)

    # ---- 7. 風報比 ----
    if risk_reward is None:
        breakdown.components["risk_reward"] = MISSING
        breakdown.missing.append("risk_reward")
    else:
        # 1:1 給 0 分,1:3 以上給滿分
        breakdown.components["risk_reward"] = _scaled(
            WEIGHTS["risk_reward"], (risk_reward - 1.0) / 2.0
        )
        breakdown.notes.append(f"風報比 1:{risk_reward}")

    breakdown.total = round(sum(breakdown.components.values()), 2)
    return breakdown
