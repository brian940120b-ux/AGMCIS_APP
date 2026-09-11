# AGMCIS PROJECT AUDIT REPORT

> PHASE 0 — Repository Audit
> 產出日期：2026-09-11
> 稽核範圍：`brian940120b-ux/AGMCIS_APP` @ `claude/resume-session-0mcy96`
> 稽核方式：逐檔閱讀 + 實際執行驗證(不靠猜測)

---

## 0. Executive Summary

AGMCIS 目前是一套**可運作的 Paper Trading 系統**，但**不是**一套可以接真實資金的交易系統。

| 項目 | 現況 |
|---|---|
| 追蹤檔案數 | 300 |
| 追蹤 Python 行數 | 16,359 |
| 其中「非備份」有效程式碼 | 6,929 行(42%) |
| 備份/死碼行數 | 9,430 行(58%) |
| Web 應用數量 | 3 套並存(root / v2 / v3) |
| 生產服務 | 4 個 systemd service(DigitalOcean) |
| BingX 整合程度 | **僅公開行情(ticker / OHLCV)** |
| Standard Futures 支援 | **完全沒有** |
| Perpetual Futures 支援 | 僅行情層,無下單/持倉 |
| 可用的自動化測試 | **0 套**(唯一的 `tests/` 目前是紅燈) |
| DB Schema 版控 | **不存在** |

**結論：現有系統的「資料層 + 指標層 + Telegram 介面 + v3 Dashboard 外殼」值得保留並作為基底改造；
「訊號決策層、風控層、回測層、交易執行層」必須重建。**

在進入任何新功能開發之前，有 **10 個 CRITICAL 問題**必須先處理，其中 3 個是安全性問題、
4 個是會讓所有績效數字失真的正確性問題。詳見第 13、14 節。

---

## 1. Current Architecture

### 1.1 技術棧

```
Python 3.11
FastAPI + Uvicorn
PostgreSQL (psycopg2, 無連線池)
ccxt (交易所存取)
ta (技術指標)
pandas / numpy
Jinja2 + 原生 JavaScript (無前端框架)
Telegram Bot API (long polling)
systemd (無 Docker、repo 內無 Nginx 設定)
```

### 1.2 實際部署拓撲

依 `DEPLOYMENT.md` 與 `logs/agmcis.log.*` 實際紀錄確認，生產環境跑 4 個 service：

| Service | 入口 | 實際行為 |
|---|---|---|
| `agmcis` | `main.py` | FastAPI Dashboard + 全部 `/api/*` |
| `agmcis-position` | `position_runner.py` | 每 60 秒 → `position_manager.manage_open_positions()` |
| `agmcis-opportunity` | `opportunity_runner.py` | 每 30 分 → `opportunity_scanner.scan_opportunities()` |
| `agmcis-telegram-listener` | `telegram_listener.py` | 每 2 秒輪詢 Telegram getUpdates |

另有 **兩個沒有列在 systemd 但存在於程式碼的排程器**：
`scheduler.py`(60 秒迴圈)與 `auto_runner.py`(300 秒迴圈)。
`logs/agmcis.log.1` 出現 `Scheduler | monitor=0 | trader=NO_TRADE_SIGNAL`，
證明 `scheduler.py` 曾在(或仍在)生產環境執行 → **同時存在多條開倉與平倉路徑**(見 18.1)。

### 1.3 三套 Web 應用並存

| 版本 | 路徑 | 狀態 | 特徵 |
|---|---|---|---|
| v1 | `main.py` + `api/` + `templates/` + `static/` | **生產中** | 40 行 f-string 內嵌 HTML;`/` 與 `/v1` 兩個 Dashboard |
| v2 | `v2/` | 開發中、已停滯 | 抽出 `api/` router,`config.py` 寫 `PRIMARY_EXCHANGE = "okx"` |
| v3 | `v3/` | 最新、最乾淨 | WebSocket push、component 化 JS、Chart.js、TradingView |

v3 是唯一採用 **WebSocket 主動推播**而非前端輪詢的版本，架構方向正確，建議作為新 Dashboard 基底。

---

## 2. Existing Features

以下功能**經確認實際可運作**：

- BingX 公開行情讀取(`exchange_engine.py`)：`fetch_ticker` / `fetch_ohlcv`，含
  指數退避重試(3 次)與「API 失敗退回舊快取值」的降級策略。此設計品質良好，應保留。
- 技術指標計算(`technical_service.py` / `strategy.py`)：EMA20/50/60、RSI14、MACD、ADX14、ATR14、Volume MA20。
- 多時間框架分析(`multi_timeframe_service.py` + `timeframes.py`)：15m / 1h / 4h。
- Paper Trading on PostgreSQL：開倉、平倉、帳戶餘額、勝敗計數。
- TP / SL 自動平倉(兩條路徑,見 18.1)。
- 分層 Trailing Stop(`trailing_stop.py`)：ROI ≥5% / ≥10% / ≥20% → gap 4% / 3% / 2%。
- 風控狀態查詢(`risk_control.py`)：回撤、曝險、持倉數、浮虧、Profit Factor、`emergency.stop` 檔案旗標。
- Telegram 指令中心：約 20 個指令(`/status` `/positions` `/close` `/emergency` `/resume_trading` …)。
- 績效分析(`analytics.py`)：勝率、Profit Factor、最大回撤、平均盈虧、R:R、連勝連敗、幣種統計。
- Trade Journal(`trade_journal.py` / `journal_service.py`)：OPEN / CLOSE 事件寫入 `trade_journal` 表。
- v3 Dashboard：KPI 卡、持倉列表、資金曲線、Market Scanner Top 10、AI Decision Center、TradingView 圖表。

---

## 3. Existing Trading Logic

### 3.1 系統中存在 **兩條互不相干、結論會互相矛盾** 的訊號管線

```
管線 A(Dashboard 顯示用)
market_universe.SCAN_SYMBOLS (12 檔硬編碼)
  → technical_service.get_indicators()
  → direction_engine.get_trade_direction()   → LONG / SHORT / WAIT
  → scanner_service.calculate_scanner_confidence()  → 0-100
  → decision_engine.get_trade_signal()       → "🟢 Strong Buy" / "🟡 Hold" / "🔴 Sell" …
  → mtf_engine 三框架同向檢查
  使用者:api/market_scan.py、v2、v3、auto_trader.py

管線 B(實際開倉用)
exchange_universe.get_top_volume_symbols(50)  ← 仍然同時查 OKX + BingX
  → symbol_filter.filter_symbols()
  → strategy.analyze_symbol()                → "做多" / "做空" / "觀望"
  → + volume_score + news_impact + optimizer_bonus
  → smart_ranking.get_smart_ranking()         → 0-100
  使用者:opportunity_scanner.py(生產環境實際開倉者)、rebalance_engine.py
```

兩條管線的**幣種池不同、評分公式不同、訊號詞彙不同**
(管線 A 用 emoji 字串，管線 B 用中文字串)。
結果：**Dashboard 上看到的訊號，不是實際下單所依據的訊號。**

