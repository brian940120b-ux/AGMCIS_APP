"""
新系統 — 監控層(系統的自我判別)· 2026-09-08

═══ 先講清楚這一層**不**做什麼 ═══
它不會自動調參數讓績效變好。

那正是殺死舊系統的東西:1391 個配置、十個積木全部負期望,
而多重檢定算出「實際通過 15 案 vs 雜訊預期 52 案」——
**實際低於雜訊**,意思是那個搜尋本身在製造假陽性,不是在找優勢。
一個會自己調參數直到好看的系統,只是把過擬合自動化。

═══ 這一層做的是專業系統真正在做的事:活體監控 ═══
策略上線之後唯一重要的問題是「它還是不是回測時的那條策略」。
回測給了明確的預期範圍:
    年化 23.1% · 最大回撤 17.4% · Sharpe 1.23 · 在場 82%
如果前向跑出來的行為跑到範圍外,那不是「運氣不好」,
是**這條策略的假設可能已經不成立** —— 而那件事必須有人講出來。

所以這裡量三件事,每一件都有預先登記的界線:

一、回撤突破(硬界線)
    前向最大回撤 > 回測最大回撤 × BREACH_MULT → 示警。
    回撤是最誠實的指標:它不需要等樣本量,踩到就是踩到。

二、曝險偏離
    前向在場比例與回測差太多 → 訊號的行為變了(例如市場長期在均線下)。
    這不一定是壞事,但必須被看見,否則「策略沒賺錢」會被誤讀成策略壞了,
    而真相可能是「這段期間它根本沒進場」。

三、對基準的相對表現
    絕對報酬會被行情主導。真正的問題永遠是「比躺著不動好嗎」。

═══ 樣本量紀律 ═══
少於 MIN_DAYS 天一律只報進度,不下任何判斷 ——
20 天的 Sharpe 是雜訊,拿它下結論跟擲骰子沒有差別。
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.atomic import write_json_atomic
from core.logging import get_logger

log = get_logger("portfolio.monitor")

BASE = Path(__file__).resolve().parents[1]
CURVE = BASE / "data" / "portfolio_equity.jsonl"
OUT = BASE / "data" / "portfolio_monitor.json"

# ── 回測基準線(portfolio/run.py 全段實測,2026-09-08 寫死)──────
# 這些數字是「策略在歷史上長什麼樣」。前向要跟它們比。
BACKTEST = {
    "cagr_pct": 23.1,
    "max_dd_pct": 17.4,
    "sharpe": 1.23,
    "days_in_market_pct": 82.0,
    "calmar": 1.33,
    "window": "2023-05-25 → 2026-09-06(3.3 年、1201 個交易日、7 幣)",
}
# ── 預先登記的示警界線:之後不得為了讓燈變綠而放寬 ──────────────
MIN_DAYS = 30                # 少於此只報進度
BREACH_MULT = 1.3            # 前向回撤 > 回測 × 此倍數 → 示警
EXPOSURE_TOL_PP = 25.0       # 在場比例偏離回測超過此百分點 → 示警
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


def analyze() -> dict:
    rows = _rows()
    n = len(rows)
    out: dict = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "backtest": BACKTEST,
        "days": n,
        "min_days": MIN_DAYS,
        "rule": {
            "breach_mult": BREACH_MULT,
            "exposure_tol_pp": EXPOSURE_TOL_PP,
            "registered_at": "2026-09-08",
            "note": "界線在上線前寫死;不得為了讓燈變綠而放寬",
        },
        "alerts": [],
    }
    if n == 0:
        out["verdict"] = "尚未記帳。"
        _save(out)
        return out

    eq = [float(r.get("return_pct") or 0) for r in rows]
    bm = [float(r.get("benchmark_pct") or 0) for r in rows]
    dd = max(float(r.get("drawdown_pct") or 0) for r in rows)
    expo = statistics.mean(float(r.get("exposure") or 0) for r in rows) * 100
    out.update({
        "return_pct": round(eq[-1], 3),
        "benchmark_pct": round(bm[-1], 3),
        "excess_pp": round(eq[-1] - bm[-1], 3),
        "max_dd_pct": round(dd, 3),
        "avg_exposure_pct": round(expo, 1),
    })

    # ── 一、回撤突破:硬界線,不等樣本量 ────────────────────
    limit = BACKTEST["max_dd_pct"] * BREACH_MULT
    if dd > limit:
        out["alerts"].append({
            "level": "HIGH", "kind": "回撤突破",
            "msg": (f"前向最大回撤 {dd:.1f}% 已超過回測 "
                    f"{BACKTEST['max_dd_pct']}% 的 {BREACH_MULT} 倍"
                    f"({limit:.1f}%)。這條策略的行為已在歷史範圍之外 —— "
                    "不是運氣不好,是它的假設可能不成立了。")})

    # ── 二、曝險偏離 ─────────────────────────────────────
    gap = expo - BACKTEST["days_in_market_pct"]
    if abs(gap) > EXPOSURE_TOL_PP:
        # 低曝險有兩個完全不同的原因,講錯會誤導判斷:
        #   (a) 訊號關閉 —— 幣大多在均線之下,策略根本沒進場
        #   (b) 波動目標 —— 幣都在均線之上,但波動高所以規模被調小
        # 前者是「市場沒機會」,後者是「風控在起作用」。
        # 用最後一天實際持有幾檔來分辨,不要用猜的。
        n_held = len(rows[-1].get("holdings") or [])
        cause = ""
        if gap < 0:
            cause = (f"最後一日持有 {n_held} 檔 —— "
                     + ("訊號大多關閉(幣在均線之下),"
                        "此時『沒賺錢』反映的是它沒進場,不是它壞了。"
                        if n_held <= 2 else
                        "訊號是開的,規模低是波動目標在起作用"
                        "(波動越高持有越少)—— 這是風控生效,不是沒機會。"))
        else:
            cause = "曝險高於歷史常態,回撤風險相應提高。"
        out["alerts"].append({
            "level": "INFO", "kind": "曝險偏離",
            "msg": (f"前向平均曝險 {expo:.0f}%,回測 "
                    f"{BACKTEST['days_in_market_pct']:.0f}%,"
                    f"差 {gap:+.0f} 個百分點。" + cause)})

    # ── 三、樣本量紀律 ───────────────────────────────────
    if n < MIN_DAYS:
        out["sufficient"] = False
        out["verdict"] = (
            f"前向 {n}/{MIN_DAYS} 天。以下是進度不是證據 —— "
            f"{n} 天的夏普值是雜訊,拿它下結論跟擲骰子沒有差別。"
            + ("但回撤示警不受樣本量限制:踩到就是踩到。"
               if any(a["level"] == "HIGH" for a in out["alerts"]) else ""))
        _save(out)
        return out

    rets = [(eq[i] - eq[i - 1]) / 100 for i in range(1, len(eq))]
    sd = statistics.pstdev(rets) if len(rets) > 2 else 0.0
    sharpe = (statistics.mean(rets) / sd * (365 ** 0.5)) if sd > 0 else 0.0
    out["sharpe"] = round(sharpe, 2)
    out["sufficient"] = True

    beat = out["excess_pp"] > 0
    in_env = not any(a["level"] == "HIGH" for a in out["alerts"])
    out["verdict"] = (
        ("行為在歷史範圍內" if in_env else "行為已超出歷史範圍")
        + (f",且勝過基準 {out['excess_pp']:+.2f}pp" if beat
           else f",但落後基準 {out['excess_pp']:+.2f}pp")
        + f"(前向 {n} 天)。"
        + ("" if in_env else
           "系統不會自己調參數把它救回來 —— 那是把過擬合自動化,"
           "也正是舊系統 1391 案全滅的原因。是否停用由執政官裁決。"))
    _save(out)
    return out


def _save(out: dict) -> None:
    try:
        write_json_atomic(OUT, out)
    except Exception as e:
        log.warning(f"監控寫入失敗:{e}")


def main() -> int:
    s = analyze()
    print(f"=== 策略監控 · 前向 {s['days']} 天 ===\n")
    print(f"  回測基準:年化 {BACKTEST['cagr_pct']}% · 回撤 "
          f"{BACKTEST['max_dd_pct']}% · Sharpe {BACKTEST['sharpe']} · "
          f"在場 {BACKTEST['days_in_market_pct']}%")
    print(f"  窗口:{BACKTEST['window']}\n")
    for k in ("return_pct", "benchmark_pct", "excess_pp", "max_dd_pct",
              "avg_exposure_pct", "sharpe"):
        if k in s:
            print(f"  {k}: {s[k]}")
    if s["alerts"]:
        print()
        for a in s["alerts"]:
            print(f"  [{a['level']}] {a['kind']}:{a['msg']}")
    print(f"\n{s['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
