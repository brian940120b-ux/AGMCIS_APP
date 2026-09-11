# PHASE 1 — 清理與架構骨架

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`
策略:原地改造。新分層放進 `agmcis/`,根目錄舊模組變成 re-export shim,
生產服務的 import 路徑完全不用改。

---

## 成果數字

| 項目 | Phase 0.5 後 | Phase 1 後 |
|---|---|---|
| 追蹤 Python 行數 | 18,289 | **8,698** |
| 追蹤檔案數 | 300 | 207 |
| 備份 / 死碼檔案 | 104 | **0** |
| 自動化測試 | 112 | **168** |
| 排程器實作份數 | 3 | **1** |
| 組態來源 | 3 個檔案 + 各模組硬編碼 | **1 個** |

刪除 9,591 行死碼,佔原本追蹤 Python 的 52%。

---

## 1. 清除備份死碼

刪掉 104 個檔案,包含:

- `main.py` 的 24 份備份(`main_before_*.py`、`main.py.save.1/.2`、`main_broken_backup.py` …)
- `static/` 的 12 份 JS / CSS 備份副本
- `backup_conflict/`、`backup_v16/`、`backup_vps/` 三個備份目錄
- 誤建的垃圾檔:`=`(0 byte)、`how HEAD:market_data.py`、`smart_ranking.py.save`

刪除前先驗證過沒有任何活的程式碼 import 它們。第一次檢查出現 26 個假陽性 ——
因為 `backup_vps/market_data.py` 這類檔案的模組名與活的根目錄模組同名,
重新用「只檢查唯一屬於死碼的模組名」再跑一次,結果是零引用。

### 根目錄 19 個假測試

原本根目錄有 19 個 `test_*.py`,但它們不是測試:沒有 assert、沒有 test function、
import 即執行,而且需要活的資料庫。其中 `test_paper.py` 會**真的建立模擬倉位**,
`test_write.py` 的內容是 `print("ok")`。

在專案根目錄跑 `pytest` 會把它們收集進來然後失敗。現在:
- 兩個純噪音的檔案刪除
- 其餘 17 個移到 `scripts/manual/check_*.py`,附 README 說明哪些會改動資料

---

## 2. `agmcis/` 套件骨架

```
agmcis/
├── core/
│   ├── enums.py      Direction / MarketType / OrderState / TradingMode / ...
│   ├── models.py     Signal / TradeIntent / RiskDecision / TradingRules /
│   │                 Order / Position / ClosedTrade
│   └── errors.py     例外階層
├── config/
│   └── settings.py   全系統組態的單一來源
└── scheduling/
    ├── jobs.py       Job 宣告
    └── runner.py     單一排程器 + 任務組合
