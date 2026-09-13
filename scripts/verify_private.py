"""
證明「我看得到帳戶」—— 走向自動下單的第一步 · 2026-09-13

═══ 這支做什麼 ═══
拿一把**唯讀**金鑰,對 BingX 的私有端點做一次簽名查詢,
把結果印出來:簽章對不對、餘額多少、持倉幾檔。

**它不會下單。** 整個 `exchange/bingx/private.py` 裡沒有下單的程式碼
—— 不是被擋住,是根本沒寫。

═══ 為什麼下單之前要先做這一步 ═══
看不到帳戶就下單,等於閉著眼睛開槍。而這一步跑通,整條認證 / 簽章 /
解析的路也就跑通了 80%,全程沒有一毛錢的風險。

簽章規格是查來的,而**查過不等於對**。交易所說 yes 才算數,
這支就是去問它。

═══ 用法 ═══
    # 1. 到 BingX 建一把 API 金鑰
    #    權限:只勾「讀取」。**提款一律關閉**(第十條,沒有例外)。
    #    先用 Demo Trading 的金鑰。
    #
    # 2. 填進 .env(已 gitignore,不要放進任何原始碼):
    #      BINGX_API_KEY=...
    #      BINGX_API_SECRET=...
    #      BINGX_ENV=demo
    #
    # 3. 跑:
    .venv/bin/python scripts/verify_private.py

預設連 Demo(VST 虛擬資金)。要連實盤得把 BINGX_ENV 改成 live,
而且**即使連上實盤,這支能做的也只有讀**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

# 用錯直譯器的時候講人話,而不是丟一個 ModuleNotFoundError 讓人猜。
interpreter.require()

from core.config import load_env
from exchange.bingx import private


def main() -> int:
    load_env()

    try:
        creds = private.Credentials.from_env()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    live = private.is_live()
    if live:
        # 連實盤是一個要被看見的決定,不是一個預設值。
        print("\n" + "=" * 58)
        print("⚠️  BINGX_ENV=live —— 這把金鑰指向**真實帳戶**。")
        print("    這支腳本只會讀,不會下單。但請再確認一次:")
        print("    這把金鑰的**提款權限是關閉的**(第十條)。")
        print("=" * 58)

    client = private.ReadOnlyClient(creds)
    report = client.verify()

    print()
    print("═" * 58)
    print(f"  環境      {report['環境']}")
    print(f"  主機      {report['主機']}")
    print(f"  金鑰      {report['金鑰']}")
    print("═" * 58)
    print(f"  簽章      {report['簽章']}")

    if not str(report["簽章"]).startswith("通過"):
        print()
        print("  簽章沒過就不必往下看了 —— 後面每一個查詢都會用同一個簽章。")
        print("═" * 58)
        print()
        return 1

    print(f"  餘額端點  {report['餘額端點']}")
    print(f"  可讀      {'、'.join(report['可讀']) or '(無)'}")
    if report["讀不到"]:
        print("  讀不到")
        for row in report["讀不到"]:
            print(f"            · {row}")
    print("─" * 58)

    summary = report.get("餘額摘要") or {}
    for label, value in summary.items():
        if value is None:
            # 「讀不到」與「是零」是兩件事,照實顯示(第九十四條)。
            print(f"  {label:<10} —(這個欄位交易所沒給)")
        elif isinstance(value, float):
            print(f"  {label:<10} {value:,.4f}")
        else:
            print(f"  {label:<10} {value}")

    if "持倉數" in report:
        print(f"  {'持倉檔數':<10} {report['持倉數']}")

    print("═" * 58)
    print()
    print("  下一步不是下單,是**對帳**:把這裡看到的真實帳戶,")
    print("  跟紙上帳本擺在一起比對。兩本對得起來,才談得上下單。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
