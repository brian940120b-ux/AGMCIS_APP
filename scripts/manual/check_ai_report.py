from ai_report import generate_ai_report

report = generate_ai_report()

print()
print("========== AGMCIS AI Market Report ==========")

print(f"市場情緒：{report['sentiment']}")
print(f"市場分數：{report['score']}")
print(f"熱門標的：{report['best_symbol']}")

print()
print("AI 建議：")
print(report["recommendation"])

print()
print("重點新聞：")

for news in report["headlines"]:
    print("-", news)

print()
print("============================================")