from database_service import get_account
from notifier import send_telegram

def send_daily_report():

    a = get_account()

    msg = f"""
📊 AGMCIS Daily Report

資金：{a.get('balance')} USDT
交易：{a.get('trades')}
勝場：{a.get('wins')}
敗場：{a.get('losses')}

Paper Trading
"""

    send_telegram(msg)

if __name__ == "__main__":
    send_daily_report()
