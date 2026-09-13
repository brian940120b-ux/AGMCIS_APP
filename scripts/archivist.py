"""
AGMCIS — 守墓人(Archivist)
執行:systemd timer 每日一次(也可手動:.venv/bin/python scripts/archivist.py)

職權:唯讀。把系統的「記憶」打包成 tar.gz,經 Telegram 寄給操作者,
作為異地備份。伺服器毀滅時,帳本 / 名冊 / 封印 / 帳戶 / 廣度歷史不陪葬。

安全紀律:絕不打包 .env(密鑰不進聊天記錄)。.env 請另行人工備份。
"""
from __future__ import annotations

import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"

# 系統的記憶:遺失即無法重建的檔案
TARGETS = [
    DATA / "research" / "ledger.json",        # 假說帳本(科學史)
    DATA / "research" / "seals.json",         # walk-forward 封印
    DATA / "strategy_roster.json",            # 策略名冊
    DATA / "paper" / "account.json",          # Paper 帳戶
    DATA / "paper" / "trades.csv",            # 成交史(第十條計數)
    DATA / "equity_history.jsonl",            # 主城權益快照史(9月主野對比同尺,8/28維護日補)
    DATA / "shadow_trades.jsonl",             # 影子成交前向紀錄(9/4 訊號台立案)
    DATA / "shadow_account.json",             # 影子帳戶(9/4,起始 10000)
    DATA / "shadow_account_trades.jsonl",     # 影子逐筆成交(9/6,遺失即無法重建)
    DATA / "graduation.json",                  # 畢業契約進度(9/6)
    DATA / "multiplicity.json",                # 多重檢定計量(9/6)
    DATA / "frontiers.json",                   # 未驗殺方向進度(9/6)
    DATA / "promotable.json",                  # 晉升就緒偵測(9/6)
    # 隔離區不備份 —— 它是已知無效的資料,存在的意義只有事後查證,
    # 打進每日備份只是讓遺失即無法重建的東西被雜訊稀釋。
    # 影子歸檔目錄由下方 _extra_dirs 處理(月份檔會持續增加)
    DATA / "pulse_history.jsonl",             # 廣度 + 榜單快照
    DATA / "sentinel_state.json",             # 檢修官狀態
    DATA / "audit_log.json",                  # 稽核紀錄
    DATA / "gauge_history.jsonl",             # 測量官費率/OI 史(7/20 維護日補)
    DATA / "funding_events.jsonl",             # 案五獨立事件(9/5,累積式立案的計數依據)
    DATA / "gauge_board.json",                # 測量官最新快照
    DATA / "hunter_history.jsonl",            # 獵手榜單快照史
    BASE / "docs" / "charter.md",             # 遠征憲章
    BASE / "docs" / "maintenance.md",         # 維護制度與待辦
    BASE / "docs" / "README.md",              # 系統簡介
]


def _env() -> dict:
    out = {}
    try:
        for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def _chat_id(token: str) -> str:
    e = _env()
    if e.get("TELEGRAM_CHAT_ID"):
        return e["TELEGRAM_CHAT_ID"]
    try:
        r = subprocess.run(
            ["curl", "-s",
             f"https://api.telegram.org/bot{token}/getUpdates?limit=5"],
            capture_output=True, text=True, timeout=10)
        for x in reversed(json.loads(r.stdout).get("result", [])):
            m = x.get("message") or {}
            if m.get("chat"):
                return str(m["chat"]["id"])
    except Exception:
        pass
    return ""


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = Path(f"/tmp/agmcis-memory-{stamp}.tar.gz")
    packed = 0
    with tarfile.open(out, "w:gz") as tar:
        for f in TARGETS:
            if f.exists():
                tar.add(f, arcname=f.relative_to(BASE))
                packed += 1
    size_kb = out.stat().st_size / 1024
    print(f"打包 {packed}/{len(TARGETS)} 檔,{size_kb:.0f} KB → {out}")

    tok = _env().get("TELEGRAM_BOT_TOKEN", "")
    cid = _chat_id(tok)
    if not (tok and cid):
        print("Telegram 未配置,備份僅留在 /tmp")
        return 1
    r = subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://api.telegram.org/bot{tok}/sendDocument",
         "-F", f"chat_id={cid}",
         "-F", f"document=@{out}",
         "-F", f"caption=🗄 守墓人日備份 {stamp} · {packed} 檔 "
               f"{size_kb:.0f}KB(不含 .env,密鑰請另行備份)"],
        capture_output=True, text=True, timeout=60)
    ok = '"ok":true' in (r.stdout or "")
    print("✅ 已寄出" if ok else f"寄送失敗:{r.stdout[:200]}")
    try:
        out.unlink()
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
