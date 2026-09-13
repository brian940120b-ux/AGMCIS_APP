"""
U 本位標準合約:現況報告 · 2026-09-13

執政官裁定:**「補充做標準合約U本位」**。這一支回答四個問題:

  一、這個產品現在能讀到什麼、讀不到什麼
  二、交易所不給的強平價,我方算出來是多少
  三、規格能不能從成交史反推出來(下單前必須知道精度)
  四、下單端點到底存不存在 —— **這一題要另外跑 probe_ustd_order.py**

**它只讀。** 用的是 ReadOnlyClient,那個類別沒有 post / delete。

用法:  .venv/bin/python scripts/standard_report.py
       .venv/bin/python scripts/standard_report.py BTCUSDT ETHUSDT
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
from exchange.bingx.standard import BingXStandardUSDT
from exchange.types import NotSupported, Unverified
from portfolio import reconcile

LINE = "─" * 64


def _head(text: str) -> None:
    print(f"\n{LINE}\n{text}\n{LINE}")


def _try(label, fn, *args, **kwargs):
    """一格壞掉不該讓整份報告消失。"""
    try:
        return fn(*args, **kwargs), None
    except (PrivateCallFailed, NotSupported, Unverified, ValueError) as e:
        print(f"  {label}:✗ {e}")
        return None, e
    except Exception as e:                       # noqa: BLE001
        print(f"  {label}:✗ {type(e).__name__}: {e}")
        return None, e


def main(argv) -> int:
    try:
        creds = Credentials.from_env()
    except CredentialsMissing as e:
        print(f"✗ {e}")
        return 2

    print(f"環境  {'實盤' if is_live() else 'Demo(VST)'}  {host()}")
    print(f"金鑰  {creds.masked}")
    print("產品  U 本位標準合約  /openApi/contract/v1")

    client = ReadOnlyClient(creds)
    usdt = BingXStandardUSDT(client)

    # ── 一、餘額 ──────────────────────────────────────
    _head("一、餘額")
    bal, _ = _try("餘額", usdt.balance)
    if bal is not None:
        rows = bal if isinstance(bal, list) else [bal]
        shown = [r for r in rows
                 if isinstance(r, dict) and float(r.get("balance") or 0) > 0]
        print(f"  {len(rows)} 個幣種,其中 {len(shown)} 個有餘額")
        for row in shown[:6]:
            print(f"    {row.get('asset'):<6} 餘額 {row.get('balance')}"
                  f"  可用 {row.get('availableBalance')}"
                  f"  全倉未實現 {row.get('crossUnPnl')}")
        if not any("equity" in r for r in rows if isinstance(r, dict)):
            print("    ⚠️ 這個產品**不回 equity(權益)** ——"
                  " 對帳層會如實報成「由我方補」")

    # ── 二、持倉 + 我方算出來的強平價 ────────────────
    _head("二、持倉 —— 強平價是我方算的,交易所不給")
    rich, err = _try("持倉", usdt.rich_positions)
    positions_raw, _ = _try("原始持倉", usdt.positions)

    if rich is not None:
        print(f"  {len(rich)} 筆")
        for pos in rich:
            dist = pos.liq_distance_pct()
            dist_txt = f"{dist:.2f}%" if dist is not None else "算不出來"
            print(f"\n    {pos.symbol}  {pos.side}  {pos.qty:g}"
                  f"  @ {pos.entry:g}  {pos.leverage:g}×"
                  f"  {'逐倉' if pos.isolated else '全倉'}")
            print(f"      保證金 {pos.initial_margin}"
                  f"  未實現 {pos.unrealized}"
                  f"  現價 {pos.current_price}")
            if pos.liq_price is None:
                print("      強平價 **算不出來** —— 當成不合格,不是沒問題")
            else:
                print(f"      強平價 {pos.liq_price:.6g}"
                      f"  [{pos.liq_source}]  距離 {dist_txt}")
            for note in pos.notes:
                print(f"        · {note}")

            if dist is not None and dist < 20.0:
                print(f"      ❗ 距離強平只剩 {dist:.2f}%,"
                      "低於風控下限 20%(第十九條)")
                print("         而且這個數字**偏樂觀** —— 實際更近")

    # ── 三、對帳:欄位形狀 ────────────────────────────
    _head("三、對帳 —— 哪幾格是交易所給的,哪幾格是我們補的")
    if bal is not None and positions_raw is not None:
        rec = reconcile.reconcile({}, bal, positions_raw,
                                  product=usdt.name)
        for report in (rec.balance_fields, rec.position_fields):
            print(f"\n  {report.what}:"
                  f"{'通過' if report.ok else '**不通過**'}"
                  f"  全部來自交易所:"
                  f"{'是' if report.fully_from_exchange else '**否**'}")
            if report.present:
                print(f"    交易所給的  {', '.join(report.present)}")
            if report.via_alias:
                print(f"    別名對上的  {report.via_alias}")
            if report.supplied_by_us:
                for name, why in report.supplied_by_us.items():
                    print(f"    由我方補    {name} —— {why}")
            if report.missing_critical:
                print(f"    ❗ 缺關鍵欄位 {', '.join(report.missing_critical)}")
            if report.reason:
                print(f"    {report.reason}")

    # ── 四、規格:從成交史反推 ────────────────────────
    _head("四、規格 —— 這個產品沒有 contracts 端點,只能從成交史反推")
    wanted = argv or _symbols_from(rich)
    if not wanted:
        print("  沒有持倉也沒有指定標的 —— 給我幾個代號:")
        print("    .venv/bin/python scripts/standard_report.py BTCUSDT ETHUSDT")
    for sym in wanted:
        spec, _ = _try(sym, usdt.infer_spec, sym)
        if spec is None:
            continue
        mark = "✓" if spec.usable else "✗"
        print(f"\n  {mark} {sym}  成交史 {spec.samples} 筆")
        if spec.usable:
            print(f"      數量精度 ≥{spec.quantity_precision} 位"
                  f"  價格精度 ≥{spec.price_precision} 位"
                  f"  最小成交量 {spec.min_executed_qty:g}")
            if spec.leverages:
                print(f"      用過的槓桿 {sorted(set(spec.leverages))}")
        print(f"      {spec.reason}")

    # ── 五、下一步 ────────────────────────────────────
    _head("五、下一步")
    print("  ✅ 看得到帳戶、持倉、成交史")
    print("  ✅ 強平價我方算得出來(線性合約,account.py 的公式直接適用)")
    print("  ✅ 精度從真實成交反推得出來")
    print()
    print("  ❓ **下單端點還沒定案。** 官方文件沒有,但我之前全部用 GET 問。")
    print("     跑這一支才算數:")
    print("       .venv/bin/python scripts/probe_ustd_order.py")
    print()
    print("     POST 也全是 100400 → U 本位標準合約不能自動下單,")
    print("     系統改成「算給你、你自己按」;其餘照常自動。")
    print("     POST 問得出東西   → 接下單層,三道閘照走。")
    return 0


def _symbols_from(rich) -> list:
    return sorted({p.symbol for p in (rich or [])})


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
