#!/usr/bin/env bash
#
# 把 BingX API 金鑰寫進 .env —— 2026-09-13
#
# ═══ 為什麼要一支腳本,而不是叫人用 nano ═══
# 執政官用 iPhone + Termius。在手機上開 nano 編一個檔案很難用,
# 而且更重要的是:**打錯一個字,拿到的錯誤訊息是「簽名錯誤」**,
# 那句話不會告訴你是金鑰打錯還是簽章寫錯。
#
# ═══ 金鑰不會留在 bash history ═══
# 用 read -rs 讀,不走命令列參數 —— 命令列參數會進 history,
# 也會被同一台機器上的其他人用 ps 看到。
#
# 螢幕上也不會顯示(-s 是靜音輸入)。
#
# ═══ 其他變數原封不動 ═══
# 只動 BINGX_ 開頭那三行。TELEGRAM_BOT_TOKEN、GEMINI_API_KEY
# 那些都不碰。
#
# 用法:  bash scripts/set_bingx_key.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=".env"

echo
echo "═══════════════════════════════════════════════════════"
echo "  BingX API 金鑰"
echo "═══════════════════════════════════════════════════════"
echo "  在 BingX 建金鑰時:"
echo "    · 權限只勾【讀取】—— 這一步不需要交易權限"
echo "    · 【提款權限一律關閉】(第十條,沒有例外)"
echo "    · 先用 Demo Trading 的金鑰"
echo
echo "  輸入時螢幕不會顯示任何字元,那是正常的。"
echo "═══════════════════════════════════════════════════════"
echo

read -rsp "  API Key    : " BX_KEY;    echo
read -rsp "  API Secret : " BX_SECRET; echo
echo

if [ -z "$BX_KEY" ] || [ -z "$BX_SECRET" ]; then
  echo "  ✗ 空白 —— 沒有寫入任何東西。"
  exit 1
fi

read -rp "  環境 [demo/live,直接按 Enter = demo]: " BX_MODE
BX_MODE="${BX_MODE:-demo}"
if [ "$BX_MODE" != "demo" ] && [ "$BX_MODE" != "live" ]; then
  echo "  ✗ 只能是 demo 或 live —— 沒有寫入任何東西。"
  exit 1
fi
if [ "$BX_MODE" = "live" ]; then
  echo
  echo "  ⚠️  live = 真實帳戶。目前的程式只會讀,不會下單,"
  echo "      但請再確認一次這把金鑰的提款權限是關的。"
  read -rp "  確定?輸入 yes 繼續: " CONFIRM
  [ "$CONFIRM" = "yes" ] || { echo "  取消。"; exit 1; }
fi

touch "$ENV_FILE"

# 檔尾沒有換行的話先補一個 —— 不然新的一行會黏在舊的後面,
# 而 load_env() 會把那一整行當成一個看不懂的變數靜靜跳過。
if [ -s "$ENV_FILE" ] && [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
  echo >> "$ENV_FILE"
fi

# 只刪 BINGX_ 那三行,其他變數不碰
sed -i '/^BINGX_API_KEY=/d;/^BINGX_API_SECRET=/d;/^BINGX_ENV=/d' "$ENV_FILE"

{
  echo "BINGX_API_KEY=${BX_KEY}"
  echo "BINGX_API_SECRET=${BX_SECRET}"
  echo "BINGX_ENV=${BX_MODE}"
} >> "$ENV_FILE"

chmod 600 "$ENV_FILE"
unset BX_KEY BX_SECRET

echo
echo "  ✓ 寫好了(權限 600,只有 root 讀得到)"
echo "    .env 裡的 BINGX_ 變數:$(grep -c '^BINGX_' "$ENV_FILE") 個"
echo "    環境:${BX_MODE}"
echo
echo "  接著驗證(這一步只會讀,不會下單):"
echo "      .venv/bin/python scripts/verify_private.py"
echo