### 3.2 進出場參數來源不一致

| 路徑 | SL | TP |
|---|---|---|
| `strategy.py`(算出但被丟棄) | ATR × 1.5 | ATR × 3 |
| `scanner_service.py` | ATR × 2 | ATR × 3 |
| `opportunity_scanner.py`(**實際使用**) | 固定 3% | 固定 6% |
| `auto_trader.py` 做空分支 | ATR × 2 | ATR × 3 |

`opportunity_scanner.py` 用**固定百分比**覆蓋掉 `strategy.py` 算好的 ATR 停損，
等於波動度資訊在最後一步被丟掉。

### 3.3 槓桿邏輯有三套

| 檔案 | 規則 |
|---|---|
| `leverage_engine.py` | confidence ≥95→8x, ≥90→6x, ≥80→5x, ≥70→3x, else 1x;再被 MTF 與 ATR 上限壓制 |
| `opportunity_scanner.get_leverage()` | score ≥90→5x, ≥80→3x, else 2x |
| 硬編碼 `3` | 出現在 9 個模組(`main.py`, `portfolio_manager.py`, `risk_alert.py`, `trailing_stop.py`, `ai_decision_service.py`, `api/dashboard.py`, `telegram_commands.py`, `paper_trading.py`, `database_service.py`) |

`leverage_engine` 讓 confidence 直接決定槓桿，違反「訊號強不等於可以開高槓桿」原則。

---

## 4. Existing Strategies

`strategies/` 下有 3 個策略，介面是 10 個位置參數的 `buy_signal()` / `sell_signal()`：

| 策略 | 進場 | 出場 |
|---|---|---|
| `ema_strategy` | price>EMA20 且 EMA20>EMA50 且 45≤RSI≤70 且 MACD 多 且 量>均量 且 ADX>25 | price<EMA20 或 MACD 空 或 ADX<20 |
| `rsi_strategy` | RSI<30 且 量>均量 | RSI>60 |
| `breakout_strategy` | price>EMA20 且 MACD 多 且 RSI>55 且 量>均量 且 ADX>25 | MACD 空 或 ADX<20 |

問題：
- **全部只做多**，系統卻宣稱支援 Long / Short。
- 參數全部硬編碼，無法做參數敏感度分析。
- 這 3 個策略**只被回測用**，生產環境實際下單走的是 `strategy.analyze_symbol()`
  的加總評分(score ≥75 做多 / ≤25 做空)，**與這 3 個策略無關**。
- `strategies/backtest_engine.py` 是 **0 byte 空檔**。

---

## 5. Existing Backtesting

存在兩個獨立回測器，**兩者都不足以作為決策依據**。

### 5.1 `backtest.py`

```python
exchange = ccxt.binance()   # ← 不是 BingX
...
run_backtest()              # ← 模組載入即執行,import 就會打網路
```

- 資料來源是 **Binance**，與實際交易所(BingX)不同。
- **只做多**。
- 停損/停利**只比對 close 價**，完全不看 `high` / `low`
  → 盤中穿刺停損的 K 棒不會被判定停損 → **勝率被系統性高估**。
- 手續費固定 0.1% × 2；**無滑點、無 Funding、無點差、無槓桿、無強平、無部分成交**。
- `limit=1500` 根 1h K 棒 ≈ **62 天**，樣本嚴重不足。

### 5.2 `backtest_engine.py` + `strategy_lab.py` + `strategy_optimizer.py`

```python
def run_strategy(df, strategy_module):
    capital = 10000
    ...
    capital *= (1 + pnl)     # 每筆都押上 100% 資金
```

- **完全沒有停損、沒有停利、沒有手續費、沒有滑點、沒有 Funding、沒有槓桿**。
- 每筆交易押上全部資金並完全複利 → 報酬率數字沒有意義。
- `strategy_optimizer.py` 在**模組層**呼叫 `get_dynamic_symbols(limit=20)`
  → **import 這個檔案就會同步載入兩間交易所的全部市場與 ticker**，
  任何 API 只要 import 它就會被卡住數十秒。

### 5.3 完全缺席的部分

Out-of-Sample、Walk Forward、Monte Carlo、Expectancy、Sharpe、Sortino、
Calmar、Recovery Factor、MFE / MAE、持倉時間、參數敏感度、Regime 分組測試。

---

## 6. Existing Paper Trading

`paper_trading.py` + PostgreSQL。可運作，但會計不正確。

### 6.1 已實現損益與未實現損益的槓桿處理不一致(CRITICAL)

```python
# paper_trading.close_paper_trade() — 已實現
pnl_usdt = size_usdt * pnl_pct                    # 沒有乘槓桿

# portfolio_manager.get_portfolio_summary() — 未實現
roi  = raw * leverage                             # 乘了槓桿
upnl = size * roi / 100
```

同一筆倉位，**浮動損益是已實現損益的 N 倍**(N = 槓桿)。
`accounts.balance` 由未乘槓桿的版本累加 → 帳戶餘額、資金曲線、
勝率、Profit Factor、最大回撤、Expectancy **全部建立在錯誤基準上**。

### 6.2 其他缺陷

- **無手續費、無滑點、無 Funding、無強平模擬** → 不是合約模擬，只是價差計算。
- 每個 symbol 最多一倉(`has_open_trade()`)，無法加倉 / 減倉 / 部分平倉 / 反手。
- `create_paper_trade()` 例外處理使用 `logger`，但該模組**從未 import logger**
  → journal 寫入失敗時會拋 `NameError` 而不是記錄錯誤。
- `save_trades()` 是空函式(`pass`)，V13.4 後遺留。
- 方向以中文字串 `"做多"` / `"做空"` 比對，散落 12 個以上模組，無 Enum 保護。

---

## 7. Existing Auto Trading

三個自動交易入口，**風控覆蓋不一致**：

| 入口 | 觸發 | 是否經過風控 |
|---|---|---|
| `opportunity_scanner.scan_opportunities()` | `agmcis-opportunity`(30 分) | ✅ 有查 `get_risk_control_status()` |
| `auto_trader.run_auto_trader()` | `scheduler.py`(60 秒) | ❌ **完全沒有查風控** |
| `GET /api/auto_trader` | **任何人發一個 HTTP GET** | ❌ 沒有風控、沒有驗證 |

`api/auto_trader.py`：

```python
@router.get("/api/auto_trader")
def auto_trader():
    return run_auto_trader()      # GET 請求直接開倉
```

**一個未驗證身分的 GET 請求就能讓系統開倉**，且該路徑不經過風控引擎。
這同時違反「Risk Engine 是 HARD GATE」與「GET 不應變更狀態」。

---

## 8. Existing BingX Integration

**現況：只有公開行情，等級約為整體需求的 10%。**

