import time
from datetime import datetime

from auto_trader import run_auto_trader


INTERVAL_SECONDS = 300  # 300秒 = 5分鐘


def main():
    print()
    print("========== AGMCIS V11.2 自動排程器 ==========")
    print("系統會每 5 分鐘自動執行一次模擬交易掃描")
    print("若要停止，請按 Ctrl + C")
    print("===========================================")

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print()
        print(f"執行時間：{now}")

        try:
            run_auto_trader()

        except Exception as e:
            print(f"自動交易流程發生錯誤：{e}")

        print()
        print("等待 5 分鐘後再次執行...")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()