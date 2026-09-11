"""
多幣種策略驗證報表(命令列用)。

Phase 8 重寫。舊版按總報酬率排名,而且是在同一段資料上算的報酬率 ——
排第一的就是最會擬合那段歷史的那個。

現在每個 (標的, 策略) 都走完整驗證流程,輸出的是**判定**而不是排名:
通過、勉強、拒絕,以及被拒絕的理由。

用法:
    python strategy_lab.py [SYMBOL ...]
"""
import sys

from agmcis.config import settings
from strategy_optimizer import SURVIVORSHIP_WARNING, evaluate_symbol

DEFAULT_SYMBOLS = list(settings.WATCHLIST_SYMBOLS)


def run_lab(symbols=None):
    symbols = list(symbols) if symbols else DEFAULT_SYMBOLS

    print()
    print("========== AGMCIS 策略驗證 ==========")

    for symbol in symbols:
        print()
        print(f"----- {symbol} -----")

        try:
            evaluations = evaluate_symbol(symbol)
        except Exception as exc:
            # 失敗就明說失敗。舊版把它記成 0% 報酬混進排名裡。
            print(f"⛔ 驗證失敗:{type(exc).__name__}: {exc}")
            continue

        for evaluation in evaluations:
            for line in evaluation.summary_lines():
                print(line)
            print()

    print("⚠️ ", SURVIVORSHIP_WARNING)
    print("=====================================")


if __name__ == "__main__":
    run_lab(sys.argv[1:] or None)
