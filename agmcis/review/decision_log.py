"""
決策紀錄(Master Prompt 第六十九 / 七十 / 七十一節)。

在這一層出現以前,系統可以回答「我現在為什麼要開這一單」,
但答不出「三週前那一單為什麼開」。決策只寫在 log 與 API 回應裡,
而 log 會輪替。

第七十一節要的可解釋性不是「當下說得出來」,是**事後查得到**。
一個只在決策當下存在的理由,對檢討沒有任何用處 —— 而檢討是
唯一能讓系統變好的東西。

## 三個設計決定

**一、沒下單的決策也要寫。**
「為什麼沒開」跟「為什麼開」一樣重要,而且發生得頻繁得多。
系統連續三天沒有交易的時候,唯一能回答「它是壞了還是在等」的
就是這批紀錄。

**二、Agent 意見與分數細項存 JSONB,不攤平成欄位。**
這些結構會隨著 Agent 增減而改變。攤平之後,加一個 Agent 就要改 schema,
而且舊紀錄會被新的 schema 重新詮釋 —— 歷史紀錄必須保留當時的形狀。

**三、寫紀錄失敗不能擋住交易。**
這一層是觀測,不是控制。資料庫掛掉的時候,「因為寫不了紀錄所以不能下單」
是錯誤的取捨方向 —— 風控該擋的東西風控會擋,這裡不是第二道閘門。
但失敗必須大聲喊,因為一個安靜失效的紀錄層會讓人以為「沒有紀錄 =
沒有發生過」。
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("agmcis.review.decision_log")


# 決策結果
OPENED = "OPENED"
REJECTED_BY_RISK = "REJECTED_BY_RISK"
REJECTED_BY_RULES = "REJECTED_BY_RULES"
WAIT = "WAIT"
BLOCKED = "BLOCKED"
FAILED = "FAILED"


def new_decision_id(symbol):
    """
    可讀又唯一。前綴是時間,所以按字串排序就是按時間排序 ——
    查 log 的時候這件事比想像中重要。
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    tag = str(uuid.uuid4())[:8]
    return f"{stamp}-{str(symbol).replace('/', '').replace(':', '')}-{tag}"


@dataclass
class Decision:
    symbol: str
    outcome: str
    decision_id: str = ""
    market_type: Optional[str] = None
    direction: Optional[str] = None
    score: Optional[float] = None
    confidence: Optional[float] = None
    market_regime: Optional[str] = None
    volatility: Optional[str] = None
    reason: Optional[str] = None
    agent_votes: Optional[dict] = None
    strategy_verdicts: Optional[list] = None
    score_breakdown: Optional[dict] = None
    risk_decision: Optional[dict] = None
    news_risk: Optional[dict] = None
    trade_id: Optional[int] = None

    def __post_init__(self):
        if not self.decision_id:
            self.decision_id = new_decision_id(self.symbol)

    def to_dict(self):
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "direction": self.direction,
            "score": self.score,
            "confidence": self.confidence,
            "market_regime": self.market_regime,
            "volatility": self.volatility,
            "outcome": self.outcome,
            "reason": self.reason,
            "agent_votes": self.agent_votes,
            "strategy_verdicts": self.strategy_verdicts,
            "score_breakdown": self.score_breakdown,
            "risk_decision": self.risk_decision,
            "news_risk": self.news_risk,
            "trade_id": self.trade_id,
        }


