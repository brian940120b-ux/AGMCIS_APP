"""
SMC 獵場 —— 把 IG 上那套四步驟流程,當成假說跑一遍 · 2026-09-25

執政官傳來 @1336cryptoclub 的貼文(帳號自標「AI 內容」,文案
「用 SMC 搞定一天的餐錢」),要求:「不管用任何指標、網路上的資訊
還是什麼,你就是要幫我提高盈利的機率。」

處理方式與前面那 13 個假說**一模一樣**:先寫死翻譯與驗收條件、
先提交,然後才准跑第一次。翻譯表在 portfolio/smc.py 的 docstring,
跑完不准改。

═══ 預先登記的階梯,剛好四套(2026-09-25 寫死)═══
  L1  原版做多      結構 15m · 進場 5m  · 目標 前一日高
  L2  原版加鏡像    同上,另外接受向下 CHOCH 做空
  L3  慢一級做多    結構 4h  · 進場 1h  · 目標 前一週高
  L4  慢一級加鏡像  同上,雙向

L3 / L4 存在的唯一理由是**樣本**:交易所給的 5m 只有約 33 天,
15m 約 100 天,而 4h 有 333 天。一個月的樣本量得到的任何結論都
只是「這個月」,不是「這套方法」。L3 / L4 的時間框比例與貼文
不完全相同(貼文是 4H:15M:5M),這一點我寫在這裡,不假裝一樣。

═══ 幣種數量:30,在跑之前決定,理由是**樣本量**不是結果 ═══
2026-09-25 用合成資料乾跑了一遍管線,三個幣只生出 14~33 筆交易。
原因不是 bug,是這套流程本來就稀有:CHOCH + 訂單塊 + 失衡區 +
吞沒 K + 同一天內 + 目標還沒被穿過,六個條件要同時成立。

七個幣撐不到 100 筆的門檻,那樣跑出來的結論只會是「樣本不足」——
跑了等於沒跑。所以幣種擴到**成交額前 30**。

⚠️ 這個決定是在**看到任何真實結果之前**做的,而且理由是統計檢定力
(樣本不足就檢定不出任何東西),不是「換一批幣看看會不會比較好看」。
兩者的差別就是預先登記的全部意義。跑完之後**不准**再動這個數字。

═══ 驗收條件(寫死,跑完不准改)═══
  一、樣本 >= 100 筆。不足就只報樣本數,**不報結論**
  二、扣成本後平均 R > 0
  三、平均 R 要贏過**隨機對照組的第 95 百分位**
      ← 這關最重要:固定 R:R 拉大的話,亂進場也會有漂亮的盈虧比。
        策略要贏的是同樣停損 / 止盈距離、隨機時點的那一組,不是贏「零」
  四、前半段與後半段的平均 R **都**要 > 0(只贏一半 = 樣本運氣)
  五、自助法 p,乘上試過的階梯數(Bonferroni ×4),要 < 0.05
  六、固定 1% 風險的權益曲線,最大回撤 <= 合約上限 15%

沒過就蓋棺,**不換分形 k、不換時間框、不換幣種再試一次**。
換了再試就是舊系統那 1391 次(通過 15 案,純雜訊預期 52 案)。

═══ 這支不改任何設定 ═══
與 research.py / hunt.py 同一條:只產生證據,套用要執政官裁決(§102)。
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from market_data.history import load_or_download
from portfolio import research as R
from portfolio import smc
from portfolio.costs import round_trip_pct
from portfolio.event_sim import (RISK_PCT, Outcome, beats_control,
                                 bootstrap_positive, control, run, stats)
from portfolio.paper import SYMBOLS, screened_universe
from portfolio.sim import Bar

LINE = "═" * 66
MIN_TRADES = 100
MAX_DD_PCT = 15.0
#: 幣種數量。2026-09-25 在跑之前定死,理由見 docstring(樣本量)。
UNIVERSE_N = 30

#: 四套階梯:(代號, 說明, 結構週期, 進場週期, 目標週期, 是否雙向, 抓幾天)
LADDERS = [
    ("L1", "原版做多      15m/5m · 前一日高", "15m", "5m", "day", False, 120),
    ("L2", "原版雙向      15m/5m · 前一日高低", "15m", "5m", "day", True, 120),
    ("L3", "慢一級做多    4h/1h · 前一週高", "4h", "1h", "week", False, 400),
    ("L4", "慢一級雙向    4h/1h · 前一週高低", "4h", "1h", "week", True, 400),
]


def universe() -> tuple[list[str], str]:
    """成交額前 UNIVERSE_N 個。挑不到就退回主城名單,**而且說出來**。"""
    try:
        syms = screened_universe(limit=UNIVERSE_N)
    except Exception as e:                            # noqa: BLE001
        return list(SYMBOLS), f"篩選失敗({type(e).__name__}),退回主城名單"
    if not syms:
        return list(SYMBOLS), "篩選一個都沒挑到,退回主城名單"
    return syms, f"成交額前 {len(syms)}"


def bars_of(symbol: str, interval: str, days: int) -> list[Bar]:
    ks = load_or_download(symbol, days, interval=interval)
    return [Bar(k.open_time, k.open, k.high, k.low, k.close, k.volume)
            for k in ks]


def run_ladder(lad, symbols: list[str], verbose: bool) -> dict:
    code, label, si, ei, period, both, days = lad
    trades, skipped = [], {}
    # 對照組的 p95 逐幣算(每個幣的 K 棒不同),**按訊號數加權**彙總 ——
    # 直接取平均的話,只有 2 個訊號的幣會跟有 80 個訊號的幣一樣重。
    ctrl: list[tuple[float, int]] = []
    for sym in symbols:
        st = bars_of(sym, si, days)
        en = bars_of(sym, ei, days)
        if len(st) < 200 or len(en) < 200:
            skipped[sym] = f"資料不足 {si}={len(st)} {ei}={len(en)}"
            continue
        ss = smc.setups(st, en, symbol=sym, period=period, both_sides=both)
        if not ss:
            if verbose:
                print(f"      {sym:<14}{si}={len(st):>6} {ei}={len(en):>6}"
                      f"  訊號   0")
            continue
        o = run(en, ss)
        trades += o.trades
        c = control(en, ss, reps=60)
        if c.get("p95_r") is not None:
            ctrl.append((c["p95_r"], len(o.trades)))
        if verbose:
            print(f"      {sym:<14}{si}={len(st):>6} {ei}={len(en):>6}"
                  f"  訊號 {len(ss):>3}  成交 {len(o.trades):>3}")
    rs = [t.r for t in trades]
    w = sum(n for _, n in ctrl)
    return {"code": code, "label": label, "trades": trades, "rs": rs,
            "skipped": skipped,
            "ctrl_p95": (round(sum(v * n for v, n in ctrl) / w, 4)
                         if w else None)}


def equity_stats(rs: list[float]) -> tuple[float, float]:
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= (1 + r * RISK_PCT / 100.0)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return round(100.0 * (eq - 1.0), 2), round(100.0 * dd, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跑預先登記的 SMC 階梯")
    ap.add_argument("--verbose", action="store_true", help="逐幣印資料量")
    args = ap.parse_args(argv)

    syms, how = universe()
    print(f"\n{LINE}\n  SMC 獵場 · {len(LADDERS)} 套預先登記的階梯\n{LINE}\n")
    print(f"  來源      IG @1336cryptoclub(帳號自標「AI 內容」)。"
          f"貼文未附任何績效證據 ——")
    print(f"            只有一個範例的宣稱結果,沒有樣本數、期間,"
          f"也沒有賠錢的例子。")
    print(f"            而且「進場乾淨」是事後判斷的 —— 這裡的規則在"
          f"收盤那刻就決定,")
    print(f"            賺賠一律算進統計。**比貼文本身更嚴格。**")
    print(f"  翻譯表    portfolio/smc.py 的 docstring,"
          f"跑之前就提交,跑完不改")
    print(f"  幣種      {len(syms)} 個({how})")
    print(f"            {', '.join(syms)}")
    print(f"  ⏳ 這一趟要下載 {len(syms)} 幣 × 4 種週期的分鐘線,"
          f"限流 2 req/s —— 會跑一段時間(約 20~40 分鐘)。")
    print(f"  成本      一次進出 {round_trip_pct():.3f}%(走 costs.py,"
          f"不自己寫常數)")
    print(f"  驗收      樣本>=100 · 平均R>0 · 贏過隨機對照 p95 · 前後半都>0")
    print(f"            · Bonferroni ×{len(LADDERS)} 後 p<{R.ALPHA}"
          f" · 回撤<={MAX_DD_PCT}%")

    results = []
    for lad in LADDERS:
        code, label = lad[0], lad[1]
        print(f"\n  ── {code} {label} " + "─" * 20)
        try:
            res = run_ladder(lad, syms, args.verbose)
        except Exception as e:                        # noqa: BLE001
            print(f"     跑不起來:{type(e).__name__}: {e}")
            continue
        results.append(res)
        for sym, why in res["skipped"].items():
            print(f"     跳過 {sym}:{why}")
        n = len(res["rs"])
        if n == 0:
            print("     0 筆成交 —— 這個階梯在這批資料上沒有出現過訊號。")
            continue
        pct, dd = equity_stats(res["rs"])
        eq = [1.0]
        for r in res["rs"]:
            eq.append(eq[-1] * (1 + r * RISK_PCT / 100.0))
        st = stats(Outcome(trades=res["trades"], equity=eq))
        print(f"     {n} 筆 · 勝率 {st['win_pct']}% · 平均 "
              f"{st['expectancy_r']}R · 達標出場 {st['tp_pct']}%")
        print(f"     權益 {pct:+.2f}%(每筆風險 {RISK_PCT}%)· 回撤 {dd}%"
              f" · 隨機對照 p95 {res['ctrl_p95']}R")

    print(f"\n{LINE}\n  驗收\n{LINE}")
    passed = []
    for res in results:
        code, rs = res["code"], res["rs"]
        n = len(rs)
        if n < MIN_TRADES:
            print(f"\n  {code}  樣本 {n} 筆 < {MIN_TRADES} —— "
                  f"**只報樣本數,不報結論**。")
            print("       樣本不足時給出的任何勝率或報酬都是雜訊的形狀,"
                  "不是方法的形狀。")
            continue
        exp = statistics.fmean(rs)
        half = n // 2
        a, b = statistics.fmean(rs[:half]), statistics.fmean(rs[half:])
        p95 = res["ctrl_p95"]
        _, dd = equity_stats(rs)
        gates = [
            ("平均 R > 0", exp > 0, f"{exp:+.4f}R"),
            ("贏過隨機對照 p95", beats_control(exp, {"p95_r": p95}),
             f"{exp:+.4f} vs {p95}"),
            ("前半 > 0", a > 0, f"{a:+.4f}R"),
            ("後半 > 0", b > 0, f"{b:+.4f}R"),
            (f"回撤 <= {MAX_DD_PCT}%", dd <= MAX_DD_PCT, f"{dd}%"),
        ]
        print(f"\n  {code}  {n} 筆")
        for name, ok, val in gates:
            print(f"       {'✓' if ok else '✗'} {name:<22}{val}")
        if not all(g[1] for g in gates):
            continue
        p_raw = bootstrap_positive(rs)
        p_corr = R.bonferroni(p_raw, len(LADDERS))
        ok = p_corr is not None and p_corr < R.ALPHA
        print(f"       {'✓' if ok else '✗'} Bonferroni ×{len(LADDERS)}"
              f"        p={p_raw} → {p_corr}")
        if ok:
            passed.append((res, exp, p_corr))

    print(f"\n{LINE}")
    if not passed:
        print("\n  **沒有任何一套階梯通過。**")
        print("\n  這是結果,不是失敗 —— 它說的是:把那套流程原原本本")
        print("  翻譯成規則之後,在這批幣、這段期間,它沒有可測量的優勢。")
        print("\n  而這正是先寫死翻譯表的用處。沒有它,現在會有人開始")
        print("  改分形參數、改時間框、改幣種,直到某一套看起來贏為止 ——")
        print("  然後說「我只是把 SMC 定義得更正確」。那就是那 1391 次。")
        print("\n  ⚠️ 這**不代表** SMC 對人類交易者無效。它代表的是:")
        print("     這套流程裡讓它有效的東西,不在我寫得出來的規則裡。")
        print("     如果它靠的是看盤的人當下的判斷,那它就不能自動化 ——")
        print("     而不能自動化的東西,這個系統就跑不了。")
        return 0

    best = max(passed, key=lambda x: x[1])
    print(f"\n  ✓ {len(passed)} 套通過全部關卡。最好的是 "
          f"**{best[0]['code']} {best[0]['label']}**")
    print(f"    平均 {best[1]:+.4f}R · 校正後 p={best[2]}")
    print("\n  ⚠️ 通過不等於生效 —— 而且這是單筆賭注型策略,")
    print("     與現役的曝險型策略是兩種東西,不能直接替換。")
    print("     要不要讓它上場、用多少資金,是執政官的裁決(§102)。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
