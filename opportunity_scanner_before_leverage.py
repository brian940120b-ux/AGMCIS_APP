from smart_ranking import get_smart_ranking
from risk_control import get_risk_control_status
from notifier import send_telegram
from paper_trading import create_paper_trade, get_paper_summary


MIN_SCORE = 70
TOP_N = 5
MAX_OPEN_TRADES = 5
POSITION_SIZE_USDT = 1000
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

    summary = get_paper_summary()
    open_trades = summary.get("open_trades", [])

    if len(open_trades) >= MAX_OPEN_TRADES:
        print("Open trade limit reached. No new trades.")
        return

    opened_count = 0

    for item in ranking[:TOP_N]:
        symbol = item["symbol"]
        score = item["score"]
        signal = item["signal"]
        price = item["price"]

        if score < MIN_SCORE:
            continue

        if signal not in ALLOWED_SIGNALS:
            continue

        if signal == "做多":
            stoploss = round(float(price) * 0.97, 6)
            takeprofit = round(float(price) * 1.06, 6)
        elif signal == "做空":
            stoploss = round(float(price) * 1.03, 6)
            takeprofit = round(float(price) * 0.94, 6)
        else:
            continue

        result = create_paper_trade(symbol=symbol, entry_price=price, signal=signal, size_usdt=POSITION_SIZE_USDT, stoploss=stoploss, takeprofit=takeprofit)

        if not result.get("success"):
            print(f"Skip {symbol}: {result.get('message')}")
            continue

        opened_count += 1

        message = f"""
AGMCIS AUTO PAPER TRADE OPENED

Exchange Data: OKX + BingX
Rank Scope: Top {TOP_N}

Symbol: {symbol}
Signal: {signal}
Score: {score}
Entry: {price}
Size: {POSITION_SIZE_USDT} USDT

Stop Loss: {stoploss}
Take Profit: {takeprofit}

Status: Paper trade opened successfully.
Note: This is not a real order.
"""
        send_telegram(message)

        if len(open_trades) + opened_count >= MAX_OPEN_TRADES:
            break

    if opened_count == 0:
        print("No new paper trades opened.")
    else:
        print(f"Opened {opened_count} paper trades.")