def from_deliberation(deliberation, report=None, outcome=None, decision=None,
                      news=None, reason=None, trade_id=None):
    """
    從 Agent 審議與 Supervisor 報告組一筆決策紀錄。

    `decision` 是 RiskDecision;`report` 是 SupervisorReport。
    兩個都可以是 None —— 在管線更早的地方就被擋下時本來就沒有它們,
    而那種情況同樣要留紀錄。
    """
    intent = getattr(deliberation, "intent", None)
    payload = report.to_dict() if hasattr(report, "to_dict") else (report or {})

    risk_payload = None
    if decision is not None:
        risk_payload = (
            decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
        )

    direction = getattr(deliberation, "direction", None)

    return Decision(
        symbol=getattr(deliberation, "symbol", None) or payload.get("symbol"),
        outcome=outcome or (OPENED if intent is not None else WAIT),
        market_type=(
            getattr(intent.market_type, "value", intent.market_type)
            if intent is not None else None
        ),
        direction=getattr(direction, "value", direction),
        score=getattr(intent, "score", None) or payload.get("score"),
        confidence=(
            getattr(intent, "confidence", None)
            if intent is not None else getattr(deliberation, "confidence", None)
        ),
        market_regime=payload.get("regime") or getattr(intent, "market_regime", None),
        volatility=payload.get("volatility"),
        reason=reason or getattr(deliberation, "blocked_reason", None),
        agent_votes=dict(getattr(deliberation, "votes", {}) or {}),
        strategy_verdicts=payload.get("verdicts") or payload.get("strategy_verdicts"),
        score_breakdown=payload.get("score_breakdown") or payload.get("breakdown"),
        risk_decision=risk_payload,
        news_risk=news.to_dict() if hasattr(news, "to_dict") else news,
        trade_id=trade_id,
    )


# ---------------- 寫入 ----------------

def record(decision, writer=None):
    """
    寫一筆決策紀錄。**永遠不會拋例外。**

    回傳寫進去的 decision_id,失敗時回 None。呼叫端不需要處理失敗 ——
    這一層是觀測不是控制。但失敗會用 ERROR 記錄,因為一個安靜失效的
    紀錄層會讓人以為「沒有紀錄 = 沒有發生過」。
    """
    try:
        if writer is None:
            from database_service import insert_ai_decision as writer
        writer(decision.to_dict())
        return decision.decision_id
    except Exception:
        logger.exception(
            "決策紀錄寫入失敗 | %s | %s | 這一次的決策無法在事後被查到",
            decision.symbol, decision.decision_id,
        )
        return None


def record_risk_event(event_type, symbol=None, severity="INFO", blockers=None,
                      detail=None, payload=None, writer=None):
    """風控事件。同樣永遠不拋例外。"""
    try:
        if writer is None:
            from database_service import insert_risk_event as writer
        writer({
            "event_type": event_type,
            "symbol": symbol,
            "severity": severity,
            "blockers": list(blockers or []),
            "detail": detail,
            "payload": payload,
        })
        return True
    except Exception:
        logger.exception("風控事件寫入失敗 | %s | %s", event_type, symbol)
        return False


def audit(action, actor="system", target=None, before=None, after=None,
          detail=None, source_ip=None, writer=None):
    """
    系統稽核(第六十五節)。

    actor 很多時候會是 'system' 或 'env' —— 設定來自環境變數,
    環境變數沒有作者。誠實地寫 'env' 比編一個假的使用者名稱好。
    """
    try:
        if writer is None:
            from database_service import insert_audit_log as writer
        writer({
            "action": action,
            "actor": actor,
            "target": target,
            "before_value": None if before is None else str(before),
            "after_value": None if after is None else str(after),
            "detail": detail,
            "source_ip": source_ip,
        })
        return True
    except Exception:
        logger.exception("稽核寫入失敗 | %s | %s", action, target)
        return False


def record_regime(symbol, regime, timeframe="1h", writer=None):
    """
    市況快照。

    只從交易推市況,沒有交易的那些時段就完全看不見了 ——
    而「系統在盤整時不交易」正是我們想確認的事情之一。
    """
    try:
        if writer is None:
            from database_service import insert_market_regime as writer
        writer({
            "symbol": symbol,
            "timeframe": timeframe,
            "regime": getattr(regime.regime, "value", regime.regime),
            "volatility": getattr(regime.volatility, "value", regime.volatility),
            "adx": getattr(regime, "adx", None),
            "atr_pct": getattr(regime, "atr_pct", None),
            "tradeable": bool(getattr(regime, "is_tradeable", False)),
        })
        return True
    except Exception:
        logger.exception("市況紀錄寫入失敗 | %s", symbol)
        return False


# ---------------- 查詢:為什麼開這一單 ----------------

