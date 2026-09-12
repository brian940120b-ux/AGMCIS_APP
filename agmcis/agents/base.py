"""
Agent 的共同介面。

三個型別:
    AgentContext  Agent 看得到的東西(唯讀)
    AgentOpinion  Agent 能產出的東西(只有意見,沒有下單)
    BaseAgent     所有 Agent 的基底

設計上 Agent 拿不到交易所連線,也拿不到下單函式 —— 不是「約定不要用」,
是根本沒有給。
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from agmcis.core.enums import Direction

logger = logging.getLogger("agmcis.agents")


class Vote(str, Enum):
    """
    Agent 的立場。

    ABSTAIN 與 WAIT 是**不同**的兩件事,這個區別在 Phase 6 用血換來過:

      WAIT    = 「我看得懂這個盤,而我的結論是不要進場。」
      ABSTAIN = 「這不是我負責判斷的東西,別把我算進去。」

    把 ABSTAIN 當成反對票,會讓專職抓特定型態的 Agent 在
    型態沒出現時變成反對者,整個系統就永遠不交易了。
    """
    LONG = "做多"
    SHORT = "做空"
    WAIT = "觀望"
    ABSTAIN = "棄權"

    @property
    def is_directional(self):
        return self in (Vote.LONG, Vote.SHORT)

    @property
    def direction(self):
        if self is Vote.LONG:
            return Direction.LONG
        if self is Vote.SHORT:
            return Direction.SHORT
        return Direction.WAIT

    def opposes(self, other):
        """只有兩個明確且相反的方向才算對立。棄權不對立任何人。"""
        return (
            self.is_directional and other.is_directional and self is not other
        )


@dataclass
class AgentContext:
    """
    Agent 的輸入。**唯讀**,而且只有資料。

    刻意不放:交易所連線、下單函式、資料庫 handle。
    Agent 要什麼資料就由呼叫端先取好放進來 —— 這樣 Agent 永遠是純函式,
    可以離線測試,也不可能在分析過程中意外送出請求。
    """
    symbol: str
    timeframe: str = "1h"
    indicators: Optional[object] = None
    regime: Optional[object] = None
    price: Optional[float] = None

    # 衍生資訊(沒有就是 None,Agent 必須容忍)
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    open_interest_change_pct: Optional[float] = None
    news_impact: Optional[float] = None
    btc_indicators: Optional[object] = None

    # 訂單簿(第十一節)。dict 或 None ——
    # 取不到就是 None,不是一個「平衡」的預設值。
    order_book: Optional[Dict] = None
    # 多空持倉比。> 1 代表做多的人比較多。
    long_short_ratio: Optional[float] = None
    # 近期爆倉統計。
    liquidations: Optional[Dict] = None
    # 消息面情緒:-100 到 100。與 news_impact 的差別是它看的是
    # **整體市場**而不是這一檔 —— 全市場恐慌時個別標的的好消息不算數。
    sentiment_score: Optional[float] = None
    # 重大事件風險(agmcis/risk/news_risk.py 的 NewsRisk)。
    news_risk: Optional[object] = None

    # 已有部位(沒有就是 None)。給管理型 Agent 用。
    position: Optional[Dict] = None

    # 帳戶層資訊
    open_position_count: int = 0
    correlated_symbols: List[str] = field(default_factory=list)

    extra: Dict = field(default_factory=dict)

    @property
    def data_ok(self):
        indicators = self.indicators
        return bool(indicators is not None and getattr(indicators, "data_ok", False))


@dataclass
class AgentOpinion:
    """
    Agent 唯一的輸出。

    沒有 size、沒有 leverage、沒有 order type —— 那些都不是 Agent 的職權。
    停損建議是允許的(Agent 最清楚自己的邏輯在哪裡失效),
    但最終的部位大小仍由 Risk Engine 決定。
    """
    agent: str
    vote: Vote = Vote.ABSTAIN
    confidence: float = 0.0          # 0-100,只在 vote 有方向時才有意義
    weight: float = 1.0              # Agent 在共識中的份量
    reasons: List[str] = field(default_factory=list)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    error: Optional[str] = None
    details: Dict = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.vote, str):
            self.vote = Vote(self.vote)
        self.confidence = max(0.0, min(100.0, float(self.confidence or 0.0)))

    @property
    def counts_towards_consensus(self):
        """棄權與出錯的意見不參與共識,但也不算反對票。"""
        return self.error is None and self.vote is not Vote.ABSTAIN

    @property
    def effective_weight(self):
        return self.weight * (self.confidence / 100.0)

    def to_dict(self):
        return {
            "agent": self.agent,
            "vote": self.vote.value,
            "confidence": round(self.confidence, 2),
            "weight": self.weight,
            "reasons": list(self.reasons),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "error": self.error,
            "details": dict(self.details),
        }


class BaseAgent:
    """
    所有 Agent 的基底。

    子類別只實作 `_analyse(context) -> AgentOpinion`。
    外層的 `analyse()` 負責兩件事:

      1. 資料不可用時直接棄權 —— 資料壞掉不等於市場中性(Phase 0 稽核)。
      2. 例外**不會被吞掉**,會變成一個帶 error 的意見。
         靜默失敗是被禁止的(Master Prompt 第九十四節)。
         一個壞掉的 Agent 必須看得見,而不是安靜地變成「沒意見」。
    """
    name = "base"
    weight = 1.0

    # 這個 Agent 需要已有部位才有意義(管理型 Agent)
    requires_position = False

    def analyse(self, context) -> AgentOpinion:
        if self.requires_position and not context.position:
            return self.abstain("沒有部位,不適用")

        if not context.data_ok:
            return self.abstain("資料不可用")

        try:
            opinion = self._analyse(context)
        except Exception as exc:
            logger.exception("Agent %s 在 %s 上出錯", self.name, context.symbol)
            return AgentOpinion(
                agent=self.name, vote=Vote.ABSTAIN, weight=self.weight,
                error=f"{type(exc).__name__}: {exc}",
            )

        if opinion is None:
            return self.abstain("沒有結論")

        return opinion

    def _analyse(self, context) -> Optional[AgentOpinion]:
        raise NotImplementedError

    # ---- 給子類別用的小工具 ----

    def opinion(self, vote, confidence=0.0, reasons=None, **kwargs):
        return AgentOpinion(
            agent=self.name, vote=vote, confidence=confidence,
            weight=self.weight, reasons=list(reasons or []), **kwargs
        )

    def abstain(self, reason):
        return AgentOpinion(
            agent=self.name, vote=Vote.ABSTAIN, weight=self.weight,
            reasons=[reason],
        )

    def wait(self, reason, confidence=0.0):
        """「我看得懂,但結論是不要進場。」與棄權不同。"""
        return AgentOpinion(
            agent=self.name, vote=Vote.WAIT, confidence=confidence,
            weight=self.weight, reasons=[reason],
        )
