"""
把多個 Agent 的意見彙總成一個決議。

輸出是 `Deliberation`,裡面可能含一個 TradeIntent,也可能只是 WAIT。
**WAIT 是完全合法的結論**,而且應該是最常見的結論。

彙總規則:

  1. 棄權不計入任何一邊。棄權不是反對票(Phase 6 的教訓)。
  2. WAIT 票有否決力道 —— 環境型 Agent 說「這個盤不能碰」時,
     方向型 Agent 再有信心也不該進場。
  3. 管理型 Agent(ExitAgent)不參與進場投票。
     它的職責是既有部位,不是要不要開新倉。
  4. 權重是「Agent 權重 × 該 Agent 的信心」。
     一個信心 30 的 Agent 不該和信心 90 的 Agent 同等份量。
  5. **Consensus 不決定 size 與 leverage。** 那是 Risk Engine 的職責。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agmcis.agents.base import Vote
from agmcis.core.enums import Direction, MarketType
from agmcis.core.errors import TradingRuleViolation
from agmcis.core.models import TradeIntent

logger = logging.getLogger("agmcis.agents.consensus")

# 至少要有這麼多方向票才考慮進場
MIN_DIRECTIONAL_VOTES = 2

# 勝方權重必須佔方向票總權重的這個比例以上
MIN_WINNING_SHARE = 0.6

# WAIT 權重佔全部參與權重的這個比例以上就直接否決
WAIT_VETO_SHARE = 0.35

# 不參與進場投票的 Agent(管理型)
NON_ENTRY_AGENTS = {"exit"}


@dataclass
class Deliberation:
    symbol: str
    direction: Direction = Direction.WAIT
    confidence: float = 0.0
    intent: Optional[TradeIntent] = None
    opinions: List = field(default_factory=list)
    votes: Dict[str, str] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    blocked_reason: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    @property
    def is_actionable(self):
        return self.intent is not None

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "confidence": round(self.confidence, 2),
            "has_intent": self.intent is not None,
            "votes": dict(self.votes),
            "reasons": list(self.reasons),
            "blocked_reason": self.blocked_reason,
            "errors": list(self.errors),
            "opinions": [o.to_dict() for o in self.opinions],
        }


def _tally(opinions):
    """回傳 (方向權重, 方向票數, WAIT 權重, 參與權重)。"""
    weights = {Vote.LONG: 0.0, Vote.SHORT: 0.0}
    counts = {Vote.LONG: 0, Vote.SHORT: 0}
    wait_weight = 0.0
    participating_weight = 0.0

    for opinion in opinions:
        if not opinion.counts_towards_consensus:
            continue
        if opinion.agent in NON_ENTRY_AGENTS:
            continue

        participating_weight += opinion.effective_weight

        if opinion.vote is Vote.WAIT:
            # WAIT 票的份量用 Agent 權重 × 信心。信心 0 的 WAIT 沒有否決力。
            wait_weight += opinion.effective_weight
        elif opinion.vote.is_directional:
            weights[opinion.vote] += opinion.effective_weight
            counts[opinion.vote] += 1

    return weights, counts, wait_weight, participating_weight


def _pick_levels(opinions, direction, entry):
    """
    停損取**最保守**的一個(離進場最近),停利取最保守的一個(離進場最近)。

    不同 Agent 對同一筆交易給的停損不會一樣。取最保守的那個,
    是因為調整只能讓部位更安全,不能更寬鬆 —— 這條規則從 Phase 4 沿用至今。
    """
    stops = [
        o.stop_loss for o in opinions
        if o.stop_loss is not None and o.vote.direction is direction
    ]
    targets = [
        o.take_profit for o in opinions
        if o.take_profit is not None and o.vote.direction is direction
    ]

    if direction is Direction.LONG:
        stop = max((s for s in stops if s < entry), default=None)
        target = min((t for t in targets if t > entry), default=None)
    else:
        stop = min((s for s in stops if s > entry), default=None)
        target = max((t for t in targets if t < entry), default=None)

    return stop, target


def decide(symbol, opinions, entry=None, market_type=MarketType.PERPETUAL,
           default_stop_pct=2.0, timeframe=None, market_regime=None):
    """
    opinions: List[AgentOpinion]

    entry 是現價。沒有現價就無法建立 TradeIntent —— 回傳 WAIT。
    """
    result = Deliberation(symbol=symbol, opinions=list(opinions))
    result.votes = {o.agent: o.vote.value for o in opinions}
    result.errors = [
        f"{o.agent}: {o.error}" for o in opinions if o.error
    ]

    weights, counts, wait_weight, participating = _tally(opinions)

    if participating <= 0:
        result.blocked_reason = "所有 Agent 都棄權或出錯,沒有可用意見"
        return result

    # ---- WAIT 否決 ----
    wait_share = wait_weight / participating
    if wait_share >= WAIT_VETO_SHARE:
        waiters = [
            o for o in opinions
            if o.vote is Vote.WAIT and o.agent not in NON_ENTRY_AGENTS
        ]
        result.blocked_reason = (
            f"觀望票佔 {wait_share * 100:.0f}% 權重,超過否決門檻"
        )
        result.reasons = [
            f"{o.agent}: {'; '.join(o.reasons)}" for o in waiters
        ]
        return result

    # ---- 方向 ----
    long_weight, short_weight = weights[Vote.LONG], weights[Vote.SHORT]
    directional_weight = long_weight + short_weight

    if directional_weight <= 0:
        result.blocked_reason = "沒有任何方向票"
        return result

    if long_weight >= short_weight:
        winner, winner_weight, winner_count = Vote.LONG, long_weight, counts[Vote.LONG]
    else:
        winner, winner_weight, winner_count = Vote.SHORT, short_weight, counts[Vote.SHORT]

    if winner_count < MIN_DIRECTIONAL_VOTES:
        result.blocked_reason = (
            f"只有 {winner_count} 個 Agent 支持{winner.value},"
            f"低於 {MIN_DIRECTIONAL_VOTES} 票門檻"
        )
        return result

    share = winner_weight / directional_weight
    if share < MIN_WINNING_SHARE:
        result.blocked_reason = (
            f"{winner.value}只佔方向票權重的 {share * 100:.0f}%,"
            f"多空分歧過大"
        )
        return result

    result.direction = winner.direction
    result.confidence = round(share * 100, 2)
    result.reasons = [
        f"{o.agent}: {'; '.join(o.reasons)}"
        for o in opinions
        if o.vote is winner and o.agent not in NON_ENTRY_AGENTS
    ]

    # ---- 建立 TradeIntent ----
    if entry is None or entry <= 0:
        result.blocked_reason = "沒有現價,無法建立 TradeIntent"
        return result

    stop, target = _pick_levels(opinions, result.direction, entry)

    if stop is None:
        # 沒有 Agent 給停損時用預設距離。**絕不允許沒有停損的 intent**。
        stop = (
            entry * (1 - default_stop_pct / 100)
            if result.direction is Direction.LONG
            else entry * (1 + default_stop_pct / 100)
        )

    try:
        result.intent = TradeIntent(
            symbol=symbol,
            market_type=market_type,
            direction=result.direction,
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            confidence=result.confidence,
            strategy="multi_agent",
            timeframe=timeframe,
            market_regime=market_regime,
            reasons=list(result.reasons),
            agent_votes=dict(result.votes),
        )
    except TradingRuleViolation as exc:
        # 停損不合法時**不建立 intent**,而且要講清楚原因。
        logger.warning("共識通過但 TradeIntent 建立失敗 | %s | %s", symbol, exc)
        result.blocked_reason = f"TradeIntent 建立失敗:{exc}"
        result.direction = Direction.WAIT

    return result
