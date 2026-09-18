"""
今天要按什麼 —— U 本位標準合約的下單指令單 · 2026-09-13

═══ 為什麼是「按」不是「送」═══
2026-09-13 定案:U 本位標準合約(/openApi/contract/v1)沒有下單 API。
GET 與 POST 問 order / trade/order 都回 100400,而**對照組**
(幣本位 cswap/v1/trade/order)POST 回 100004「只是缺權限」——
對照組問得出東西,所以那不是探測壞掉,是真的沒有。

於是整條鏈只有最後一吋是人做的:

    訊號 → 部位大小 → 風控閘 → 強平距離 → **指令單** → 人按 → 對帳
     自動    自動        自動      自動         自動       手動   自動

═══ 三個用法 ═══
    .venv/bin/python scripts/ticket.py
        算今天的單,印成可以照著按的指令單

    .venv/bin/python scripts/ticket.py --check
        再問一次現價,說每張單「現在還能不能按」

    .venv/bin/python scripts/ticket.py --verify
        按完之後跑這個 —— 拿交易所實際的持倉回頭比對,
        數量、方向、槓桿、保證金模式差一格就報出來

═══ 停損 25%:2026-09-13 執政官裁定 ═══
`--stop-pct` 現在有預設值了,而那個值有出處(§102):

  · 20% 是分水嶺 —— 從那裡開始,被掃出去的部位沒有一段最後是賺的
  · 選 25% 不選 20%,因為這是**災難後備**不是策略出場:
    20% 會在 5.0% 的持倉上觸發,25% 只在 1.9% 上
  · 上限那側:3× 的強平約 32.8%,而**那是偏樂觀的估計** ——
    25% 留 7.8pp 給誤差,30% 只留 2.8pp

依據見 `scripts/stop_evidence.py`,決定的全文見
`exchange/bingx/trade.BACKSTOP_DECISION`(含它的已知限制)。

`BACKSTOP_PCT` 若被改回 None,這一支會拒絕執行 ——
「沒有人決定停損擺哪裡」不該被一個看起來合理的預設值蓋掉。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from core.config import load_env
from portfolio import paper, ticket as tk
from portfolio.ticket import app_symbol, make_tickets  # noqa: F401

LINE = "═" * 50


def cmd_show(args) -> int:
    plan = paper.plan()
    if plan.get("error"):
        print(f"✗ 算不出今天的單:{plan['error']}")
        return 1

    print(f"訊號日 {plan['signal_day']}   成交日 {plan['exec_day']}")
    print(f"權益   {plan['equity']:.2f} USDT")
    if plan.get("already_done"):
        print("⚠️ 這個訊號日已經記過帳了 —— 下面這些可能已經執行過")

    made, refused = make_tickets(plan, args.stop_pct, args.leverage)

    if not made and not refused:
        print("\n今天沒有要動的部位。")
        return 0

    live = {}
    if args.check:
        from portfolio.live import prices
        live = prices(list({o.symbol for o in (plan.get("orders") or [])}))

    print(f"\n{LINE}\n  今天要按的:{len(made)} 張\n{LINE}")
    for t in made:
        print()
        print(t.render())
        if args.check:
            now_px = live.get(t.symbol.replace("USDT", "-USDT"))
            if now_px is None:
                print("  ?  問不到現價,無法判斷還能不能按")
            else:
                ok, why = t.executable(now_px)
                print(f"  {'✓' if ok else '✗'} 現價 {now_px:g} —— {why}")

    if refused:
        print(f"\n{LINE}\n  開不出來的:{len(refused)} 張\n{LINE}")
        for sym, why in refused:
            print(f"\n  {sym}\n    {why}")

    from portfolio.costs import standard_cost_caveat
    caveat = standard_cost_caveat()
    if caveat:
        print(f"\n{LINE}")
        print(f"  ⚠️ {caveat}")

    _show_catch_up(args, plan)

    print(f"\n{LINE}")
    print("  按完之後跑這個,確認有沒有按對:")
    print("    .venv/bin/python scripts/ticket.py --verify")
    print(LINE)
    return 0


def _show_catch_up(args, plan: dict) -> None:
    """對齊單 —— 讓真實帳戶追上模擬。

    上面那批是**鏡像**:模擬今天換手什麼就推什麼。但模擬幾天前就開好
    了倉,而真實帳戶可能是空的 —— 鏡像只鏡像「從現在開始的變動」。
    「今天沒有要按的」在那個狀態下是真話,**也是誤導**。
    """
    load_env()
    try:
        from exchange.bingx.standard import BingXStandardUSDT
        from portfolio.account import Account
        from portfolio.ticket import catch_up

        held = {sym: pos.position_amt
                for sym, pos in Account.load().positions.items()
                if abs(pos.position_amt) > 1e-12}
        made, refused, notes = catch_up(
            held, BingXStandardUSDT().rich_positions(),
            plan.get("prices") or {}, args.stop_pct, args.leverage,
            strategy=str(getattr(plan.get("cfg"), "strategy", "")),
            signal_day=str(plan.get("signal_day") or ""))
    except Exception as e:                       # noqa: BLE001
        print(f"\n{LINE}\n  對齊單\n{LINE}\n")
        print(f"  算不出來:{type(e).__name__}: {e}")
        print("  **這不代表兩邊一致**,只代表沒問到。")
        return

    if not made and not refused and not notes:
        print(f"\n  真實帳戶跟模擬對得上,沒有對齊單。")
        return

    print(f"\n{LINE}\n  對齊單 —— 讓真實帳戶追上模擬({len(made)} 張)"
          f"\n{LINE}")
    print("\n  這一批是**一次性**的:按完之後就交給日常的鏡像。")
    for t in made:
        print()
        print(t.render())
    for sym, why in refused:
        print(f"\n  {sym}\n    {why}")
    for note in notes:
        print(f"\n  · {note}")


def cmd_verify(args) -> int:
    load_env()
    from exchange.bingx.private import CredentialsMissing
    from exchange.bingx.standard import BingXStandardUSDT

    plan = paper.plan()
    if plan.get("error"):
        print(f"✗ 算不出今天的單:{plan['error']}")
        return 1

    made, _ = make_tickets(plan, args.stop_pct, args.leverage)
    if not made:
        print("今天沒有指令單可以比對。")
        return 0

    try:
        positions = BingXStandardUSDT().rich_positions()
    except CredentialsMissing as e:
        print(f"✗ {e}")
        return 2
    except Exception as e:                       # noqa: BLE001
        print(f"✗ 問不到持倉:{type(e).__name__}: {e}")
        return 1

    by_symbol = {p.symbol: p for p in positions}

    print(f"{LINE}\n  指令單 vs 交易所實際\n{LINE}")
    dirty = 0
    for t in made:
        ex = tk.verify(t, by_symbol.get(t.symbol))
        mark = "✓" if ex.clean else "✗"
        print(f"\n  {mark} {t.symbol}  單號 {t.ticket_id}")
        for line in ex.matches:
            print(f"      {line}")
        for line in ex.mismatches:
            print(f"      {line}")
        if ex.slippage_pct is not None:
            print(f"      滑價:{ex.slippage_pct:+.3f}%"
                  "(正 = 比報價吃虧)")
        if not ex.clean:
            dirty += 1

    # 交易所有、指令單沒有的倉 —— **不要當成沒事**。
    extra = sorted(set(by_symbol) - {t.symbol for t in made})
    if extra:
        print(f"\n  交易所還有這些倉,今天的指令單裡沒有:{', '.join(extra)}")
        print("    那不一定是錯的(可能是昨天留下的),但要看得見")

    print(f"\n{LINE}")
    if dirty:
        print(f"  ❗ {dirty} 張對不上。**只報告,不修正**(第十七條)——")
        print("     差異是一個訊號,不是一個待辦事項。先看清楚為什麼。")
    else:
        print("  全部對得上。")
    print(LINE)
    return 0 if not dirty else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="U 本位標準合約下單指令單")
    from exchange.bingx.trade import BACKSTOP_DECISION, BACKSTOP_PCT
    from portfolio.paper import LEVERAGE_CAP

    # ⚠️ help 字串會被 argparse 拿去做 `%` 格式化 —— 裡面的 `%`
    #    必須寫成 `%%`,否則 `--help` 會當場拋 ValueError。
    #    (2026-09-13 真的踩到:`25.0% ——` 的 `% —` 被當成格式符。)
    ap.add_argument("--stop-pct", type=float, default=BACKSTOP_PCT,
                    help=f"後備停損百分比。預設 {BACKSTOP_PCT}%% —— "
                         f"{BACKSTOP_DECISION['decided_by']} "
                         f"{BACKSTOP_DECISION['decided_on']} 裁定"
                         "(§102),證據見 scripts/stop_evidence.py")
    ap.add_argument("--leverage", type=float, default=LEVERAGE_CAP,
                    help=f"槓桿。預設 {LEVERAGE_CAP:g}× —— "
                         "2026-09-10 的 20× 真的爆了")
    ap.add_argument("--check", action="store_true",
                    help="再問一次現價,說每張單現在還能不能按")
    ap.add_argument("--verify", action="store_true",
                    help="按完之後比對交易所實際的持倉")
    args = ap.parse_args(argv)

    if args.stop_pct is None:
        ap.error("BACKSTOP_PCT 是 None(沒有人決定停損擺哪裡),"
                 "而這裡不會替你挑一個。\n"
                 "  先跑 scripts/stop_evidence.py 拿證據,"
                 "或明確傳 --stop-pct。")

    return cmd_verify(args) if args.verify else cmd_show(args)


if __name__ == "__main__":
    raise SystemExit(main())
