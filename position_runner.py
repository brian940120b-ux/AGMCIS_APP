import time
from datetime import datetime

from position_manager import manage_open_positions


INTERVAL_SECONDS = 60


def main():
    print("AGMCIS Position Manager Started")

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"Position check: {now}")

        manage_open_positions()

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
