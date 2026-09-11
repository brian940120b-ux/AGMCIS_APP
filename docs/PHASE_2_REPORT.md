# PHASE 2 — Market Data

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:資料異常時系統回 NO TRADE,而不是猜測。** 已達成。

---

## 1. ExchangeAdapter 抽象層

```
agmcis/exchange/
├── base.py              ExchangeAdapter 介面(ABC)
└── bingx/
    └── adapter.py       BingXAdapter 實作
```

### 為什麼需要這一層

原本 `exchange_engine.py` 在模組載入時就 `ccxt.bingx()` 實體化 ——
沒辦法注入、沒辦法測試、沒辦法切換到 paper 或 testnet。
所有呼叫端也直接依賴 ccxt 的回傳格式,換函式庫就整片要改。

新介面定義的是「AGMCIS 需要交易所提供什麼」,不是「ccxt 提供什麼」。

### 失敗處理的明確約定

這是這一層最重要的設計決定:

| 資料類型 | 失敗時 | 理由 |
|---|---|---|
| 核心行情(ticker / ohlcv / tickers) | 拋 `ExchangeUnavailableError` | 呼叫端**必須知道**自己沒有資料,不能拿舊值當新值默默用下去 |
| 輔助資料(funding / OI / order book) | 回傳 `None` | 這些缺了不該讓整個 Dashboard 掛掉 |

### 新增的能力

- `get_order_book()` —— 深度、買賣價差、**買賣失衡**(-1 ~ 1,正值代表買盤較厚)
- `get_tickers()` —— 一次取得全市場 ticker,供成交量排名使用
- `get_trading_rules()` —— tick size / step size / min qty / min notional /
  contract size / 精度 / 槓桿上限,**全部從交易所動態取得,不寫死**
- `get_order()` —— 這是 `OrderState.UNKNOWN` 時的唯一正確反應:
  先查清楚交易所到底收到什麼,絕不盲目重送
- `capabilities()` —— 呼叫端可以先問「支不支援」,而不是呼叫了才發現拋例外。
  `standard_futures` **刻意不在清單裡**:符號與規則處理已就位,
  但尚未對真實 BingX Standard 市場驗證過(Phase 3)

### Standard 與 Perpetual 明確分離

| | Perpetual | Standard |
|---|---|---|
| ccxt defaultType | `swap` | `futures` |
| 符號格式 | `BTC/USDT:USDT` | `BTC/USDT` |
| 資金費率 | 有 | **無**(直接回 None,不白打 API) |

兩者用不同的 ccxt 實體與不同的 markets 快取。

---

## 2. 資料品質 Gate

`agmcis/data/quality.py`。這是 Phase 2 的核心。

原本的行為鏈是:指標算失敗 → 回傳全 None → 評分函式給 50 分「中性」
→ **系統分不出「市場中性」與「資料壞掉」的差別**。

Phase 0.5 讓指標失敗變成 `data_ok=False`。Phase 2 再往前一步:
在**算指標之前**就檢查原始 K 棒本身是否可信。

### 檢查項目

對應 Master Prompt 第 50 條:

| 檢查 | 嚴重度 | 說明 |
|---|---|---|
| `EMPTY` / `INSUFFICIENT_CANDLES` | ERROR | 沒資料或不足以算指標 |
| `MALFORMED_ROW` / `NON_NUMERIC` / `NAN_VALUE` | ERROR | 資料結構壞掉 |
| `NON_POSITIVE_PRICE` / `NEGATIVE_VOLUME` | ERROR | 不可能的數值 |
| `DUPLICATE_CANDLE` / `OUT_OF_ORDER` | ERROR | 時間戳錯誤 |
| `MISSING_CANDLES` | ERROR / WARNING | 缺超過 5% 才算 ERROR |
| `INVALID_OHLC` | ERROR | high 不是極值,代表資料源有問題 |
| `OUTLIER` | ERROR | 單根變動超過 50% |
| `STALE_DATA` | ERROR | 最後一根超過 3 個週期沒更新 |
| `ZERO_VOLUME` | ERROR | 整段視窗沒有成交 |
| `SPARSE_VOLUME` | WARNING | 超過 30% 的 K 棒沒成交量 |

Ticker 另外檢查 `CROSSED_BOOK`(ask 低於 bid)、`WIDE_SPREAD`、`STALE_TICKER`。

### 門檻的取捨

兩個門檻是刻意放寬的,因為誤判會讓系統停止交易:

- **離群值 50%**:加密貨幣本來就會暴漲暴跌。20% 的單根變動是正常市場行為,
  不該被當成壞資料。50% 只抓明顯的壞值(價格變 0 或變 10 倍)。
- **缺 K 棒 5%**:交易所維護造成的少量缺漏很常見。少量缺漏只記 WARNING,
  超過 5% 才視為不可信。

兩者都有測試鎖住:正常波動不會被誤判,少量缺漏不會停擺。

