from database_service import get_open_trades
from market_data import get_price
from notifier import send_telegram

ALERT_PERCENT = 3
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
