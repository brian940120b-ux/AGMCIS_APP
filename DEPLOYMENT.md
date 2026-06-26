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

API Test:

curl -s http://127.0.0.1:8000/api/dashboard | python3 -m json.tool

---

## Git Workflow

git status
git add .
git commit -m "message"
git push origin main

Create Tag:

git tag -a VXX_STABLE -m "AGMCIS VXX Stable"
git push origin VXX_STABLE
