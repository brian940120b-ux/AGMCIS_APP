"""
策略介面。

與舊版 strategies/*.py 的差別:

  1. **舊策略只做多。** buy_signal/sell_signal 的 sell 是「平多」不是「做空」,
     所以整個系統宣稱支援 Long/Short 但策略層根本產不出空單。
     新介面回傳方向,LONG 與 SHORT 是對等的。

  2. **舊策略吃 10 個位置參數。** 加一個指標要改所有策略的簽名。
     新介面吃一個 Indicators 物件。

  3. **舊策略不知道市況。** 趨勢策略在盤整市會被反覆洗出場。
     新介面拿得到 MarketRegime,可以自己決定要不要出手。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from agmcis.core.enums import Direction


@dataclass
class StrategyVerdict:
    """
    單一策略的看法。

    這不是交易指令 —— WAIT 是完全合法而且常見的結論。
    confidence 是這個策略對自己這次判斷的信心(0-100),
    不是「會賺的機率」。
    """
    strategy: str
    direction: Direction = Direction.WAIT
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    @property
    def is_actionable(self):
        return self.direction.is_directional and self.confidence > 0

    def to_dict(self):
        return {
            "strategy": self.strategy,
            "direction": self.direction.value,
            "confidence": round(self.confidence, 2),
            "reasons": list(self.reasons),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
        }


class Strategy(ABC):
    name = "base"
    # 這個策略適合哪些市況。空集合代表不挑市況。
    suitable_regimes = frozenset()

    @abstractmethod
    def evaluate(self, indicators, regime) -> StrategyVerdict:
        """看一眼市場,回傳看法。不得有副作用,不得碰交易所。"""

    def wait(self, *reasons):
        return StrategyVerdict(
            strategy=self.name, direction=Direction.WAIT, reasons=list(reasons)
        )

    def suits(self, regime):
        if not self.suitable_regimes:
            return True
        return regime.regime.value in self.suitable_regimes

    # ---------------- 停損停利的共用算法 ----------------

    @staticmethod
    def atr_levels(indicators, direction, stop_mult=2.0, target_mult=3.0):
        """
        用 ATR 計算停損停利,並**依方向擺在正確的一側**。

        舊系統的 scanner 一律算 `price - atr*2`,做空時會把停損放在
        進場價下方 —— 那在開倉的瞬間就會觸發。
        """
        price, atr = indicators.price, indicators.atr
        if not price or not atr:
            return None, None

        if direction is Direction.LONG:
            return (
                round(price - atr * stop_mult, 8),
                round(price + atr * target_mult, 8),
            )
        if direction is Direction.SHORT:
            return (
                round(price + atr * stop_mult, 8),
                round(price - atr * target_mult, 8),
            )
        return None, None