| 能力 | 狀態 | 說明 |
|---|---|---|
| Ticker / Last Price | ✅ | ccxt `fetch_ticker`,`defaultType: "swap"` |
| OHLCV / Kline | ✅ | ccxt `fetch_ohlcv` |
| 合約 symbol 轉換 | ✅ | `to_contract_symbol()` → `BTC/USDT:USDT` |
| 重試 / 退避 | ✅ | 3 次,可重試錯誤與不可重試錯誤已分流 |
| Authentication / HMAC-SHA256 簽章 | ❌ | 只把 key 丟給 ccxt,無自管簽章、無 server time 同步、無 recvWindow |
| Balance 查詢 | ❌ | |
| Position 查詢 | ❌ | |
| 下單 / 撤單 | ❌ | |
| TP / SL 掛到交易所 | ❌ | TP/SL 只存在自家 DB,靠輪詢價格自行平倉 |
| Contract List / Trading Rules | ❌ | tick size、step size、min qty、min notional、precision 全無 |
| Leverage / Margin Mode 設定 | ❌ | |
| Position Mode(One-Way / Hedge) | ❌ | |
| Funding Rate | ❌ | `config.py` 的 `CACHE_TTL` 有預留 key,但無實作 |
| Open Interest | ❌ | 同上 |
| Order Book / Depth | ❌ | |
| WebSocket | ❌ | v3 的 WebSocket 是「伺服器推給瀏覽器」,不是「BingX 推給伺服器」 |
| Rate Limiter | ⚠️ | 只有 ccxt 內建 `enableRateLimit`,無自管 429 / backoff 策略 |
| Client Order ID | ❌ | |
| Reconciliation | ❌ | |
| Error Code 對應 | ❌ | |

### 8.1 殘留的 OKX 痕跡(與「BingX Primary」矛盾)

| 位置 | 問題 |
|---|---|
| `exchange_universe.py` | `EXCHANGES = {"okx": ccxt.okx(), "bingx": ccxt.bingx()}` — 生產排名管線仍在查 OKX |
| `v2/config.py` | `PRIMARY_EXCHANGE = "okx"` |
| `v3/config.py` | `PRIMARY_EXCHANGE = "okx"` |
| `api/system_health.py` | 回傳 `"okx"` 欄位,而 `exchange_engine` 已無 okx → **永遠顯示 error** |
| `opportunity_scanner.py` | Telegram 訊息寫 `Exchange Data: OKX + BingX` |
| `how HEAD:market_data.py` | 誤建檔案,內含舊 OKX 版本 |

---

## 9. Existing Exchange Abstraction

**幾乎不存在。**

`exchange_engine.py` 是一組模組級函式 + 一個模組級 `EXCHANGES` dict，
`ccxt.bingx()` 在 **import 時就被實體化**，無法注入、無法測試、無法切換。

沒有 `ExchangeAdapter` 介面，沒有 `PaperExchange` / `BingXTestExchange` / `BingXLiveExchange`
的多型分派。目前「Paper」與「Live」的差別不是換 adapter，而是**根本沒有 Live 程式碼**。

值得注意：`tests/test_exchange_engine.py` 是針對一個 **class 版本的 `ExchangeEngine`**
(含 `exchange_factory` 注入、`ExchangeUnavailableError`、`get_funding_rate`、
`get_open_interest`、`get_positions`、`get_balance`、`create_order`)所寫的測試。
該重構在 commit `bf4bdd4` 被回退(留下 `exchange_engine.py.broken_backup`)，
但測試檔案留了下來 → **測試現在是紅燈**(見第 19 節)。
換句話說：**正確的抽象層曾經寫過又被丟棄，而它的測試還在，可以直接當規格書用。**

---

## 10. Existing Database

### 10.1 已知的表(由程式碼反推,repo 內無 schema)

| 表 | 欄位(由 SQL 反推) |
|---|---|
| `accounts` | id, balance, wins, losses, trades |
| `trades` | id, symbol, signal, entry_price, exit_price, size_usdt, status, pnl_pct, pnl_usdt, opened_at, closed_at, stoploss, takeprofit, close_reason, source, leverage, position_value |
| `trade_journal` | symbol, signal, action, price, reason, score, pnl_usdt, pnl_pct, created_at |

### 10.2 問題

- **repo 內沒有任何 `.sql` / migration 檔案**。Schema 只存在於 VPS 的 Postgres 內。
  換機器、重建環境、開 staging 都做不到。`db_upgrade_v13_4.py` 是唯一的「migration」，
  而且只有 3 個 `ALTER TABLE`。
- `accounts` 用 **`DELETE FROM accounts;` 然後 `INSERT`** 更新餘額
  → 無歷史、無稽核軌跡，且 DELETE 與 INSERT 之間若中斷，帳戶資料直接消失。
- **每次查詢都開新連線**(`psycopg2.connect`),無連線池。
- **帳戶更新與交易更新不在同一個 transaction**
  (`close_paper_trade` 先 `update_account()` 再 `close_trade()`，兩次獨立 commit)
  → 中間失敗會造成餘額已變、交易仍 OPEN 的資料不一致。
- 缺少交易系統必要的表：`orders`、`order_events`、`positions`、`position_events`、
  `signals`、`agent_decisions`、`risk_events`、`audit_logs`、`market_regimes`、
  `backtests`、`symbols` / `trading_rules`。
- 沒有 index 資訊；`get_trades()` 每次 **撈全表**再用 Python 過濾 OPEN / CLOSED。

---

## 11. Existing Frontend

- **三套 Dashboard**：`main.py` 內嵌 HTML(生產)、`templates/dashboard.html`(`/v1`)、`v2/`、`v3/`。
- `main.py` 的 `home()` 是一個約 40 行的 f-string，混雜 HTML / CSS class / 資料計算 → god function。
- `static/` 有 **12 份 JS 備份副本**(`dashboard_before_v53.js`、`dashboard_before_v38.js` …)。
- v3 已 component 化(`open_positions.js` / `equity.js` / `scanner.js` / `websocket.js`)，
  且改用 WebSocket 推播，是唯一值得往前走的前端基底。
- 前端完全沒有 Agent 視覺化、沒有交易模式(PAPER / LIVE)標示、沒有 LIVE 多重確認。

---

## 12. Existing API

16 個 OpenAPI 路徑。**全部端點皆無任何身分驗證**
(`api/` 目錄下 grep 不到任何 `Depends` / auth / key 檢查)。

Dashboard 頁面本身用 `?key=` query string 比對 `DASHBOARD_KEY`(預設值硬編碼 `agmcis2026`)，
但 `/api/*` 連這層都沒有 → **帳戶餘額、全部持倉、全部交易紀錄、績效數據公開可讀**，
且 `/api/auto_trader` **公開可寫**。

### 12.1 6 條重複註冊的路由(已用 FastAPI 實際驗證)

執行 `main.py` 時 FastAPI 自己發出 6 個 `Duplicate Operation ID` 警告：

```
/api/dashboard      /api/equity_curve   /api/stats
/api/leaderboard    /api/analytics_pro  /api/journal
```

`api/` 下的 router 在檔案頂端先註冊 → **router 版本生效**；
`main.py` 後面同名的 6 個 handler 是**死碼**。
維護者改 `main.py` 裡的版本會完全沒有效果，這是很容易踩到的陷阱。

