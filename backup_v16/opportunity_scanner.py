from smart_ranking import get_smart_ranking
from risk_control import get_risk_control_status
from notifier import send_telegram


MIN_SCORE = 70
ALLOWED_SIGNALS = ["做多", "做空"]


def scan_opportunities():
    ranking = get_smart_ranking()
    risk = get_risk_control_status()

    if risk["emergency_stop"]:
        send_telegram(
            "🚨 AGMCIS 風控警報\n\n"
            "系統目前為 EMERGENCY_STOP\n"
            "暫停所有新機會推送。"
        )
        return

    if not risk["allow_new_trade"]:
        send_telegram(
            "⚠️ AGMCIS 風控限制\n\n"
            "目前風控不允許新開倉。\n"
            f"System Status: {risk['system_status']}"
        )
        return

    opportunities = []

    for item in ranking:
        symbol = item["symbol"]
        score = item["score"]
        signal = item["signal"]

        if score >= MIN_SCORE and signal in ALLOWED_SIGNALS:
            opportunities.append(item)

    if not opportunities:
        print("目前沒有符合條件的交易機會")
        return

    for item in opportunities[:3]:
        message = f"""
🚀 AGMCIS 機會偵測

幣種：{item["symbol"]}
訊號：{item["signal"]}
分數：{item["score"]}
價格：{item["price"]}

狀態：通過 Smart Ranking + Risk Control
提醒：目前僅為模擬訊號，不是真實下單。
"""
        send_telegram(message)

    print(f"已推送 {len(opportunities[:3])} 個機會")

