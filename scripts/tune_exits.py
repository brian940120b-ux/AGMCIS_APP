"""
在**真實 BingX 歷史資料**上調校出場參數(Master Prompt 第五十六節)。

    .venv/bin/python scripts/tune_exits.py
    .venv/bin/python scripts/tune_exits.py --symbol BTC/USDT --candles 5000

## 為什麼需要這支腳本

第五十六節說 TP/SL 可以用 ATR、結構、波動度等等,然後加了一句
「**但必須由回測驗證**」。

`agmcis/execution/exit_plan.py` 現在的預設值(1R / 2R / 3R 分批
30/30/40、ATR 2.0 倍移動停損)是**起點不是結論** —— 它們沒有經過驗證,
因為驗證需要真實 K 棒,而開發容器連不到 BingX(網路政策擋掉)。

這支腳本把驗證變成一個指令。它只能在 VPS 上跑。

## 它做什麼

固定進場訊號,只換出場參數,每一組重跑一次回測:

    無移動停損 / 2% / 3% / 5%      ×      一次全平 / 1R+2R / 1R+2R+3R / 1.5R+3R

然後回報每一組的期望值 R、PF、樣本數,以及**最佳與中位數的差距**。

那個差距是這份報告最重要的數字。差距大代表參數面崎嶇,而崎嶇的
參數面上的最高點幾乎一定是過度擬合 —— 那時候正確的動作是**維持現狀**,
不是換成那個最高點。

## 它不做什麼

**它不會自己改預設值。** 第七十八節禁止「自己修改 → 自己測試 →
自己批准 → 自己 Live」。要改,把這份報告接到
`agmcis/review/proposals.py` 的提案流程,由人批准。

全程唯讀:不下單、不平倉、不改設定。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agmcis.config import settings  # noqa: E402

DEFAULT_OUTPUT = "data/exit_tuning_report.json"


def parse_args():
    parser = argparse.ArgumentParser(description="出場參數調校(第五十六節)")
    parser.add_argument(
        "--symbol", default=None,
        help="單一標的。預設用 WATCHLIST_SYMBOLS 的第一檔。",
    )
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument(
        "--candles", type=int, default=5000,
        help="要抓幾根 K 棒。樣本不足時每一組都會標成不可靠,"
             "而那份報告不能拿來下結論。",
    )
    parser.add_argument("--min-score", type=int, default=65,
                        help="進場訊號的分數門檻。整輪固定不變 —— "
                             "同時換進場與出場,結果好了也說不出是哪一邊的功勞。")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-uncalibrated", action="store_true",
        help="合約規格沒校準也照跑。報告會蓋上 trustworthy=false。",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    symbol = args.symbol or list(settings.WATCHLIST_SYMBOLS)[0]

    print("=" * 70)
    print("AGMCIS — 出場參數調校(第五十六節)")
    print("=" * 70)
    print(f"標的     : {symbol}")
    print(f"時間框架 : {args.timeframe}")
    print(f"K 棒數   : {args.candles}")
    print(f"進場門檻 : min_score={args.min_score}(整輪固定)")
    print()

    from agmcis.lab import preconditions

    quality = preconditions.require([symbol], args.allow_uncalibrated)
    if quality is None:
        return 1

    from agmcis.backtest.costs import DEFAULT_COSTS
    from agmcis.backtest.engine import BacktestEngine
    from agmcis.data import history
    from agmcis.lab import exit_tuning, pipeline_bridge

    fetched = history.fetch(symbol, timeframe=args.timeframe, total=args.candles)
    frame = history.to_frame(fetched)
    candles = fetched.candles

    print(f"拿到 {len(candles)} 根 K 棒。")
    print()

    signal_fn = pipeline_bridge.build_signal_fn(
        frame, params={"min_score": args.min_score},
        symbol=symbol, timeframe=args.timeframe,
    )

    def run_backtest(trailing, stages):
        engine = BacktestEngine(
            costs=DEFAULT_COSTS,
            trailing=trailing,
            take_profit_stages=list(stages) if stages else None,
        )
        return engine.run(
            candles, signal_fn, symbol=symbol,
            warmup=pipeline_bridge.WARMUP_BARS,
        )

    report = exit_tuning.run(run_backtest)

    print()
    for line in report.summary_lines():
        print(line)

    payload = report.to_dict()
    payload.update({
        "symbol": symbol,
        "timeframe": args.timeframe,
        "candles": len(candles),
        "min_score": args.min_score,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        preconditions.STAMP_KEY: quality.to_dict(),
    })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    print()
    print(f"報告寫到 {output}")
    print()
    print("⚠️  這份報告**不會自己套用**。要改 exit_plan.py 的預設值,")
    print("    走 agmcis/review/proposals.py 的提案流程,由人批准(第七十八節)。")

    return 0 if report.verdict != exit_tuning.VERDICT_INSUFFICIENT else 1


if __name__ == "__main__":
    sys.exit(main())
