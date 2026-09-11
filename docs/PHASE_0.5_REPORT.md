# PHASE 0.5 — 止血(Stop the Bleeding)

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`
範圍:只修 Phase 0 稽核找到的 CRITICAL 與直接相關的 HIGH 問題,不做架構重構。

---

## ⚠️ 部署前必須先做的兩件事

這個 Phase 移除了兩個硬編碼的機密,**部署前如果沒有補上環境變數,服務會起不來**
(這是刻意的 —— 寧可明確失敗,也不要靜默沿用寫在程式碼裡的密碼)。

在 VPS 的 `.env` 加上:

```
DB_PASSWORD=<資料庫密碼>
DASHBOARD_KEY=<自己設一把新的,不要用 agmcis2026>
TELEGRAM_ALLOWED_CHAT_IDS=<你的 chat id>
```

並執行 migration:

```
/root/AGMCIS_APP/.venv/bin/python scripts/migrate.py
```

另外建議:
- **輪替資料庫密碼**。舊密碼曾以明文提交進 git,即使現在移除,歷史紀錄仍在。
- **輪替 Dashboard 金鑰**。`agmcis2026` 曾出現在 4 個追蹤檔案中。

---

## 修正清單

### 安全性

| 編號 | 問題 | 修正 |
|---|---|---|
| S1 | Telegram listener 不檢查 `chat_id`,任何人都能發 `/emergency`、`/close` | 新增 `telegram_config.is_authorized()` 白名單。未授權來源只記錄不回覆,也不執行。**白名單為空時拒絕所有人**,而不是放行所有人 |
| S2 | PostgreSQL 密碼硬編碼在 `db.py` 並已提交進 git | 改讀環境變數。未設定時拋出 `DatabaseConfigError` 並在訊息中說明要補哪個變數 |
| S3 | 全部 16 個 `/api/*` 端點無驗證,其中 `GET /api/auto_trader` 會開倉 | 新增 `web_auth.require_api_key` 掛在每個 router 上;`/api/auto_trader` 改為 **POST** |
| S5 | 金鑰走 URL query string,會進 access log 與瀏覽歷史 | 驗證後改發 HttpOnly cookie 並 303 導回乾淨路徑。金鑰比對改用 `secrets.compare_digest` |
| S6 | 環境變數命名不一致(`TELEGRAM_BOT_TOKEN` vs `BOT_TOKEN`)導致通知可能靜默失效 | 集中到 `telegram_config`,以 `TELEGRAM_*` 為正式名稱並向下相容舊名 |
| S8 | `.gitignore` 有一行黏在一起,`*.tar.gz` 實際上沒被忽略 | 重寫 `.gitignore`,並把 log 與 runtime 狀態檔納入忽略 |

### 風控

| 編號 | 問題 | 修正 |
|---|---|---|
| R1 | `auto_trader`(每 60 秒執行、且有 HTTP 端點)**完全不查風控** | `run_auto_trader()` 第一件事就是 `assert_can_open()`,被擋下直接回傳 `BLOCKED_BY_RISK` 且不掃描、不開倉 |
| R2 | 沒有「開倉必須有停損」的強制檢查 | `create_paper_trade()` 缺停損一律拒絕,且驗證停損在正確方向(做多須低於進場價、做空須高於)。停利若有設定也一併驗證方向 |
| R3 | 風控參數硬編碼在模組層常數 | 新增 `risk_limits.py`,全部改讀環境變數 |
| R4 | 缺單日虧損、連續虧損、單日交易次數、最大槓桿上限 | 四項都已加入 Risk Engine。`cap_leverage()` 讓任何策略都無法突破槓桿上限 |

**尚未完成(Phase 5)**:Position Sizing 仍是固定金額,還沒有依 `equity × risk% ÷ 停損距離` 計算。
`MAX_RISK_PER_TRADE_PCT` 目前只是設定值,還沒有被真正使用。

### 正確性與可觀測性

| 編號 | 問題 | 修正 |
|---|---|---|
| A1 | 已實現損益不乘槓桿、未實現損益乘槓桿,所有績效數字失真 | 統一為 `pnl_usdt = size_usdt × 價格變動 × leverage`,`pnl_pct` 為保證金 ROI。兩者滿足 `pnl_usdt = size_usdt × pnl_pct / 100` |
| A1b | `get_trades()` 的 SELECT **漏掉 `leverage` 欄位**,所以每一筆都 fallback 成 3x | SELECT 補上 `id` / `leverage` / `position_value` / `pnl_basis`。9 個模組的硬編碼 `3` 全部移除 |
| D1 | `run_position_monitor()` 的 `closed_count` 與 `closed` 寫死為 0 與 `[]` | 回傳真實數量,並額外回報 `skipped` 與 `unprotected` |
| D2 | 併發開倉 / 平倉無鎖,可能重複計算損益 | 資料庫 partial unique index 阻止同 symbol 重複開倉;平倉改為 `close_trade_atomic()`,`SELECT FOR UPDATE` + 更新交易 + 更新帳戶在**同一 transaction** 內。沒平到任何倉位時回傳 `None`,呼叫端不調整餘額 |
| D3 | 取價或停損為 `None` 時比較大小會拋 TypeError,整輪停損檢查被跳過 | 全部補上 None 防護,取價失敗只跳過該筆並記錄 |
| D4 | `except Exception: pass` 讓指標失敗變成 confidence 50 的「中性」訊號 | 記錄失敗,回傳 `data_ok=False` 與 `data_error`;訊號層一路傳導到 `⚪ No Data` 並拒絕開倉 |
| D5 | `accounts` 用 `DELETE` 後 `INSERT` 更新餘額,中斷即失資料 | 改為 UPDATE 最新一列,沒有列才 INSERT |
| D6 | 備份系統備份的是 V13.4 之後就沒人寫的 JSON 檔 | `backup_system.py` 改用 `pg_dump` 備份資料庫並自動保留最近 30 份;`restore_system.py` 配套改為從 SQL dump 還原,且要求明確輸入 `RESTORE` 確認 |
| D7 | 做空的停損被算成 `price - atr×2`(放在進場價下方,會立刻停損) | `scanner_service` 依方向計算停損停利 |

### 其他清理

- 刪掉 6 個被 `api/` router 遮蔽的死碼 endpoint。FastAPI 原本會發出 6 個
  `Duplicate Operation ID` 警告,實際提供服務的一直是 router 版本 ——
  改 `main.py` 裡那幾個完全沒有效果。
- `telegram_listener.py` 重複 6 次的 elif 區塊收成一張指令表。
- `scheduler.py` 重複定義的 `write_status()` 移除一份,並改為記錄真實平倉數與風控 blocker。
- `market_data.py` 自己維護的兩份 ad-hoc 快取改用 `cache_service`(原本完全沒人使用)。
- 起始資金 `10000` 的 4 處硬編碼改讀 `config.PAPER_START_BALANCE`。
- 移除 `api/system_health.py` 的 `okx` 欄位(永遠回傳 error 的誤導性紅燈),
  改為回報 BingX 與資料庫狀態。

### 復原被回退的抽象層

`tests/test_exchange_engine.py` 是針對一個 class 版 `ExchangeEngine` 寫的,
該重構在 commit `bf4bdd4` 被回退,測試留了下來,所以測試一直是紅燈。

回退的原因是 `get_ohlcv()` 的回傳格式從 DataFrame 換成 list of dict,
而 `technical_service.py` 與 `strategy.py` 直接對它做 `df["close"]`。

這次的做法是兩者兼顧:
- 復原 `ExchangeEngine` class(可注入 `exchange_factory`、`ExchangeUnavailableError`、
  `get_funding_rate`、`get_open_interest`、`get_positions`、`get_balance`、`create_order`)。
- **保留** `get_ohlcv()` 回傳 DataFrame 的契約,新增 `get_ohlcv_raw()` 與
  `get_ohlcv_dicts()` 給不同需求的呼叫端。
- 保留全部模組層函式(`fetch_ticker_safe` / `get_price_safe` / `fetch_ohlcv_safe` /
  `test_connection` / `get_exchange`),生產呼叫端完全不用改。

這同時讓 Phase 3 的 BingX 整合有了基礎:funding rate 與 open interest 的介面已經就位。

---

## 驗證

```
$ pytest tests/ -q
112 passed
```

稽核時的狀態是 **1 error + 1 failed**(唯一的測試目錄整個無法 collect)。

新增的測試檔案:

| 檔案 | 測試數 | 鎖住什麼 |
|---|---|---|
| `tests/test_direction.py` | 7 | 方向判斷與停損方向驗證 |
| `tests/test_pnl_leverage.py` | 11 | 已實現損益乘槓桿、與 uPnL 一致、原子平倉、重複平倉回傳 None |
| `tests/test_paper_trading_guards.py` | 15 | 強制停損、停損方向、重複開倉、平倉防護 |
| `tests/test_position_monitor.py` | 16 | 真實 closed_count、None 防護、unprotected 回報 |
| `tests/test_risk_gate.py` | 12 | 風控擋下時不開倉、槓桿上限 |
| `tests/test_access_control.py` | 19 | Telegram 來源授權、API 金鑰、未設定金鑰時拒絕所有人 |
| `tests/test_data_quality_gate.py` | 9 | 指標失敗被記錄、壞資料不會變成訊號、做空停損方向 |
| `tests/test_api_surface.py` | 7 | 實際啟動 app:無重複路由、每個 `/api/*` 都要金鑰、auto_trader 不再是 GET |

另外全部生產模組都做過 import 與 py_compile 檢查。

---

## 一項稽核報告的更正

Phase 0 報告的 R2 寫「`create_paper_trade()` 允許 `stoploss=None`」。
實際上原本的 `float(stoploss)` 對 `None` 會拋 TypeError 並被 except 攔下,
所以缺停損的開倉本來就會被拒絕。

真正的漏洞比較窄,但仍然存在:
1. 沒有驗證停損**方向**。做多的停損放在進場價上方會通過檢查,然後在下一輪立刻停損。
2. 資料庫裡既有的、或由其他路徑寫入的無停損倉位,`position_manager` 只寫一行 warning 就跳過。

兩者都已修正,並且有測試覆蓋。

---

## 已知仍未解決(依計畫排在後續 Phase)

- **仍有三套排程器**(`scheduler` 60s、`auto_runner` 300s、`opportunity_runner` 1800s)
  各自會觸發開倉。併發已由資料庫層擋住重複開倉,但架構上的重複要到 Phase 1 才收斂。
- **仍有兩條訊號管線**。Dashboard 顯示的訊號仍不是 `opportunity_scanner` 下單的依據
  (Phase 6)。
- **Paper Trading 仍無手續費、滑點、Funding、強平模擬**(Phase 10)。
  目前的模擬損益是不含成本的上界,不要當成真實可達成的績效。
- **回測仍有 look-ahead bias**。停損只比對 close 不看 high/low(Phase 7)。
- **Position Sizing 仍是固定金額**(Phase 5)。
- **BingX 仍只有公開行情**,無下單、無持倉、無對帳,Standard Futures 仍未支援
  (Phase 3)。
- **歷史 `pnl_usdt` 仍是舊基準**。已標記為 `LEGACY_UNLEVERAGED` 並保留原值,
  沒有回頭改寫任何歷史數字 —— 這一項在等使用者決定要保留標記還是依 leverage 重算。
- **v2 / v3 的 Dashboard 尚未加上驗證**。兩者目前不在 systemd 服務中,屬開發用途(Phase 14)。
- 9,430 行備份死碼、12 份 JS 備份副本尚未清理(Phase 1)。
