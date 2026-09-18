"""
研究迴路的跑者 —— 跑遍網格,產生提案 · 2026-09-13

執政官:「讓系統不斷地經過多次的模擬交易之後,發現該怎麼樣調整
會有利於後續的交易的盈利。」

這一支就是那個「不斷地試」。而它產生的是**提案,不是改動** ——
套用要另外一個動作,見 scripts/decide.py。

═══ 它會做什麼 ═══
一、拿現任參數與網格裡每一組跑同一段歷史
二、切成訓練段(前 2/3)與驗證段(後 1/3),**時序切,不是幣種切**
三、挑出驗證段 Calmar 最高的那個挑戰者
四、拿它跟現任做配對 block bootstrap,算 p 值
五、p 值乘上試過的次數(Bonferroni),再過五道關卡
六、全過才寫成提案;沒過也把原因印出來

═══ 它不會做什麼 ═══
**不改任何設定。** 一次都不會。

用法:  .venv/bin/python scripts/research.py
       .venv/bin/python scripts/research.py --dry   # 不寫檔,只印
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
from portfolio.paper import (LEVERAGE_CAP, STRATEGY, SYMBOLS,
                             VOL_LOOKBACK, VOL_TARGET_ANNUAL_PCT)
from portfolio.rules import donchian_breakout, ma_filter, vol_target
from portfolio.sim import align, metrics, simulate

LINE = "═" * 64
WARMUP = 205          # 夠 200 日均線 + 緩衝


def weights_fn(v: R.Variant):
    """把一組參數變成 simulate() 吃的權重函式。"""
    base = (donchian_breakout(SYMBOLS, v.ma, v.exit_n)
            if v.kind == "breakout" else ma_filter(SYMBOLS, v.ma))
    return vol_target(base, v.vol_target_pct, VOL_LOOKBACK, SYMBOLS,
                      v.leverage_cap)


def run(dates, idx, v: R.Variant):
    res = simulate(dates, idx, weights_fn(v), warmup=WARMUP)
    return res, metrics(res)


def daily_returns(res) -> list:
    eq = res.equity
    return [(eq[i] / eq[i - 1] - 1) for i in range(1, len(eq))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="研究迴路:跑網格、產生提案")
    ap.add_argument("--dry", action="store_true",
                    help="不寫檔,只印出來")
    args = ap.parse_args(argv)

    dates, idx = align(SYMBOLS)
    if not idx or len(dates) < WARMUP + 120:
        print("✗ 日線快取不足。先跑:.venv/bin/python scripts/daily.py")
        return 1

    cut = judge.split_index(len(dates))
    train_dates = dates[:cut]
    # 驗證段要帶暖身,否則前 200 天算不出均線
    test_dates = dates[max(0, cut - WARMUP - 5):]

    incumbent = R.Variant(ma=50, vol_target_pct=VOL_TARGET_ANNUAL_PCT,
                          leverage_cap=LEVERAGE_CAP)

    print(f"\n{LINE}\n  研究迴路 · {STRATEGY}\n{LINE}\n")
    print(f"  現任      {incumbent.describe()}")
    print(f"  樣本      {len(dates)} 天 · {len(SYMBOLS)} 幣")
    print(f"  訓練段    {train_dates[0].date()} ~ {train_dates[-1].date()}")
    print(f"  驗證段    {dates[cut].date()} ~ {dates[-1].date()}")
    print("  切分是**時序**不是幣種 —— 這條策略的風險是時間上的過擬合")

    inc_train_res, inc_train = run(train_dates, idx, incumbent)
    inc_test_res, inc_test = run(test_dates, idx, incumbent)
    print(f"\n  現任 Calmar   訓練 {inc_train.get('calmar')}"
          f"  驗證 {inc_test.get('calmar')}")

    challengers = R.grid(incumbent)
    print(f"\n{LINE}\n  試 {len(challengers)} 種變化\n{LINE}\n")

    rows = []
    for v in challengers:
        try:
            _, tr = run(train_dates, idx, v)
            te_res, te = run(test_dates, idx, v)
        except Exception as e:                   # noqa: BLE001
            print(f"  {v.key:<28} 跑不動:{type(e).__name__}")
            continue
        rows.append((v, tr, te, te_res))

    if not rows:
        print("  一個都沒跑成。")
        return 1

    # 排名照**驗證段** Calmar —— 拿訓練段排名就是在挑最會背答案的
    rows.sort(key=lambda r: (r[2].get("calmar") or -99), reverse=True)
    print(f"  {'參數':<30}{'訓練':>8}{'驗證':>8}{'回撤':>8}")
    for v, tr, te, _ in rows[:10]:
        print(f"  {v.key:<30}{tr.get('calmar') or 0:>8.2f}"
              f"{te.get('calmar') or 0:>8.2f}{te.get('max_dd_pct', 0):>7.1f}%")
    if len(rows) > 10:
        print(f"  …另外 {len(rows) - 10} 種")

    # ⚠️ 只印前十名的話,**一整族策略掛掉跟一整族表現差長得一樣**。
    #    2026-09-18 第一次跑,前十名全是 ma —— 而我在突破那支寫錯過
    #    三次,所以「bo 不在榜上」必須分得出是「跑輸」還是「沒跑」。
    print(f"\n  {'每一族最好的那一個':<30}{'訓練':>8}{'驗證':>8}{'回撤':>8}")
    for kind, label in (("ma", "均線"), ("breakout", "突破(支撐壓力)")):
        fam = [r for r in rows if r[0].kind == kind]
        if not fam:
            print(f"  {label:<28}**一個都沒跑出來** —— 那是 bug,不是結果")
            continue
        v, tr, te, _ = fam[0]
        print(f"  {label} {v.key:<22}{tr.get('calmar') or 0:>8.2f}"
              f"{te.get('calmar') or 0:>8.2f}{te.get('max_dd_pct', 0):>7.1f}%")
    print("\n  兩族量的是同一件事(均線是平滑版,突破是離散版),"
          "\n  而突破多兩個參數,所以要贏得更明顯才算數。")

    effective = R.effective_trials([(tr, te) for _, tr, te, _ in rows])
    if effective < len(rows):
        print(f"\n  ⚠️ {len(rows)} 種裡只有 {effective} 種**行為不同** —— "
              "其餘是同一個東西換個名字")
        print("     (實測:波動目標 15~35% 時總曝險碰不到槓桿上限,"
              "所以 lev2 / lev3 完全一樣)")
        print("     Bonferroni 乘的是**有效**次數,不是名目次數 —— "
              "乘名目等於無中生有地加嚴校正")

    best, best_tr, best_te, best_res = rows[0]
    # 檢定的是 **Calmar**,因為挑選的準則就是 Calmar。
    # 上一版檢定平均日報酬,於是一個靠降低回撤取勝的挑戰者永遠過不了
    # —— 用 A 挑、用 B 檢定的流程,檢定不到它挑的東西。
    p_raw = R.calmar_bootstrap(daily_returns(best_res),
                               daily_returns(inc_test_res))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    prop = R.judge_challenger(
        incumbent=incumbent, challenger=best, trials=effective,
        train=best_tr, test=best_te,
        incumbent_train=inc_train, incumbent_test=inc_test,
        p_raw=p_raw, as_of=now,
        max_dd_contract_pct=judge.MAX_DD_CONTRACT_PCT)

    print(f"\n{LINE}\n  最好的挑戰者:{best.describe()}\n{LINE}\n")
    for name, ok, detail in prop.checks:
        print(f"  {'✓' if ok else '✗'} {name}")
        print(f"      {detail}")

    print()
    if prop.passed:
        print("  → **通過全部關卡,成為提案。**")
        print(f"     提案編號 {prop.proposal_id}")
        print("     它**還沒有生效** —— 要生效請執政官裁決:")
        print(f"       .venv/bin/python scripts/decide.py {prop.proposal_id} "
              "--accept")
    else:
        print("  → 沒有通過,不成為提案。")
        print(f"     **這是好消息不是壞消息**:它代表現任參數"
              f"在這 {effective} 種變化裡站得住。")

    if args.dry:
        print("\n  --dry:沒有寫檔。\n")
        return 0

    R.save(R.merge(R.load(), [prop] if prop.passed else []))
    print(f"\n  已寫入 {R.STORE.relative_to(R.BASE)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
