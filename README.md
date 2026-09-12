# AGMCIS
## AI Cryptocurrency Intelligent Trading System

AGMCIS 是一套以 Python + FastAPI + PostgreSQL 建構的 AI 加密貨幣交易系統。

狀態:**模擬盤**。`LiveBroker` 已經寫好但**沒有被接上**,
而且 LIVE SAFETY GATE 的實單路徑那一項要有人逐檔讀過實單原始碼、
簽下每個檔案的 SHA-256 才會放行(第七十八節)。那個簽章我產不出來。

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
