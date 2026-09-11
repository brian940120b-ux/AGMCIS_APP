"""手動檢查:多標的 × 多策略的驗證掃描。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from strategy_optimizer import get_strategy_optimizer

data = get_strategy_optimizer()

print()
print("========== AGMCIS Strategy Validation ==========")

print()
print("每個標的的最佳策略(只列通過驗證的):")

for item in data["symbol_best"]:
    if not item.get("strategy"):
        reason = item.get("error") or (item.get("blockers") or ["未通過驗證"])[0]
        print(f"{item['symbol']:<14} | 無通過的策略 | {reason}")
        continue

    print(
        f"{item['symbol']:<14} | {item['strategy']:<20} | "
        f"{item['verdict']:<8} | "
        f"期望值 {item['oos_expectancy_r']} R | "
        f"{item['oos_trades']} 筆"
    )

print()
print("按策略彙總:")

for item in data["strategy_summary"]:
    print(
        f"{item['strategy']:<20} | 驗證 {item['evaluated']} | "
        f"通過 {item['passed']} | 勉強 {item['marginal']} | "
        f"拒絕 {item['rejected']} | 出錯 {item['errors']} | "
        f"平均 Health {item['avg_health']}"
    )

print()
best = data["best_overall"]
print("整體最佳:", f"{best['strategy']} @ {best['symbol']}" if best
      else "沒有任何策略通過驗證")

print()
print("建議組合:")
for line in data["ensemble"]["members"]:
    print(f"  {line['name']:<20} 權重 {line['weight']}  {line['verdict']}  {line['reason']}")

print()
for warning in data.get("warnings", []):
    print("⚠️ ", warning)

for error in data.get("errors", []):
    print("⛔ ", error["symbol"], error["error"])

print()
print("===============================================")
