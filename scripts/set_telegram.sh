#!/usr/bin/env bash
#
# 把 Telegram 的鑰匙與對話號碼寫進 .env —— 2026-09-25
#
# ═══ 為什麼要一支腳本 ═══
# 跟 set_bingx_key.sh 同一條理由:執政官用 iPhone + Termius,
# 在手機上開 nano 編檔案很難用。而且這次還多一條 ——
#
# 2026-09-25 我給了一串「去 @BotFather 確認 token,再開 getUpdates
# 看 chat.id」的步驟。回覆是:**看不懂。**
#
# 那是我的問題:他不是工程師,而我把讀 JSON、拼網址的活丟給他。
# 所以現在:**能自動的全部自動**,只留「用手機講一句話」給人做。
#
# ═══ 鑰匙不會留在 bash history,也不會顯示在螢幕上 ═══
# 用 read -rs 讀,不走命令列參數。
#
# ═══ 其他變數原封不動 ═══
# 只動 TELEGRAM_ 那兩行。BINGX_ 那些都不碰。
#
# 用法:  bash scripts/set_telegram.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=".env"
PY="./.venv/bin/python"

echo
echo "═══════════════════════════════════════════════════════"
echo "  Telegram 通知設定"
echo "═══════════════════════════════════════════════════════"
echo "  要兩樣東西:"
echo
echo "   一、**鑰匙**(token)—— 像 123456789:AAF... 那樣一長串"
echo "       手機開 Telegram → 搜尋 @BotFather"
echo "         · 已經有機器人:傳 /mybots → 點它 → API Token"
echo "         · 還沒有:傳 /newbot,照它問的取名字"
echo
echo "   二、**對話號碼**(chat id)—— 純數字,像 123456789"
echo "       不知道的話直接按 Enter,這支會自己去問"
echo
echo "  輸入鑰匙時螢幕不會顯示任何字元,那是正常的。"
echo "═══════════════════════════════════════════════════════"
echo

read -rsp "  鑰匙 token : " TG_TOKEN; echo
echo
if [ -z "$TG_TOKEN" ]; then
  echo "  ✗ 空白 —— 沒有寫入任何東西。"
  exit 1
fi

# ── 先驗鑰匙,再問號碼 ──────────────────────────────
# 鑰匙是壞的就沒必要往下問 —— 不然使用者會填完全部,
# 最後拿到一個「送不出去」而不知道是哪一樣錯。
echo "  先問 Telegram 這把鑰匙認不認…"
BOT="$(TG_TOKEN="$TG_TOKEN" "$PY" - <<'PYEOF'
import json, os, sys
sys.path.insert(0, ".")
from core import ratelimit          # 走限流器,不繞道
t = os.environ["TG_TOKEN"]
try:
    with ratelimit.urlopen(
            f"https://api.telegram.org/bot{t}/getMe", timeout=15) as r:
        d = json.loads(r.read().decode())
    print((d.get("result") or {}).get("username", "") if d.get("ok") else "")
except Exception:
    print("")
PYEOF
)"
if [ -z "$BOT" ]; then
  echo
  echo "  ✗ **Telegram 不認這把鑰匙。** 沒有寫入任何東西。"
  echo
  echo "    常見原因:貼的時候少了頭或尾、或這個機器人被刪掉了。"
  echo "    回 @BotFather 傳 /mybots 再拿一次,然後重跑這一支。"
  unset TG_TOKEN
  exit 1
fi
echo "  ✓ 認得,這把鑰匙屬於 @${BOT}"
echo

read -rp "  對話號碼 chat id(不知道就直接按 Enter): " TG_CHAT
if [ -z "$TG_CHAT" ]; then
  echo
  echo "  好,我自己去問。**但你要先開口** ——"
  echo "  Telegram 規定:你沒先傳訊息給機器人,它就不准傳給你。"
  echo
  echo "    現在請用手機打開 Telegram → 搜尋 @${BOT}"
  echo "    → 點進去 → 隨便傳一句話(例如 hi)"
  echo
  read -rp "  傳好了就按 Enter… " _
  TG_CHAT="$(TG_TOKEN="$TG_TOKEN" "$PY" - <<'PYEOF'
import json, os, sys
sys.path.insert(0, ".")
from core import ratelimit          # 走限流器,不繞道
t = os.environ["TG_TOKEN"]
try:
    with ratelimit.urlopen(
            f"https://api.telegram.org/bot{t}/getUpdates?limit=50",
            timeout=15) as r:
        d = json.loads(r.read().decode())
    ids = []
    for u in (d.get("result") or []):
        m = u.get("message") or u.get("channel_post") or {}
        c = (m.get("chat") or {}).get("id")
        if c is not None and str(c) not in ids:
            ids.append(str(c))
    print(ids[-1] if ids else "")
except Exception:
    print("")
PYEOF
)"
  if [ -z "$TG_CHAT" ]; then
    echo
    echo "  ✗ 還是看不到任何對話。沒有寫入任何東西。"
    echo "    可能是訊息還沒傳出去,或是傳給了別的機器人。"
    echo "    確認一下傳的對象是 @${BOT},然後重跑這一支。"
    unset TG_TOKEN
    exit 1
  fi
  echo "  ✓ 找到了:${TG_CHAT}"
fi

touch "$ENV_FILE"
# 檔尾沒有換行的話先補一個 —— 不然新的一行會黏在舊的後面,
# 而讀 .env 的地方會把那一整行當成看不懂的變數靜靜跳過。
if [ -s "$ENV_FILE" ] && [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
  echo >> "$ENV_FILE"
fi

sed -i '/^TELEGRAM_BOT_TOKEN=/d;/^TELEGRAM_CHAT_ID=/d' "$ENV_FILE"
{
  echo "TELEGRAM_BOT_TOKEN=${TG_TOKEN}"
  echo "TELEGRAM_CHAT_ID=${TG_CHAT}"
} >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
unset TG_TOKEN

echo
echo "  ✓ 寫好了(權限 600,只有 root 讀得到)"
echo
echo "  ── 立刻驗證 ──"
"$PY" scripts/telegram_doctor.py || true

echo
echo "  最後一步:讓服務讀到新的設定"
echo "      sudo systemctl restart agmcis-dash"
echo
