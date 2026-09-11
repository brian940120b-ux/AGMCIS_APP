from paper_trading import (
    create_paper_trade,
    close_paper_trade,
    get_paper_summary
)

print()
print("========== AGMCIS V10.1 Paper Trading 測試 ==========")

print()
print("1. 建立 ETH 做多模擬單")
result = create_paper_trade(
    symbol="ETH/USDT",
    entry_price=2500,
    signal="做多",
    size_usdt=1000
)
print(result)

print()
print("2. 測試重複開倉")
result = create_paper_trade(
    symbol="ETH/USDT",
    entry_price=2510,
    signal="做多",
    size_usdt=1000
)
print(result)

print()
print("3. 平倉 ETH")
result = close_paper_trade(
    symbol="ETH/USDT",
    exit_price=2600
)
print(result)

print()
print("4. 查看模擬帳戶摘要")
summary = get_paper_summary()
print(summary)

print()
print("====================================================")