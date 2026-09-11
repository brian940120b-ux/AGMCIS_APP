"""
策略註冊表與集成。

Master Prompt 第 40 條:不要只依賴一個策略,只有在達到共識時才交易。

共識規則:

  1. **有策略投反方向 -> 一律 WAIT。** 這是硬規則。
     策略互相矛盾時不交易,比「多數決」安全 —— 多數決會在 2 比 1 時開單,
     但那個 1 可能正好看到了另外兩個沒看到的東西。

  2. **棄權不等於反對。** min_agreeing 預設是 1,不是 2。

     這一點我一開始弄錯了,實測才發現:四個策略在找的是**不同的 setup**。
     breakout 沒出手不代表它反對趨勢,只代表它沒看到突破;
     mean_reversion 在趨勢市棄權是它的設計,不是它有意見。

     用 min_agreeing=2 的結果是:強趨勢時只有 trend_following 出手,
     系統就永遠不交易 —— 而強趨勢正是順勢系統最該交易的情境。
     等於用一條看起來保守的規則,把系統的核心功能關掉了。

     正確的做法是讓**分數**去反映佐證強度(scorer 的 strategy_consensus
     項目已經用同向比例加權),再由 MIN_SIGNAL_SCORE 當真正的品質門檻。
     只有一個策略同向的訊號分數自然會低,不需要另外用硬門檻擋。

  3. 停損取**最保守**的那個(離進場最近的),停利取最近的。
     不同策略對同一個方向的風險評估不同時,聽最謹慎的那個。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from agmcis.core.enums import Direction
from agmcis.strategy.base import Strategy
from agmcis.strategy.builtin import BUILTIN_STRATEGIES

logger = logging.getLogger("agmcis.strategy_registry")

# 棄權不等於反對(見模組說明)。真正的品質門檻是 MIN_SIGNAL_SCORE。
DEFAULT_MIN_AGREEING = 1


@dataclass
class Consensus:
    direction: Direction = Direction.WAIT
    confidence: float = 0.0
    agreeing: List[str] = field(default_factory=list)
    opposing: List[str] = field(default_factory=list)
    waiting: List[str] = field(default_factory=list)
    verdicts: List[dict] = field(default_factory=list)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    blocked_reason: Optional[str] = None

    @property
    def is_actionable(self):
        return self.direction.is_directional and self.blocked_reason is None

    def to_dict(self):
        return {
            "direction": self.direction.value,
            "confidence": round(self.confidence, 2),
            "agreeing": list(self.agreeing),
            "opposing": list(self.opposing),
            "waiting": list(self.waiting),
            "verdicts": list(self.verdicts),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reasons": list(self.reasons),
            "blocked_reason": self.blocked_reason,
        }


class StrategyRegistry:
    def __init__(self, strategies=None, min_agreeing=DEFAULT_MIN_AGREEING):
        self._strategies = list(strategies) if strategies is not None else [
            cls() for cls in BUILTIN_STRATEGIES
        ]
        self.min_agreeing = min_agreeing

    @property
    def names(self):
        return [s.name for s in self._strategies]

    def register(self, strategy):
        if not isinstance(strategy, Strategy):
            raise TypeError(f"{strategy!r} 不是 Strategy")
        self._strategies.append(strategy)
        return self

    def evaluate_all(self, indicators, regime):
        verdicts = []
        for strategy in self._strategies:
            try:
                verdicts.append(strategy.evaluate(indicators, regime))
            except Exception as exc:
                # 單一策略出錯不該讓整輪掛掉,但一定要記錄
                logger.exception(
                    "策略 %s 評估失敗 | %s | %s", strategy.name, indicators.symbol, exc,
                )
                verdicts.append(strategy.wait(f"策略錯誤: {exc}"))
        return verdicts

    def consensus(self, indicators, regime):
        """集成所有策略的看法。"""
        if not indicators.data_ok:
            return Consensus(
                blocked_reason=f"資料不可信: {indicators.data_error}",
                reasons=["資料品質不合格,不產生訊號"],
            )

        if not regime.is_tradeable:
            return Consensus(
                blocked_reason=f"市況不可交易: {regime.regime.value}/{regime.volatility.value}",
                reasons=list(regime.reasons),
            )

        verdicts = self.evaluate_all(indicators, regime)
        serialised = [v.to_dict() for v in verdicts]

        longs = [v for v in verdicts if v.direction is Direction.LONG and v.confidence > 0]
        shorts = [v for v in verdicts if v.direction is Direction.SHORT and v.confidence > 0]
        waiting = [v.strategy for v in verdicts if not v.is_actionable]

        # ---- 有策略投反方向就不交易 ----
        if longs and shorts:
            return Consensus(
                direction=Direction.WAIT,
                agreeing=[v.strategy for v in longs],
                opposing=[v.strategy for v in shorts],
                waiting=waiting,
                verdicts=serialised,
                blocked_reason="策略互相矛盾",
                reasons=[
                    f"做多: {', '.join(v.strategy for v in longs)};"
                    f"做空: {', '.join(v.strategy for v in shorts)}"
                ],
            )

        winners = longs or shorts
        if not winners:
            return Consensus(
                direction=Direction.WAIT, waiting=waiting, verdicts=serialised,
                blocked_reason="沒有策略出手",
                reasons=["所有策略都選擇觀望"],
            )

        if len(winners) < self.min_agreeing:
            return Consensus(
                direction=Direction.WAIT,
                agreeing=[v.strategy for v in winners],
                waiting=waiting, verdicts=serialised,
                blocked_reason="同向策略數不足",
                reasons=[
                    f"只有 {len(winners)} 個策略同向,需要 {self.min_agreeing} 個"
                ],
            )

        # 佐證強度不足時不擋下,而是讓分數反映出來 ——
        # 只有一個策略同向的訊號,strategy_consensus 項目自然拿不到高分。

        direction = winners[0].direction
        confidence = sum(v.confidence for v in winners) / len(winners)

        # 停損取最保守(離進場最近)的
        stops = [v.stop_loss for v in winners if v.stop_loss is not None]
        targets = [v.take_profit for v in winners if v.take_profit is not None]

        if direction is Direction.LONG:
            stop_loss = max(stops) if stops else None       # 越高越保守
            take_profit = min(targets) if targets else None  # 越低越保守
        else:
            stop_loss = min(stops) if stops else None       # 越低越保守
            take_profit = max(targets) if targets else None

        reasons = []
        for verdict in winners:
            reasons.extend(f"[{verdict.strategy}] {r}" for r in verdict.reasons)

        return Consensus(
            direction=direction,
            confidence=confidence,
            agreeing=[v.strategy for v in winners],
            waiting=waiting,
            verdicts=serialised,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasons=reasons,
        )


_registry = None


def get_registry():
    global _registry
    if _registry is None:
        _registry = StrategyRegistry()
    return _registry


def set_registry(registry):
    global _registry
    _registry = registry
