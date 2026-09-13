"""
標準合約全景報告 —— 兩個產品並排 · 2026-09-13

═══ 這一支取代了什麼 ═══
probe_standard.py / probe_standard2.py / probe_standard3.py / dump_standard.py
是四支一次性的探路腳本,它們各問了一部分,而**沒有一支能回答
「所以要用哪一個」**。四份片段拼出來的印象,正是 2026-09-10
那個錯誤結論的溫床。

這一支把答案一次講完,而且**只讀**(ReadOnlyClient 沒有 post/delete)。

═══ 它會回答的四個問題 ═══
一、兩個標準合約產品各自能做什麼、不能做什麼
二、策略的七個幣在幣本位上存不存在
三、一張合約到底等於多少 USD(文件說的 vs 回推的)
四、下一步卡在誰身上 —— 交易所,還是我們

用法:  .venv/bin/python scripts/standard_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from exchange.bingx.private import (Credentials, CredentialsMissing,
                                    PrivateCallFailed, ReadOnlyClient,
                                    host, is_live)
from exchange.bingx.standard import (BingXStandardCoinM, BingXStandardUSDT,
                                     USDT_REASON)
from exchange.types import NotSupported, Unverified


LINE = "─" * 62


def _head(text: str) -> None:
    print(f"\n{LINE}\n{text}\n{LINE}")


def _try(label, fn, *args, **kwargs):
    """跑一個唯讀查詢。失敗就印出來繼續 —— 一格壞掉不該讓整份報告消失。"""
    try:
        return fn(*args, **kwargs), None
    except (PrivateCallFailed, NotSupported, Unverified) as e:
        print(f"  {label}: ✗ {e}")
        return None, e
    except Exception as e:                       # noqa: BLE001
        print(f"  {label}: ✗ {type(e).__name__}: {e}")
        return None, e


def main() -> int:
    try:
        creds = Credentials.from_env()
    except CredentialsMissing as e:
        print(f"✗ {e}")
        return 2

    print(f"環境  {'實盤' if is_live() else 'Demo(VST)'}  {host()}")
    print(f"金鑰  {creds.masked}")

    client = ReadOnlyClient(creds)
    usdt = BingXStandardUSDT(client)
    coinm = BingXStandardCoinM(client)

    # ── 一、U 本位標準合約 ────────────────────────────
    _head("一、U 本位標準合約  /openApi/contract/v1")
    print("  下單 API:**沒有**")
    print("  " + USDT_REASON.replace(" —— ", "\n    —— "))

    bal, _ = _try("餘額", usdt.balance)
    if bal is not None:
        rows = bal if isinstance(bal, list) else [bal]
        print(f"  餘額:{len(rows)} 個幣種")
        for row in rows[:3]:
            if isinstance(row, dict):
                asset = row.get("asset") or row.get("currency") or "?"
                print(f"    {asset}: 權益 {row.get('equity')}"
                      f" 餘額 {row.get('balance')}")

    pos, _ = _try("持倉", usdt.positions)
    if pos is not None:
        print(f"  持倉:{len(pos)} 筆")
        for row in pos:
            print(f"    {row.get('symbol')} {row.get('positionSide')}"
                  f" {row.get('positionAmt')} @ {row.get('entryPrice')}"
                  f" 槓桿 {row.get('leverage')}")
            if "liquidationPrice" not in row:
                print("      ⚠️ 這筆**沒有強平價** —— 風控的強平距離"
                      "下限對這個產品算不出來,不是 bug,是讀不到")

    # ── 二、幣本位標準合約 ────────────────────────────
    _head("二、幣本位標準合約  /openApi/cswap/v1  (= Coin-M perpetual)")
    print("  下單 API:**有** POST /openApi/cswap/v1/trade/order")
    print("  而且支援下單時附帶 stopLoss —— 進場與停損可以是同一個動作")

    specs, _ = _try("合約清單", coinm.contracts)
    if specs is None:
        print("\n  合約清單問不到,後面幾項無法進行。")
        return 1

    print(f"  可交易標的:{len(specs)} 檔")
    sample = specs.get("BTC-USD")
    if sample:
        print(f"    BTC-USD  價格精度 {sample.price_precision}"
              f"  最小張數 {sample.min_qty:g}"
              f"  最小名目 {sample.min_notional:g} USD")
        print(f"    費率  taker {sample.taker_fee_pct:.4g}%"
              f"  maker {sample.maker_fee_pct:.4g}%")
        print(f"    反向 {sample.inverse}  資金費 {sample.has_funding}"
              f"  到期 {sample.expiry}")

    # 策略的七個幣在不在
    from portfolio.paper import SYMBOLS
    print("\n  策略的七個幣在幣本位上:")
    missing = []
    for sym in SYMBOLS:
        want = sym.replace("-USDT", "-USD")
        if want in specs:
            print(f"    ✓ {sym:12} → {want}")
        else:
            print(f"    ✗ {sym:12} → {want}  **沒有這個標的**")
            missing.append(sym)

    fund, _ = _try("資金費", coinm.current_funding, "BTC-USD")
    if fund:
        print(f"\n  BTC-USD 當期資金費 {fund['lastFundingRate']}"
              f"  標記價 {fund['markPrice']}")
        print("    ← 「標準合約不收資金費」那句話就是在這裡被推翻的")

    cpos, _ = _try("持倉", coinm.positions)
    if cpos is not None:
        print(f"\n  幣本位持倉:{len(cpos)} 筆")
        for row in cpos:
            print(f"    {row.get('symbol')} {row.get('positionSide')}"
                  f" {row.get('positionAmt')} @ {row.get('avgPrice')}"
                  f" 強平 {row.get('liquidationPrice')}")

    # ── 三、一張是多少 ────────────────────────────────
    _head("三、一張合約等於多少 USD —— 文件與實測對不對得上")
    bad = 0
    for sym in ("BTC-USD", "ETH-USD", "SOL-USD"):
        if sym not in specs:
            continue
        got, _ = _try(sym, coinm.measure_contract_size, sym)
        if not got:
            continue
        mark = "✓" if got["agree"] else "✗"
        print(f"  {mark} {sym:9} 文件 {got['declared_by_doc']:g}"
              f"  回推 {got['measured_from_ticker']:g}")
        if not got["agree"]:
            bad += 1
            print(f"      {got['verdict']}")
    if bad:
        print(f"\n  ⚠️ {bad} 檔對不上。回推本身有假設"
              "(quoteVolume 以幣計價,文件未載明),")
        print("     所以這不是定論,是**一個必須在 Demo 上量出來的疑點**。")
        print("     真正的定案:開一張最小單,讀回 positionAmt 與 initialMargin。")

    # ── 四、下一步卡在誰身上 ──────────────────────────
    _head("四、下一步")
    print("  U 本位標準合約:**卡在交易所**。沒有下單端點,只能等。")
    print("    → 它的倉仍然會進對帳(第十七條),看得到但動不了。")
    print("\n  幣本位標準合約:**卡在我們**。三件事:")
    print("    1. 反向合約帳本 —— account.py / paper.py 目前只寫了正向")
    print("    2. 一張多少 USD —— 見上一節,要在 Demo 量")
    print("    3. 金鑰交易權限 —— 目前 code=100004,而開權限前 IP 白名單要先設")
    print("\n  三件都是我們自己能做的工作 —— 這跟「等交易所開放」")
    print("  是完全不同的處境,而 2026-09-10 把後者誤寫成了前者。")

    if missing:
        print(f"\n  另外:{len(missing)} 個策略幣在幣本位上不存在"
              f"({', '.join(missing)})。")
        print("    改用幣本位就等於**換掉交易池** —— 這是策略層的決定,")
        print("    不是介面層可以自己換掉的(第六條)。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