`main.py` 的 `api_analytics_pro()` 在 `return` 之後還有一行 `return get_portfolio()`，
而 `get_portfolio` 在該模組**沒有被 import** → 若該行可達會直接 `NameError`。

---

## 13. Security Issues

| # | 嚴重度 | 問題 |
|---|---|---|
| S1 | **CRITICAL** | `telegram_listener.py` **不驗證訊息來源**。任何 Telegram 使用者只要找到這個 bot，就能執行 `/emergency`、`/close BTC/USDT`、`/confirm_close`、`/pause`、`/resume_trading`。程式碼從 `update` 取 `text` 就直接分派，`msg.get("chat", {}).get("id")` 從未被檢查。 |
| S2 | **CRITICAL** | `db.py` 把 PostgreSQL 密碼**硬編碼並提交進 git**：`"password": "agmcis123"`。違反「所有機密不得 hardcode」。 |
| S3 | **CRITICAL** | 全部 `/api/*` 無驗證；其中 `GET /api/auto_trader` 會**開倉**。 |
| S4 | HIGH | `DASHBOARD_KEY` 預設值 `agmcis2026` 硬編碼於 `main.py`(2 處)、`v2/config.py`、`v3/config.py`。v2/v3 甚至沒有走環境變數。 |
| S5 | HIGH | Dashboard 認證用 **URL query string**(`/?key=...`) → 會進 Nginx access log、瀏覽器歷史、Referer。 |
| S6 | MEDIUM | 環境變數命名不一致:`config.py` 讀 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`,`notifier.py` 與 `telegram_commands.py` 讀 `BOT_TOKEN` / `CHAT_ID`,`.env.example` 只寫前者 → 依 `.env` 內容不同,通知可能**靜默失效**。 |
| S7 | MEDIUM | `logs/agmcis.log.1`、`logs/agmcis.log.2`(共 5 MB)**被提交進 git**。目前內容經掃描無金鑰,但 log 進版控是長期洩漏風險。 |
| S8 | LOW | `.gitignore` 有一行被黏在一起:`Thumbs.db*.tar.gz` → `*.tar.gz` 實際上沒有被忽略。 |

**好消息**：`.env` 從未被提交(`git log --all -- .env` 為空)，
BingX API Key / Secret 目前只從環境變數讀取，沒有寫死在任何追蹤檔案中。

---

## 14. Risk Management Issues

### 14.1 已有的風控

`emergency.stop` 檔案旗標、`trading_pause.flag`、最大回撤 15%、曝險上限 80%、
最大持倉 5 筆、總浮虧 -300 USDT、Profit Factor 警告、單倉 ROI ≤ -15% 強制平倉。

### 14.2 結構性缺陷

| # | 嚴重度 | 問題 |
|---|---|---|
| R1 | **CRITICAL** | **風控不是 hard gate**。`auto_trader.run_auto_trader()`(被 `scheduler.py` 每 60 秒呼叫、也被公開 HTTP 端點呼叫)**完全沒有呼叫 `get_risk_control_status()`**。只有 `opportunity_scanner` 有查。等於風控可以被繞過。 |
| R2 | **CRITICAL** | **沒有「開倉必須有停損」的強制檢查**。`create_paper_trade()` 允許 `stoploss=None`;`position_manager` 遇到 `stoploss is None` 只是 `logger.warning` 後**跳過**,該倉位從此無人看管。 |
| R3 | HIGH | 風控參數全部硬編碼在 `risk_control.py` 模組層常數,不可組態。 |
| R4 | HIGH | 缺少:每筆風險上限(MAX_RISK_PER_TRADE)、單日虧損上限、單週虧損上限、單日交易次數上限、連續虧損熔斷、相關性曝險上限、單一幣種曝險上限、最大槓桿上限。 |
| R5 | HIGH | **沒有 Position Sizing 引擎**。倉位大小是固定 `POSITION_SIZE_USDT = 1000`,`auto_trader` 甚至用 `paper_trading` 的預設值,與帳戶權益、停損距離、ATR、流動性完全脫鉤。 |
| R6 | MEDIUM | Kill Switch 只做到「停止新開倉」,不會撤單、不會平倉、無稽核紀錄。 |
| R7 | MEDIUM | `risk_alert.py` 的熔斷平倉直接呼叫 `close_paper_trade()`,繞過任何執行層。 |
| R8 | MEDIUM | 沒有新聞 / 總經事件風控(FOMC / CPI 前後不會降風險)。 |
| R9 | MEDIUM | 沒有 Market Regime 偵測,策略不會隨市況切換。 |
| R10 | LOW | `risk_control` 依賴 `analytics.max_drawdown`,而該值來自 6.1 的錯誤 PnL → **風控判斷本身建立在錯誤數字上**。 |

---

## 15. Performance Issues

| # | 問題 |
|---|---|
| P1 | `strategy_optimizer.py` 在 **import 時**執行 `get_dynamic_symbols(limit=20)` → 載入兩間交易所全部市場 + ticker。任何 import 它的程式都會被阻塞數十秒。 |
| P2 | `backtest.py` 在 **import 時**執行 `run_backtest()` → 打網路並印出報表。 |
| P3 | `smart_ranking.get_smart_ranking()` 對 50 檔逐一 `get_ohlcv()` + 指標計算,**全同步、無併發**。`rebalance_engine` 與 `opportunity_scanner` 各自再呼叫一次。 |
| P4 | `scanner_service.scan_market()` 對 12 檔 × 4 次指標計算(主框架 + 3 個 MTF 框架)= 48 次 OHLCV 取得。v3 WebSocket **每 5 秒**推播一次就要跑完整套。 |
| P5 | `database_service.get_trades()` 撈全表後在 Python 端過濾;`get_open_trades()` / `get_closed_trades()` 都是全表掃描。 |
| P6 | 每次 DB 操作開新連線,無連線池。 |
| P7 | `cache_service.py`(設計良好的 TTL 快取)**沒有任何模組在用**;`market_data.py` 另外自己實作了兩份 ad-hoc 快取。 |
| P8 | v3 WebSocket 原本在 `async` 函式中直接呼叫同步的 DB / 交易所查詢(已於本 session 修正為 `asyncio.to_thread`)。 |

---

## 16. Technical Debt

| 項目 | 數量 |
|---|---|
| 追蹤中的備份 / 死碼 Python | **9,430 行(佔 58%)** |
| `main.py` 的備份副本 | 24 份(`main_before_*.py`, `main.py.save`, `main.py.save.1/.2`, `main_broken_backup.py` …) |
| `static/` 的 JS 備份副本 | 12 份 |
| 備份目錄 | `backup_conflict/`, `backup_v16/`, `backup_vps/`, `backups/`(30 個 JSON) |
| 誤建的垃圾檔(已追蹤) | `=`(0 byte)、`how HEAD:market_data.py`、`smart_ranking.py.save`、`*.broken_backup` |
| 提交進 git 的 log | 5 MB |
| 重複的 API 路由 | 6 條 |
| `telegram_listener.py` 重複的 elif 區塊 | 同一組指令**複製 6 次** |
| `scheduler.py` 的 `write_status()` | **定義 2 次**(第二次在 `if __name__` 之後) |
| 硬編碼槓桿 `3` | 9 個模組 |
| 硬編碼起始資金 `10000` | 6 處(`analytics.py`, `main.py` ×2, `api/equity.py`, `v2/api/equity.py`, `v3/services/dashboard.py`) |
| 硬編碼 `DASHBOARD_KEY` | 4 處 |

---

## 17. Duplicate Code

1. **兩條訊號管線**(第 3.1 節)——最嚴重的重複。
2. **兩套平倉邏輯**：`position_manager.manage_open_positions()` 與
   `position_monitor.run_position_monitor()` 做的是同一件事(TP/SL 檢查後平倉)，
   由兩個不同的排程器各自每 60 秒執行。
3. **三套排程器**：`scheduler.py`(60s)、`auto_runner.py`(300s)、`opportunity_runner.py`(1800s)。
4. **權益曲線計算 4 份**：`main.py`、`api/equity.py`、`v2/api/equity.py`、`v3/services/dashboard.py`，
   全部重複 `balance = 10000; for t in closed: balance += pnl`。
5. **統計計算 3 份**：`analytics.py`、`stats_service.py`、`performance_service.py`、
   加上 `main.py` 內的死碼版本——`profit_factor` 在 `gross_loss == 0` 時，
   一個回傳 `0`、一個回傳 `999`、一個回傳 `0 或 999`，**同一指標三種答案**。
6. **ROI / UPNL 計算 5 處**重複(`main.py`, `api/dashboard.py`, `portfolio_manager.py`,
   `telegram_commands.py`, `ai_decision_service.py`)，槓桿處理各不相同。

---

## 18. Dangerous Code

### 18.1 併發開倉 / 平倉，無鎖、無 idempotency(CRITICAL)

```
開倉路徑:opportunity_scanner(30 分) + auto_trader(60 秒) + GET /api/auto_trader(隨時)
平倉路徑:position_manager(60 秒) + position_monitor(60 秒) + risk_alert 熔斷
```

唯一的重複保護是 `has_open_trade(symbol)` —— 一個 **read-then-write** 檢查，
中間沒有交易鎖或唯一索引。兩個 process 同時檢查同一 symbol 就會開出兩張單。
平倉側更危險：兩條路徑可能同時對同一倉位呼叫 `close_paper_trade()`，
而 `close_trade()` 的 SQL 是 `UPDATE ... WHERE symbol=%s AND status='OPEN' ORDER BY id DESC LIMIT 1`，
餘額卻已被各自累加一次 → **重複計算損益**。

### 18.2 `position_monitor` 回傳值寫死為 0(CRITICAL)

```python
return {
    "checked": len(open_trades),
    "closed_count": 0,      # ← 寫死
    "checked_symbols": checked_symbols,
    "closed": []            # ← 寫死
}
```

即使實際平了倉，回傳仍是 0。`scheduler.py` 的 log 與 `/api/scheduler_status`
因此**永遠顯示 monitor=0**。這在 `logs/agmcis.log.1` 中已經實際出現：
`Scheduler | monitor=0 | trader=NO_TRADE_SIGNAL`。
**平倉事件在監控面板上是隱形的**，違反「No Silent Failure」。

### 18.3 `position_monitor` 會因 None 而崩潰(HIGH)

```python
price = get_price(symbol)          # 可能回傳 None
if signal == "做多":
    if price <= stoploss:          # None <= float → TypeError
