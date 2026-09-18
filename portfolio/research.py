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

三、**多重比較校正。** 試幾十次,最好的那一次「看起來贏」
    幾乎是必然的。p 值乘上**有效**試驗次數(Bonferroni)才算數 ——
    有效,是因為網格裡會有行為完全一樣的重複項,而那些不該各算一次。
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

#: 支撐壓力突破的(進場 N 日高, 出場 M 日低)。
#:
#: 2026-09-18 執政官問「不是有就是說利用支撐或壓力、突破假突破、
#: 回測去判斷嗎」。答案是:可以,而且就用這裡 —— 但它得跟均線
#: **在同一組閘門下比**,不是靠誰講得比較有道理。
#:
#: 這兩組是**教科書值**:海龜系統一 20/10、系統二 55/20。
#: 不是搜出來的區間,所以它們沒有把搜尋空間撐大。
BREAKOUT_GRID = ((20, 10), (55, 20))

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
    """一組參數。**現任者也是一個 Variant** —— 沒有特權。

    `kind` 決定訊號規則:
      "ma"       收盤在 N 日均線之上才持有(現任)
      "breakout" 突破 N 日高進場、跌破 M 日低出場(Donchian / 海龜)

    ⚠️ **突破類多兩個參數**(entry_n / exit_n,加上可選的 confirm_bars),
    而均線只有一個,還是教科書值。參數多的一方在同樣的證據下更容易
    「看起來贏」—— 那正是 Bonferroni 校正要扣掉的東西。
    """

    ma: int
    vol_target_pct: float
    leverage_cap: float
    kind: str = "ma"
    exit_n: int = 0                      # 只有 breakout 用得到

    @property
    def key(self) -> str:
        head = (f"ma{self.ma}" if self.kind == "ma"
                else f"bo{self.ma}-{self.exit_n}")
        return f"{head}-vol{self.vol_target_pct:g}-lev{self.leverage_cap:g}"

    def describe(self) -> str:
        head = (f"{self.ma} 日均線" if self.kind == "ma"
                else f"突破 {self.ma} 日高 / 跌破 {self.exit_n} 日低出場")
        return (f"{head} · 波動目標 {self.vol_target_pct:g}% · "
                f"槓桿上限 {self.leverage_cap:g}×")

    def to_dict(self) -> dict:
        return {"ma": self.ma, "vol_target_pct": self.vol_target_pct,
                "leverage_cap": self.leverage_cap, "kind": self.kind,
                "exit_n": self.exit_n, "key": self.key,
                "describe": self.describe()}


def grid(incumbent: Variant) -> list:
    """所有要試的變化。**現任者不算一次試驗** —— 它是被挑戰的對象。"""
    out = []
    for vol in VOL_GRID:
        for lev in LEV_GRID:
            for ma in MA_GRID:
                v = Variant(ma=ma, vol_target_pct=vol, leverage_cap=lev)
                if v != incumbent:
                    out.append(v)
            for entry_n, exit_n in BREAKOUT_GRID:
                out.append(Variant(ma=entry_n, vol_target_pct=vol,
                                   leverage_cap=lev, kind="breakout",
                                   exit_n=exit_n))
    return out


# ══════════════════════════════════════════════════════════
# 統計:試了很多次之後,「最好的那個」有多少是運氣
# ══════════════════════════════════════════════════════════

def _calmar_of(rets, periods_per_year: float = 365.0) -> float | None:
    """一條日報酬序列的 Calmar(年化報酬 ÷ 最大回撤)。"""
    if not rets:
        return None
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak)
    if equity <= 0 or mdd <= 1e-9:
        return None
    years = len(rets) / periods_per_year
    if years <= 0:
        return None
    return (equity ** (1.0 / years) - 1.0) / mdd


