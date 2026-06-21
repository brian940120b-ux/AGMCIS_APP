from database_service import get_open_trades
from market_data import get_price
from notifier import send_telegram
from paper_trading import close_paper_trade

ALERT_PERCENT = 3
EMERGENCY_ROI = -15

def check_risk_alerts():

    trades = get_open_trades()

    for t in trades:

        symbol = t.get("symbol")

        stoploss = t.get("stoploss")

        if not stoploss:
            continue

        price = get_price(symbol)

        if not price:
            continue
        distance_sl = abs(
        (float(price) - float(stoploss))
        / float(price)
        * 100
        )
        entry = float(t.get("entry_price") or 0)
        signal = t.get("signal")

        if entry > 0:
            if signal == "做多":
                roi = (float(price) - entry) / entry * 100
            elif signal == "做空":
                roi = (entry - float(price)) / entry * 100
            else:
                roi = 0

            roi = round(roi * 3, 2)

            if roi <= EMERGENCY_ROI:
                result = close_paper_trade(
                    symbol,
                    price,
                    "V34 緊急熔斷平倉"
                )

                send_telegram(
                    f"""🛑 AGMCIS Emergency Close

幣種：{symbol}
方向：{signal}

進場：{entry}
現價：{price}

ROI：{roi}%

觸發條件：ROI 小於等於 {EMERGENCY_ROI}%
狀態：已送出強制平倉

結果：{result.get("message")}
"""
                )
        if distance_sl <= ALERT_PERCENT:

            send_telegram(
                f"""🚨 AGMCIS Risk Alert
            幣種：{symbol}

            現價：{price}
 
            停損：{stoploss}

            距離停損：
            {round(distance_sl,2)} %
            """
            )


if __name__ == "__main__":
    check_risk_alerts()
