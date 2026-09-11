"""
自我檢討報告。

把歸因與 Agent 貢獻度整理成一份**可以直接看的結論**,
而且有一個明確的設計目標:

    **這份報告要能說出「我不知道」和「這裡在虧錢」。**

一份只會說好消息的檢討報告比沒有檢討報告更糟 ——
它會讓人對一個沒有優勢的系統產生信心。

所以判定只有四種,而且「還不知道」是完全合法的結論:

    HEALTHY       有正期望值,而且樣本夠
    NOT_ENOUGH_DATA  樣本不夠,不下結論
    FRAGILE       有正期望值,但依賴少數幾筆極端獲利
    LOSING        期望值為負
"""
from dataclasses import dataclass, field
from typing import Dict, List

from agmcis.review import agent_scorecard, attribution

HEALTHY = "HEALTHY"
NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"
FRAGILE = "FRAGILE"
LOSING = "LOSING"


@dataclass
class SelfReview:
    verdict: str = NOT_ENOUGH_DATA
    headline: str = ""
    total_trades: int = 0
    expectancy: float = 0.0
    findings: List[str] = field(default_factory=list)
    questions_we_cannot_answer: List[str] = field(default_factory=list)
    attribution: Dict = field(default_factory=dict)
    agents: Dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "verdict": self.verdict,
            "headline": self.headline,
            "total_trades": self.total_trades,
            "expectancy": self.expectancy,
            "findings": list(self.findings),
            "questions_we_cannot_answer": list(self.questions_we_cannot_answer),
            "attribution": dict(self.attribution),
            "agents": dict(self.agents),
        }

    def summary_lines(self):
        lines = [
            f"判定     {self.verdict}",
            f"總結     {self.headline}",
            f"已平倉   {self.total_trades} 筆   期望值 {self.expectancy:+.4f} USDT/筆",
        ]
        if self.findings:
            lines.append("發現:")
            lines.extend(f"  • {f}" for f in self.findings)
        if self.questions_we_cannot_answer:
            lines.append("目前答不出來的問題:")
            lines.extend(f"  ? {q}" for q in self.questions_we_cannot_answer)
        return lines


def build(trades=None):
    if trades is None:
        from database_service import get_trades
        trades = get_trades()

    attribution_report = attribution.build(trades)
    scorecard = agent_scorecard.build(trades)

    review = SelfReview(
        total_trades=attribution_report.total_trades,
        attribution=attribution_report.to_dict(),
        agents=scorecard.to_dict(),
    )

    check = attribution_report.concentration
    review.expectancy = check.expectancy if check else 0.0

    review.verdict, review.headline = _verdict(attribution_report, check)
    review.findings = _findings(attribution_report, scorecard)
    review.questions_we_cannot_answer = _open_questions(
        attribution_report, scorecard,
    )
    return review


def _verdict(report, check):
    if report.total_trades < attribution.MIN_SAMPLE:
        return NOT_ENOUGH_DATA, (
            f"只有 {report.total_trades} 筆已平倉交易(門檻 "
            f"{attribution.MIN_SAMPLE})。**還不知道這套系統有沒有優勢。**"
        )

    if check is None or check.expectancy <= 0:
        return LOSING, (
            f"期望值 {check.expectancy if check else 0:+.4f} USDT/筆,"
            f"長期在虧。不要加大倉位,先找出是哪個分群在拖。"
        )

    if check.depends_on_outliers:
        return FRAGILE, (
            f"期望值為正,但扣掉最好的三筆就變成 "
            f"{check.expectancy_without_top3:+.4f} —— "
            f"績效依賴少數幾筆極端獲利,那不是優勢。"
        )

    decay = report.trend
    if decay and decay.declining:
        # 整體期望值還是正的,但最近一個月掉得很明顯。
        # 那跟「穩定獲利」是兩件事,判定不該是 HEALTHY。
        return FRAGILE, (
            f"整體期望值為正,但最近一個月只有 {decay.recent_expectancy:+.4f},"
            f"先前是 {decay.earlier_expectancy:+.4f} —— 績效正在衰退。"
        )

    return HEALTHY, (
        f"{report.total_trades} 筆交易,期望值 {check.expectancy:+.4f} USDT/筆,"
        f"而且不依賴少數幾筆極端獲利。"
    )


def _findings(report, scorecard):
    findings = []

    # 歸因層級的警告本來就是「發現」
    findings.extend(report.warnings)

    for agent in scorecard.agents:
        if agent["verdict"] == "CONTRIBUTING":
            findings.append(
                f"{agent['agent']} 有正貢獻:同意時期望值 "
                f"{agent['agreed_expectancy']:+.4f},"
                f"反對時 {agent['disagreed_expectancy']:+.4f}。"
            )

    findings.extend(scorecard.warnings)

    # 找出最會賺與最會虧的分群(只看樣本夠的)
    for dimension, buckets in report.buckets.items():
        reliable = [b for b in buckets if b["reliable"]]
        if len(reliable) < 2:
            continue

        best, worst = reliable[0], reliable[-1]
        if best["expectancy"] > 0 >= worst["expectancy"]:
            findings.append(
                f"{dimension}:{best['key']} 的期望值 {best['expectancy']:+.4f},"
                f"{worst['key']} 是 {worst['expectancy']:+.4f}。"
                f"這兩個分群的差別值得看。"
            )

    return findings


def _open_questions(report, scorecard):
    """
    明確列出「目前答不出來」的問題。

    這一段跟「發現」一樣重要。看報告的人需要知道哪些結論還沒有依據,
    否則他會把沉默當成「沒問題」。
    """
    questions = []

    if report.total_trades < attribution.MIN_SAMPLE:
        questions.append(
            f"這套系統有沒有優勢?需要至少 {attribution.MIN_SAMPLE} 筆已平倉交易,"
            f"目前 {report.total_trades} 筆。"
        )

    unknown = [a["agent"] for a in scorecard.agents if a["verdict"] == "UNKNOWN"]
    if unknown:
        questions.append(
            f"這些 Agent 有沒有貢獻:{', '.join(unknown)}。"
            f"同意與反對兩邊都要有 {agent_scorecard.MIN_VOTES} 筆才算得出來。"
        )

    for dimension, buckets in report.buckets.items():
        thin = [b["key"] for b in buckets if not b["reliable"]]
        if thin:
            questions.append(
                f"{dimension} 這幾個分群樣本不足,不能下結論:"
                f"{', '.join(thin[:6])}"
                + ("…" if len(thin) > 6 else "")
            )

    if not questions:
        questions.append("目前沒有明顯的未知項。這不代表結論是對的,只代表樣本夠了。")

    return questions


def run_self_review(trades=None):
    """排程器與 API 的入口。"""
    return build(trades).to_dict()
