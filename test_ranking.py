from smart_ranking import get_smart_ranking

ranking = get_smart_ranking()

print()
print("========== AGMCIS Smart Ranking ==========")

for index, item in enumerate(ranking, start=1):

    print(
        f"{index}. "
        f"{item['symbol']} | "
        f"Score={item['score']} | "
        f"{item['signal']}"
    )

print()
print("==========================================")