from database_service import (
    get_account,
    get_trades,
    get_open_trades,
    get_closed_trades
)

print()
print("========== AGMCIS V13.3 Database Service Test ==========")

account = get_account()
trades = get_trades()
open_trades = get_open_trades()
closed_trades = get_closed_trades()

print()
print("Account:")
print(account)

print()
print(f"全部交易數：{len(trades)}")
print(f"目前持倉數：{len(open_trades)}")
print(f"已平倉數：{len(closed_trades)}")

print()
print("最近 5 筆交易：")

for trade in trades[-5:]:
    print(trade)

print()
print("=======================================================")