import time
from datetime import datetime

from opportunity_scanner import scan_opportunities


INTERVAL_SECONDS = 30 * 60


def main():
    print()
    print("========== AGMCIS V16.1 Opportunity Runner ==========")
    print("每 30 分鐘自動掃描一次市場")
    print("有符合條件的機會才會推送 Telegram")
    print("按 Ctrl + C 可停止")
    print("====================================================")

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print()
        print(f"掃描時間：{now}")

        scan_opportunities()

        print("等待 30 分鐘後再次掃描...")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