```

`get_price()` 失敗回 `None`、`stoploss` 也可能是 `None`，兩者都沒有防護。
一次 API 抖動就會讓整個 scheduler 迴圈的這一輪被 `except` 吞掉 →
**該輪所有持倉都不會被檢查停損**。

### 18.4 指標計算的靜默失敗(HIGH)

```python
# technical_service.py
except Exception:
    pass
```

指標算失敗時回傳全 `None` + `trend="UNKNOWN"`，不記錄、不告警。
下游 `calculate_scanner_confidence()` 遇到 `None` 會給出 **confidence = 50** 的「中性」分數，
系統無法區分「市場中性」與「資料壞掉」。違反「資料異常 → NO TRADE」。

### 18.5 備份系統備份錯檔案(HIGH)

`backup_system.py` 備份 `data/paper_account.json` 與 `data/paper_trades.json`，
但 **V13.4 之後交易狀態已全部搬到 PostgreSQL**(`paper_trading.save_trades()` 是空函式)。
`backups/` 裡 30 個 JSON 全是**過期資料**。
→ **實際上沒有任何交易狀態的備份機制**，而使用者以為有。

### 18.6 其他

- `telegram_listener.py`、`backup_system.py`、`restore_system.py`、`backtest.py`
  在**模組層直接執行**(`while True` / 檔案操作 / 網路呼叫)，import 即產生副作用。
- `paper_trading.py` 例外處理中使用未 import 的 `logger` → `NameError`。
- `telegram_commands.py` 第 208 行是裸 `except:`。
- `telegram_commands.py` 在檔案中段(約第 42 行)插入 `if __name__ == "__main__": handle_status()`。

---

## 19. Missing Tests

### 19.1 唯一的正式測試目錄目前是**紅燈**(已實際執行確認)

```
$ pytest tests/ -q
ImportError: cannot import name 'ExchangeEngine' from 'exchange_engine'
ERROR tests/test_exchange_engine.py
1 error during collection

$ pytest tests/test_market_data.py -q
FAILED tests/test_market_data.py::TestMarketDataSnapshot::test_snapshot_survives_partial_failure
  AttributeError: module 'market_data' does not have the attribute 'get_ticker'
