# AGMCIS Deployment Guide

## Server

- Ubuntu Server
- Python Virtual Environment
- PostgreSQL
- FastAPI
- Uvicorn
- systemd

---

## Project Path

/root/AGMCIS_APP

---

## ⚠️ Phase 0.5 之後的必要設定

Phase 0.5 移除了硬編碼的資料庫密碼與 Dashboard 金鑰。
`.env` 沒有補上以下變數,服務會以明確的錯誤訊息拒絕啟動:

```
DB_PASSWORD=<資料庫密碼>
DASHBOARD_KEY=<自己設一把,不要用舊的 agmcis2026>
TELEGRAM_ALLOWED_CHAT_IDS=<允許下指令的 chat id,逗號分隔>
```

套用 migration(idempotent,對既有資料庫不會改動資料):

```
/root/AGMCIS_APP/.venv/bin/python scripts/migrate.py
/root/AGMCIS_APP/.venv/bin/python scripts/migrate.py --status
```

建議同時輪替資料庫密碼與 Dashboard 金鑰 —— 舊值曾以明文提交進 git。

### 備份

備份已改為 pg_dump 資料庫(原本備份的是已廢棄的 JSON 檔):

```
/root/AGMCIS_APP/.venv/bin/python backup_system.py
/root/AGMCIS_APP/.venv/bin/python restore_system.py            # 列出備份
/root/AGMCIS_APP/.venv/bin/python restore_system.py <檔名>     # 還原(需確認)
```

---

## Python Environment

Virtual Environment:

/root/AGMCIS_APP/.venv

Run Python:

/root/AGMCIS_APP/.venv/bin/python

---

## Services

AGMCIS 使用 systemd 管理：

- agmcis.service
- agmcis-position.service
- agmcis-opportunity.service
- agmcis-telegram-listener.service

---

## Common Commands

Restart FastAPI:

systemctl restart agmcis

Restart Position Manager:

systemctl restart agmcis-position

Restart Opportunity Scanner:

systemctl restart agmcis-opportunity

Restart Telegram Listener:

systemctl restart agmcis-telegram-listener

Check Status:

systemctl status agmcis --no-pager
systemctl status agmcis-position --no-pager
systemctl status agmcis-opportunity --no-pager
systemctl status agmcis-telegram-listener --no-pager

---

## Health Check

Compile Python Files:

/root/AGMCIS_APP/.venv/bin/python -m py_compile main.py
/root/AGMCIS_APP/.venv/bin/python -m py_compile telegram_commands.py
/root/AGMCIS_APP/.venv/bin/python -m py_compile telegram_listener.py

Run Tests:

/root/AGMCIS_APP/.venv/bin/python -m pytest tests/ -q

API Test(所有 /api/* 現在都需要金鑰):

curl -s -H "X-AGMCIS-KEY: $DASHBOARD_KEY" http://127.0.0.1:8000/api/dashboard | python3 -m json.tool

---

## Git Workflow

git status
git add .
git commit -m "message"
git push origin main

Create Tag:

git tag -a VXX_STABLE -m "AGMCIS VXX Stable"
git push origin VXX_STABLE