def calmar_bootstrap(challenger_rets, incumbent_rets, *,
                     paths: int = BOOTSTRAP_PATHS,
                     block: int = BLOCK_DAYS,
                     seed: int = 20260913) -> float | None:
    """重抽之後,挑戰者**還是輸**的比例。當成 p 值用。

    ═══ 2026-09-18:上一版測錯了東西 ═══
    上一版 bootstrap 的是**平均日報酬**的差。但整套挑選的準則是
    **Calmar**(年化 ÷ 最大回撤)—— 兩件事不一樣:

      實測:100 日均線的驗證段 Calmar 0.65 > 現任 0.48,**而它的
      平均日報酬比現任低**。它贏在回撤小,不是贏在報酬高。

    於是舊版對它回 p=1.0(「根本沒贏」),而閘門說它贏了 0.17 Calmar。
    **一個用 A 挑、用 B 檢定的流程,永遠檢定不到它挑的東西。**
    一個靠降低回撤取勝的挑戰者在舊版裡**永遠**過不了。

    ═══ 這個數字是什麼,以及它不是什麼 ═══
    做法:把兩條日報酬**配對**按區塊重抽(同一組索引,所以看的是
    同一段市場),每次重建兩條權益曲線、各算一次 Calmar,
    數挑戰者沒贏的比例。

    ⚠️ **這不是嚴格的虛無假設檢定。** 它量的是「重抽的歷史裡,
    這個優勢有多常站得住」—— 也就是**重抽不確定性**,不是一個
    置中虛無下的尾機率。Calmar 是比值又是路徑統計量,置中沒有
    一個誠實的做法,而硬做一個出來會比說清楚更糟。

    所以它被當成 p 值丟進 Bonferroni 是一個**近似**。近似的方向:
    它比真正的 p 值**寬鬆**,所以校正後的門檻要當成下限而不是保證。
    """
    n = min(len(challenger_rets or []), len(incumbent_rets or []))
    if n < block * 3:
        return None                      # 樣本太短,**不給數字**

    a = list(challenger_rets)[-n:]
    b = list(incumbent_rets)[-n:]
    base_a, base_b = _calmar_of(a), _calmar_of(b)
    if base_a is None or base_b is None or base_a <= base_b:
        return 1.0                       # 原始樣本就沒贏

    rng = random.Random(seed)
    starts = max(1, n - block)
    losses = 0
    for _ in range(paths):
        ra, rb = [], []
        while len(ra) < n:
            i = rng.randrange(starts)
            for j in range(i, min(i + block, n)):
                ra.append(a[j])
                rb.append(b[j])
                if len(ra) >= n:
                    break
        ca, cb = _calmar_of(ra), _calmar_of(rb)
        if ca is None or cb is None or ca <= cb:
            losses += 1
    return losses / paths


def effective_trials(results) -> int:
    """真正獨立的試驗有幾次。

    ═══ 2026-09-18:網格裡有一半是重複的 ═══
    實測發現 `lev2` 與 `lev3` 每一組的訓練/驗證/回撤**完全一樣** ——
    波動目標在 15~35% 之間時,總曝險根本碰不到槓桿上限,所以那個
    參數是**惰性的**。47 種裡大約一半是同一個東西換個名字。

    而 Bonferroni 乘的是「試了幾次」。把重複的各算一次,等於**無中
    生有地加嚴了校正** —— 一個真的有效的改良會因為我把網格寫得太
    冗餘而被擋掉。那不是保守,那是算錯。

    `results` 是 [(訓練指標, 驗證指標)],用四捨五入後的結果去重。
    """
    seen = set()
    for train, test in results:
        seen.add((
            round(float(train.get("calmar") or -99), 4),
            round(float(test.get("calmar") or -99), 4),
            round(float(test.get("max_dd_pct") or -1), 3),
        ))
    return max(1, len(seen))


def bonferroni(p: float | None, trials: int) -> float | None:
    """試了 `trials` 次,最好的那一次的 p 值要乘上 `trials`。

    **這是整支檔案最重要的一行。** 試幾十次,其中一次「看起來贏」
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
    dd_ok = worst_dd <= max_dd_contract_pct
    # 2026-09-18:那句「超過契約,仍然不可交易」原本是**無條件**印的,
    # 於是 12.7% vs 15% 這個明明通過的關卡,旁邊跟著一句說它沒過。
    # 一行自相矛盾的證據比沒有證據糟:讀的人會開始不信任整張表。
    add("回撤在契約內", dd_ok,
        f"最大回撤 {worst_dd:.1f}% vs 契約 {max_dd_contract_pct:g}%"
        + ("" if dd_ok else "(贏了現任也一樣不可交易 —— "
                            "回撤契約不因為贏了而放寬)"))

    add(f"優勢 ≥ {MIN_CALMAR_EDGE:g} Calmar",
        edge is not None and edge >= MIN_CALMAR_EDGE,
        f"驗證段優勢 {edge if edge is None else round(edge, 3)} —— "
        "差不多好就不換:換參數本身有成本,而『差不多好』不值那個成本")

    add(f"多重比較校正後 p < {ALPHA}",
        p_corr is not None and p_corr < ALPHA,
        f"原始 p={p_raw if p_raw is None else round(p_raw, 4)}"
        f" × 試過 {trials} 次 = {p_corr if p_corr is None else round(p_corr, 4)}"
        f" —— 試 {trials} 次(已去掉行為重複的),"
        "其中一次看起來贏幾乎是必然的")

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
