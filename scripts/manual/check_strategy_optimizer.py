from strategy_optimizer import get_strategy_optimizer

data = get_strategy_optimizer()

print()
print("========== AGMCIS V12.8 Strategy Optimizer ==========")

print()
print("Best Strategy By Symbol:")

for item in data["symbol_best"]:
    print(
        f"{item['symbol']} | "
        f"{item['strategy']} | "
        f"{item['return_pct']}%"
    )

print()
print("Strategy Summary:")

for item in data["strategy_summary"]:
    print(
        f"{item['strategy']} | "
        f"Avg Return: {item['avg_return']}% | "
        f"Tested: {item['tested_symbols']}"
    )

print()
print("Best Overall:")
print(
    f"{data['best_overall']['strategy']} | "
    f"{data['best_overall']['avg_return']}%"
)

print()
print("=====================================================")