1 failed, 4 passed
```

原因：測試是針對 commit `bf4bdd4` 被回退掉的 class 版 `ExchangeEngine` 寫的。
**這些測試其實是很好的規格書**(它們定義了 `get_funding_rate`、`get_open_interest`、
`get_positions`、`get_balance`、`create_order`、`ExchangeUnavailableError`、
可注入的 `exchange_factory`)，重建抽象層時應該直接以它們為目標。

### 19.2 根目錄 19 個 `test_*.py` 不是測試

它們是 **print 腳本**：沒有 assert、沒有 test function、import 即執行、需要活的 DB。
其中 `test_paper.py` 會**真的建立模擬倉位**。`test_write.py` 內容是 `print("ok")`。

### 19.3 完全沒有測試的關鍵區域

Position sizing、風控 gate、TP/SL 邏輯、槓桿計算、PnL 計算、
訊號產生、回測引擎、交易規則驗證、狀態機、對帳、
Telegram 授權、API 授權、資料品質檢查。

---

## 20. Missing Features

對照 Master Prompt 的目標，以下**完全不存在**：

**交易所層**：Exchange Adapter 抽象、BingX 簽章、下單、撤單、持倉查詢、餘額查詢、
Trading Rules Engine、精度/步進/最小名目、槓桿與保證金模式設定、Position Mode、
Client Order ID、Order State Machine、Position Reconciliation、Rate Limit Manager、
BingX WebSocket、Standard Futures 的整個概念。

**市場資料層**：Funding Rate、Open Interest、Order Book / Depth / Imbalance、
Liquidation 資料、Mark Price、Index Price、Volume Profile、市場結構(HH/HL/LH/LL)、
Liquidity Sweep、資料品質檢查(缺 K 棒 / 重複 / 離群 / stale)。

**智能層**：12 個 Agent 全部不存在(目前的 `ai_decision_service.py` 是加權指標計分，
不是 Agent)、Agent Voting / Consensus、Supervisor、TradeIntent 資料結構、
Market Regime Engine、Sentiment Agent、Self Review Agent、AI Decision Record 持久化、
Trade Explainability。

**研究層**：OOS、Walk Forward、Monte Carlo、Strategy Health Score、
Overfitting 偵測、Strategy Ensemble、Strategy Status 生命週期、
Performance Drift Detection、Regime Drift。

**執行層**：Execution Engine、Exit Agent、Partial TP、動態 TP/SL(結構 / 支撐壓力)、
Reduce Only、加倉 / 減倉 / 反手。

**營運層**：Trading Modes(MANUAL / PAPER / TEST / LIVE)、Live Safety Gate、
LIVE 多重確認、Audit Log、Observability(metrics / latency / 結構化日誌)、
Failure Recovery(重啟後對帳)、環境分離(dev / test / staging / prod)、
Docker、DB migration、DB 備份、CI。

---

## 21. Recommended Architecture

### 21.1 原則

依使用者指示：**以現有系統為基底原地改造，不重寫**。
做法是「**在現有 repo 內建立新的分層，把既有可用邏輯搬進去，舊入口逐步指向新分層**」，
而不是開 v4 目錄重來。v3 的 Dashboard 外殼保留並成為唯一前端。

### 21.2 目標目錄結構

```
AGMCIS_APP/
├── agmcis/                        # 新的套件根(取代散落在根目錄的 60+ 模組)
│   ├── config/
│   │   ├── settings.py            # 單一組態入口,全部可由 env 覆寫
│   │   └── risk_limits.py         # 風控參數(不再散落硬編碼)
│   ├── core/
│   │   ├── enums.py               # Direction, MarketType, OrderType, TradingMode, OrderState
│   │   ├── models.py              # Signal, TradeIntent, Order, Position, Trade(dataclass)
│   │   └── errors.py
│   ├── exchange/
│   │   ├── base.py                # ExchangeAdapter 介面(以 tests/ 現有測試為規格)
│   │   ├── paper.py               # PaperExchange
│   │   └── bingx/
│   │       ├── client.py  auth.py  signer.py  rate_limiter.py  errors.py
│   │       ├── market.py           # ticker / ohlcv / funding / OI / orderbook
│   │       ├── contracts.py        # contract list + market_type 區分
│   │       ├── trading_rules.py    # tick/step/min qty/min notional/precision
│   │       ├── account.py  orders.py  positions.py
│   │       ├── websocket.py  reconciler.py
│   ├── data/
│   │   ├── market_data.py         # 統一走 cache_service
│   │   └── quality.py             # 缺 K 棒 / 重複 / 離群 / stale 檢查
│   ├── analysis/
│   │   ├── indicators.py          # 由 strategy.py + technical_service.py 合併
│   │   ├── structure.py           # HH/HL/LH/LL、支撐壓力
│   │   └── regime.py              # Market Regime Engine
│   ├── strategy/
│   │   ├── base.py  registry.py
│   │   ├── trend.py  breakout.py  momentum.py  mean_reversion.py
│   │   └── ensemble.py
│   ├── signal/
│   │   ├── scorer.py              # 單一 0-100 評分(取代兩條管線)
│   │   └── builder.py             # 產出 Signal / TradeIntent
│   ├── risk/
│   │   ├── engine.py              # HARD GATE:唯一的 allow/reject 入口
│   │   ├── position_sizing.py     # equity × risk% ÷ stop distance
│   │   ├── leverage.py            # 波動度驅動,非 confidence 驅動
│   │   └── kill_switch.py
│   ├── portfolio/
│   │   ├── manager.py  correlation.py  exposure.py
│   ├── execution/
│   │   ├── engine.py              # 唯一的下單出口
│   │   ├── state_machine.py       # CREATED→…→CLOSED + 異常態
│   │   ├── protection.py          # 開倉後強制 SL,失敗則進入 EMERGENCY
│   │   └── exit_manager.py        # TP/SL/Trailing/Partial/Time/Invalidation
│   ├── agents/
│   │   ├── base.py                # 只能產出 TradeIntent,不得碰交易所
│   │   ├── market.py  technical.py  quant.py  news.py  sentiment.py
│   │   ├── strategy.py  risk.py  execution.py  portfolio.py
│   │   ├── performance.py  self_review.py
│   │   ├── supervisor.py  consensus.py
│   ├── backtest/
│   │   ├── engine.py              # 含 fee/slippage/funding/leverage/liquidation
│   │   ├── metrics.py  walk_forward.py  monte_carlo.py  health_score.py
│   ├── persistence/
│   │   ├── db.py                  # 連線池 + transaction context manager
│   │   ├── migrations/            # 版控的 SQL(目前完全缺失)
│   │   └── repositories/          # trades / orders / positions / signals / agents / audit
│   ├── notify/
│   │   └── telegram.py            # 含 chat_id 白名單授權
│   ├── observability/
│   │   └── logging.py  health.py  metrics.py
│   └── web/
│       ├── app.py                 # 單一 FastAPI app(取代 main.py / v2 / v3 三套)
│       ├── auth.py                # 所有 /api/* 的驗證
│       ├── api/                   # market signals strategies backtest orders
│       │                          # positions account risk agents news performance system
│       ├── ws/                    # 由 v3/ws 搬入
│       ├── templates/             # 由 v3/templates 搬入
│       └── static/                # 由 v3/static 搬入
├── scripts/                       # 排程入口(單一 scheduler,取代三套)
├── tests/
│   ├── unit/  integration/  simulation/
└── docs/
```

### 21.3 交易決策流(唯一路徑)

```
Market Data ─┬─> Data Quality Gate ──(fail)──> NO TRADE
             │
             └─> Analysis (indicators / structure / regime)
                        │
                        v
                 AI Agents (12) ──> Consensus ──> Supervisor
                        │
                        v
                   TradeIntent            ← Agent 唯一能產出的東西
                        │
                        v
                 RISK ENGINE  ──(reject)──> NO TRADE / WAIT
                        │  (approve + position size + leverage)
                        v
              TRADING RULES ENGINE ──(invalid)──> reject + log
                        │
                        v
                EXECUTION ENGINE ──> Order State Machine
                        │
                        v
                EXCHANGE ADAPTER
                   ├── PaperExchange
                   ├── BingXTestExchange
                   └── BingXLiveExchange ──(Live Safety Gate)──
                        │
                        v
           BingX Standard Futures │ BingX Perpetual Futures
                        │
                        v
          Reconciler ⇄ Positions ⇄ Trade Journal ⇄ Self Review
