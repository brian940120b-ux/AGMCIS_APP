"""
改進提案與研究循環(Master Prompt 第四十二 / 七十七 / 七十八節)。

第四十二節允許系統 Analyze → Learn → Propose Improvement,
但**不允許 AI 自動修改 Live Strategy**。第七十八節說得更死:

    AI 不得:自己修改 → 自己測試 → 自己批准 → 自己 Live

在這個模組出現以前,系統的自我檢討(`self_review.py`)會產出結論,
但那些結論沒有地方去 —— 它們是一段文字,不是一個可以追蹤到底
有沒有被驗證過的東西。

## 這個模組是那個「地方」

一個提案有狀態,而且狀態只能往前走:

    DRAFT -> BACKTESTED -> OOS_PASSED -> WALK_FORWARD_PASSED
          -> PAPER -> APPROVED -> APPLIED
                   \\
                    -> REJECTED(任何一步都可以到這裡)

**APPROVED 這一步只有人做得到。** `approve()` 需要一個 actor,
而且它會拒絕任何看起來像系統的名字。這不是禮貌性的檢查 ——
它是第七十八節在程式碼裡的樣子。

## 為什麼狀態不能跳

一個從 DRAFT 直接跳到 APPROVED 的提案,看起來與一個走完全部驗證的
提案一模一樣。而「看起來一樣」正是自我批准最危險的地方:
它不會留下任何可疑的痕跡。

所以每一步轉移都檢查前一步,而且每一步都要附證據 ——
「OOS 通過了」必須帶著 OOS 的數字,不能只是一個布林值。
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("agmcis.review.proposals")

PROPOSAL_FILE = "data/proposals.json"

# 狀態。順序就是必須經過的順序。
DRAFT = "DRAFT"
BACKTESTED = "BACKTESTED"
OOS_PASSED = "OOS_PASSED"
WALK_FORWARD_PASSED = "WALK_FORWARD_PASSED"
PAPER = "PAPER"
APPROVED = "APPROVED"
APPLIED = "APPLIED"
REJECTED = "REJECTED"

PIPELINE = (
    DRAFT, BACKTESTED, OOS_PASSED, WALK_FORWARD_PASSED,
    PAPER, APPROVED, APPLIED,
)

# 只有人能做的轉移。名字裡有這些字的 actor 一律被拒絕 ——
# 見模組開頭的第七十八節。
HUMAN_ONLY = frozenset({APPROVED})

_MACHINE_NAMES = frozenset({
    "system", "ai", "agent", "auto", "bot", "scheduler", "claude",
    "gpt", "llm", "自動", "系統", "machine", "cron", "drift_monitor",
    "self_review", "research_loop",
})


def _utcnow():
    return datetime.now(timezone.utc)


class NotAHuman(Exception):
    """
    第七十八節:AI 不得自己批准自己的提案。

    這是一個例外而不是一個回傳 False,因為呼叫端**不該**有處理
    「批准被拒絕」的分支 —— 如果程式碼裡有一條路徑會用系統的名義
    去批准,那條路徑本身就是錯的。
    """


def _reject_machines(actor):
    name = str(actor or "").strip().lower()

    if not name:
        raise NotAHuman("批准必須指名是誰批准的")

    if name in _MACHINE_NAMES or any(tag in name for tag in _MACHINE_NAMES):
        raise NotAHuman(
            f"「{actor}」看起來不是人。第七十八節:AI 不得自己修改、"
            f"自己測試、自己批准、自己 Live。批准必須由人執行。"
        )

    return str(actor).strip()


@dataclass
class Evidence:
    """
    一步驗證的結果。**數字要在,不能只有一個布林值。**

    「OOS 通過了」這句話沒有辦法事後檢查;「OOS 75 筆、PF 1.62、
    期望值 +0.23R」可以。
    """
    step: str
    passed: bool
    metrics: Dict = field(default_factory=dict)
    detail: str = ""
    recorded_at: str = field(default_factory=lambda: _utcnow().isoformat())

    def to_dict(self):
        return dict(self.__dict__)


@dataclass
class Proposal:
    """
    一個改進提案。

    `change` 是要改什麼(自由格式的 dict),`rationale` 是為什麼。
    這個模組**不執行**那個改動 —— 它只追蹤它走到哪一步了。
    """
    proposal_id: str = ""
    title: str = ""
    target: str = ""            # 要改的東西:策略名、參數名
    change: Dict = field(default_factory=dict)
    rationale: str = ""
    origin: str = "self_review"
    status: str = DRAFT
    evidence: List[Evidence] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)
    approved_by: Optional[str] = None
    created_at: str = field(default_factory=lambda: _utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: _utcnow().isoformat())

    def __post_init__(self):
        if not self.proposal_id:
            stamp = _utcnow().strftime("%Y%m%d")
            self.proposal_id = f"{stamp}-{str(uuid.uuid4())[:8]}"

    @property
    def stage_index(self):
        return PIPELINE.index(self.status) if self.status in PIPELINE else -1

    @property
    def is_live_ready(self):
        return self.status in (APPROVED, APPLIED)

    def to_dict(self):
        return {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "target": self.target,
            "change": dict(self.change),
            "rationale": self.rationale,
            "origin": self.origin,
            "status": self.status,
            "evidence": [e.to_dict() for e in self.evidence],
            "history": list(self.history),
            "approved_by": self.approved_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload):
        evidence = [Evidence(**e) for e in payload.get("evidence") or []]
        data = {k: v for k, v in payload.items() if k != "evidence"}
        return cls(evidence=evidence, **data)


def _advance(proposal, target, actor, evidence=None, detail=""):
    """
    往前走一步。**只能走到下一步。**

    一個從 DRAFT 直接跳到 APPROVED 的提案,看起來與一個走完全部驗證的
    提案一模一樣 —— 而「看起來一樣」正是自我批准最危險的地方。
    """
    if proposal.status == REJECTED:
        raise ValueError(f"{proposal.proposal_id} 已經被否決,不能再往前")

    if target not in PIPELINE:
        raise ValueError(f"未知的狀態:{target!r}")

    current = proposal.stage_index
    wanted = PIPELINE.index(target)

    if wanted != current + 1:
        raise ValueError(
            f"不能從 {proposal.status} 跳到 {target}。"
            f"必須是 {PIPELINE[current + 1]} —— 每一步都要有證據,"
            f"跳過的那幾步事後看起來與做過一模一樣。"
        )

    if target in HUMAN_ONLY:
        actor = _reject_machines(actor)

    if evidence is not None:
        if not evidence.passed:
            raise ValueError(
                f"{evidence.step} 沒有通過,不能推進到 {target}"
            )
        proposal.evidence.append(evidence)

    proposal.status = target
    proposal.updated_at = _utcnow().isoformat()
    proposal.history.append({
        "at": proposal.updated_at,
        "to": target,
        "actor": actor,
        "detail": detail,
    })

    logger.info(
        "Proposal | %s | -> %s | %s | %s",
        proposal.proposal_id, target, actor, detail,
    )
    return proposal


def record_backtest(proposal, metrics, actor="research_loop", detail=""):
    return _advance(proposal, BACKTESTED, actor, Evidence(
        step="backtest", passed=True, metrics=dict(metrics), detail=detail,
    ), detail)


def record_oos(proposal, metrics, actor="research_loop", detail=""):
    return _advance(proposal, OOS_PASSED, actor, Evidence(
        step="oos", passed=True, metrics=dict(metrics), detail=detail,
    ), detail)


def record_walk_forward(proposal, metrics, actor="research_loop", detail=""):
    return _advance(proposal, WALK_FORWARD_PASSED, actor, Evidence(
        step="walk_forward", passed=True, metrics=dict(metrics), detail=detail,
    ), detail)


def record_paper(proposal, metrics, actor="research_loop", detail=""):
    return _advance(proposal, PAPER, actor, Evidence(
        step="paper", passed=True, metrics=dict(metrics), detail=detail,
    ), detail)


def approve(proposal, actor, detail=""):
    """
    人工核可。**這是這整個模組存在的理由。**

    actor 必須是一個人的名字。看起來像系統的名字會拋 NotAHuman ——
    而那是一個例外而不是回傳 False,因為呼叫端不該有處理
    「批准被拒絕」的分支。
    """
    return _advance(proposal, APPROVED, actor, detail=detail or "人工核可")


def mark_applied(proposal, actor, detail=""):
    """
    已套用。**只有 APPROVED 的提案才走得到這裡。**

    這個模組不執行改動 —— 套用是人或另一支腳本做的,
    這裡只記錄它發生了。
    """
    return _advance(proposal, APPLIED, actor, detail=detail)


def reject(proposal, actor, reason, evidence=None):
    """
    否決。任何狀態都可以到這裡,而且是**終點** ——
    一個被否決的提案不能被重新推進,要重來就開一個新的。

    重新推進舊提案會讓 history 變成一團看不懂的東西,
    而 history 是這整套機制唯一的稽核來源。
    """
    if evidence is not None:
        proposal.evidence.append(evidence)

    proposal.status = REJECTED
    proposal.updated_at = _utcnow().isoformat()
    proposal.history.append({
        "at": proposal.updated_at, "to": REJECTED,
        "actor": actor, "detail": reason,
    })

    logger.info("Proposal | %s | REJECTED | %s", proposal.proposal_id, reason)
    return proposal


# ---------------- 儲存 ----------------

class ProposalStore:
    """
    純檔案。與策略狀態同樣的理由:資料庫掛掉的時候,
    「所有提案看起來都已核可」是錯誤的方向,而讀不到檔案時
    回空清單代表「沒有已核可的提案」—— 那是安全的那一邊。
    """

    def __init__(self, path=None):
        self.path = Path(path or PROPOSAL_FILE)

    def load(self):
        if not self.path.exists():
            return []

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("提案檔讀取失敗 | %s | 視為沒有提案", self.path)
            return []

        if not isinstance(payload, list):
            logger.error("提案檔格式不對(不是陣列)| %s", self.path)
            return []

        proposals = []
        for item in payload:
            try:
                proposals.append(Proposal.from_dict(item))
            except Exception:
                logger.exception("提案解析失敗,略過這一筆")
        return proposals

    def save_all(self, proposals):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                [p.to_dict() for p in proposals],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        return len(proposals)

    def upsert(self, proposal):
        proposals = self.load()
        by_id = {p.proposal_id: p for p in proposals}
        by_id[proposal.proposal_id] = proposal
        self.save_all(list(by_id.values()))
        return proposal

    def get(self, proposal_id):
        for proposal in self.load():
            if proposal.proposal_id == proposal_id:
                return proposal
        return None

    def pending(self):
        """還在流程中的。被否決與已套用的不算。"""
        return [
            p for p in self.load()
            if p.status not in (REJECTED, APPLIED)
        ]


_store = None


def get_store():
    global _store
    if _store is None:
        _store = ProposalStore()
    return _store


def set_store(store):
    global _store
    _store = store


# ---------------- 研究循環(第七十七節)----------------

def from_self_review(report, store=None):
    """
    把自我檢討的結論變成提案草稿。

    **只產生 DRAFT。** 這是研究循環唯一自動做的事 —— 後面每一步
    都要有證據,而最後一步要有人。

    已經有同一個 target 的未結案提案時不重複開 ——
    每一輪檢討都開一個新提案,會讓待審清單在一週內變成一百筆,
    然後沒有人會看。
    """
    store = store or get_store()
    existing = {p.target for p in store.pending()}

    created = []

    for finding in getattr(report, "findings", []) or []:
        target = _target_of(finding)
        if target in existing:
            continue

        proposal = Proposal(
            title=str(finding)[:120],
            target=target,
            rationale=str(finding),
            origin="self_review",
            change={},          # 具體要改什麼由人填 —— 見下面
        )
        store.upsert(proposal)
        existing.add(target)
        created.append(proposal)

    return created


def _target_of(finding):
    """
    從一句檢討結論猜出它在講哪個東西。猜不到就用 "general" ——
    **不是留空**,留空會讓所有猜不到的結論互相覆蓋。
    """
    text = str(finding)
    for name in ("trend_following", "breakout", "momentum", "mean_reversion",
                 "rsi_reversion", "macd_cross", "vwap_reversion",
                 "volatility_breakout", "market_structure", "order_flow"):
        if name in text:
            return name
    return "general"


def run_research_loop(store=None, review=None):
    """
    排程入口(第七十七節)。

    它做的事只有一件:**把自我檢討的結論變成有編號、有狀態、
    追蹤得到的提案草稿。** 它不改任何東西,也不推進任何提案。

    第七十八節禁止的是「自己修改 → 自己測試 → 自己批准 → 自己 Live」
    這一整條鏈。這個循環刻意只做第一個箭頭之前的那一步:
    把「我覺得這裡有問題」變成一張可以被追蹤的單子。
    """
    store = store or get_store()

    if review is None:
        from agmcis.review.self_review import run_self_review
        review = run_self_review()

    created = from_self_review(review, store=store)
    pending = store.pending()

    return {
        "created": [p.proposal_id for p in created],
        "pending": len(pending),
        "awaiting_human": [
            p.proposal_id for p in pending
            if p.status == PAPER
        ],
        "verdict": getattr(review, "verdict", None),
    }
