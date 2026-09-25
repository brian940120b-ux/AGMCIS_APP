"""
獵場 —— 把預先登記好、卻從來沒跑過的假說全部跑一遍 · 2026-09-20

執政官:「你就是要幫我做一個機器,去模擬交易、測試出可能會有盈利的
策略,不管用任何指標、網路上的資訊還是什麼,你就是要幫我提高盈利
的機率。」

═══ 唯一真的能提高機率的做法 ═══
**誠實地試更多假說,而且每一次都付多重比較的帳。**

不是「試到有一個看起來會賺」—— 那是舊系統做過的事:1391 個配置、
通過 15 案、而純雜訊預期就有 52 案。**它實際低於雜訊**,意思是
搜尋本身在製造假陽性。試得越多、結論越糟。

所以這一支不是「多掃一點參數」,它跑的是**預先寫下來、而且驗收
條件在跑之前就定死的假說**。目前共 18 個,分兩批:

第一批 · 13 個(2026-09-08 寫下,2026-09-20 跑完,**一個都沒過**)
  · indicator_rule    MACD / KDJ / RSI / MA / BOLL,各自「單獨當訊號」
                      與「當現役策略的濾網」兩種用法,加上多數決 = 12 個
                      參數一律用 BingX App 的預設值,**一個都不搜**
  · top_half          橫截面動量:站上均線的幣裡只留離均線最遠的一半
                      (離均線距離已用 7,911 個觀測驗證,t=3.23)

  它們的共同點是:程式碼都寫好了,而研究迴路從來沒試過它們。
  那是我 2026-09-19 反查死碼時抓到的。
  結果:沒有一個在驗證段贏過現役的 0.50。最接近的是 MACD 兩種用法,
  訓練段贏、回撤也最低(14.8~14.9%),但驗證段只有 0.25 / 0.40。

第二批 · 5 個(2026-09-25 寫下,驗收條件同一套)
  · B1 成交量確認   突破要不要 20 日均量以上的量能
  · B2 資金費率     多方在付錢(擁擠)的幣該不該避開
  · B3 相對強弱     N 日報酬跑輸 BTC 的幣該不該持有
  · B4 吊燈出場     出場改用 22 根 3 倍 ATR 的移動停損
  · B5 低波動期     只在自身波動低於歷史中位數時持有

  第二批刻意**不碰指標**:第一批那 12 個算到底都是同一串收盤價的
  不同排列,彼此高度相關,所以「13 個都沒過」其實比較接近
  「一個東西沒過」。第二批問的是五件不同的事,而 B2(資金費率)
  是目前唯一一個**與價格無關**的訊號來源。

⚠️ 假說數從 13 變成 18,**多重比較的分母跟著變成 18**,不是各自算
各自的。同一批資料、同一個決定,試過幾次就付幾次的帳 —— 分批算
就是在偷偷放寬門檻,而那正是最難被抓到的一種作弊。

═══ 驗收條件(2026-09-08 寫死,這裡一字不改)═══
  一、訓練段 Calmar 贏過現役
  二、驗證段 Calmar 也贏過現役  ← 只贏一段 = 樣本運氣
  三、全段最大回撤不得高於現役
  四、多重比較校正後 p < 0.05

**沒過就蓋棺,不換參數再試。** 換參數再試就是那 1391 次。

═══ 這支不改任何設定 ═══
跟 research.py 一樣:它只產生證據與提案,套用要執政官另外裁決。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from portfolio import judge, research as R
from portfolio.paper import (LEVERAGE_CAP, STRATEGY, SYMBOLS, VOL_LOOKBACK,
                             VOL_TARGET_ANNUAL_PCT)
from portfolio.rules import (chandelier, funding_filter, indicator_rule,
                             low_vol_only, ma_filter, relative_strength,
                             top_half_by_momentum_strength, volume_confirm,
                             vol_target)
from portfolio.sim import align, metrics, simulate

LINE = "═" * 64
WARMUP = 205

#: 指標,以及 BingX App 上的預設參數(**一個都不搜**)。
KINDS = [("macd", "MACD 12/26/9"), ("kdj", "KDJ 9/3/3"),
         ("rsi", "RSI 14"), ("ma", "MA 7/25"), ("boll", "BOLL 20/2"),
         ("vote", "五指標多數決")]


def funding_history() -> tuple[dict, list[str]]:
    """{幣: [(毫秒, 費率)]},以及**沒有資料的幣**。

    缺漏要回報出來,不能靜靜當成 0 —— B2 的結論如果建立在
    「沒資料就當費率 0」上面,那它量到的是缺漏不是費率。
    """
    from portfolio import specs
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    out, missing = {}, []
    for s in SYMBOLS:
        try:
            rows = specs.funding_settlements(s, 0, now)
        except Exception:                            # noqa: BLE001
            missing.append(s)
            continue
        if not rows:
            missing.append(s)
            continue
        out[s] = [(r["t"], r["rate"]) for r in rows]
    return out, missing


def hypotheses(funding: dict) -> list:
    """18 個預先登記的假說,回傳 (代號, 說明, **工廠**)。

    **清單在跑之前定死,跑完不准增刪。**

    回傳工廠而不是函式物件,是因為 B4(吊燈)有狀態 —— 它要記得
    持有期間的最高點。同一個物件先跑訓練段再跑驗證段,狀態會從
    前一段漏進來。工廠讓每一段各拿一個乾淨的。
    """
    base = lambda: ma_filter(SYMBOLS, 50)            # noqa: E731
    out = []
    for kind, label in KINDS:
        out.append((f"{kind}-單獨", f"{label} 單獨當訊號",
                    lambda k=kind: indicator_rule(SYMBOLS, k)))
        out.append((f"{kind}-濾網", f"{label} 當 50 日均線的濾網",
                    lambda k=kind: indicator_rule(SYMBOLS, k, mode="filter",
                                                  base_fn=base())))
    out.append(("動能前一半", "站上均線的幣裡只留離均線最遠的一半",
                lambda: top_half_by_momentum_strength(SYMBOLS, 50)))

    # ── 第二批 · 2026-09-25 ────────────────────────────
    out.append(("B1成交量", "當日量高於 20 日均量才持有",
                lambda: volume_confirm(base())))
    out.append(("B2資金費", "資金費率為正(多方付錢)的幣不持有",
                lambda: funding_filter(base(), funding)))
    out.append(("B3相對強弱", "50 日報酬跑輸 BTC 的幣不持有",
                lambda: relative_strength(base(), "BTC-USDT", 50)))
    out.append(("B4吊燈出場", "跌破 波段高 − 3×22日ATR 就出場",
                lambda: chandelier(base())))
    out.append(("B5低波動", "自身波動低於歷史中位數時才持有",
                lambda: low_vol_only(base())))
    return out


def wrap(fn):
    """套上跟現役完全相同的波動目標與槓桿上限。

    **這裡不動任何風控參數。** 要比的是訊號,不是規模 —— 兩邊規模
    不同的話,贏的那個可能只是賭比較大。
    """
    return vol_target(fn, VOL_TARGET_ANNUAL_PCT, VOL_LOOKBACK, SYMBOLS,
                      LEVERAGE_CAP)


def run(dates, idx, fn):
    res = simulate(dates, idx, wrap(fn), warmup=WARMUP)
    return res, metrics(res)


def rets(res) -> list:
    eq = res.equity
    return [(eq[i] / eq[i - 1] - 1) for i in range(1, len(eq))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="跑預先登記的指標與動量假說")
    ap.add_argument("--dry", action="store_true", help="不寫檔,只印")
    args = ap.parse_args(argv)

    dates, idx = align(SYMBOLS)
    if not idx or len(dates) < WARMUP + 120:
        print("✗ 日線快取不足。先跑:.venv/bin/python scripts/daily.py")
        return 1

    cut = judge.split_index(len(dates))
    train_dates = dates[:cut]
    test_dates = dates[max(0, cut - WARMUP - 5):]

    funding, missing = funding_history()
    hyps = hypotheses(funding)
    print(f"\n{LINE}\n  獵場 · {len(hyps)} 個預先登記的假說\n{LINE}\n")
    print(f"  現役      {STRATEGY}")
    print(f"  樣本      {len(dates)} 天 · {len(SYMBOLS)} 幣")
    print(f"  訓練段    {train_dates[0].date()} ~ {train_dates[-1].date()}")
    print(f"  驗證段    {dates[cut].date()} ~ {dates[-1].date()}")
    print("\n  參數一律用各自領域的通行預設值,**一個都不搜** ——")
    print("  搜參數就是舊系統那 1391 次(通過 15 案,雜訊預期 52 案)。")
    if missing:
        print(f"\n  ⚠️ B2 缺資金費率歷史的幣:{', '.join(missing)}")
        print("     這些幣在 B2 裡一律**不持有**(不假設費率是 0)。")
        print("     要補的話,在 VPS 上跑:")
        print("       .venv/bin/python -c 'from portfolio import specs, paper;"
              " specs.refresh_funding(paper.SYMBOLS)'")

    inc = ma_filter(SYMBOLS, 50)
    inc_tr_res, inc_tr = run(train_dates, idx, inc)
    inc_te_res, inc_te = run(test_dates, idx, inc)
    c_tr, c_te = inc_tr.get("calmar"), inc_te.get("calmar")
    dd_inc = max(inc_tr.get("max_dd_pct", 99), inc_te.get("max_dd_pct", 99))
    print(f"\n  現役 Calmar   訓練 {c_tr}  驗證 {c_te}"
          f"  最大回撤 {dd_inc:.1f}%")

    rows = []
    print(f"\n{LINE}\n  {'假說':<26}{'訓練':>8}{'驗證':>8}{'回撤':>8}\n{LINE}")
    for key, label, make in hyps:
        try:
            tr_res, tr = run(train_dates, idx, make())
            te_res, te = run(test_dates, idx, make())
        except Exception as e:                       # noqa: BLE001
            print(f"  {key:<26}跑不起來:{type(e).__name__}: {e}")
            continue
        dd = max(tr.get("max_dd_pct", 99), te.get("max_dd_pct", 99))
        rows.append((key, label, tr, te, dd, te_res))
        print(f"  {key:<26}{tr.get('calmar') or 0:>8.2f}"
              f"{te.get('calmar') or 0:>8.2f}{dd:>7.1f}%")

    if not rows:
        print("\n  ✗ 一個都沒跑出來 —— 那是 bug,不是結果。")
        return 1

    # ── 驗收:四關,2026-09-08 寫死 ────────────────────
    survivors = [r for r in rows
                 if (r[2].get("calmar") or -99) > (c_tr or -99)
                 and (r[3].get("calmar") or -99) > (c_te or -99)
                 and r[4] <= dd_inc]

    print(f"\n{LINE}\n  前三關(訓練贏 + 驗證贏 + 回撤不更差)\n{LINE}")
    if not survivors:
        print(f"\n  {len(rows)} 個假說,**一個都沒過**。")
        print("\n  這是**結果,不是失敗**:它說的是這些教科書指標在這批幣、")
        print("  這段期間,沒有一個比現役的 50 日均線更好。")
        print("\n  而這正是預先寫死驗收條件的用處 —— 沒有它,現在會有人")
        print("  開始挑參數、挑期間、挑幣種,直到某一個看起來贏為止。")
        print("  那就是舊系統那 1391 次。")
        return 0

    best = max(survivors, key=lambda r: r[3].get("calmar") or -99)
    key, label, tr, te, dd, te_res = best
    print(f"\n  {len(survivors)} 個通過前三關,最好的是 **{key}**")
    print(f"    {label}")
    print(f"    訓練 {tr.get('calmar')}  驗證 {te.get('calmar')}"
          f"  回撤 {dd:.1f}%(現役 {dd_inc:.1f}%)")

    # ── 第四關:多重比較校正 ──────────────────────────
    p_raw = R.calmar_bootstrap(rets(te_res), rets(inc_te_res))
    trials = len(rows)
    p_corr = R.bonferroni(p_raw, trials)
    print(f"\n{LINE}\n  第四關:多重比較校正\n{LINE}")
    print(f"\n  原始 p={p_raw} × 試過 {trials} 次 = {p_corr}")
    if p_corr is None or p_corr >= R.ALPHA:
        print(f"\n  ✗ 沒過(要 < {R.ALPHA})。")
        print(f"    試 {trials} 次,其中一次看起來贏幾乎是必然的 ——")
        print("    校正就是在付那筆帳。**不通過不是壞消息**,")
        print("    它代表現役策略在這 13 個假說裡站得住。")
        return 0

    print(f"\n  ✓ 通過(< {R.ALPHA})。**這是一個真的提案。**")
    print(f"    {label}")
    print("\n  ⚠️ 提案不等於生效 —— 套用要執政官裁決(§102)。")
    if args.dry:
        print("  (--dry:沒有寫檔)")
        return 0

    prop = R.judge_challenger(
        incumbent=R.Variant(ma=50, vol_target_pct=VOL_TARGET_ANNUAL_PCT,
                            leverage_cap=LEVERAGE_CAP),
        challenger=R.Variant(ma=50, vol_target_pct=VOL_TARGET_ANNUAL_PCT,
                             leverage_cap=LEVERAGE_CAP, kind=key),
        trials=trials, train=tr, test=te,
        incumbent_train=inc_tr, incumbent_test=inc_te,
        p_raw=p_raw,
        as_of=datetime.now(timezone.utc).isoformat())
    R.save(R.merge(R.load(), [prop]))
    print(f"  已寫入 {R.STORE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
