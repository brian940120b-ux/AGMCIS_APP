from smart_ranking import get_smart_ranking

ranking = get_smart_ranking()

print()
print("========== AGMCIS Smart Ranking V18.8 ==========")

for index, item in enumerate(ranking[:20], start=1):
    print(
        f"{index}. {item['symbol']} | "
        f"Score={item['score']} | "
        f"Tech={item['technical_score']} | "
        f"Vol={item['volume_score']} | "
        f"News={item['news_impact']} | "
        f"Signal={item['signal']} | "
        f"Exchanges={item['exchanges']}"
    )

print()
print("Total Ranked:", len(ranking))
print("================================================")