```

### TradeIntent 讓「開倉必須有停損」變成型別保證

`TradeIntent` 是 AI Agent 與策略層**唯一**能產出的東西,而它在建構時就驗證:

- 方向必須是明確的多或空(`WAIT` 會被拒絕)
- 停損必須存在
- 停損必須在正確方向(做多低於進場價、做空高於)
- 停利若有設定也必須在正確方向

驗證失敗直接拋 `TradingRuleViolation`。換句話說,**一個沒有停損的 TradeIntent
根本無法被建立出來** —— 這不再依賴每個呼叫端自己記得檢查。

它也刻意**不包含**倉位大小與槓桿。Agent 說「我想做多 BTC,停損在這裡」,
由 Risk Engine 決定「可以,size 這麼大」。職責分離寫進型別裡。

### Direction enum 取代散落的中文字串

原本 `"做多"` / `"做空"` 在 12 個以上模組裡被直接比對,打錯一個字就會靜默變成
「不做多也不空」而把 PnL 算成 0。現在:

- `Direction.parse()` 同時接受中文、英文、`buy`/`sell`、`bullish`/`bearish`
- 認不出來時回傳 `None`,**絕不猜**
- value 維持中文 —— 資料庫的 `signal` 欄位存的就是中文,Phase 1 不動 schema
- 價格變動、方向反轉、order side 都收斂成 enum 的方法

根目錄的 `direction.py` 變成 shim,只有一份實作。

### 其他值得一提的型別決定

- `OrderState.needs_reconciliation` 明確標出 `UNKNOWN` / `TIMEOUT` / `SUBMITTING`
  三個狀態。這些狀態下系統不知道交易所收到什麼,**絕對不可以直接重送** ——
  重送是最容易造成重複開倉的路徑。
- `Position.notional` 與 `size_usdt` 明確區分保證金與名目價值。
  舊系統把兩者混用,正是 Phase 0.5 那個損益差 N 倍問題的根源。
- `ClosedTrade.pnl_is_comparable` 讓舊基準的歷史資料不會被默默混進新的績效統計。
- `ConfidenceBand.of(None)` 回傳 `NO_TRADE` 而不是中性 —— 資料壞掉不等於市場中性。
- `MarketType` 已就位,`STANDARD` 與 `PERPETUAL` 明確分開,Phase 3 可以直接用。

---

## 3. 組態集中

`agmcis/config/settings.py` 成為唯一來源。根目錄的 `config.py`、`risk_limits.py`、
`telegram_config.py` 變成 re-export shim。

過程中確立了一條規則:

- **機密與存取控制**(資料庫密碼、Dashboard 金鑰、Telegram token、授權名單)
  用**函式**,每次呼叫重讀環境變數。這樣輪替金鑰不需要重啟服務。
- 其他設定用模組常數,啟動時讀一次。

這條規則是被測試逼出來的:把 Telegram 設定搬進 settings 之後,
`test_access_control.py` 有兩個測試失敗,因為它 reload `telegram_config`
但 settings 已經被快取。改成函式之後測試不用改就通過了,
而且順帶得到「金鑰輪替免重啟」這個實際好處。

`settings.as_dict()` 預設遮蔽所有機密 —— 這個結果可能出現在 log、
健康檢查或 Dashboard 上,不能讓 token 漏出去。

---

## 4. 三套排程器收斂為一套

原本:

| 檔案 | 間隔 | 做什麼 |
|---|---|---|
| `scheduler.py` | 60s | position_monitor + auto_trader |
| `auto_runner.py` | 300s | auto_trader + daily_report |
| `opportunity_runner.py` | 1800s | opportunity_scanner(也會開倉) |
| `position_runner.py` | 60s | position_manager |

四個互不知道對方存在的迴圈,開倉路徑三條、平倉路徑兩條。

現在只有一份實作(`agmcis/scheduling/`),一個 tick 迴圈驅動所有任務,
每個任務有自己的間隔設定、共用同一份狀態檔與例外處理。

### 但沒有改變各 service 的職責範圍

一開始我讓四個入口都跑完整任務組合,這其實會讓情況**更糟** ——
三個 process 各自跑 position_monitor + auto_trader + opportunity_scanner。

改成每個 service 沿用它原本負責的子集:

| Job set | 任務 | 對應 service |
|---|---|---|
| `position` | position_monitor / trailing_stop / risk_alert | agmcis-position(**不開倉**) |
| `opportunity` | opportunity_scanner | agmcis-opportunity |
| `trader` | auto_trader / daily_report | 原 auto_runner |
| `all` | 全部 | agmcis(**建議的最終狀態**) |

有測試鎖住這件事:`position` 這組不得包含任何開倉任務,
而且每個子集都必須是 `all` 的子集。

**建議的最終狀態**是只啟用 `agmcis` 一個 service,停用 `agmcis-position` 與
`agmcis-opportunity`。過渡期間三個並存也不會壞,因為重複開倉已由
Phase 0.5 的資料庫 unique index 擋住,只是浪費資源。

排程間隔全部可由 `.env` 調整:`SCHEDULER_POSITION_INTERVAL`、
`SCHEDULER_TRADER_INTERVAL`、`SCHEDULER_OPPORTUNITY_INTERVAL`、
`SCHEDULER_TRAILING_INTERVAL`、`SCHEDULER_RISK_ALERT_INTERVAL`。

### 一個細節

`Job.mark_failure()` 也會更新時間戳。失敗的任務如果不推進時鐘,
會在每個 tick 重試並把 log 灌爆 —— 這有測試覆蓋。

---

## 5. 移除 import 時的網路副作用

三個檔案原本在**模組層**就打網路:

| 檔案 | 原本的行為 | 現在 |
|---|---|---|
| `backtest.py` | 模組層直接 `run_backtest()`,import 就打網路並印報表 | 收進 `if __name__ == "__main__"` |
| `strategy_lab.py` | 模組層跑完整多幣種回測 | 包成 `run_lab()` |
| `strategy_optimizer.py` | 模組層呼叫 `get_dynamic_symbols(20)`,載入兩間交易所全部 ticker | 改為呼叫時才取得,可傳入 symbols |

實測三者的 import 時間從「數十秒或阻塞」變成 0.6 秒以內。

同時在這三個檔案的 docstring 標明它們的回測結果**不可作為策略上線依據**,
並寫清楚原因(資料來自 Binance 而非 BingX、停損只比對 close、
沒有成本模型、survivorship bias)。Phase 7 與 Phase 8 會重寫。

---

## 驗證

```
$ pytest tests/ -q
168 passed
```

新增測試:

| 檔案 | 測試數 | 鎖住什麼 |
|---|---|---|
| `tests/test_core_types.py` | 40 | Direction 解析、TradeIntent 強制停損、Position 損益一致性、Signal→Intent gate、OrderState 對帳語意 |
| `tests/test_scheduler.py` | 16 | 任務到期判斷、單一任務失敗不影響其他、各 service 職責範圍不變 |

另外全部 31 個生產模組都做過 import 檢查與 `py_compile`。

---

## 已知仍未解決(依計畫排在後續 Phase)

- **兩條訊號管線仍未合併**。Dashboard 顯示的訊號仍不是 `opportunity_scanner`
  下單的依據(Phase 6)。
- **`exchange_universe.py` 仍在查 OKX**,與 BingX-primary 矛盾(Phase 2)。
  `v2/config.py` 與 `v3/config.py` 也還寫著 `PRIMARY_EXCHANGE = "okx"`。
- **BingX 仍只有公開行情**。無下單、無持倉、無合約規則、無對帳;
  `MarketType.STANDARD` 型別已就位但還沒有實作(Phase 3)。
- **Position Sizing 仍是固定金額**。`MAX_RISK_PER_TRADE_PCT` 目前只是設定值,
  還沒有被真正使用(Phase 5)。
- **回測仍有 look-ahead bias**(Phase 7)。
- **Paper Trading 仍無手續費、滑點、Funding、強平模擬**(Phase 10)。
- **12 個 Agent 尚未存在**。`TradeIntent` 這個它們唯一的輸出型別已就位(Phase 9)。
- **v2 / v3 的 Dashboard 尚未加上驗證**,兩者目前不在 systemd 服務中(Phase 14)。
- **歷史 `pnl_usdt` 仍是舊基準**,已標記 `LEGACY_UNLEVERAGED` 並保留原值。
  這一項在等使用者決定要保留標記還是依 leverage 重算。
