# AGMCIS Change Log

---

## Phase 0.5 止血 (2026-09-11)

### ⚠️ 部署需求(不做會起不來,這是刻意的)
- `.env` 必須新增 `DB_PASSWORD`、`DASHBOARD_KEY`、`TELEGRAM_ALLOWED_CHAT_IDS`
- 必須執行 `python scripts/migrate.py`
- 建議輪替資料庫密碼與 Dashboard 金鑰(舊值曾提交進 git)

### Security
- Telegram listener 加入 chat_id 白名單。原本任何 Telegram 使用者都能發
  `/emergency`、`/close` 控制整套系統
- 移除 db.py 內硬編碼並已提交進 git 的資料庫密碼
- 全部 `/api/*` 加上金鑰驗證。原本完全公開,且 `GET /api/auto_trader` 會實際開倉
- `/api/auto_trader` 改為 POST
- Dashboard 金鑰改走 HttpOnly cookie,不再留在 URL 與 access log
- 金鑰比對改用 secrets.compare_digest;金鑰未設定時拒絕所有人

### Risk
- auto_trader 接上 Risk Engine。原本這條每 60 秒執行的開倉路徑完全不查風控
- 開倉強制要有停損,且驗證停損方向正確
- 風控參數全部改為環境變數(risk_limits.py)
- 新增單日虧損上限、連續虧損熔斷、單日交易次數上限、最大槓桿上限

### Fixed
- 已實現損益補上槓桿,與未實現損益統一算法。原本兩者相差 N 倍,
  導致資金曲線、勝率、Profit Factor、最大回撤全部失真
- get_trades() 補回遺漏的 leverage 欄位。原本每筆交易都被當成 3x
- 平倉改為單一 transaction 內完成,杜絕兩條平倉路徑重複計算損益
- trades 加上 partial unique index,阻止同 symbol 重複開倉
- position_monitor 回傳真實平倉數。原本寫死為 0,生產監控一直顯示 monitor=0
- position_monitor 補上 None 防護。原本取價失敗會讓整輪停損檢查被跳過
- technical_service 不再靜默吞掉指標失敗;壞資料一路傳導為 NO TRADE
- accounts 改為 UPDATE 而非 DELETE 後 INSERT
- 做空的停損不再被算在進場價下方
- 備份改為 pg_dump 資料庫。原本備份的是 V13.4 之後就沒人寫的 JSON 檔
- 移除 6 個被 router 遮蔽的死碼 endpoint
- 統一 Telegram 環境變數命名,修掉通知可能靜默失效的問題

### Added
- migrations/ 與 scripts/migrate.py。schema 首次納入版控
- direction.py:方向判斷的單一來源
- 復原被回退的 ExchangeEngine class,同時保留 DataFrame 契約不破壞生產
- 測試從 1 error + 1 failed 變成 112 passed

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