```

**兩條不可違反的鐵律**：
1. Agent 與 Strategy **只能**產出 `TradeIntent`，不得 import 任何 exchange 模組。
2. **所有**開倉 / 平倉路徑**只能**經由 `execution.engine`，
   而 `execution.engine` 只接受通過 `risk.engine` 的 intent。
   以架構強制達成，不靠開發者自律。

### 21.4 Standard Futures 與 Perpetual Futures 的分離

在 `core/models.py` 讓 `market_type` 成為**必填欄位**，並貫穿
symbol 解析、contract 查詢、trading rules、下單、持倉、對帳、DB 與 UI：

```python
class MarketType(str, Enum):
    STANDARD  = "standard"
    PERPETUAL = "perpetual"
```

`symbols` / `trading_rules` 表以 `(exchange, market_type, symbol)` 為複合唯一鍵，
兩種市場的 contract_size / tick_size / step_size / min_notional / leverage 上限各自獨立儲存，
且**永遠從 BingX API 動態取得，不寫死**。

---

## 22. Migration Plan

### 為什麼要先插入一個 Phase 0.5

Master Prompt 的 PHASE 1 是 Architecture Refactor。
但生產環境**現在**就存在：任何人可控制 Telegram bot(S1)、
未驗證 HTTP 端點可開倉(S3)、風控可被繞過(R1)、
所有績效數字失真(6.1)、平倉事件在監控上隱形(18.2)。

在重構期間讓這些繼續存在是不必要的風險，而且 6.1 不修的話，
**Phase 7 的回測基準線會建立在錯誤的歷史資料上**。
因此建議把「止血」獨立成第一個 Phase，範圍嚴格限縮、不動架構。

### Phase 表

| Phase | 名稱 | 內容 | 完成判準 |
|---|---|---|---|
| **0** | Audit | 本報告 | ✅ 已完成 |
| **0.5** | **止血(Stop the Bleeding)** | S1 Telegram chat_id 白名單;S2 DB 密碼移入 env;S3 `/api/*` 加驗證 + `/api/auto_trader` 改 POST 並納入風控;R1 `auto_trader` 接上風控 gate;6.1 統一 PnL 槓桿處理;18.2 修 `closed_count`;18.3 補 None 防護;18.4 指標失敗改為記錄 + NO TRADE;S6 統一環境變數名 | 6 個 CRITICAL + 4 個 HIGH 修掉,`pytest` 綠燈,生產行為不變 |
| **1** | 清理與架構骨架 | 刪 9,430 行備份死碼、12 份 JS 備份、5 MB log、垃圾檔;建 `agmcis/` 套件;建 `core/enums.py` 與 `models.py`;`Direction` Enum 取代中文字串;抽出 `settings.py` 與 `risk_limits.py`;移除 6 條重複路由;修復 `tests/` | repo 只剩一套有效程式碼;組態集中;測試綠燈 |
| **2** | Market Data | `ExchangeAdapter` 介面(以現有 `tests/test_exchange_engine.py` 為規格);統一走 `cache_service`;加 Funding / OI / Order Book;`data/quality.py` 資料品質 gate;移除全部 OKX 殘留 | 資料異常時系統回 NO TRADE 而非猜測 |
| **3** | BingX Integration | 簽章 / server time / recvWindow;contract list;`trading_rules`;account / positions / balance;rate limiter;error code 對應;**Standard 與 Perpetual 明確分離**;WebSocket | 能以唯讀方式正確讀出真實帳戶與兩種市場的合約規則 |
| **4** | Trading Rules Engine | 下單前全部驗證(symbol / qty / price / notional / precision / leverage / margin / position mode / duplicate) | 不合規的 intent 一律被擋並記錄原因 |
| **5** | Risk Engine | 唯一 hard gate;position sizing(equity × risk% ÷ stop distance);波動度驅動槓桿;全部風控參數可組態;kill switch(停新單 + 撤單 + 平倉 + 稽核) | 任何路徑繞過風控在架構上不可能 |
| **6** | Strategy Engine | 策略註冊表;trend / breakout / momentum / mean reversion;**支援做空**;單一 0-100 評分取代兩條管線;Market Regime Engine | Dashboard 顯示的訊號 = 實際下單依據的訊號 |
| **7** | Backtesting | 專業回測:fee / slippage / funding / spread / leverage / liquidation / 部分成交 / 部分平倉;**訊號在 K 棒收盤產生,成交在下一根可執行點**;停損停利比對 high/low;完整 metrics | 現有策略取得誠實的基準線(即使勝率只有 55%) |
| **8** | Strategy Lab | OOS / Walk Forward / Monte Carlo / Strategy Health Score / Overfitting 偵測 / Ensemble | 策略有 RESEARCH→…→LIVE 的生命週期狀態 |
| **9** | Multi-Agent | 12 個 Agent + Consensus + Supervisor;`TradeIntent` 為唯一輸出;AI Decision Record 持久化 | Agent 無法 import exchange 模組(以測試強制) |
| **10** | Paper Trading 2.0 | 以新架構重建 paper 模式,含手續費 / 滑點 / funding / 強平;與舊路徑**並行比對** | 新舊路徑在同一市況下的差異可解釋 |
| **11** | BingX Test Validation | 測試環境下單 / 撤單 / TP-SL / 精度 / 錯誤碼驗證 | 真實 API 往返全部行為符合預期 |
| **12** | Execution Engine | Order State Machine;Client Order ID;開倉後強制 SL 保護(失敗則 retry→減倉→平倉→停單→告警);Exit Agent | 不可能出現「有倉位但沒有停損」 |
| **13** | Reconciliation | 內部狀態 ⇄ BingX 實際帳戶定期對帳;重啟後自動復原 | 重啟不會重複下單 |
| **14** | Dashboard | 以 v3 為基底統一前端;Agent 視覺化;交易模式明示;LIVE 多重確認 | 三套 Dashboard 收斂為一套 |
| **15** | Performance / Self Review | Performance Analyst + Self Review Agent;Trade Explainability;Drift Detection | 每筆交易可回答「為什麼開這一單」 |
| **16** | Production Safety | Audit Log;Observability;環境分離;Docker;DB migration + 真正的 DB 備份 | 可從零重建整套環境 |
| **17** | **LIVE SAFETY GATE** | Master Prompt 第 45 條的完整檢查清單 + **人工確認** | 全部通過才解鎖 |
| **18** | SMALL LIVE | 極小額;`MAX_LIVE_*` 限制;SAFE LIVE MODE | — |
| **19** | MONITOR | — | — |
| **20** | SCALE | — | — |

### 不要碰的部分(直到新路徑在 paper 模式驗證通過)

- 4 個生產 systemd service 的入口腳本行為
- 生產 PostgreSQL 的既有資料(`accounts` / `trades` / `trade_journal`)
- Telegram 指令的**對外介面**(指令名稱與回覆格式可保留，內部實作可換)

新舊並行一段時間、確認結果可對照之後，再切換入口。

---

## Answers to the 17 Audit Questions

**1. 現在系統是什麼架構?**
Python 3.11 + FastAPI + PostgreSQL + ccxt + ta 的單體應用，4 個 systemd service，
三套並存的 Web 應用，60 多個平放在根目錄的模組，無分層、無抽象、無 Docker、無 migration。

**2. 已經完成什麼?**
BingX 公開行情(含重試與降級)、技術指標、多時間框架、Paper Trading on Postgres、
TP/SL 自動平倉、分層 Trailing Stop、風控狀態查詢、20 個 Telegram 指令、
績效分析、Trade Journal、v3 WebSocket Dashboard。

**3. 哪些功能可以保留?**
`exchange_engine` 的重試與 stale fallback 策略、`cache_service`、
`strategy.py` 的指標計算邏輯、`analytics.py` 的指標公式、Trade Journal、
Telegram 指令介面、v3 Dashboard 外殼與 WebSocket 架構、
`tests/test_exchange_engine.py`(當作 adapter 規格書)。

**4. 哪些需要重構?**
資料層(連線池 + transaction + migration)、訊號層(兩條管線併一條)、
風控層(改為 hard gate)、回測層(加入全部成本與 high/low 判定)、
執行層(全新)、交易所層(全新 adapter)、前端(三套併一套)。

**5. 現在 BingX 整合到什麼程度?**
約 10%：只有 ticker 與 OHLCV 兩個公開端點，透過 ccxt。
無簽章、無下單、無持倉、無餘額、無合約規則、無 funding、無 OI、無 order book、
無 WebSocket、無對帳、無 rate limiter。且系統中仍有 6 處 OKX 殘留。

**6. Standard Futures 是否支援?**
**完全沒有。** 程式碼中不存在 `market_type` 的概念，
`exchange_engine` 硬寫 `defaultType: "swap"`。

**7. Perpetual Futures 是否支援?**
僅行情層(`defaultType: "swap"` + `BTC/USDT:USDT` symbol 轉換)。
無下單、無持倉、無 funding、無槓桿設定。

**8. Paper Trading 是否可靠?**
**不可靠。** 三個原因：
(a) 已實現損益不乘槓桿、未實現損益乘槓桿，帳戶餘額與所有績效數字失真；
(b) 無手續費 / 滑點 / Funding / 強平，不是合約模擬；
(c) 兩條平倉路徑併發執行且無鎖，可能重複計算損益。

**9. Backtest 是否存在 look-ahead bias?**
**存在，而且不只一種：**
(a) 停損停利只比對 close，不看 high/low → 盤中穿刺不計 → 勝率被高估(最嚴重)；
(b) `strategy_optimizer` 用**當前**成交量前 20 名回測過去 → survivorship bias；
(c) `backtest_engine` 完全沒有成本模型 → 報酬率虛高；
(d) 資料來自 Binance 而非 BingX → 價格與流動性不符實際成交環境。

**10. Risk Management 是否足夠?**
**不足夠，且結構上可被繞過。** 最關鍵：`auto_trader`(每 60 秒執行、
且有公開 HTTP 端點)完全不呼叫風控。另缺每筆風險上限、日/週虧損上限、
連續虧損熔斷、相關性曝險、最大槓桿上限、Position Sizing，
以及「開倉必須有停損」的強制檢查。

**11. Live Trading 有哪些風險?**
以目前狀態接真實資金會直接虧錢或失控，主要風險：
沒有交易所端 TP/SL(TP/SL 只在自家 DB，程式一停倉位就裸奔)、
沒有 Trading Rules 驗證(精度 / 最小量錯誤會被交易所拒單或成交出非預期數量)、
沒有 Order State Machine(UNKNOWN 狀態會重複下單)、
沒有對帳(內部與交易所不一致無法察覺)、
沒有 rate limit 管理(429 會讓風控指令送不出去)、
風控可被繞過、Telegram 任何人可控制、Paper 績效不可信所以無法判斷策略是否該上線。

**12. Database 是否能承受交易狀態?**
**不能。** `accounts` 用 DELETE+INSERT 更新餘額(無歷史、中斷即失資料)、
帳戶與交易更新不在同一 transaction、無連線池、全表掃描、
缺 orders / order_events / positions / audit_logs 等必要表、
**repo 內完全沒有 schema 或 migration**，而且備份系統備份的是已廢棄的 JSON 檔。

**13. Multi-Agent 如何加入?**
在 `agmcis/agents/` 建立 12 個 Agent，全部繼承 `agents/base.py`，
**唯一允許的輸出是 `TradeIntent`**，並以測試強制 Agent 模組不得 import
任何 `agmcis.exchange.*`。Consensus 收集投票、Supervisor 解決衝突，
Risk Agent 具備一票否決權。全部 Agent 決策寫入 `agent_decisions` 表以支援
Trade Explainability。此為 Phase 9，必須在 Risk Engine(Phase 5)
與回測(Phase 7)之後，否則 Agent 的建議無法被驗證。

**14. 哪些地方需要新增?**
見第 20 節。優先順序上最關鍵的三個新增物：
`agmcis/exchange/base.py`(抽象層)、`agmcis/risk/engine.py`(hard gate)、
`agmcis/persistence/migrations/`(schema 版控)。

**15. 哪些地方不要碰?**
4 個生產 systemd service 的行為、生產 Postgres 既有資料、
Telegram 指令的對外介面 —— 直到新路徑在 paper 模式與舊路徑並行比對通過。

**16. 最終建議架構是什麼?**
見第 21 節。核心是把「AI 智能層」與「交易核心」徹底分離，
中間只用 `TradeIntent` 這一種資料結構連接，
並讓 Risk Engine 與 Trading Rules Engine 成為架構上無法繞過的關卡。

**17. 開發應該分幾個 Phase?**
Master Prompt 的 0-20 共 21 個 Phase，**建議插入 Phase 0.5「止血」**成為 22 個。
理由：生產環境現存 3 個安全性 CRITICAL 與 4 個正確性 CRITICAL，
而其中 PnL 槓桿不一致(6.1)若不先修，Phase 7 的回測基準線會建立在錯誤資料上。

---

## 需要人工確認的事項

依 Master Prompt 第 102 條，以下涉及真實資金 / 風險限制，必須由使用者決定：

1. **架構確認**：是否採用第 21 節的目標架構與「原地改造」策略。
2. **Phase 0.5 授權**：是否同意先修 10 個 CRITICAL 問題再開始重構。
   其中 S1(Telegram 授權)與 S3(公開開倉端點)建議立即處理。
3. **風控參數初始值**：`MAX_RISK_PER_TRADE`、`MAX_DAILY_LOSS`、`MAX_LEVERAGE`、
   `MAX_OPEN_POSITIONS` 的實際數值。
4. **歷史資料處理**：現有 `trades` 表的 `pnl_usdt` 是在錯誤的槓桿基準下寫入的。
   修正 PnL 邏輯後，歷史資料要「保留原值並標記」還是「依 leverage 欄位重算」?
   這會改變所有歷史績效數字。
