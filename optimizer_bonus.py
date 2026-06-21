import json

BONUS_FILE = "data/optimizer_bonus.json"


def get_optimizer_bonus(symbol):
    try:
        with open(BONUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data.get(symbol, 0)

    except Exception:
        return 0
