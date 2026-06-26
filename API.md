# AGMCIS API Documentation

## GET /api/dashboard

用途：
Dashboard 首頁資料。

回傳：
- balance
- trades
- wins
- losses
- open_count
- closed_count
- total_open_upnl
- risk_level
- system_status
- positions

---

## GET /api/portfolio

用途：
投資組合資訊。

回傳：
- balance
- margin_used
- available
- open_positions
- positions[]

---

## GET /api/performance

用途：
交易績效摘要。

回傳：
- total_trades
- closed_trades
- win_rate
- total_pnl
- avg_pnl
- best
- worst

---

## GET /api/stats

用途：
交易統計資料。

回傳：
- total
- wins
- losses
- win_rate
- avg_win
- avg_loss
- profit_factor
- balance
- open_positions

---

## GET /api/journal

用途：
最近交易紀錄。

回傳：
- symbol
- action
- price
- reason
- created_at

---

## GET /api/equity_curve

用途：
資金曲線資料。

回傳：
- labels
- equity

---

## GET /api/leaderboard

用途：
最佳交易排行。

---

## GET /api/analytics_pro

用途：
進階分析資料。
