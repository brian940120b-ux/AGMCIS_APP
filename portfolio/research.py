"""
研究迴路 —— 系統不停地試,但試出來的結果不能自己生效 · 2026-09-13

═══ 執政官要的 ═══
「讓系統不斷地經過多次的模擬交易之後,發現該怎麼樣調整會有利於
  後續的交易的盈利。」

═══ 而這件事有一個會殺死它的形狀 ═══
模擬只有**一條**歷史。系統試一千種變化,一定會找到在那條歷史上最
漂亮的一組參數 —— 而那組參數漂亮的原因是它**記住了過去**,
不是它理解了市場。

結果是一條非常好看的模擬曲線,和一個賠錢的真實帳戶。

所以這一支的每一個設計都在對抗同一件事:

一、**網格是預先登記的,而且很小。**
    32 種組合,寫死在 `GRID` 裡。要加一個維度就得改程式碼、進 git ——
    那個摩擦是刻意的。一個可以隨手加維度的搜尋器就是過擬合機器。

二、**訓練段挑,驗證段驗。** 切分沿用 `judge.TRAIN_FRACTION`
    (前 2/3 / 後 1/3),而且是**時序切分不是幣種切分**:
    這條策略的風險是時間上的過擬合(在已知的崩盤前離場)。

三、**多重比較校正。** 試 32 次,最好的那一次「看起來贏」
    幾乎是必然的。p 值乘上試過的次數(Bonferroni)才算數。
    **試了幾次這個數字會被記在提案裡** —— 沒有它,
    「我們找到更好的了」這句話沒有意義。

四、**現任者贏平手。** 挑戰者要贏,而且要贏到通過校正後的顯著水準。
    差不多好 = 不換。換參數本身有成本(重新累積證據、
    回測數字全部要重跑),而「差不多好」不值那個成本。

五、**提案不會自己生效。** 這一支只產生提案。套用是另一個動作,
    需要執政官點頭,而且走設定稽核(第 102 條)。

═══ 這一支不做的事 ═══
· 不改任何設定
· 不寫帳本
· 不碰網路(資料由呼叫端餵進來,所以它可以被完整測試)
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.atomic import write_json_atomic

BASE = Path(__file__).resolve().parents[1]
STORE = BASE / "data" / "proposals.json"

# ── 預先登記的搜尋網格 ───────────────────────────────────
#
# **小、而且寫死。** 要加一個維度就得改這裡、進 git、被 review。
# 那個摩擦是刻意的:一個可以隨手加維度的搜尋器就是過擬合機器。
#
# 每個值都不是隨便挑的:
#   均線     20/50/100/200 —— 教科書的四個標準值,不是搜出來的區間
#   波動目標 15/20/27/35   —— 27 是現任;上下各給兩檔看敏感度
#   槓桿上限 2/3           —— 3 是現任(2026-09-10 從爆倉反推),
#                             只往下試,**不試更高的**
MA_GRID = (20, 50, 100, 200)
VOL_GRID = (15.0, 20.0, 27.0, 35.0)
LEV_GRID = (2.0, 3.0)

#: 校正後要達到的顯著水準。
ALPHA = 0.05

#: bootstrap 的區塊長度(天)。日報酬有自相關,逐日重抽會低估風險。
BLOCK_DAYS = 20

#: bootstrap 重抽次數。
BOOTSTRAP_PATHS = 2000

#: 挑戰者至少要比現任好這麼多 Calmar,才值得談。
#: 純統計顯著但幅度微小的改動不值得換 —— 換參數本身有成本。
MIN_CALMAR_EDGE = 0.10


@dataclass(frozen=True)
class Variant:
    """一組參數。**現任者也是一個 Variant** —— 沒有特權。"""

    ma: int
    vol_target_pct: float
    leverage_cap: float

    @property
    def key(self) -> str:
        return f"ma{self.ma}-vol{self.vol_target_pct:g}-lev{self.leverage_cap:g}"

    def describe(self) -> str:
        return (f"{self.ma} 日均線 · 波動目標 {self.vol_target_pct:g}% · "
                f"槓桿上限 {self.leverage_cap:g}×")

    def to_dict(self) -> dict:
        return {"ma": self.ma, "vol_target_pct": self.vol_target_pct,
                "leverage_cap": self.leverage_cap, "key": self.key}


def grid(incumbent: Variant) -> list:
    """所有要試的變化。**現任者不算一次試驗** —— 它是被挑戰的對象。"""
    out = []
    for ma in MA_GRID:
        for vol in VOL_GRID:
            for lev in LEV_GRID:
                v = Variant(ma=ma, vol_target_pct=vol, leverage_cap=lev)
                if v != incumbent:
                    out.append(v)
    return out


# ══════════════════════════════════════════════════════════
# 統計:試了很多次之後,「最好的那個」有多少是運氣
# ══════════════════════════════════════════════════════════

def block_bootstrap_pvalue(challenger_rets, incumbent_rets, *,
                           paths: int = BOOTSTRAP_PATHS,
                           block: int = BLOCK_DAYS,
                           seed: int = 20260913) -> float | None:
    """挑戰者的優勢是運氣的機率(單尾)。

    ═══ 做法:配對差值 + 區塊 bootstrap + **置中的虛無** ═══
    一、先算逐日**差值** d[i] = 挑戰者[i] − 現任[i]。
        配對是關鍵:兩邊看的是同一天的市場,所以差值裡沒有
        「抽到不同時期」的雜訊,只有策略的差別。
    二、把 d **減掉它自己的平均**,得到一條「沒有優勢」的序列 ——
        這就是虛無假設長的樣子。
    三、從那條置中序列裡**按區塊**重抽,看重抽出來的平均
        還有多常大到跟觀察值一樣。

    ═══ 第二步是我第一版漏掉的,而漏掉它整個統計就是錯的 ═══
    第一版直接從原始資料重抽 —— 但原始資料**含有那個效果**,
    所以重抽出來的差值圍繞著觀察值而不是零,`P(重抽 ≥ 觀察)` 會
    趨近 0.5。那不是 p 值,那是一個看起來像 p 值的數字。
    測試 `test_a_clearly_better_challenger_gets_a_small_p` 抓到它:
    一條每天都穩定贏 0.4% 的序列被算出 p=0.17。

    ═══ 為什麼要 block ═══
    日報酬有自相關(趨勢策略尤其明顯:連續在場的日子長得像)。
    逐日獨立重抽會打散那個結構,讓路徑看起來比實際平順,於是
    低估風險、高估顯著性 —— 而那個方向剛好是「讓提案容易通過」。
    """
    n = min(len(challenger_rets or []), len(incumbent_rets or []))
    if n < block * 3:
        return None                      # 樣本太短,**不給數字**

    a = list(challenger_rets)[-n:]
    b = list(incumbent_rets)[-n:]
    diff = [a[i] - b[i] for i in range(n)]
    observed = sum(diff) / n
    if observed <= 0:
        return 1.0                       # 根本沒贏

    centred = [x - observed for x in diff]      # ← 虛無:沒有優勢
    rng = random.Random(seed)
    starts = max(1, n - block)
    hits = 0
    for _ in range(paths):
        total = 0.0
        count = 0
        while count < n:
            i = rng.randrange(starts)
            for j in range(i, min(i + block, n)):
                total += centred[j]
                count += 1
                if count >= n:
                    break
        if total / n >= observed:
            hits += 1
    return hits / paths


def bonferroni(p: float | None, trials: int) -> float | None:
    """試了 `trials` 次,最好的那一次的 p 值要乘上 `trials`。

    **這是整支檔案最重要的一行。** 試 32 次,其中一次「看起來贏」
    幾乎是必然的 —— 不校正的話,這個引擎每次都會找到一個提案,
    而那些提案平均而言一文不值。
    """
    if p is None:
        return None
    return min(1.0, p * max(1, trials))


# ══════════════════════════════════════════════════════════
# 提案
# ══════════════════════════════════════════════════════════

@dataclass
class Proposal:
    """一個「這樣改可能更好」的主張,連同它的全部證據。

    **每一格都要能回答「你憑什麼」。** 尤其 `trials` ——
    沒有它,「我們找到更好的了」這句話沒有意義。
    """

    proposal_id: str
    created_utc: str
    incumbent: dict
    challenger: dict
    trials: int
    train: dict = field(default_factory=dict)
    test: dict = field(default_factory=dict)
    incumbent_train: dict = field(default_factory=dict)
    incumbent_test: dict = field(default_factory=dict)
    p_raw: float | None = None
    p_corrected: float | None = None
    calmar_edge: float | None = None
    checks: list = field(default_factory=list)
    status: str = "pending"          # pending / accepted / rejected
    decided_by: str | None = None
    decided_on: str | None = None
    note: str | None = None

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id, "created_utc": self.created_utc,
            "incumbent": self.incumbent, "challenger": self.challenger,
            "trials": self.trials,
            "train": self.train, "test": self.test,
            "incumbent_train": self.incumbent_train,
            "incumbent_test": self.incumbent_test,
            "p_raw": self.p_raw, "p_corrected": self.p_corrected,
            "calmar_edge": self.calmar_edge,
            "checks": [{"name": n, "ok": ok, "detail": d}
                       for n, ok, d in self.checks],
            "passed": self.passed, "status": self.status,
            "decided_by": self.decided_by, "decided_on": self.decided_on,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        out = cls(
            proposal_id=d["proposal_id"], created_utc=d["created_utc"],
            incumbent=d.get("incumbent", {}),
            challenger=d.get("challenger", {}),
            trials=int(d.get("trials", 0)),
            train=d.get("train", {}), test=d.get("test", {}),
            incumbent_train=d.get("incumbent_train", {}),
            incumbent_test=d.get("incumbent_test", {}),
            p_raw=d.get("p_raw"), p_corrected=d.get("p_corrected"),
            calmar_edge=d.get("calmar_edge"),
            status=d.get("status", "pending"),
            decided_by=d.get("decided_by"), decided_on=d.get("decided_on"),
            note=d.get("note"))
        out.checks = [(c["name"], c["ok"], c.get("detail", ""))
                      for c in (d.get("checks") or [])]
        return out


def proposal_id(incumbent: Variant, challenger: Variant,
                as_of: str) -> str:
    """確定性 id。同一天、同一組對照重跑一百次是同一個提案 ——

    否則每天跑一次就會堆出一百個長得一樣的提案,而人會停止讀它們。
    """
    seed = f"{incumbent.key}|{challenger.key}|{as_of}"
    return "prop" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _calmar(m: dict | None) -> float | None:
    if not m:
        return None
    return m.get("calmar")


def judge_challenger(incumbent: Variant, challenger: Variant, *,
                     trials: int, train: dict, test: dict,
                     incumbent_train: dict, incumbent_test: dict,
                     p_raw: float | None, as_of: str,
                     max_dd_contract_pct: float = 15.0) -> Proposal:
    """一個挑戰者要通過的每一關。**任何一關沒過就不是提案。**

    這些關卡不是我挑的 —— 前四關直接沿用 `portfolio/judge.py` 的
    預先登記判準(2026-09-08 在看到任何結果之前寫下的)。
    """
    p_corr = bonferroni(p_raw, trials)
    c_new_tr, c_new_te = _calmar(train), _calmar(test)
    c_old_tr, c_old_te = _calmar(incumbent_train), _calmar(incumbent_test)
    edge = (c_new_te - c_old_te
            if c_new_te is not None and c_old_te is not None else None)

    checks = []

    def add(name, ok, detail):
        checks.append((name, bool(ok), detail))

    add("訓練段贏過現任",
        c_new_tr is not None and c_old_tr is not None and c_new_tr > c_old_tr,
        f"Calmar {c_new_tr} vs 現任 {c_old_tr}")

    add("驗證段也贏過現任",
        c_new_te is not None and c_old_te is not None and c_new_te > c_old_te,
        f"Calmar {c_new_te} vs 現任 {c_old_te}"
        "(只贏訓練段 = 過擬合的典型形狀)")

    worst_dd = max(train.get("max_dd_pct", 99.0),
                   test.get("max_dd_pct", 99.0))
    add("回撤在契約內", worst_dd <= max_dd_contract_pct,
        f"最大回撤 {worst_dd:.1f}% vs 契約 {max_dd_contract_pct:g}%"
        "(贏了現任但超過契約,仍然不可交易)")

    add(f"優勢 ≥ {MIN_CALMAR_EDGE:g} Calmar",
        edge is not None and edge >= MIN_CALMAR_EDGE,
        f"驗證段優勢 {edge if edge is None else round(edge, 3)} —— "
        "差不多好就不換:換參數本身有成本,而『差不多好』不值那個成本")

    add(f"多重比較校正後 p < {ALPHA}",
        p_corr is not None and p_corr < ALPHA,
        f"原始 p={p_raw if p_raw is None else round(p_raw, 4)}"
        f" × 試過 {trials} 次 = {p_corr if p_corr is None else round(p_corr, 4)}"
        " —— 試 32 次,其中一次看起來贏幾乎是必然的")

    return Proposal(
        proposal_id=proposal_id(incumbent, challenger, as_of),
        created_utc=as_of,
        incumbent=incumbent.to_dict(), challenger=challenger.to_dict(),
        trials=trials, train=train, test=test,
        incumbent_train=incumbent_train, incumbent_test=incumbent_test,
        p_raw=p_raw, p_corrected=p_corr, calmar_edge=edge, checks=checks)


# ══════════════════════════════════════════════════════════
# 存放與核可
# ══════════════════════════════════════════════════════════

def load(path: Path | None = None) -> list:
    p = path or STORE
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Proposal.from_dict(d) for d in (raw.get("proposals") or [])]


def save(proposals, path: Path | None = None) -> None:
    write_json_atomic(path or STORE, {
        "updated_utc": datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proposals": [p.to_dict() for p in proposals]})


def merge(existing, fresh) -> list:
    """新跑出來的提案併進舊的。

    **已經被裁決過的提案不會被覆蓋。** 否則今天核可、明天重跑,
    那個決定就無聲地消失了 —— 而系統會再問一次同樣的問題。
    """
    by_id = {p.proposal_id: p for p in existing}
    for p in fresh:
        old = by_id.get(p.proposal_id)
        if old is not None and old.status != "pending":
            continue                     # 裁決過的不動
        by_id[p.proposal_id] = p
    return sorted(by_id.values(), key=lambda p: p.created_utc, reverse=True)


class NotFound(RuntimeError):
    pass


class AlreadyDecided(RuntimeError):
    """裁決過的提案不得再裁決一次。

    改變主意是可以的,但那要是一個**新的決定**,有自己的時間戳 ——
    偷偷改掉舊的那一筆,就是把稽核軌跡抹掉。
    """


def decide(proposal_id_: str, status: str, *, by: str = "執政官",
           note: str | None = None, path: Path | None = None) -> Proposal:
    """核可或駁回一個提案。**這一步不套用任何設定。**

    它只記錄「執政官說可以」。真正改參數是另一個動作,
    而那個動作會讀這裡的紀錄 —— 兩步分開,是為了讓
    「決定」與「生效」在稽核上是兩筆事。
    """
    if status not in ("accepted", "rejected"):
        raise ValueError(f"status 只能是 accepted / rejected,收到 {status!r}")

    items = load(path)
    target = next((p for p in items if p.proposal_id == proposal_id_), None)
    if target is None:
        raise NotFound(f"沒有這個提案:{proposal_id_}")
    if target.status != "pending":
        raise AlreadyDecided(
            f"{proposal_id_} 已經在 {target.decided_on} 被 "
            f"{target.decided_by} 裁決為 {target.status} —— "
            "改變主意要開一個新的決定,不是改掉舊的那一筆")
    if status == "accepted" and not target.passed:
        raise ValueError(
            "這個提案沒有通過全部關卡,不得核可。\n  "
            + "\n  ".join(f"{'✓' if ok else '✗'} {n}:{d}"
                          for n, ok, d in target.checks))

    target.status = status
    target.decided_by = by
    target.decided_on = datetime.now(timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    target.note = note
    save(items, path)
    return target


def pending(path: Path | None = None) -> list:
    return [p for p in load(path) if p.status == "pending"]
