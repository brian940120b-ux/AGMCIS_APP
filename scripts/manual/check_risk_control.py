from risk_control import get_risk_control_status

risk = get_risk_control_status()

print()
print("========== AGMCIS V12.7 Risk Control ==========")

print(f"系統狀態：{risk['system_status']}")
print(f"允許新開倉：{risk['allow_new_trade']}")
print(f"緊急停止：{risk['emergency_stop']}")

print()
print(f"最大回撤：{risk['max_drawdown']}%")
print(f"曝險比例：{risk['exposure_ratio']}%")
print(f"目前持倉：{risk['open_positions']}")
print(f"Profit Factor：{risk['profit_factor']}")

print()
print("風控警示：")

for alert in risk["alerts"]:
    print()
    print(f"[{alert['level']}] {alert['title']}")
    print(alert["message"])

print()
print("===============================================")