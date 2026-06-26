# AGMCIS Architecture

## Overview

AGMCIS 是一套 AI 加密貨幣交易系統，主要由以下模組組成：

- FastAPI Backend
- PostgreSQL Database
- Telegram Command Center
- Paper Trading Engine
- Opportunity Scanner
- Risk Control
- Portfolio Service
- Performance Analytics
- Trade Journal
- Web Dashboard

---

## System Flow

User
↓
Telegram Bot / Web Dashboard
↓
FastAPI Backend
↓
Service Layer
↓
PostgreSQL Database
↓
Trading / Risk / Analytics Modules

---

## Core Modules

### FastAPI Backend

主要檔案：

- main.py

負責：

- Web Dashboard
- API Routes
- JSON Data Response

---

### Telegram Command Center

主要檔案：

- telegram_listener.py
- telegram_commands.py

負責：

- /health
- /positions
- /portfolio
- /performance
- /stats
- /journal
- /scan
- /emergency
- /resume_trading

---

### Trading Engine

主要檔案：

- paper_trading.py
- position_manager.py
- opportunity_scanner.py

負責：

- 模擬開倉
- 模擬平倉
- 掃描交易機會
- 管理持倉

---

### Risk Control

主要檔案：

- risk_control.py

負責：

- Emergency Stop
- Drawdown Control
- New Trade Permission
- Risk Level

---

### Analytics

主要檔案：

- performance_service.py
- stats_service.py
- portfolio_service.py
- journal_service.py

負責：

- 績效統計
- 勝率
- Profit Factor
- Portfolio Summary
- Trade Journal

---

## Data Layer

主要檔案：

- db.py
- database_service.py

資料庫：

- PostgreSQL

主要資料：

- account
- trades
- trade_journal

---

## Deployment

AGMCIS 使用 systemd 管理服務：

- agmcis.service
- agmcis-position.service
- agmcis-opportunity.service
- agmcis-telegram-listener.service
