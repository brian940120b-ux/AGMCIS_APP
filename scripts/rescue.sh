#!/usr/bin/env bash
# 一次做完:換版 → 查狀態 → 解開記帳死結 → 驗證 → 測警報 · 2026-09-25
#
# ═══ 為什麼把這五步綁在一起 ═══
# 執政官在手機上用 Termius,每多一個指令就多一次打字、多一次貼上、
# 多一次「上一段是不是成功了」要自己判斷。而這五步**有先後依賴**:
# 沒換到新版,死結修復就不在;沒解開死結,驗證就沒有東西可看。
#
# 用法:  cd /root/agmcis && git pull origin agmcis-base && sudo bash scripts/rescue.sh
#
# ⚠️ 這一支**會改東西**(第三段會真的記一次帳、真的下單平倉)。
#    前面兩段和後面兩段都是唯讀。哪一段在改,下面標得很清楚。
set -u

REPO="${REPO:-/root/agmcis}"
PY="${REPO}/.venv/bin/python"
LINE="══════════════════════════════════════════════════════════════"

step() { printf '\n\n%s\n  %s\n%s\n' "$LINE" "$1" "$LINE"; }

cd "$REPO" || { echo "✗ 進不去 $REPO"; exit 1; }

# ── 一、換版(會改:重啟服務)────────────────────────────
step "一 / 五  換版並驗證服務跑的是新版"
bash scripts/deploy.sh || {
  printf '\n✗ 換版沒過。**後面全部不跑** —— 死結修復不在舊版裡,\n'
  printf '  硬跑下去會用舊的程式碼改帳本,那比不跑更糟。\n\n'
  exit 1
}

# ── 二、查狀態(唯讀)──────────────────────────────────
step "二 / 五  記帳為什麼停了(唯讀,不改任何東西)"
"$PY" scripts/why_stopped.py

# ── 三、解開死結(**會改帳本**)────────────────────────
step "三 / 五  補跑 PRIMARY 記帳 —— ⚠️ 這一段會真的改帳本"
"$PY" - <<'PYEOF'
import sys, traceback
sys.path.insert(0, "/root/agmcis")
from portfolio import paper

print("  跑之前:")
from portfolio.account import Account
a0 = Account.load(paper.PRIMARY.state_path)
print(f"    在倉 {len(a0.positions)} 檔 · 最後記帳 {a0.funding_through_ms}")

try:
    r = paper.tick(cfg=paper.PRIMARY)
except Exception as e:
    print(f"\n  ✗ 還是掛了:{type(e).__name__}: {e}\n")
    traceback.print_exc()
    print("\n  **這不是預期結果。** 把上面整段貼回來 —— 死結不只一處。")
    raise SystemExit(1)

if r.get("skipped"):
    print(f"\n  這個交易日已經記過帳了:{r['skipped']}")
    # 但如果帳戶還在硬上限外面,那道冪等鎖鎖住的是一個**已知是錯的**
    # 狀態 —— 今天的額度被一次什麼都沒做的 tick 用掉了。那種情況下
    # 重跑一次是修復,不是重複記帳(理由見 paper.tick 的 force 說明)。
    over = len(a0.positions) > paper.MAX_UNIVERSE
    if not over:
        print("  在倉沒有超過池子上限 —— 不需要重跑。")
    else:
        print(f"\n  ⚠️ 但在倉 {len(a0.positions)} 檔 > 上限"
              f" {paper.MAX_UNIVERSE} —— 那道鎖鎖住的是一個已知是錯的狀態。")
        print("     重跑一次(force):資金費區間左開右閉不會重複收,")
        print("     訂單走目標與現有的差額不會重複下。")
        r = paper.tick(cfg=paper.PRIMARY, force=True)
        if r.get("error"):
            print(f"\n  ✗ 重跑失敗:{r['error']}")
            raise SystemExit(1)
        hold = r.get("holdings") or []
        pool = r.get("symbols") or []
        print(f"\n  重跑之後:在倉 {len(hold)} 檔 · 目標池 {len(pool)} 檔"
              f" · 成交 {len(r.get('orders') or [])} 筆")
        if r.get("risk_verdict"):
            print(f"    風控判決 {r['risk_verdict']}"
                  f" · 未過的檢查 {r.get('risk_failures')}")
