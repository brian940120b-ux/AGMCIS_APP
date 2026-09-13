"""
新系統 — 實盤資格契約 · 2026-09-08

═══ 為什麼要重寫,而不是沿用舊的畢業契約 ═══
舊的 research/graduation.py 量的是**影子帳戶** —— 而影子已退役,
它當時在前向測試 343 個已被判死的配置。拿一個沒有人會做的東西
去判「可不可以碰真錢」,那個 1/8 沒有任何意義。

契約本身的八條沒有改,改的只有「量誰」。
門檻數字全部沿用執政官 2026-09-06 的裁定,我一個字都沒動 ——
在乾淨樣本為零時寫下的規則,不得事後放寬。

═══ 八條 ═══
一、樣本量:100 個交易日(前向,不是回測)
二、期望值:總報酬 > 0
三、勝過基準:對同期等權買入持有為正
四、回撤:最大回撤 <= 15%
五、行為在範圍內:監控層無 HIGH 級示警
六、幣種分散:單一幣種貢獻 <= 40% 的獲利
七、時間分散:單月貢獻 <= 50% 的獲利
八、持續性:連續 3 個完整月正報酬

八條全過也只是「有資格談」——
實盤閘門人工簽署(舊憲法第十條的精神),永遠不自動化。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.atomic import write_json_atomic
from core.logging import get_logger

log = get_logger("portfolio.contract")

BASE = Path(__file__).resolve().parents[1]
CURVE = BASE / "data" / "portfolio_equity.jsonl"
MONITOR = BASE / "data" / "portfolio_monitor.json"
OUT = BASE / "data" / "portfolio_contract.json"

# ── 門檻:2026-09-06 執政官裁定,不得事後放寬 ──────────────────
MIN_DAYS = 100
MAX_DD_PCT = 15.0
MAX_SYMBOL_SHARE = 0.40
MAX_MONTH_SHARE = 0.50
MIN_POSITIVE_MONTHS = 3
# ─────────────────────────────────────────────────────────


def _rows() -> list[dict]:
    out: list[dict] = []
    try:
        with CURVE.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return out


def _crit(name: str, passed: bool, detail: str, progress: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail,
            "progress": progress}


def evaluate() -> dict:
    rows = _rows()
    n = len(rows)
    cs: list[dict] = []

    cs.append(_crit("樣本量", n >= MIN_DAYS,
                    f"{n}/{MIN_DAYS} 個前向交易日",
                    f"{min(n / MIN_DAYS * 100, 100):.0f}%"))

    ret = float(rows[-1].get("return_pct") or 0) if rows else 0.0
    bm = float(rows[-1].get("benchmark_pct") or 0) if rows else 0.0
    cs.append(_crit("報酬為正", ret > 0, f"{ret:+.2f}%"))
    cs.append(_crit("勝過基準", ret > bm,
                    f"組合 {ret:+.2f}% vs 基準 {bm:+.2f}%"
                    f"(超額 {ret - bm:+.2f}pp)"))

    dd = max((float(r.get("drawdown_pct") or 0) for r in rows), default=0.0)
    cs.append(_crit("回撤", dd <= MAX_DD_PCT,
                    f"最大回撤 {dd:.2f}%(上限 {MAX_DD_PCT}%)"))

    try:
        m = json.loads(MONITOR.read_text(encoding="utf-8"))
        highs = [a for a in (m.get("alerts") or []) if a.get("level") == "HIGH"]
    except Exception:
        highs = []
    cs.append(_crit("行為在歷史範圍內", not highs,
                    "監控層無 HIGH 級示警" if not highs
                    else f"{len(highs)} 項:"
                         + "; ".join(a.get("kind", "?") for a in highs)))

    # ── 分散度:用「哪幾天賺的錢來自哪些幣 / 哪個月」──────────
    # 尚無獲利時不判定 —— 對零獲利算集中度是沒有意義的。
    gains = defaultdict(float)
    months = defaultdict(float)
    prev = 0.0
    for r in rows:
        cur = float(r.get("return_pct") or 0)
        d = cur - prev
        prev = cur
        if d <= 0:
            continue
        hold = r.get("holdings") or []
        if hold:
            for s in hold:
                gains[s] += d / len(hold)
        months[str(r.get("signal_day", ""))[:7]] += d
    tot_g = sum(gains.values())
    if tot_g <= 0:
        cs.append(_crit("幣種分散", False, "尚無獲利可分析"))
        cs.append(_crit("時間分散", False, "尚無獲利可分析"))
    else:
        top_s = max(gains.values()) / tot_g
        cs.append(_crit("幣種分散", top_s <= MAX_SYMBOL_SHARE,
                        f"最大單幣貢獻 {top_s:.1%}(上限 "
                        f"{MAX_SYMBOL_SHARE:.0%})"))
        tot_m = sum(months.values())
        top_m = max(months.values()) / tot_m if tot_m > 0 else 1.0
        cs.append(_crit("時間分散", top_m <= MAX_MONTH_SHARE,
                        f"最大單月貢獻 {top_m:.1%}(上限 "
                        f"{MAX_MONTH_SHARE:.0%})"))

    # ── 持續性:連續 N 個完整月正報酬 ────────────────────────
    by_month: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_month[str(r.get("signal_day", ""))[:7]].append(
            float(r.get("return_pct") or 0))
    streak = best = 0
    for mk in sorted(by_month):
        vals = by_month[mk]
        if len(vals) < 18:            # 不完整的月份不算(交易日不足)
            continue
        gain = vals[-1] - vals[0]
        streak = streak + 1 if gain > 0 else 0
        best = max(best, streak)
    cs.append(_crit("持續性", best >= MIN_POSITIVE_MONTHS,
                    f"最長連續正報酬 {best} 個完整月(需 "
                    f"{MIN_POSITIVE_MONTHS})"))

    passed = sum(1 for c in cs if c["passed"])
    out = {
        "updated": datetime.now().astimezone().isoformat(),
        "passed": passed, "total": len(cs), "criteria": cs,
        "thresholds_registered_at": "2026-09-06",
        "verdict": (
            f"{len(cs) - passed} 條未達成 —— 還沒到。"
            "不列『接近了』之類的描述,那正是讓人提前上場的東西。"
            if passed < len(cs) else
            "八條全過 —— 有資格談實盤。仍需人工簽署,永不自動化。"),
    }
    try:
        write_json_atomic(OUT, out)
    except Exception as e:
        log.warning(f"契約寫入失敗:{e}")
    return out


def main() -> int:
    s = evaluate()
    print(f"=== 實盤資格契約 {s['passed']}/{s['total']} ===\n")
    for c in s["criteria"]:
        print(f"  {'✓' if c['passed'] else '✗'} {c['name']}:{c['detail']}"
              + (f"  [{c['progress']}]" if c.get("progress") else ""))
    print(f"\n{s['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
