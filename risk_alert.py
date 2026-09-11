"""
風險告警與 ROI 熔斷平倉。

Phase 0.5 的修正:
  1. ROI 改用每筆交易的真實槓桿(原本一律乘 3,槓桿是 8x 時 ROI 會被低估近三倍,
     熔斷門檻等於形同失效)。
  2. 取價失敗 / 方向無法辨識時明確記錄並跳過,不再靜默 continue。
  3. 熔斷門檻可由環境變數調整。
"""
import os

from database_service import get_open_trades
from direction import is_long, is_short
from logger_service import logger
from market_data import get_price
from notifier import send_telegram
from paper_trading import close_paper_trade

ALERT_DISTANCE_PCT = float(os.getenv("RISK_ALERT_DISTANCE_PCT", "3"))
EMERGENCY_ROI_PCT = float(os.getenv("RISK_EMERGENCY_ROI_PCT", "-15"))


def check_risk_alerts():
    for trade in get_open_trades():
        symbol = trade.get("symbol")
        signal = trade.get("signal")
        stoploss = trade.get("stoploss")
        entry = float(trade.get("entry_price") or 0)
        leverage = float(trade.get("leverage") or 1)

        price = get_price(symbol)

        if price is None:
            logger.warning("Risk Alert | NO_PRICE | %s | 本輪跳過", symbol)
            continue

        price = float(price)

        if entry <= 0:
            logger.error("Risk Alert | BAD_ENTRY | %s | entry=%s", symbol, entry)
            continue

        if is_long(signal):
            change = (price - entry) / entry
        elif is_short(signal):
            change = (entry - price) / entry
        else:
            logger.error("Risk Alert | UNKNOWN_DIRECTION | %s | %r", symbol, signal)
            continue

        roi = round(change * leverage * 100, 2)

        if roi <= EMERGENCY_ROI_PCT:
            result = close_paper_trade(symbol, price, "ROI 熔斷平倉")

            logger.warning(
                "Risk Alert | EMERGENCY_CLOSE | %s | roi=%s%% lev=%sx | %s",
                symbol, roi, leverage, result.get("message"),
            )

            send_telegram(
                f"""🛑 AGMCIS Emergency Close

幣種：{symbol}
方向：{signal}
槓桿：{leverage}x

進場：{entry}
現價：{price}
ROI：{roi}%

觸發條件：ROI ≤ {EMERGENCY_ROI_PCT}%
結果：{result.get("message")}
"""
            )
            continue

        if stoploss:
            distance_sl = abs((price - float(stoploss)) / price * 100)

            if distance_sl <= ALERT_DISTANCE_PCT:
                send_telegram(
                    f"""🚨 AGMCIS Risk Alert

幣種：{symbol}
現價：{price}
停損：{stoploss}
距離停損：{round(distance_sl, 2)}%
ROI：{roi}%
"""
                )
        else:
            logger.warning("Risk Alert | NO_STOPLOSS | %s | 該倉位沒有停損保護", symbol)


if __name__ == "__main__":
    check_risk_alerts()
