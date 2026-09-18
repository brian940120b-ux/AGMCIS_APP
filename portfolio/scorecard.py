"""
成績單 —— 這套現在到底好不好 · 2026-09-18

執政官:「我想和之前一樣能讓系統自行去交易模擬,現在這個的績效
到底好不好?」

═══ 這支存在的理由,是為了能說出「還不知道」═══
績效面板最容易做成一件壞事:把一個**還不能回答的問題**回答掉。

模擬帳戶跑了十天、賺了 3%,做成一張卡就是「+3.00%」，綠色的。
那個數字沒有錯,但它**不是那個問題的答案** —— 十天裡策略可能連一次
完整的進出都還沒走完,3% 是那七個幣自己漲的,跟這套規則沒有關係。

所以這支不先算報酬,先算**有沒有資格回答**:

  ① 模擬還在不在跑 —— 停掉的模擬會安安靜靜地一直報最後一天的數字
  ② 走完幾次進出 —— 沒走完的倉,損益是帳面的,不是結果
  ③ 贏不贏得過買入持有 —— 絕對報酬不是答案(判準二、三)

前兩關沒過,裁決就是「還不知道」,而且說清楚還缺什麼。

═══ 門檻是預先登記的,不是看到結果才定的 ═══
`MIN_ROUND_TRIPS = 5` 寫在這裡,不因為任何一次的結果而改。

**而 5 不是「到 5 就可信了」。** 它是「不到 5 就連講都不該講」。
回測用的是四百多天、幾十次進出;實跑要達到同一個信心水準,需要的是
同一個量級的時間,不是五次。5 只擋掉最離譜的那種說法。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CURVE = BASE / "data" / "portfolio_equity.jsonl"
PROPOSALS = BASE / "data" / "proposals.json"

#: 少於這麼多次完整進出,一律不給裁決。預先登記,不得因結果調整。
MIN_ROUND_TRIPS = 5
#: 記帳超過這麼多天沒動,就當模擬停了。每日作業是一天一次,
#: 給兩天的餘裕(一次失敗還能補),第三天沒動就是真的停了。
STALE_DAYS = 2.5
#: 回撤契約,沿用畢業契約第四條。這裡不另立標準。
MAX_DD_CONTRACT_PCT = 15.0

UNKNOWN = "還不知道"
GOOD = "贏得過"
BAD = "贏不過"
BLOCKED = "贏了基準,但不可交易"


@dataclass(frozen=True)
class Scorecard:
    """實跑的成績。**每一格都要能回答「你憑什麼這樣說」。**"""

    days: int = 0
    first_day: str = ""
    last_day: str = ""
    stale_days: float | None = None

    equity: float | None = None
    start_equity: float | None = None
    return_pct: float | None = None
    benchmark_pct: float | None = None
    excess_pct: float | None = None
    max_dd_pct: float | None = None
    round_trips: int = 0
    #: 報酬拆成兩塊。**這個拆解就是「還不知道」那句話的證據** ——
    #: 已實現是結果,未實現是還沒定案的東西。
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None

    verdict: str = UNKNOWN
    because: list = field(default_factory=list)
    #: 回測的證據:(訓練 Calmar, 驗證 Calmar, 驗證回撤, 這份提案的日期)
    backtest: tuple | None = None

    @property
    def running(self) -> bool:
        return self.stale_days is not None and self.stale_days <= STALE_DAYS


def _rows(path: Path) -> list:
    out = []
    try:
        with path.open(encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except json.JSONDecodeError:
                        continue          # 壞掉的一行不該讓整張卡消失
    except OSError:
        pass
    return out


def round_trips(rows: list) -> int:
    """走完幾次完整的進出。

    曲線每天記一次 `holdings`(當天持有哪幾檔)。一檔出現又消失,
    就是一次走完的進出 —— 那一次的損益已經**落袋**,是結果。

    ⚠️ **還抱著的倉不算。** 帳面浮盈不是成績:它可以在明天變成
    浮虧,而一個把浮盈算進成績的系統會在每一次上漲時說自己很行。
    """
    open_now: set = set()
    done = 0
    for r in rows:
        held = set(r.get("holdings") or [])
        done += len(open_now - held)
        open_now = held
    return done


def latest_backtest(path: Path | None = None) -> tuple | None:
    """最近一次研究迴路記下的**現任**的訓練 / 驗證數字。

    從 proposals.json 讀,不寫死 —— 寫死的數字會在策略換掉之後
    繼續講上一個策略的成績,而那看起來完全正常。
    """
    try:
        raw = json.loads((path or PROPOSALS).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = raw if isinstance(raw, list) else raw.get("proposals") or []
    best = None
    for d in rows:
        if not isinstance(d, dict):
            continue
        created = str(d.get("created_utc") or "")
        if best is None or created > best[0]:
            best = (created, d)
    if best is None:
        return None
    created, d = best
    tr = d.get("incumbent_train") or {}
    te = d.get("incumbent_test") or {}
    if tr.get("calmar") is None and te.get("calmar") is None:
        return None
    return (tr.get("calmar"), te.get("calmar"), te.get("max_dd_pct"),
            created[:10])


def score(curve_path: Path | None = None,
          proposals_path: Path | None = None,
          now: datetime | None = None) -> Scorecard:
    """算出成績單。**算不出來就說算不出來,不給一個看起來合理的數字。**"""
    now = now or datetime.now(timezone.utc)
    rows = _rows(curve_path or CURVE)
    bt = latest_backtest(proposals_path)

    if not rows:
        return Scorecard(
            verdict=UNKNOWN, backtest=bt,
            because=["模擬還沒記過任何一天 —— "
                     "`data/portfolio_equity.jsonl` 是空的或不存在"])

    last = rows[-1]
    first = rows[0]
    stale = None
    try:
        t = datetime.fromisoformat(str(last.get("t")))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        stale = (now - t).total_seconds() / 86400.0
    except (TypeError, ValueError):
        pass

    eq = last.get("equity")
    ret = last.get("return_pct")
    bench = last.get("benchmark_pct")
    excess = (None if (ret is None or bench is None) else
              float(ret) - float(bench))
    dd = max((float(r.get("drawdown_pct") or 0.0) for r in rows),
             default=0.0)
    trips = round_trips(rows)
    start = (None if eq is None or ret is None else
             float(eq) / (1.0 + float(ret) / 100.0))

    base = dict(
        days=len(rows),
        first_day=str(first.get("signal_day") or "")[:10],
        last_day=str(last.get("signal_day") or "")[:10],
        stale_days=stale, equity=eq, start_equity=start,
        return_pct=ret, benchmark_pct=bench, excess_pct=excess,
        max_dd_pct=dd, round_trips=trips, backtest=bt,
        realized_pnl=last.get("realized_pnl"),
        unrealized_pnl=last.get("unrealized_pnl"))

    # ── 一、模擬還在不在跑 ────────────────────────────
    # 停掉的模擬會安靜地一直報最後一天的數字,而那個數字看起來
    # 跟一個活著的系統完全一樣。所以這一關排在最前面。
    if stale is None:
        return Scorecard(**base, verdict=UNKNOWN,
                         because=["最後一筆記帳沒有時間戳,判斷不了"
                                  "模擬還在不在跑"])
    if stale > STALE_DAYS:
        return Scorecard(
            **base, verdict=UNKNOWN,
            because=[f"**模擬停了。** 最後一筆記帳是 {stale:.1f} 天前"
                     f"({base['last_day']}),而每日作業是一天一次。",
                     "下面那些數字是停掉那天的,不是現在的 —— "
                     "在它重新跑起來之前,它們只會一直是這幾個。",
                     "查:`systemctl status agmcis-portfolio.timer` 與 "
                     "`journalctl -u agmcis-portfolio -n 50`"])

    # ── 二、走完幾次進出 ─────────────────────────────
    if trips < MIN_ROUND_TRIPS:
        why = [f"**還不能回答。** 走完的進出只有 {trips} 次"
               f"(門檻 {MIN_ROUND_TRIPS} 次,預先登記)。",
               "沒走完的倉,損益是帳面的 —— 明天就可能變號。"
               "把浮盈算進成績的系統,會在每一次上漲時說自己很行。"]
        if ret is not None:
            line = (f"目前帳面 {float(ret):+.2f}%"
                    + (f",基準 {float(bench):+.2f}%" if bench is not None
                       else ""))
            # 把帳面拆成已實現 / 未實現 —— 這個拆解**就是上面那句話的
            # 證據**,不是多印一個數字:未實現是還沒定案的東西。
            r, u = last.get("realized_pnl"), last.get("unrealized_pnl")
            if r is not None and u is not None:
                line += (f";其中已實現 {float(r):+,.2f}、"
                         f"未實現 {float(u):+,.2f} USDT")
            why.append(line + " —— 記著,這還不是成績。")
        why.append("而 " + str(MIN_ROUND_TRIPS) +
                   " 次也**不是「到了就可信」** —— 它只擋掉最離譜的說法。"
                   "回測用的是四百多天;實跑要一樣的信心,要一樣的時間。")
        return Scorecard(**base, verdict=UNKNOWN, because=why)

    # ── 三、贏不贏得過買入持有 ────────────────────────
    if excess is None:
        return Scorecard(**base, verdict=UNKNOWN,
                         because=["沒有基準可比 —— "
                                  "絕對報酬不是答案(判準二、三):"
                                  "七個幣自己漲的不算這套規則的功勞"])
    why = [f"報酬 {float(ret):+.2f}% vs 等權買入持有 "
           f"{float(bench):+.2f}%,差 {excess:+.2f}%",
           f"最大回撤 {dd:.1f}%(契約 {MAX_DD_CONTRACT_PCT:g}%)",
           f"走完 {trips} 次進出,共 {len(rows)} 天"]
    if excess <= 0:
        why.insert(0, "**贏不過買入持有。** 判準三:只要有一段輸給基準,"
                      "這套規則就沒有存在的理由 —— 不交易比較省事。")
        return Scorecard(**base, verdict=BAD, because=why)
    if dd > MAX_DD_CONTRACT_PCT:
        why.insert(0, "贏了基準,**但回撤超過契約** —— "
                      "這是「贏了但不可交易」,不是「好」。"
                      "回撤契約不因為賺錢而放寬。")
        return Scorecard(**base, verdict=BLOCKED, because=why)
    why.insert(0, "贏得過買入持有,而且回撤在契約內。")
    return Scorecard(**base, verdict=GOOD, because=why)


# ══════════════════════════════════════════════════════════
# 即時快照 —— 同一個瞬間的所有數字
# ══════════════════════════════════════════════════════════
#
# 2026-09-18 執政官:「數字對不上,我只要一個,就是系統自行模擬的
# 資訊,並且我想看到即時的盈虧數字變化。」
#
# 對不上是真的,而且是設計出來的:成績單的權益來自每日記帳那一刻
# (00:30 的收盤價),持倉卡的未實現用的是現在的即時價。兩個不同
# 時間點的價格,當然是兩個數字。
#
# 修法不是「挑一個顯示」,是**讓所有「現在」的數字出自同一組價格**。
# 所以這裡一次算完:權益、報酬、基準、未實現、逐檔盈虧 ——
# 全部餵同一個 marks,同一個瞬間。
#
# 只有歷史才會的東西(最大回撤、走完幾次進出、記了幾天)留在帳本,
# 而且**標明它們是到上一次記帳為止的**,不假裝是即時的。


@dataclass(frozen=True)
class Live:
    """此刻的模擬帳戶。**每一格都出自同一組價格。**"""

    at: str = ""
    equity: float | None = None
    start_equity: float | None = None
    return_pct: float | None = None
    benchmark_pct: float | None = None
    excess_pct: float | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    exposure: float | None = None
    #: 逐檔:{代號: {qty, avg, mark, pnl, roi}}。問不到價的那檔 mark=None。
    legs: dict = field(default_factory=dict)
    #: 問不到現價的代號。**它們沒有算進上面任何一個合計。**
    missing: list = field(default_factory=list)


def live(account, marks: dict, now: datetime | None = None) -> Live:
    """把帳戶 + 一組價格,算成一個瞬間的完整畫面。

    `marks` 缺哪一檔,那一檔就**整個不參與合計** —— 不用開倉均價
    頂替。用均價頂替會讓那一檔的盈虧顯示成 0,而 0 跟「不知道」
    在畫面上長得一樣,意思卻相反。
    """
    from portfolio.paper import benchmark_pct
    now = now or datetime.now(timezone.utc)

    held = {s: p for s, p in account.positions.items()
            if abs(p.position_amt) > 1e-12}
    # 缺價的只算**持倉**那些 —— 基準籃子缺一檔是基準的事,
    # 不該被報成「你有一個倉的死活不知道」。
    missing = sorted(s for s in held if marks.get(s) is None)
    # ⚠️ 價格要涵蓋**持倉 ∪ 基準籃子**,不是只有持倉。
    # 第一版只餵持倉的價格,於是策略空手的時候基準整個算不出來 ——
    # 而空手正是最想知道「不交易的話現在是賺是賠」的時候。
    ok = {s: float(v) for s, v in marks.items() if v is not None}

    legs, unreal = {}, 0.0
    for s, pos in sorted(held.items()):
        mark = ok.get(s)
        if mark is None:
            legs[s] = {"qty": pos.position_amt, "avg": pos.avg_price,
                       "mark": None, "pnl": None, "roi": None}
            continue
        pnl = pos.unrealized(mark)
        margin = pos.initial_margin()
        legs[s] = {"qty": pos.position_amt, "avg": pos.avg_price,
                   "mark": mark, "pnl": pnl,
                   "roi": (pnl / margin * 100) if margin else None}
        unreal += pnl

    eq = account.balance + unreal
    start = account.start_equity or None
    ret = (eq / start * 100 - 100) if start else None

    # 基準走 paper.benchmark_pct —— **同一個公式**,只是餵即時價。
    # 面板自己再寫一份的話,兩份遲早分岔,而那就是「數字對不上」。
    bench = None
    try:
        bench = benchmark_pct(account, ok, int(now.timestamp() * 1000))
    except Exception:                                # noqa: BLE001
        bench = None

    return Live(
        at=now.isoformat(timespec="seconds"),
        equity=eq, start_equity=start, return_pct=ret,
        benchmark_pct=bench,
        excess_pct=(None if (ret is None or bench is None) else ret - bench),
        realized_pnl=account.realized_pnl, unrealized_pnl=unreal,
        exposure=(account.exposure(ok) if held and ok else None),
        legs=legs, missing=missing)
