from portfolio_manager import get_portfolio_summary

portfolio = get_portfolio_summary()

print()
print("========== AGMCIS Portfolio Manager ==========")

print(f"帳戶資金：{portfolio['balance']} USDT")
print(f"目前持倉數：{portfolio['open_positions']}")
print(f"總曝險：{portfolio['total_exposure']} USDT")
print(f"曝險比例：{portfolio['exposure_ratio']}%")
print(f"風險等級：{portfolio['risk_level']}")

print()
print("持倉配置：")

for item in portfolio["allocation"]:
    print(
        f"{item['symbol']} | "
        f"{item['size_usdt']} USDT | "
        f"{item['percent']}%"
    )

print()
print("==============================================")