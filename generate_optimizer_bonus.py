"""
把驗證結果寫成每個標的的評分加權,給 smart_ranking 用。

⚠️ 這個檔案的輸出會**直接影響實際下單的評分**(smart_ranking 把它加進
最終分數),所以它的來源必須是驗證過的結果,不是回測報酬率。

Phase 8 之前:加權直接來自回測總報酬率。那條路徑等於
「在一段歷史上擬合得越好的標的,live 分數加越多」——
把過擬合原封不動送進實單。

現在:加權來自 Health Score 判定。
  PASS      -> 正加權(依 OOS 期望值分級)
  MARGINAL  -> 小幅正加權
  REJECT    -> 0,不是負的
  ERROR     -> 0

**沒驗過或驗證失敗一律給 0(中性)。** 給負分等於因為「我們沒能驗證它」
而懲罰一個標的,那不是它的問題;給正分則是憑空生出信心。
"""
import json
import logging
import os

from strategy_optimizer import get_strategy_optimizer

logger = logging.getLogger("agmcis.optimizer_bonus")

OUTPUT = "data/optimizer_bonus.json"

MAX_BONUS = 10
MARGINAL_BONUS = 2


def verdict_to_bonus(verdict, expectancy_r):
    """
    加權完全由驗證判定與 OOS 期望值決定。

    期望值 0.30 R 以上給滿分 10。用 R 倍數而不是報酬率,
    因為 R 與部位大小無關,不會因為某個標的波動大就自動拿高分。
    """
    if verdict == "PASS":
        if expectancy_r is None or expectancy_r <= 0:
            return 0
        return int(min(MAX_BONUS, round(expectancy_r / 0.30 * MAX_BONUS)))

    if verdict == "MARGINAL":
        if expectancy_r is None or expectancy_r <= 0:
            return 0
        return MARGINAL_BONUS

    # REJECT / ERROR / 其他:中性
    return 0


def build_bonus(result):
    bonus = {}

    for item in result.get("symbol_best", []):
        symbol = item.get("symbol")
        if not symbol:
            continue

        bonus[symbol] = verdict_to_bonus(
            item.get("verdict"), item.get("oos_expectancy_r"),
        )

    return bonus


def main(symbols=None, output=OUTPUT):
    result = get_strategy_optimizer(symbols=symbols)
    bonus = build_bonus(result)

    directory = os.path.dirname(output)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(output, "w", encoding="utf-8") as handle:
        json.dump(bonus, handle, ensure_ascii=False, indent=2)

    print("Optimizer bonus generated:", output)
    print(bonus)

    for warning in result.get("warnings", []):
        print("⚠️ ", warning)

    for error in result.get("errors", []):
        print("⛔ ", error["symbol"], error["error"])

    return bonus


if __name__ == "__main__":
    main()
