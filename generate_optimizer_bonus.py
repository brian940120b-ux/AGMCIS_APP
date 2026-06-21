import json
from strategy_optimizer import get_strategy_optimizer

OUTPUT = "data/optimizer_bonus.json"

def score_to_bonus(return_pct):
    if return_pct >= 10:
        return 10
    if return_pct >= 5:
        return 6
    if return_pct >= 2:
        return 3
    if return_pct >= 0:
        return 1
    if return_pct <= -5:
        return -10
    if return_pct < 0:
        return -5
    return 0

def main():
    result = get_strategy_optimizer()
    bonus = {}

    for item in result.get("symbol_best", []):
        symbol = item.get("symbol")
        strategy = item.get("strategy")
        return_pct = item.get("return_pct", 0)

        if not symbol:
            continue

        if strategy == "ERROR":
            bonus[symbol] = -10
        else:
            bonus[symbol] = score_to_bonus(return_pct)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(bonus, f, ensure_ascii=False, indent=2)

    print("Optimizer bonus generated:", OUTPUT)
    print(bonus)

if __name__ == "__main__":
    main()
