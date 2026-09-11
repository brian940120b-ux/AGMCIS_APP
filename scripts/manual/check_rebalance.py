from rebalance_engine import get_rebalance_recommendation

data = get_rebalance_recommendation()

print()
print("========== AGMCIS V12.6 Auto Rebalance ==========")

print("Top Symbols:")
for symbol in data["top_symbols"]:
    print("-", symbol)

print()
print("Rebalance Recommendations:")

for item in data["recommendations"]:
    print()
    print(f"[{item['type']}] {item['action']}")
    print(item["reason"])

print()
print("=================================================")