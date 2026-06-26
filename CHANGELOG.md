# AGMCIS Change Log

---

## V75 Dashboard Foundation (2026-06-26)

### Added
- Portfolio API (/api/portfolio)
- Performance API (/api/performance)
- Journal API (/api/journal)
- Portfolio Telegram Command (/portfolio)
- Trading Statistics Command (/stats)

### Improved
- Performance Summary
- Portfolio Service
- Dashboard API Structure

### Fixed
- entry_price = 0 開倉問題
- 無效持倉資料清理
- total_trades 統計錯誤
- Journal 寫入格式修正
- Telegram Command Router

---

## V74 Trading Statistics

### Added
- Performance Summary
- Win Rate
- Profit Factor
- Average Win
- Average Loss

---

## V73 Portfolio

### Added
- Portfolio Service
- Telegram /portfolio
- Margin Calculation
- Available Balance

---

## V72 Telegram Upgrade

### Added
- /performance
- /journal
- /resume_trading
- /emergency

### Improved
- Telegram Command System

---

## V71 Journal

### Added
- Trade Journal
- Journal Service
- OPEN / CLOSE Logging

---

## V70 Risk Control

### Added
- Emergency Stop
- Resume Trading
- Risk Control Status
- Drawdown Protection
