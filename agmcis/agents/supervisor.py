"""
Supervisor:監督 Agent 群本身。

Consensus 負責「這些意見加起來是什麼結論」。
Supervisor 負責的是另一個問題:**這些意見本身可不可信?**

它能做的只有兩件事:

  1. **否決**一個已經成立的決議(把它降級成 WAIT)。
  2. **回報** Agent 群的健康狀態。

它**不能**製造交易。一個監督者如果能把 WAIT 變成進場,那就不是監督者。
這條規則有測試釘住。

會被否決的情況:

  * 太多 Agent 出錯 —— 半個系統壞掉時的「共識」沒有意義。
  * 太多 Agent 棄權 —— 沒人看得懂這個盤,那就不要碰。
  * 意見完全一致到不正常 —— 十二個獨立 Agent 從不完全同意,
    如果它們完全同意,通常代表它們其實在看同一個東西(或全都壞了)。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from agmcis.agents.base import Vote
from agmcis.core.enums import Direction

logger = logging.getLogger("agmcis.agents.supervisor")

# 出錯的 Agent 超過這個比例就否決
MAX_ERROR_RATIO = 0.25

# 棄權的 Agent 超過這個比例就否決
MAX_ABSTAIN_RATIO = 0.75

# 連續這麼多次給出完全相同的投票,就標記這個 Agent 可能卡住了
STUCK_THRESHOLD = 20


@dataclass
class SupervisorReport:
    total_agents: int = 0
    errored: List[str] = field(default_factory=list)
    abstained: List[str] = field(default_factory=list)
    vetoed: bool = False
    veto_reasons: List[str] = field(default_factory=list)
    health_warnings: List[str] = field(default_factory=list)

    @property
    def error_ratio(self):
        return len(self.errored) / self.total_agents if self.total_agents else 0.0

    @property
    def abstain_ratio(self):
        return len(self.abstained) / self.total_agents if self.total_agents else 0.0

    def to_dict(self):
        return {
            "total_agents": self.total_agents,
            "errored": list(self.errored),
            "abstained": list(self.abstained),
            "error_ratio": round(self.error_ratio, 4),
            "abstain_ratio": round(self.abstain_ratio, 4),
            "vetoed": self.vetoed,
            "veto_reasons": list(self.veto_reasons),
            "health_warnings": list(self.health_warnings),
        }


class Supervisor:
    """
    跨次數的 Agent 健康追蹤。

    單次呼叫也可以用(health 統計就只有這一次的資料)。
    """

    def __init__(self):
        self._vote_history: Dict[str, List[str]] = {}

    def review(self, deliberation):
        """
        檢查一次決議。**只會把 actionable 變成不 actionable,不會反過來。**
        回傳 SupervisorReport,並在否決時就地修改 deliberation。
        """
        opinions = deliberation.opinions
        report = SupervisorReport(total_agents=len(opinions))

        report.errored = [o.agent for o in opinions if o.error]
        report.abstained = [
            o.agent for o in opinions
            if o.error is None and o.vote is Vote.ABSTAIN
        ]

        self._record(opinions)
        report.health_warnings.extend(self._stuck_agents())

        if report.total_agents == 0:
            report.veto_reasons.append("沒有任何 Agent 參與")
        else:
            if report.error_ratio > MAX_ERROR_RATIO:
                report.veto_reasons.append(
                    f"{len(report.errored)}/{report.total_agents} 個 Agent 出錯"
                    f"({', '.join(report.errored)}),系統狀態不可信"
                )

            if report.abstain_ratio > MAX_ABSTAIN_RATIO:
                report.veto_reasons.append(
                    f"{len(report.abstained)}/{report.total_agents} 個 Agent 棄權,"
                    f"沒有足夠的判斷依據"
                )

            if self._suspiciously_unanimous(opinions):
                report.veto_reasons.append(
                    "所有 Agent 意見完全一致 —— 獨立的 Agent 不會完全同意,"
                    "這通常代表它們在看同一個東西或全部壞掉"
                )

        # 沒有決議可以否決時,只回報,不動作
        if not report.veto_reasons or not deliberation.is_actionable:
            report.vetoed = False
            return report

        report.vetoed = True
        logger.warning(
            "Supervisor 否決 %s 的決議 | %s",
            deliberation.symbol, "; ".join(report.veto_reasons),
        )

        deliberation.intent = None
        deliberation.direction = Direction.WAIT
        deliberation.blocked_reason = "Supervisor 否決:" + "; ".join(
            report.veto_reasons
        )
        return report

    # ---- 健康追蹤 ----

    def _record(self, opinions):
        for opinion in opinions:
            history = self._vote_history.setdefault(opinion.agent, [])
            history.append(opinion.vote.value)
            if len(history) > STUCK_THRESHOLD * 2:
                del history[:-STUCK_THRESHOLD]

    def _stuck_agents(self):
        warnings = []
        for agent, history in self._vote_history.items():
            if len(history) < STUCK_THRESHOLD:
                continue
            recent = history[-STUCK_THRESHOLD:]
            if len(set(recent)) == 1:
                warnings.append(
                    f"{agent} 連續 {STUCK_THRESHOLD} 次都投 {recent[0]},"
                    f"可能已經卡住或邏輯失效"
                )
        return warnings

    @staticmethod
    def _suspiciously_unanimous(opinions):
        """
        所有**有發言**的 Agent 都投同一個方向,而且發言的人夠多。

        少數幾個 Agent 同意很正常;十個以上完全一致才可疑。
        """
        voting = [
            o for o in opinions
            if o.error is None and o.vote is not Vote.ABSTAIN
        ]
        if len(voting) < 10:
            return False

        return len({o.vote for o in voting}) == 1
