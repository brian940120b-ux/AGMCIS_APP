from market_data import get_ohlcv
from strategy import analyze_symbol

from paper_trading import (
    create_paper_trade,
    close_paper_trade,
    get_all_open_trades,
    has_open_trade
)

from notifier import send_telegram

symbols = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT"
]


def get_latest_price(symbol):
    df = get_ohlcv(symbol)
    return float(df["close"].iloc[-1])


def manage_open_positions():
    open_trades = get_all_open_trades()

    if not open_trades:
        print("目前沒有持倉需要管理")
        return

    print()
    print("開始檢查目前持倉...")

    for trade in open_trades:
        symbol = trade["symbol"]
        signal = trade["signal"]
        stoploss = trade.get("stoploss")
        takeprofit = trade.get("takeprofit")

        try:
            price = get_latest_price(symbol)

            should_close = False
            close_reason = ""

            if signal == "做多":
                if stoploss is not None and price <= stoploss:
                    should_close = True
                    close_reason = "觸發停損"

                elif takeprofit is not None and price >= takeprofit:
                    should_close = True
                    close_reason = "觸發止盈"

            elif signal == "做空":
                if stoploss is not None and price >= stoploss:
                    should_close = True
                    close_reason = "觸發停損"

                elif takeprofit is not None and price <= takeprofit:
                    should_close = True
                    close_reason = "觸發止盈"

            print(
                f"{symbol} 現價={price} 停損={stoploss} 止盈={takeprofit}"
            )

            if should_close:
                result = close_paper_trade(
                    symbol=symbol,
                    exit_price=price,
                    close_reason=close_reason
                )

                if result["success"]:
                    closed = result["trade"]

                    send_telegram(
                        f"""
📕 AGMCIS 自動模擬平倉

幣種：{closed["symbol"]}
方向：{closed["signal"]}
原因：{closed["close_reason"]}

進場價：{closed["entry_price"]}
出場價：{closed["exit_price"]}

盈虧：{closed["pnl_usdt"]} USDT
報酬率：{closed["pnl_pct"]}%
"""
                    )

                    print(result["message"])

        except Exception as e:
            print(f"{symbol} 持倉管理失敗：{e}")


def scan_and_open_trade():
    results = []

    print()
    print("開始掃描市場...")

    for symbol in symbols:
        try:
            df = get_ohlcv(symbol)
            analysis = analyze_symbol(symbol, df)
            results.append(analysis)

            print(
                f"{symbol:<10} "
                f"{analysis['signal']:<4} "
                f"Score={analysis['score']}"
            )

        except Exception as e:
            print(f"{symbol} 掃描失敗：{e}")

    if not results:
        print("沒有取得任何市場資料")
        return

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    best = results[0]

    print()
    print(f"最佳標的：{best['symbol']}")
    print(f"訊號：{best['signal']}")
    print(f"分數：{best['score']}")

    if best["signal"] not in ["做多", "做空"]:
        print("最佳標的仍是觀望，不開倉")
        return

    if best["score"] < 75:
        print("分數未達 75，不開倉")
        return

    if has_open_trade(best["symbol"]):
        print("已有持倉，不建立新單")
        return

    result = create_paper_trade(
        symbol=best["symbol"],
        entry_price=best["entry"],
        signal=best["signal"],
        size_usdt=1000,
        stoploss=best["stoploss"],
        takeprofit=best["takeprofit"]
    )

    if result["success"]:
        trade = result["trade"]

        send_telegram(
            f"""
📘 AGMCIS 自動模擬開倉

幣種：{trade["symbol"]}
方向：{trade["signal"]}

進場價：{trade["entry_price"]}
停損價：{trade["stoploss"]}
止盈價：{trade["takeprofit"]}

倉位：{trade["size_usdt"]} USDT
"""
        )

        print(result["message"])
    else:
        print(result["message"])


def run_auto_trader():
    print()
    print("========== AGMCIS V11.1 ==========")

    manage_open_positions()
    scan_and_open_trade()

    print()
    print("自動模擬交易流程完成")
    print("=================================")


if __name__ == "__main__":
    run_auto_trader()