elif r.get("error"):
    print(f"\n  ✗ plan() 回了錯誤:{r['error']}")
    raise SystemExit(1)
else:
    hold = r.get("holdings") or []
    pool = r.get("symbols") or []
    gaps = r.get("funding_gaps") or []
    print("\n  跑之後:")
    print(f"    在倉 {len(hold)} 檔 · 今天的目標池 {len(pool)} 檔")
    print(f"    成交 {len(r.get('orders') or [])} 筆")
    print(f"    權益 {r.get('equity')}  報酬 {r.get('return_pct')}%")
    if gaps:
        print(f"\n    收不到資金費而放掉的 {len(gaps)} 檔:{'、'.join(gaps)}")
        print("    這幾檔這一輪記 0 並準備出場 —— 少收的範圍是一檔一輪,")
        print("    而且**看得見**。整本帳不再被它們擋住。")
    else:
        print("\n    沒有收不到費率的幣。")
PYEOF

# ── 四、驗證(唯讀)────────────────────────────────────
step "四 / 五  驗證:記帳有沒有真的動起來(唯讀)"
"$PY" - <<'PYEOF'
import json, sys
from datetime import datetime, timezone
sys.path.insert(0, "/root/agmcis")
from portfolio.account import Account
from portfolio.paper import CONTROL, MAX_UNIVERSE, PRIMARY

now = datetime.now(timezone.utc).timestamp() * 1000
bad = []
for cfg, tag in ((PRIMARY, "PRIMARY(系統本身)"), (CONTROL, "CONTROL(對照組)")):
    a = Account.load(cfg.state_path)
    ms = a.funding_through_ms or 0
    hrs = (now - ms) / 3600_000 if ms else 9e9
    n = 0
    last = None
    try:
        with cfg.curve_path.open(encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    n += 1
                    try:
                        last = json.loads(ln).get("t") or last
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    over = len(a.positions) > MAX_UNIVERSE
    stale = hrs > 26
    print(f"\n  {cfg.name:10} {tag}")
    print(f"    最後記帳   {hrs:.1f} 小時前      {'✗ 停了' if stale else '✓'}")
    print(f"    權益曲線   {n} 點,最後 {last}")
    print(f"    在倉       {len(a.positions)} 檔(上限 {MAX_UNIVERSE})"
          f"  {'✗ 超過上限' if over else '✓'}")
    if stale or over:
        bad.append(cfg.name)

print()
if bad:
    print(f"  ✗ 還沒好:{'、'.join(bad)}。整段貼回來。")
else:
    print("  ✓ 兩組都在記帳,而且都沒超過池子上限。")
PYEOF

# ── 五、測警報(會改:真的送一則訊息)──────────────────
step "五 / 五  警報通道健檢"
# ⚠️ 2026-09-25 第二修:原本這一段自己拼了一串 getMe / getUpdates 的
#    排查步驟,執政官的回覆是「看不懂」。那是我的問題 —— 他用手機、
#    不是工程師,而我把讀 JSON、拼網址的活丟給他。
#    現在交給 telegram_doctor.py:它自己去問 Telegram,用人話講結論,
#    而且**永遠不會印出 token**。
if [ -f "${REPO}/.env" ]; then
  echo "  (讀 ${REPO}/.env —— 與服務同一份設定)"
fi
"$PY" scripts/telegram_doctor.py || true

printf '\n\n%s\n  跑完了。整段貼回來。\n%s\n\n' "$LINE" "$LINE"
