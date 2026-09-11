"""
Agent 群的組裝與執行。

`deliberate()` 是這一層唯一的對外入口:

    context -> 每個 Agent 各自出意見 -> Consensus -> Supervisor -> Deliberation

呼叫端拿到的可能是一個 TradeIntent,也可能只是 WAIT 與理由。
**無論哪一種,它都還沒有下單** —— TradeIntent 要先過 Risk Engine。
"""
import logging
from typing import List

from agmcis.agents import consensus as consensus_module
from agmcis.agents.base import AgentContext, BaseAgent
from agmcis.agents.builtin import ALL_AGENT_CLASSES
from agmcis.agents.supervisor import Supervisor
from agmcis.core.enums import MarketType

logger = logging.getLogger("agmcis.agents.registry")


class AgentRegistry:
    def __init__(self, agents=None):
        self._agents: List[BaseAgent] = list(agents) if agents else [
            cls() for cls in ALL_AGENT_CLASSES
        ]

    @property
    def agents(self):
        return list(self._agents)

    @property
    def names(self):
        return [agent.name for agent in self._agents]

    def add(self, agent):
        self._agents.append(agent)
        return self

    def collect(self, context):
        """跑完所有 Agent。任何一個壞掉都不會中斷其他人。"""
        return [agent.analyse(context) for agent in self._agents]


_DEFAULT_REGISTRY = None
_DEFAULT_SUPERVISOR = None


def get_registry():
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = AgentRegistry()
    return _DEFAULT_REGISTRY


def get_supervisor():
    global _DEFAULT_SUPERVISOR
    if _DEFAULT_SUPERVISOR is None:
        _DEFAULT_SUPERVISOR = Supervisor()
    return _DEFAULT_SUPERVISOR


def deliberate(context, registry=None, supervisor=None,
               market_type=MarketType.PERPETUAL, **consensus_kwargs):
    """
    跑完整個 Agent 流程。

    回傳 (Deliberation, SupervisorReport)。
    Supervisor 只能把決議降級成 WAIT,不可能反過來製造出一筆交易。
    """
    registry = registry or get_registry()
    supervisor = supervisor if supervisor is not None else get_supervisor()

    opinions = registry.collect(context)

    entry = context.price
    if entry is None and context.indicators is not None:
        entry = getattr(context.indicators, "price", None)

    deliberation = consensus_module.decide(
        context.symbol, opinions, entry=entry,
        market_type=market_type,
        timeframe=context.timeframe,
        market_regime=(
            context.regime.regime.value if context.regime is not None else None
        ),
        **consensus_kwargs,
    )

    report = supervisor.review(deliberation)
    return deliberation, report
