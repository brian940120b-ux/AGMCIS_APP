"""
Trading Panel(Master Prompt 第八十七節)。

第八十七節要求開倉前看得到:Symbol、Market、LONG/SHORT、Entry、SL、TP、
Leverage、Position Size、Risk、R:R、Confidence、Score。

那十二個數字目前散在四個地方 —— Agent 共識給方向與進場,Risk Engine
給槓桿與倉位,intent 給 R:R,report 給分數。要在下單前看到它們,
以前只能翻 log。

## 這個面板**不會下單**

它是一個預覽:走與 auto_trader 完全相同的鏈,但在 Execution Engine
之前停下來。

    Agent 共識 → Supervisor → Risk Engine → 【停在這裡】→ Execution

刻意不做「送出」按鈕。理由與第九十二節不做 LIVE 網頁開關相同:
一個會下單的網頁按鈕沒辦法表達「這組數字是三十秒前算的」,
而價格在那三十秒裡會動。真的要下單,走自動交易或腳本。

## 它也**不寫決策紀錄**

預覽不是決策。把每一次開面板都寫進 ai_decisions,那張表會被
「使用者看了一眼但系統從沒打算做」的紀錄灌滿,而第七十節那張表
的用途正是事後回答「系統當時打算做什麼」。看一眼不是打算。

## 為什麼重跑而不是讀快取

因為顯示一組過期的 Entry / SL 比不顯示更危險 —— 使用者會照著那個
價格去手動下單。每一次請求都重跑,而且回傳 `as_of` 時間戳。
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from agmcis.config import settings

router = APIRouter()
logger = logging.getLogger("agmcis.api.trade_panel")


@router.get("/api/trade_plan")
def api_trade_plan(symbol: str = Query(...)):
    return build_plan(symbol)


@router.get("/api/trade_plans")
def api_trade_plans(limit: int = Query(3, ge=1, le=10)):
    """
    監控清單前幾檔的面板。**含被擋下的** ——
    只列可以開的會讓「沒有機會」與「有機會但被風控擋住」看起來一樣,
    而後者是使用者最需要知道的那一種。
    """
    symbols = list(settings.WATCHLIST_SYMBOLS)[:limit]
    return {
        "as_of": _now(),
        "plans": [build_plan(s) for s in symbols],
    }


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _empty(symbol, stage, reason, **extra):
    """
    走不到最後的面板。

    **每一格都給 None,不給 0。** 0 在這張表上讀起來像一個真的數字 ——
    「Position Size 0」看起來是系統算出來的結論,而實際上是沒算。
    """
    payload = {
        "symbol": symbol,
        "market": None,
        "direction": None,
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "leverage": None,
        "position_size_usdt": None,
        "notional_usdt": None,
        "risk_usdt": None,
        "risk_pct_of_equity": None,
        "risk_reward": None,
        "confidence": None,
        "score": None,
        "tradable": False,
        "stage": stage,
        "reason": reason,
        "blockers": [],
        "warnings": [],
        "votes": {},
        "why": [],
        "supervisor": None,
        "as_of": _now(),
    }
    payload.update(extra)
    return payload


def build_plan(symbol):
    """
    一檔的完整開倉預覽。

    走 auto_trader 用的**同一組函式** —— `_agent_intent` 與
    `evaluate_intent`。兩條會算出不同數字的路徑,遲早會有人拿其中一條
    的數字去解釋另一條的行為。
    """
    try:
        return _build(symbol)
    except Exception as exc:
        logger.exception("交易面板失敗 | %s", symbol)
        return _empty(
            symbol, "ERROR", f"{type(exc).__name__}: {exc}",
        )


def _build(symbol):
    from auto_trader import _agent_intent
    from risk_control import evaluate_intent

    intent, problem, deliberation, report = _agent_intent(symbol)

    votes = dict(deliberation.votes) if deliberation is not None else {}

    if intent is None:
        # Agent 沒有共識。這不是錯誤 —— 第二十九節說觀望是合法結論。
        return _empty(
            symbol, "NO_INTENT", problem,
            votes=votes,
            why=_why(deliberation),
            supervisor=report.to_dict() if report is not None else None,
        )

    supervisor = report.to_dict() if report is not None else None

    # ---------- Risk Engine:同一個 hard gate,不是另一套算式 ----------
    decision = evaluate_intent(intent)

    base = {
        "symbol": intent.symbol,
        "market": intent.market_type.value,
        "direction": intent.direction.value,
        "entry": intent.entry,
        "stop_loss": intent.stop_loss,
        "take_profit": intent.take_profit,
        "stop_distance_pct": round(intent.stop_distance_pct, 4),
        "risk_reward": intent.risk_reward,
        "confidence": intent.confidence,
        # Agent 共識管線**不產生分數** —— 第八十七節列了 Score 這一格,
        # 而這條路徑填不出它。填 None 而不是填 confidence:
        # 拿信心冒充分數,會讓一個沒有分數的系統看起來有兩個獨立指標。
        "score": intent.score,
        "strategy": intent.strategy,
        "timeframe": intent.timeframe,
        "regime": intent.market_regime,
        "votes": votes,
        "why": _why(deliberation),
        # Supervisor 的結論。第六十三節的流程圖要顯示它有沒有否決,
        # 而「沒有否決」與「沒有跑到」在畫面上必須不一樣。
        "supervisor": supervisor,
        "as_of": _now(),
    }

    if not decision.approved:
        # 被擋下來也要把 Entry / SL / TP 顯示出來 —— 那是 Agent 的結論,
        # 它成立與否跟風控放不放行是兩件事。只有倉位相關的數字沒有意義。
        base.update({
            "leverage": None,
            "position_size_usdt": None,
            "notional_usdt": None,
            "risk_usdt": None,
            "risk_pct_of_equity": None,
            "tradable": False,
            "stage": "REJECTED_BY_RISK",
            "reason": decision.reason,
            "blockers": list(decision.blockers),
            "warnings": list(decision.warnings),
        })
        return base

    base.update({
        "leverage": decision.leverage,
        "position_size_usdt": decision.size_usdt,
        "notional_usdt": decision.notional,
        "risk_usdt": (
            round(decision.risk_usdt, 4) if decision.risk_usdt is not None else None
        ),
        "risk_pct_of_equity": _risk_pct(decision),
        "tradable": True,
        "stage": "APPROVED",
        "reason": decision.reason,
        "blockers": [],
        "warnings": list(decision.warnings),
        # 這是預覽,不是已下的單。前端必須看得出差別。
        "submitted": False,
    })
    return base


def _risk_pct(decision):
    """
    這筆風險佔權益的百分比。

    帳戶查不到就回 None —— 回 0 會讓一筆風險看起來是零。
    """
    if decision.risk_usdt is None:
        return None

    try:
        from agmcis.risk.account_state import build_account_state
        equity = float(build_account_state().equity or 0)
    except Exception as exc:
        logger.warning("交易面板 | 權益讀取失敗 | %s", exc)
        return None

    if equity <= 0:
        return None

    return round(decision.risk_usdt / equity * 100, 4)


# ---------------- Agent Interaction(第六十三節)----------------
#
# 第六十三節要求 UI 讓使用者「感覺多個 AI Agent 正在協同工作」,
# 並列了 animated nodes / glowing connections / signal pulses 等做法,
# 但同一節也寫著「UI Animation 不得影響交易核心」。
#
# 這裡做的是**那條鏈的真實狀態**,不是動畫:每一關到了沒有、
# 過了沒有、為什麼沒過。一個會動但顯示假流程的圖,比一張靜態
# 但正確的圖糟得多 —— 前者會讓人相信一個沒發生的推理過程。
#
# 關卡名稱用系統裡真的存在的那些,不是第六十三節的示意名稱。
# 把 Quant Agent 畫進流程圖但系統裡沒有這個東西,是在編造。

STAGE_ORDER = ("agents", "consensus", "supervisor", "risk", "execution")

PASSED = "PASSED"
BLOCKED = "BLOCKED"
ERROR = "ERROR"
NOT_REACHED = "NOT_REACHED"
NOT_RUN = "NOT_RUN"


@router.get("/api/agent_flow")
def api_agent_flow(symbol: str = Query(...)):
    return build_flow(symbol)


def build_flow(symbol):
    """
    一檔標的走過整條鏈的樣子(第六十三節)。

    走的是**同一個 build_plan** —— 流程圖顯示的必須是真的跑過的那一輪,
    不是另外跑一次。兩次跑出來的結果可以不同,而一張跟決策對不起來的
    流程圖沒有用。
    """
    plan = build_plan(symbol)
    return {
        "symbol": symbol,
        "as_of": plan.get("as_of"),
        "stage": plan.get("stage"),
        "stages": _stages(plan),
        "plan": plan,
    }


def _stages(plan):
    stage = plan.get("stage")
    votes = plan.get("votes") or {}
    supervisor = plan.get("supervisor") or {}

    tally = {}
    for vote in votes.values():
        tally[vote] = tally.get(vote, 0) + 1

    # Agent 層:只要有票就是跑過了。全部錯誤才算這一關壞掉。
    errored = list(supervisor.get("errored") or [])
    if stage == "ERROR" and not votes:
        agents = _stage("agents", ERROR, plan.get("reason"))
    elif votes and len(errored) == len(votes):
        agents = _stage("agents", ERROR, "所有 Agent 都失敗")
    else:
        agents = _stage(
            "agents", PASSED if votes else NOT_REACHED,
            f"{len(votes)} 個 Agent 出意見",
            tally=tally, errored=errored,
        )

    reached = agents["status"] == PASSED

    # 共識層:有 intent 代表過了;沒有 intent 而且有票代表這一關擋下來。
    # **棄權不是反對** —— 全體棄權時共識層不算「否決」,算「沒有意見」。
    if not reached:
        consensus = _stage("consensus", NOT_REACHED, None)
    elif stage == "NO_INTENT":
        consensus = _stage("consensus", BLOCKED, plan.get("reason"))
    else:
        consensus = _stage(
            "consensus", PASSED,
            f"{plan.get('direction')} 信心 {plan.get('confidence')}",
        )

    # 監督者:它只會把 actionable 變成不 actionable,不會反過來。
    # 所以它「通過」的意思是「沒有否決」,不是「它同意」。
    if not reached:
        sup = _stage("supervisor", NOT_REACHED, None)
    elif supervisor.get("vetoed"):
        sup = _stage(
            "supervisor", BLOCKED,
            "; ".join(supervisor.get("veto_reasons") or []) or "已否決",
        )
    else:
        sup = _stage(
            "supervisor",
            PASSED if consensus["status"] == PASSED else NOT_REACHED,
            "沒有否決",
            health_warnings=list(supervisor.get("health_warnings") or []),
        )

    if consensus["status"] != PASSED:
        risk = _stage("risk", NOT_REACHED, None)
    elif stage == "REJECTED_BY_RISK":
        risk = _stage(
            "risk", BLOCKED, plan.get("reason"),
            blockers=list(plan.get("blockers") or []),
        )
    elif stage == "APPROVED":
        risk = _stage(
            "risk", PASSED,
            f"槓桿 {plan.get('leverage')}x 保證金 {plan.get('position_size_usdt')} USDT",
            warnings=list(plan.get("warnings") or []),
        )
    else:
        risk = _stage("risk", ERROR, plan.get("reason"))

    # 執行層在預覽裡**永遠不跑**。它顯示 NOT_RUN 而不是 PASSED ——
    # 一張把最後一格畫成綠色的流程圖,會讓人以為單已經送出去了。
    execution = _stage(
        "execution", NOT_RUN,
        "預覽不下單" if risk["status"] == PASSED else None,
    )

    return [agents, consensus, sup, risk, execution]


STAGE_LABELS = {
    "agents": "AI Agents 各自出意見",
    "consensus": "Consensus 彙總成交易意圖",
    "supervisor": "Supervisor 檢查 Agent 群可不可信",
    "risk": "Risk Engine(hard gate)",
    "execution": "Execution Engine 送單",
}


def _stage(name, status, detail, **extra):
    payload = {
        "stage": name,
        "label": STAGE_LABELS[name],
        "status": status,
        "detail": detail,
    }
    payload.update(extra)
    return payload


def _why(deliberation):
    """每個 Agent 的理由。點進面板要看得到,不是只有一句結論。"""
    if deliberation is None:
        return []

    return [
        {
            "agent": o.agent,
            "vote": o.vote.value,
            "confidence": round(o.confidence, 1),
            "reasons": list(o.reasons)[:3],
            "error": o.error,
        }
        for o in deliberation.opinions
    ]