@dataclass
class Explanation:
    found: bool = False
    symbol: Optional[str] = None
    decision_id: Optional[str] = None
    lines: List[str] = field(default_factory=list)
    raw: Optional[dict] = None

    def to_dict(self):
        return {
            "found": self.found,
            "symbol": self.symbol,
            "decision_id": self.decision_id,
            "lines": list(self.lines),
            "raw": self.raw,
        }

    def as_text(self):
        return "\n".join(self.lines)


def _vote_summary(votes):
    if not votes:
        return None

    if isinstance(votes, str):
        try:
            votes = json.loads(votes)
        except Exception:
            return votes

    if not isinstance(votes, dict):
        return str(votes)

    grouped = {}
    for agent, vote in votes.items():
        grouped.setdefault(str(vote), []).append(str(agent))

    return "; ".join(
        f"{vote}: {', '.join(sorted(agents))}"
        for vote, agents in sorted(grouped.items())
    )


def explain(row):
    """
    把一筆決策紀錄翻成人看得懂的解釋(第七十一節)。

    刻意**不隱藏缺漏**:沒有 Agent 投票就寫「沒有紀錄」,
    而不是把那一行省略掉。省略會讓讀的人以為那一項沒有意見,
    但實際上是我們沒存。
    """
    if not row:
        return Explanation(found=False, lines=["找不到這筆交易的決策紀錄。"])

    result = Explanation(
        found=True,
        symbol=row.get("symbol"),
        decision_id=row.get("decision_id"),
        raw=dict(row),
    )

    lines = result.lines
    lines.append(
        f"{row.get('symbol')} {row.get('direction') or '方向未記錄'} — "
        f"{row.get('outcome')}"
    )
    lines.append(f"時間:{row.get('created_at') or '未記錄'}")

    score = row.get("score")
    confidence = row.get("confidence")
    lines.append(
        f"分數 {score if score is not None else '未記錄'} / "
        f"信心 {confidence if confidence is not None else '未記錄'}"
    )

    lines.append(
        f"市況:{row.get('market_regime') or '未記錄'}"
        f"({row.get('volatility') or '波動度未記錄'})"
    )

    votes = _vote_summary(row.get("agent_votes"))
    lines.append(f"Agent 投票:{votes or '沒有紀錄'}")

    breakdown = row.get("score_breakdown")
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown)
        except Exception:
            breakdown = None

    if isinstance(breakdown, dict) and breakdown.get("components"):
        parts = ", ".join(
            f"{name} {value}" for name, value in breakdown["components"].items()
        )
        lines.append(f"分數組成:{parts}")
        if breakdown.get("missing"):
            lines.append(
                f"缺少的評分項:{', '.join(breakdown['missing'])}"
                f"(這些項目沒有資料,分母已扣除)"
            )
    else:
        lines.append("分數組成:沒有紀錄")

    risk = row.get("risk_decision")
    if isinstance(risk, str):
        try:
            risk = json.loads(risk)
        except Exception:
            risk = None

    if isinstance(risk, dict):
        if risk.get("approved"):
            lines.append(
                f"風控:通過,倉位 {risk.get('size_usdt')} USDT、"
                f"槓桿 {risk.get('leverage')}x"
            )
        else:
            lines.append(
                f"風控:拒絕 — {risk.get('reason') or ','.join(risk.get('blockers') or [])}"
            )
        for warning in risk.get("warnings") or []:
            lines.append(f"風控提醒:{warning}")
    else:
        lines.append("風控裁決:沒有紀錄")

    news = row.get("news_risk")
    if isinstance(news, str):
        try:
            news = json.loads(news)
        except Exception:
            news = None

    if isinstance(news, dict) and news.get("reasons"):
        lines.append(f"消息面:{' / '.join(news['reasons'])}")

    if row.get("reason"):
        lines.append(f"結論:{row['reason']}")

    return result


def explain_trade(trade_id, fetch=None):
    """「為什麼你開這一單?」"""
    try:
        if fetch is None:
            from database_service import get_decision_for_trade as fetch
        return explain(fetch(trade_id))
    except Exception as exc:
        logger.exception("決策查詢失敗 | trade_id=%s", trade_id)
        return Explanation(
            found=False,
            lines=[f"決策紀錄查詢失敗:{type(exc).__name__}: {exc}"],
        )
