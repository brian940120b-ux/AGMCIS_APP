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
    .venv/bin/python scripts/ticket.py --stop-pct 8
        算今天的單,印成可以照著按的指令單

    .venv/bin/python scripts/ticket.py --stop-pct 8 --check
        再問一次現價,說每張單「現在還能不能按」

    .venv/bin/python scripts/ticket.py --verify
        按完之後跑這個 —— 拿交易所實際的持倉回頭比對,
        數量、方向、槓桿、保證金模式差一格就報出來

═══ --stop-pct 沒有預設值 ═══
第 102 條:Risk Limit 由執政官決定。一張沒有停損的單不該存在(§19),
所以這裡不會替你挑一個「看起來合理」的數字。

決定它需要證據:回測裡**均線出場真的觸發之前,單一部位最深的逆向
走勢是多少**?擺得比那個淺,就會在歷史上真的發生過的正常波動裡
被掃出去 —— 而那是另一條策略,Calmar 1.33 不是它的數字。
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

LINE = "═" * 50


def app_symbol(symbol: str) -> str:
    """`BTC-USDT` → `BTCUSDT`。

    App 與 allPosition 用的是無槓的寫法(實測 FLOCKUSDT),
    而策略內部用有槓的。轉換只做一次,放在這裡。
    """
    return symbol.replace("-", "")


def make_tickets(plan: dict, stop_pct: float, leverage: float) -> tuple:
    """把今日訂單變成指令單。**開不出來的那些不會消失,會被列出來。**"""
    made, refused = [], []
    for order in plan.get("orders") or []:
        is_close = getattr(order, "weight_to", 0.0) == 0.0
        action = (tk.CLOSE if is_close else
                  tk.OPEN_LONG if order.side == "BUY" else tk.OPEN_SHORT)
        try:
            made.append(tk.build(
                symbol=app_symbol(order.symbol),
                action=action,
                quantity=abs(order.qty),
                price=order.price,
                leverage=leverage,
                stop_pct=stop_pct,
                strategy=str(plan.get("cfg").strategy if plan.get("cfg")
                             else ""),
                signal_day=str(plan.get("signal_day") or ""),
            ))
        except tk.TicketRefused as e:
            refused.append((order.symbol, str(e)))
    return made, refused


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

    print(f"\n{LINE}")
    print("  按完之後跑這個,確認有沒有按對:")
    print("    .venv/bin/python scripts/ticket.py --verify")
    print(LINE)
    return 0


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
    ap.add_argument("--stop-pct", type=float, required=True,
                    help="後備停損百分比。第 102 條:由執政官決定,"
                         "這裡沒有預設值")
    ap.add_argument("--leverage", type=float, default=3.0,
                    help="槓桿。預設 3× —— 2026-09-10 的 20× 真的爆了")
    ap.add_argument("--check", action="store_true",
                    help="再問一次現價,說每張單現在還能不能按")
    ap.add_argument("--verify", action="store_true",
                    help="按完之後比對交易所實際的持倉")
    args = ap.parse_args(argv)

    return cmd_verify(args) if args.verify else cmd_show(args)


if __name__ == "__main__":
    raise SystemExit(main())
