"""
Telegram 通道健檢 —— 告訴你**哪一個環節壞了**,以及下一步做什麼
· 2026-09-25

═══ 為什麼要有這一支 ═══
2026-09-25 查出來:檢修官每 10 分鐘報一次警,而每一則都卡在
`Telegram 回應 401`。archivist 從 09-13 起連續 12 天同一個錯。
**警鈴一直在響,而電話線是斷的。**

我原本給的排查步驟是「去 @BotFather 確認 token,再開 getUpdates
看 chat.id」。執政官的回覆是:**看不懂。**

那是我的問題。他不是工程師,而我把讀 JSON、拼網址這些活丟給他做了。
這一支把那些活收回來:它自己去問 Telegram,然後用人話講結論。

═══ 三個環節,分開檢查 ═══
  一、**有沒有設定**   .env 裡有沒有那兩個值
  二、**token 有沒有效** 打 getMe。401 = token 被撤銷或打錯
  三、**chat id 對不對** 打 getUpdates 看機器人看得到哪些對話
  四、**真的送得出去嗎** 真的送一則

分開檢查的理由:這四件事的**修法完全不同**,而它們失敗時的症狀
在 log 裡長得一模一樣(都是「送不出去」)。混在一起講,就會像我
上一則那樣,給出一串使用者看不懂的步驟。

═══ 這一支永遠不會印出 token ═══
§10 / §84:金鑰不得出現在對話、log、前端或任何輸出。
要顯示的時候一律遮成 `1234…wxyz`。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 走限流器,不直接 urlopen。Telegram 跟 BingX 不是同一個主機,一輪也
# 只打三次 —— 但 tests/test_ratelimit.py 掃 AST 擋繞道,而那條規則
# 的理由(「一個可以繞過的限流器,只是一個讓人放心的裝飾品」)
# 不因為「我這次只打三次」而不成立。例外開一次就會開第二次。
from core import ratelimit

BASE = Path(__file__).resolve().parents[1]
ENV = BASE / ".env"
LINE = "═" * 62


def mask(s: str) -> str:
    """遮成 `1234…wxyz`。**這支唯一被允許碰 token 的地方。**"""
    s = str(s or "")
    return f"{s[:4]}…{s[-4:]}" if len(s) > 12 else "(太短,看起來不像)"


def load_env() -> dict:
    out = {}
    try:
        for ln in ENV.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    # 環境變數優先(systemd 可能是別的來源)
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def api(token: str, method: str, params: dict | None = None) -> dict:
    """打一次 Telegram API。回 {ok, ...} 或 {ok: False, _err: ...}。"""
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params or {}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with ratelimit.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:                            # noqa: BLE001
            return {"ok": False, "error_code": e.code, "description": str(e)}
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "_err": f"{type(e).__name__}: {e}"}


def main() -> int:
    print(f"\n{LINE}\n  Telegram 通道健檢\n{LINE}\n")
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID", "")

    # ── 一、有沒有設定 ────────────────────────────────
    print("  ── 一、.env 裡有沒有設定 ──")
    print(f"     TELEGRAM_BOT_TOKEN  {mask(token) if token else '✗ 沒有'}")
    print(f"     TELEGRAM_CHAT_ID    {chat or '✗ 沒有'}")
    if not token:
        print(f"\n{LINE}")
        print("  結論:**機器人的鑰匙根本沒設定。**")
        print("\n  下一步(只有你能做,大概三分鐘):")
        print("    1. 手機打開 Telegram,搜尋 @BotFather")
        print("    2. 傳 /newbot 給它,照它問的取名字")
        print("    3. 它會回一串像 `123456789:AAF...` 的東西,那就是鑰匙")
        print("    4. 回到這裡跑:bash scripts/set_telegram.sh")
        print("       (輸入時螢幕不會顯示,那是正常的)")
        print("\n  ⚠️ **不要把那串鑰匙貼進跟我的對話。**")
        return 0

    # ── 二、鑰匙有沒有效 ──────────────────────────────
    print("\n  ── 二、這把鑰匙 Telegram 還認不認 ──")
    me = api(token, "getMe")
    if not me.get("ok"):
        code = me.get("error_code")
        print(f"     ✗ 不認({code} {me.get('description') or me.get('_err')})")
        print(f"\n{LINE}")
        print("  結論:**鑰匙是壞的。** 被撤銷了,或是當初貼錯 / 貼不全。")
        print("\n  下一步(只有你能做,大概兩分鐘):")
        print("    1. 手機打開 Telegram,搜尋 @BotFather")
        print("    2. 傳 /mybots 給它 → 點你的機器人")
        print("    3. 點 API Token → 它會再給你一次那串鑰匙")
        print("       (看不到機器人的話代表被刪了,改傳 /newbot 建一個)")
        print("    4. 回到這裡跑:bash scripts/set_telegram.sh")
        print("\n  ⚠️ **不要把那串鑰匙貼進跟我的對話。**")
        return 0
    bot = (me.get("result") or {}).get("username") or "?"
    print(f"     ✓ 認得,這把鑰匙屬於 @{bot}")

    # ── 三、機器人看得到哪些對話 ──────────────────────
    print("\n  ── 三、這個機器人看得到哪些對話 ──")
    ups = api(token, "getUpdates", {"limit": 50})
    seen = {}
    for u in (ups.get("result") or []):
        m = u.get("message") or u.get("channel_post") or {}
        c = m.get("chat") or {}
        if c.get("id") is not None:
            seen[str(c["id"])] = (c.get("title") or c.get("username")
                                  or c.get("first_name") or "?")
    if seen:
        for cid, who in seen.items():
            mark = "  ← .env 裡設的就是這個" if cid == str(chat) else ""
            print(f"     {cid:<16}{who}{mark}")
    else:
        print("     (一個都沒有 —— 機器人還沒收過任何訊息)")

    # ── 四、真的送一則 ────────────────────────────────
    print("\n  ── 四、真的送一則試試 ──")
    if not chat:
        print("     ✗ .env 沒有 TELEGRAM_CHAT_ID,不知道要送給誰")
    else:
        r = api(token, "sendMessage",
                {"chat_id": chat, "text": "AGMCIS 通道健檢:這則收到就是通了。"})
        if r.get("ok"):
            print("     ✓ **送出去了。去看你的 Telegram。**")
            print(f"\n{LINE}")
            print("  結論:**通道是好的。** 從現在起系統喊的話你聽得到。")
            return 0
        print(f"     ✗ 送不出去({r.get('error_code')} "
              f"{r.get('description') or r.get('_err')})")

    print(f"\n{LINE}")
    print("  結論:**鑰匙是好的,但送不到你那裡** —— chat id 不對。")
    print("\n  下一步(只有你能做,大概一分鐘):")
    print(f"    1. 手機打開 Telegram,搜尋 @{bot}")
    print("    2. 點進去,**隨便傳一句話給它**(例如 hi)")
    print("       ← 這一步是關鍵:Telegram 規定你要先開口,")
    print("         機器人才被允許傳訊息給你")
    print("    3. 回到這裡再跑一次這支:")
    print("         .venv/bin/python scripts/telegram_doctor.py")
    print("       第三段就會列出你的 chat id")
    print("    4. 用那個號碼跑:bash scripts/set_telegram.sh")
    print("\n  ⚠️ chat id 只是一串數字,不是密碼,貼給我沒關係。")
    print("     **鑰匙(token)才不能貼。**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