### 接進訊號鏈

`technical_service.get_indicators()` 現在走 `get_ohlcv_checked()`。
任何一項 ERROR 就直接 `data_ok=False`,**連指標都不算**,更不會產生訊號。
下游 `scanner_service` → `decision_engine` → `auto_trader` 的傳導鏈在
Phase 0.5 已經建好,壞資料一路變成 `⚪ No Data` 並拒絕開倉。

---

## 3. 移除全部 OKX 殘留

這一項比看起來嚴重。原本 `exchange_universe.get_top_volume_symbols()`
同時查 OKX 與 BingX 的 ticker,**把兩邊的成交量加總**來排名,
然後把排名結果交給 `opportunity_scanner` 開倉。

結果是:系統可能對「BingX 根本沒有掛牌」的標的下單,
而且用來排序的成交量數字混了另一間交易所的量。

| 位置 | 處理 |
|---|---|
| `exchange_universe.py` | 只查 BingX USDT 永續合約 |
| `v2/config.py`、`v3/config.py` | `PRIMARY_EXCHANGE` 改為 bingx,並改從 settings 取共用值 |
| `v2/api/system_health.py` | 移除永遠回傳 error 的 okx 欄位 |
| `static/js/dashboard.js`、`v2/` 前端 | 移除 OKX 狀態徽章 |
| `v3/static/js/tradingview.js` | 圖表資料源從 `OKX:BTCUSDT` 改為 `BINGX:BTCUSDT.P` —— 看 A 的圖下 B 的單本來就不對 |
| `tests/fake_ccxt.py` | 移除 okx stub |

交易所不可用時 `get_top_volume_symbols()` 回傳**空清單**並記錄。
空清單代表沒有開倉候選,這正是資料不可信時應有的行為。

---

## 4. 順帶修掉的問題

### `get_market_snapshot` 的承諾與實作不符

它的 docstring 說「任一項失敗都不讓整體掛掉」,但只有 `ExchangeUnavailableError`
被處理;交易所回傳非預期結構造成的 `AttributeError` 會直接往上炸。

現在每一項輔助資料都包一層 `_safe()`,失敗回 None 但**一定會記錄** ——
「不讓 Dashboard 掛掉」不等於「假裝什麼都沒發生」。

### v3 的 `from config import PAPER_START_BALANCE`(我在 Phase 0.5 引入的錯誤)

Phase 0.5 我把 `v3/services/dashboard.py` 改成 `from config import PAPER_START_BALANCE`,
但 v3 有自己的 `config.py`,而 Phase 0.5 才剛把 sys.path 改成讓 v3 自己的 config 優先。
從 v3 目錄啟動時這行會直接 ImportError。

修法是用完整套件路徑 `from agmcis.config.settings import PAPER_START_BALANCE` ——
同名模組靠 sys.path 順序決勝負,本來就不該寫成那樣。

### `exchange_universe` 伸手進 adapter 的 `_call`

我第一版讓它呼叫 `adapter._call("fetch_tickers", ...)`,這違反了剛建立的抽象 ——
抽象層的價值就在於呼叫端不知道底層是 ccxt。改成介面上的公開方法 `get_tickers()`,
並加測試鎖住。

---

## 驗證

```
$ pytest tests/ -q
236 passed
```

| 檔案 | 測試數 | 鎖住什麼 |
|---|---|---|
| `tests/test_market_quality.py` | 30 | 每一項品質檢查;正常波動不誤判;少量缺漏不停擺 |
| `tests/test_exchange_adapter.py` | 36 | 核心行情拋例外、輔助資料回 None、Standard/Perpetual 符號分離、交易規則動態取得、交易所實體是注入的、universe 只回 BingX 標的 |

v2 與 v3 都以實際啟動方式(cwd 在各自目錄)驗證過 import。

---

## 已知仍未解決

- **兩條訊號管線仍未合併**(Phase 6)。Dashboard 顯示的訊號仍不是
  `opportunity_scanner` 下單的依據。
- **BingX 仍無下單、持倉、餘額的實際驗證**。介面就位,但簽章、
  Standard Futures 實測、WebSocket、rate limiter、對帳都在 Phase 3。
- **`create_order` 可以呼叫但不該呼叫** —— 沒有精度驗證(Phase 4)、
  沒有風控 sizing(Phase 5)、沒有狀態機(Phase 12)。
  程式碼與 docstring 都標明了。
- **Order Book 資料已可取得但尚未進入訊號評分**(Phase 6)。
- Funding rate 與 Open Interest 同樣已可取得,尚未進入評分。
- **回測仍有 look-ahead bias**(Phase 7)。
- **Paper Trading 仍無手續費、滑點、Funding、強平模擬**(Phase 10)。
- **歷史 `pnl_usdt` 仍是舊基準**,已標記 `LEGACY_UNLEVERAGED` 並保留原值,
  等使用者決定要保留標記還是依 leverage 重算。
