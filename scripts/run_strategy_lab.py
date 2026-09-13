"""
在**真實 BingX 歷史資料**上跑完整的 Strategy Lab。

    .venv/bin/python scripts/run_strategy_lab.py
    .venv/bin/python scripts/run_strategy_lab.py --symbols BTC/USDT,ETH/USDT
    .venv/bin/python scripts/run_strategy_lab.py --candles 1500 --timeframe 4h

## 為什麼需要這支腳本

ROADMAP 的第一項待辦是「**沒有策略通過完整驗證**」。那不是程式的問題 ——
Lab(Phase 8)早就寫好了,缺的是真實資料。

開發容器連不到 BingX(網路政策擋掉),所以這件事只能在 VPS 上做。
這支腳本把它變成一個指令。

## 它做什麼

對每個 (標的, 策略) 組合跑:

    IS 調參 → OOS 驗證 → Walk Forward → Monte Carlo → Health Score

然後輸出一份 JSON 報告,以及一份人看得懂的摘要。

**第一個評估的一定是 LIVE_PIPELINE** —— live 實際在用的訊號管線
(agmcis/strategy/builtin.py 的策略集成)。其餘是舊的模組式策略,
留著只是對照:live 不會用它們,LIVE SAFETY GATE 也只認 LIVE_PIPELINE。

## 它不做什麼

**它不會挑出「最好的」策略拿去交易。** 它只回報每個組合通過或沒通過,
以及沒通過的理由。挑選是 Ensemble 的事,而 Ensemble 只收 PASS 的。

⚠️ 這支腳本會打很多次行情 API(每個標的一次 K 棒,再乘上參數組合數
   在本地重跑)。它是**上線前跑一次**的東西,不是排程工作。

⚠️ 全程唯讀:不下單、不平倉、不改設定。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.config import settings  # noqa: E402

DEFAULT_OUTPUT = "data/strategy_lab_report.json"


def parse_args():
    parser = argparse.ArgumentParser(description="在真實資料上跑 Strategy Lab")
    parser.add_argument(
        "--symbols",
        help="逗號分隔。預設用 WATCHLIST_SYMBOLS。",
    )
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument(
        "--candles", type=int, default=5000,
        help="要抓幾根 K 棒。超過 1200 會自動分頁抓取。"
             "**樣本數是現在擋住驗證的真正原因** —— 1h 的 1500 根只有 62 天,"
             "樣本外交易筆數湊不到 30 筆門檻。",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-uncalibrated", action="store_true",
        help="合約規格沒校準也照跑。報告會蓋上 trustworthy=false。",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Monte Carlo 的隨機種子。給了才能重現同一份結果。",
    )
    return parser.parse_args()


def summarise(row):
    verdict = row.get("verdict")
    mark = {"PASS": "✅", "MARGINAL": "🟡", "REJECT": "⛔", "ERROR": "💥"}.get(
        verdict, "?",
    )
    live = "★" if row.get("is_live_pipeline") else " "

    line = (
        f"  {live}{mark} {row.get('symbol', '-'):<14} "
        f"{row.get('strategy') or '-':<20} {verdict:<9}"
    )

    expectancy = row.get("oos_expectancy_r")
    if expectancy is not None:
        line += f" 期望值 {expectancy:+.3f} R"

    trades = row.get("oos_trades")
    if trades is not None:
        line += f" / {trades} 筆"

    consistency = row.get("walk_forward_consistency")
    if consistency is not None:
        line += f" / WF {consistency * 100:.0f}%"

    return line


def main():
    args = parse_args()

    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else list(settings.WATCHLIST_SYMBOLS)
    )

    print("=" * 70)
    print("AGMCIS — Strategy Lab(真實資料)")
    print("=" * 70)
    print(f"標的     : {', '.join(symbols)}")
    print(f"時間框架 : {args.timeframe}")
    print(f"K 棒數   : {args.candles}")
    print(f"隨機種子 : {args.seed if args.seed is not None else '(未固定)'}")
    print()
    from agmcis.lab import preconditions

    quality = preconditions.require(symbols, args.allow_uncalibrated)
    if quality is None:
        return 1

    print("這會花一段時間 —— 每個標的都要抓 K 棒(超過 1200 根會分頁),")
    print("每個參數組合都要重跑回測。K 棒會快取到 data/history/,第二次會快很多。")
    print()

    from strategy_optimizer import SURVIVORSHIP_WARNING, get_strategy_optimizer

    result = get_strategy_optimizer(
        symbols=symbols,
        timeframe=args.timeframe,
        candles=args.candles,
        seed=args.seed,
    )

    rows = result.get("all_results", [])

    print("結果   (★ = live 實際在用的訊號管線)")
    print("-" * 70)
    for row in rows:
        print(summarise(row))
        for blocker in (row.get("blockers") or [])[:2]:
            print(f"        ⛔ {blocker}")
        if row.get("error"):
            print(f"        💥 {row['error']}")

    print()
    print("按策略彙總")
    print("-" * 70)
    for item in result.get("strategy_summary", []):
        print(
            f"  {item['strategy']:<20} 驗證 {item['evaluated']:>3} | "
            f"通過 {item['passed']:>3} | 勉強 {item['marginal']:>3} | "
            f"拒絕 {item['rejected']:>3} | 出錯 {item['errors']:>3} | "
            f"平均 Health {item['avg_health']}"
        )

    print()
    ensemble = result.get("ensemble", {})
    print("建議組合")
    print("-" * 70)
    if not ensemble.get("weights"):
        print("  (空的)沒有任何策略通過驗證。")
        print("  這是正確的結果,不是錯誤 —— 不要從被拒絕的裡面挑一個比較不差的。")
    else:
        for name, weight in ensemble["weights"].items():
            print(f"  {name:<20} 權重 {weight * 100:.1f}%")

    for warning in ensemble.get("warnings", []):
        print(f"  ⚠️  {warning}")

    print()
    for warning in result.get("warnings", []):
        print(f"⚠️  {warning}")
    for error in result.get("errors", []):
        print(f"⛔ {error['symbol']}:{error['error']}")

    # ---- 寫檔 ----
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "timeframe": args.timeframe,
        "candles": args.candles,
        "seed": args.seed,
        "survivorship_warning": SURVIVORSHIP_WARNING,
        preconditions.STAMP_KEY: quality.to_dict(),
        "result": result,
    }

    with open(output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)

    print()
    print(f"完整報告已寫入 {output}")

    live_passed = result.get("live_pipeline_passed") or []
    other_passed = [
        r for r in rows
        if r.get("verdict") == "PASS" and not r.get("is_live_pipeline")
    ]

    print("=" * 70)
    if not live_passed:
        print("**live 訊號管線沒有通過驗證。**")

        if other_passed:
            print()
            print(f"({len(other_passed)} 個舊策略通過了,但 live 不會用它們 ——")
            print(" 驗證一組永遠不會下單的策略等於沒有驗證。)")

        print()
        print("這通常不代表程式壞了。最常見的三個原因:")
        print("  1. 樣本外交易筆數不足 30 筆 —— 抓更多 K 棒或換更短的時間框架")
        print("  2. 樣本外期望值不為正 —— 這套訊號在這個標的上沒有優勢")
        print("  3. Walk Forward 一致性低 —— 只有某幾段時間有效,那是運氣")
        print()
        print("LIVE SAFETY GATE 的第 4 項條件因此不會通過,而那是正確的。")
        return 1

    print(f"live 訊號管線在 {len(live_passed)} 個標的上通過驗證。")
    print()
    print("⚠️  通過驗證**不等於**可以上線。LIVE SAFETY GATE 還有另外八項條件:")
    print("    .venv/bin/python scripts/live_gate.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
