# AGMCIS
## AI Cryptocurrency Intelligent Trading System

AGMCIS 是一套以 Python + FastAPI + PostgreSQL 建構的 AI 加密貨幣交易系統。

狀態:**模擬盤**。系統目前沒有任何一條路徑能送出真實訂單
(`LiveBroker` 不存在,這是刻意的)。

- 架構:`ARCHITECTURE.md`
- 進度與待辦:`ROADMAP.md`
- 各階段報告:`docs/`

## 快速檢查

```bash
python -m unittest discover -s tests      # 測試
python scripts/preflight.py               # 上線前檢查
python scripts/live_gate.py               # 實單閘門狀態
python scripts/calibration_report.py      # 合約規格校準狀態
```

## 目前功能

- FastAPI Dashboard
- PostgreSQL Database
- Telegram Bot Control
- Paper Trading
- Smart Ranking
- Opportunity Scanner
- Portfolio Management
- Performance Analytics
- Trade Journal
- Risk Control
- Emergency Stop
- Position Manager

主要 API：

- /api/dashboard
- /api/portfolio
- /api/performance
- /api/stats
- /api/journal
- /api/equity_curve
- /api/leaderboard
- /api/analytics_pro

版本：

Current Version:
V75 Dashboard Foundation

# AGMCIS

## AI Cryptocurrency Intelligent Trading System

AGMCIS 是一套以 Python + FastAPI + PostgreSQL 建構的 AI 加密貨幣交易系統。

## Features

- FastAPI Dashboard
- PostgreSQL Database
- Telegram Bot Control
- Paper Trading
- Smart Ranking
- Opportunity Scanner
- Portfolio Management
- Performance Analytics
- Trade Journal
- Risk Control
- Emergency Stop
- Position Manager

## API

- /api/dashboard
- /api/portfolio
- /api/performance
- /api/stats
- /api/journal
- /api/equity_curve
- /api/leaderboard
- /api/analytics_pro

## Version

V75 Dashboard Foundation

## Author

Brian Huang

## License

PrivateAuthor:
Brian Huang

License:
Private
