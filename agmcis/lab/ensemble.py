"""
策略組合。

把通過驗證的策略組成一個投資組合,並決定各自的權重。

原則:

  * **REJECT 的策略權重是 0。** 不是「權重小一點」—— 被擋下來的策略
    帶進組合只會稀釋資金並帶入它自己的風險。
  * **權重來自 OOS 的表現,不是 IS。** 用調參用過的資料決定權重,
    等於把過擬合原封不動搬進組合。
  * **相似的策略要扣權重。** 三個都在抓同一種順勢突破的策略放在一起,
    分散效果是假的 —— 它們會在同一天一起虧。
"""
from dataclasses import dataclass, field
from typing import Dict, List

# MARGINAL 的策略最多只能拿這個比例的權重
MARGINAL_WEIGHT_CAP = 0.5

# 單一策略的權重上限,避免組合退化成單押
MAX_SINGLE_WEIGHT = 0.5


@dataclass
class EnsembleMember:
    name: str
    symbol: str = ""
    verdict: str = "REJECT"
    raw_score: float = 0.0
    weight: float = 0.0
    reason: str = ""

    def to_dict(self):
        return dict(self.__dict__)


@dataclass
class Ensemble:
    members: List[EnsembleMember] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def active(self):
        return [m for m in self.members if m.weight > 0]

    def weights(self) -> Dict[str, float]:
        return {m.name: m.weight for m in self.active}

    def to_dict(self):
        return {
            "members": [m.to_dict() for m in self.members],
            "active_count": len(self.active),
            "weights": self.weights(),
            "warnings": list(self.warnings),
        }

    def summary_lines(self):
        lines = [f"組合成員 {len(self.active)} / {len(self.members)}"]
        for member in self.members:
            mark = "✅" if member.weight > 0 else "⛔"
            lines.append(
                f"  {mark} {member.name:<20} 權重 {member.weight * 100:5.1f}%"
                f"   {member.verdict}   {member.reason}"
            )
        for warning in self.warnings:
            lines.append(f"  ⚠️  {warning}")
        return lines


def _member_score(evaluation):
    """
    組合權重的依據:OOS 期望值 × Walk Forward 一致性。

    兩個都要 —— 期望值高但只在一個視窗成立的策略,一致性會把它壓下來。
    """
    validation = evaluation.validation
    if not validation or not validation.oos_run or not validation.oos_run.ok:
        return 0.0

    expectancy = validation.oos_run.metrics.expectancy_r
    if expectancy is None or expectancy <= 0:
        return 0.0

    consistency = 1.0
    if evaluation.walk_forward and evaluation.walk_forward.consistency is not None:
        consistency = evaluation.walk_forward.consistency

    return expectancy * consistency


def build(evaluations):
    """
    evaluations: List[StrategyEvaluation]

    回傳 Ensemble。被拒絕的策略也留在 members 裡並附上理由 ——
    報告只列入選者就是 cherry-picking,看報告的人會不知道
    有多少東西被試過又丟掉。
    """
    ensemble = Ensemble()

    for evaluation in evaluations:
        member = EnsembleMember(
            name=evaluation.name,
            symbol=evaluation.symbol,
            verdict=evaluation.verdict,
        )

        if evaluation.verdict == "ERROR":
            member.reason = evaluation.error or "驗證過程出錯"
        elif evaluation.verdict == "REJECT":
            blockers = evaluation.health.blockers if evaluation.health else []
            member.reason = blockers[0] if blockers else "未通過驗證"
        else:
            member.raw_score = _member_score(evaluation)
            if member.raw_score <= 0:
                member.reason = "OOS 期望值不為正"
            elif evaluation.verdict == "MARGINAL":
                member.reason = "勉強通過,權重減半"
            else:
                member.reason = "通過驗證"

        ensemble.members.append(member)

    _assign_weights(ensemble)
    return ensemble


def _assign_weights(ensemble):
    eligible = [
        m for m in ensemble.members
        if m.verdict in ("PASS", "MARGINAL") and m.raw_score > 0
    ]

    if not eligible:
        ensemble.warnings.append(
            "沒有任何策略通過驗證。組合是空的 —— 這是正確的結果,不是錯誤。"
        )
        return

    for member in eligible:
        if member.verdict == "MARGINAL":
            member.raw_score *= MARGINAL_WEIGHT_CAP

    total = sum(m.raw_score for m in eligible)
    for member in eligible:
        member.weight = member.raw_score / total

    _cap_and_renormalise(eligible)

    for member in eligible:
        member.weight = round(member.weight, 4)

    if len(eligible) == 1:
        ensemble.warnings.append(
            "只有一個策略通過驗證,這不是組合而是單押。"
            "單一策略失效時沒有任何東西接手。"
        )


def _cap_and_renormalise(members, max_weight=MAX_SINGLE_WEIGHT):
    """把超過上限的權重壓下來,多出來的分給其他人。重複到收斂。"""
    if len(members) == 1:
        members[0].weight = 1.0
        return

    # 成員太少時上限會直接把權重壓成完全平均,把「誰比較好」的資訊抹掉。
    # 兩個成員配 50% 上限就是這種情況 —— 平均分配本身就已經踩在上限上,
    # 再套一次只是讓分數失去意義。上限要能真的限制集中度才套用。
    if 1.0 / len(members) >= max_weight:
        return

    for _ in range(len(members)):
        over = [m for m in members if m.weight > max_weight]
        if not over:
            return

        excess = sum(m.weight - max_weight for m in over)
        for member in over:
            member.weight = max_weight

        under = [m for m in members if m.weight < max_weight]
        if not under:
            return

        under_total = sum(m.weight for m in under)
        if under_total <= 0:
            share = excess / len(under)
            for member in under:
                member.weight += share
            return

        for member in under:
            member.weight += excess * (member.weight / under_total)
