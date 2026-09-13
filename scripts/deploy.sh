#!/usr/bin/env bash
# 換版並且確認真的換了 · 2026-09-13
#
# ═══ 為什麼要有這一支 ═══
# 「pull 完重啟」在手機上是四五行指令,而其中任何一行安靜地失敗,
# 結果都長得一模一樣:面板沒變。2026-09-13 為了這件事來回了三輪。
#
# 這一支把整串包起來,而且**最後一定會驗證** ——
# 服務自己回報的 commit 要跟目錄的 commit 一樣,不一樣就當成失敗。
#
# 用法:  sudo bash scripts/deploy.sh
set -u

REPO="${REPO:-/root/agmcis}"
BRANCH="${BRANCH:-agmcis-base}"
UNIT="${UNIT:-agmcis-dash}"
PORT="${DASHBOARD_PORT:-8765}"
LINE="══════════════════════════════════════════════════════"

say() { printf '\n%s\n  %s\n%s\n\n' "$LINE" "$1" "$LINE"; }
die() { printf '\n✗ %s\n\n' "$1"; exit 1; }

cd "$REPO" || die "進不去 $REPO"

# ── 一、工作目錄要乾淨,否則 pull 會被擋住而且不吵 ──────
say "一、檢查工作目錄"
DIRTY="$(git status --porcelain)"
if [ -n "$DIRTY" ]; then
  echo "$DIRTY"
  echo
  echo "  ⚠️ 有未提交的改動。pull 可能被它擋住 —— 而且**不會報錯**。"
  echo "     要丟掉這些改動的話:git checkout -- . && git clean -fd"
  echo "     要留著的話:git stash"
  die "工作目錄不乾淨,先處理它。這一支不會替你決定丟掉什麼。"
fi
echo "  乾淨。"

# ── 二、拉 ──────────────────────────────────────────
say "二、拉新的"
BEFORE="$(git rev-parse --short=12 HEAD)"
git pull origin "$BRANCH" || die "git pull 失敗"
AFTER="$(git rev-parse --short=12 HEAD)"
echo
echo "  之前  $BEFORE"
echo "  之後  $AFTER"
[ "$BEFORE" = "$AFTER" ] && echo "  (沒有新東西 —— 但還是會重啟,因為行程可能是舊的)"
git log --oneline -1

# ── 三、重啟 ────────────────────────────────────────
say "三、重啟 $UNIT"

# ⚠️ systemd 的 Type=simple 在**行程 fork 出來的那一刻**就報 active,
#    而不是在它成功 bind 到埠的時候。所以「active」不代表新的那個
#    真的在服務 —— 如果埠被別人占著,新的會 Address already in use
#    死掉,而你連到的還是舊的那個。
#
#    先看重啟**之前**誰在聽,這樣待會兒對得起來。
BEFORE_PIDS="$(ss -lptnH "sport = :${PORT}" 2>/dev/null \
  | grep -o 'pid=[0-9]*' | sort -u | tr '\n' ' ')"
[ -n "$BEFORE_PIDS" ] && echo "  重啟前在聽 ${PORT} 的:${BEFORE_PIDS}"

systemctl restart "$UNIT" || die "重啟失敗。看:journalctl -u $UNIT -n 40"

# ⚠️ 2026-09-13:第一版 sleep 3 之後看到 activating 就判死。
#    **activating 不是失敗,是還在起。** 面板啟動要 import pandas /
#    pydantic 再讀快取,三秒不一定夠。等太短就下結論,
#    跟這一整輪要修的毛病是同一個。
STATE=""
for i in $(seq 1 20); do
  STATE="$(systemctl is-active "$UNIT")"
  case "$STATE" in
    active)  echo "  狀態  active(等了 $((i * 2)) 秒)"; break ;;
    failed|inactive) break ;;
    *)       printf '  等 %s… (%ss)\r' "$STATE" "$((i * 2))"; sleep 2 ;;
  esac
done
echo
if [ "$STATE" != "active" ]; then
  echo "  狀態  $STATE"
  echo
  echo "  ── 最後 30 行日誌 ─────────────────────────────"
  journalctl -u "$UNIT" -n 30 --no-pager 2>/dev/null | sed 's/^/  /'
  echo "  ───────────────────────────────────────────────"
  die "服務起不來。上面的日誌就是原因,整段貼出來。"
fi

# ── 四、問服務自己(這一步才是重點)──────────────────
say "四、驗證:服務跑的是哪一版"
SERVED=""
for i in 1 2 3 4 5; do
  BODY="$(curl -sS --max-time 8 "http://127.0.0.1:${PORT}/health" 2>/dev/null)"
  SERVED="$(printf '%s' "$BODY" | tr ',' '\n' | grep -o '"commit": *"[^"]*"' \
            | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
  [ -n "$SERVED" ] && break
  sleep 2
done

if [ -z "$SERVED" ]; then
  echo "  /health 沒有回 build.commit。"
  echo
  echo "  ── /health 到底回了什麼(前 600 字)──────────────"
  printf '  %s\n' "$(printf '%s' "$BODY" | head -c 600)"
  echo "  ───────────────────────────────────────────────"
  echo
  # ⚠️ 不要只說「跑另一支去查」。2026-09-13 的教訓:每多一次
  #    來回,兩邊就多猜一輪。查得出來的就當場查完。
  echo "  ── 誰在聽 ${PORT} ─────────────────────────────"
  "${REPO}/.venv/bin/python" "${REPO}/scripts/why_old.py" 2>/dev/null \
    | sed -n '/二、誰在聽/,/三、問服務/p' | sed 's/^/  /'
  echo "  ───────────────────────────────────────────────"
  echo
  echo "  最常見的原因:**另一個行程占著 ${PORT}**。新的起不來"
  echo "  (Address already in use),systemd 只看到 fork 成功就報"
  echo "  active,而你連到的還是那個舊的。"
  echo
  echo "  上面如果看到不只一個 pid、或工作目錄不是 ${REPO},就是它。"
  echo "  另一個嫌疑犯:agmcis-wild(CLAUDE.md 說已停用,但它在跑)。"
  echo
  echo "  ── $UNIT 最後 25 行日誌 ────────────────────────"
  journalctl -u "$UNIT" -n 25 --no-pager 2>/dev/null | sed 's/^/  /'
  echo "  ───────────────────────────────────────────────"
  echo
  echo "  日誌裡如果有「綁不上 ${PORT} 埠」,那就確定是埠被占了。"
  echo "  處理:找出占著的那個 pid,停掉它的服務,再跑一次這一支。"
  die "驗證不過 —— **不要當成換好了**"
fi

echo "  目錄    $AFTER"
echo "  服務    $SERVED"
if [ "$SERVED" != "$AFTER" ]; then
  echo
  echo "  ⚠️ **兩個不一樣。** 在聽 ${PORT} 的不是這個目錄跑起來的行程。"
  echo "     跑這個查:.venv/bin/python scripts/why_old.py"
  die "換版沒生效"
fi

say "換好了 —— 服務跑的就是 $AFTER"
echo "  手機上重新整理。標頭應該看到:"
echo "    [U 本位標準合約 · 手動送單]  [$AFTER · 幾秒前啟動]"
echo
echo "  看不到的話那是瀏覽器在快取 —— 在網址後面加 ?v=$(date +%s) 再試一次。"
echo
