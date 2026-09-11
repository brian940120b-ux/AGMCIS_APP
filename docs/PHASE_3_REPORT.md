# PHASE 3 — BingX Integration

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**原定完成判準:「能以唯讀方式正確讀出真實帳戶與兩種市場的合約規則」。**
**兩種市場這一半無法達成 —— 原因不是還沒做,是 BingX Standard Futures
目前在我們的 HTTP 層根本沒有可用的 API 面。詳見第 1 節。**

---

## 0. 關於「不要靠模型記憶猜 API」

Master Prompt 第 5 條明確要求 BingX 的一切以官方最新文件為準,不可靠記憶猜。
這一個 Phase 的第一件事就是去查,而不是憑印象寫。

查到的(可引用來源):

| 項目 | 值 | 來源 |
|---|---|---|
| Base URL | `https://open-api.bingx.com` | [BingX-Standard-Contract-doc](https://github.com/BingX-API/BingX-Standard-Contract-doc/blob/main/REST%20API.md) |
| 認證 header | `X-BX-APIKEY` | 同上 |
| 簽章 | HMAC SHA256 over assembled parameter string | 同上 |
| 時間戳 | 毫秒,超過 5000ms 的請求會被拒 | 同上 |
| Standard 餘額 | `GET /openApi/contract/v1/balance` | 同上 |
| Standard 持倉 | `GET /openApi/contract/v1/allPosition` | 同上 |
| Swap 端點前綴 | `/openApi/swap/v2/` | [BingX-swap-api-doc](https://github.com/BingX-API/BingX-swap-api-doc) |

官方文件站 `bingx-api.github.io` 在這個環境被 egress proxy 擋住,
Swap 的完整端點清單無法逐一確認。

### 因此的決定:HTTP 與簽章層繼續用 ccxt

**我沒有自己手寫 BingX 的 HTTP 客戶端與簽章。** 理由:

手寫一份端點對應表與簽章實作,在無法完整查證文件的情況下,
等於把「猜」寫死進程式碼 —— 這正是 Master Prompt 要禁止的事。
ccxt 是持續對著活的 API 維護的第三方實作,拿它當 HTTP + 簽章層,
比我依據片段文件重寫一份可靠得多。

所以這個 Phase 做的是 **ccxt 不提供、而交易系統一定需要**的東西:
限流策略、錯誤分類、對時偵測、合約規則快取與驗證、狀態正規化。

驗證方式不是讀 ccxt 原始碼,是**直接檢查安裝的 ccxt 4.5.78 實際宣告了什麼**。

---

## 1. ⚠️ Standard Futures 無法支援(推翻我在 Phase 2 的假設)

Phase 2 我寫了這段:

```python
_CCXT_MARKET_TYPE = {
    MarketType.PERPETUAL: "swap",
    MarketType.STANDARD: "futures",   # ← 這是錯的
}
```

實測結果:

| 檢查 | 結果 |
|---|---|
| `ccxt.bingx().has['swap']` | `True` |
| `ccxt.bingx().has['spot']` | `True` |
| **`ccxt.bingx().has['future']`** | **`False`** |
| `defaultType='futures'` 建構 | **成功,不報錯** |
| 實際呼叫時 | **才失敗** |

也就是說我的 Phase 2 程式碼會**建構成功、看起來正常,然後在很深的地方
用看不出根因的錯誤訊息炸掉**。

再查 ccxt 對 BingX Standard Contract 的支援,只有三個 private 端點:

```
contractV1PrivateGetBalance
contractV1PrivateGetAllPosition
contractV1PrivateGetAllOrders
```

**沒有行情、沒有 K 線、沒有合約清單、沒有下單、沒有合約規則。**
而 BingX 自己的 Standard Contract 文件也寫著 "currently in internal testing",
且該文件本身就缺少 symbols 與 server time 端點。

### 處理方式

不假裝支援。每一條進入點都明確拒絕並說明原因:

```python
STANDARD_UNSUPPORTED_REASON = (
    "BingX Standard Futures 目前無法透過 ccxt 支援:"
    "ccxt 的 bingx 統一 API 只涵蓋 spot 與 swap(has['future'] = False),"
    "Standard Contract 只有 balance / allPosition / allOrders 三個 private 端點,"
    "沒有行情、沒有合約清單、也沒有下單。"
)
```

`to_market_symbol` / `get_ticker` / `get_ohlcv` / `get_trading_rules` /
`_instance` 全部會擋下,且 `capabilities()` 不宣稱支援。有 6 個測試鎖住。

**`MarketType` 的架構保留不動** —— 型別分離是對的,
資料庫、模型、快取鍵都已經用 `(exchange, market_type, symbol)` 當唯一鍵。
等 BingX 的 Standard API 成熟(或我們改用官方 SDK)就能接上,架構不用改。

### 給使用者的決定點

Master Prompt 第 6 條要求系統必須明確支援 Standard 與 Perpetual 兩種市場。
目前只有 Perpetual 可行。三個選項:

| 選項 | 說明 |
|---|---|
| A | **只做 Perpetual**(建議)。永續是主流市場,流動性與工具都最完整 |
| B | 等 BingX Standard API 成熟後再接。架構已經留好位置 |
| C | 為 Standard 手寫 HTTP 客戶端。需要你提供完整且最新的官方文件,否則就是在猜 |

---

## 2. 錯誤分類與重試策略

原本的重試邏輯是:

```python
except (ccxt.NetworkError, ...):   # -> 重試
except ccxt.BaseError:             # -> 放棄
```

問題是 ccxt 把語意完全不同的錯誤都放在 `NetworkError` 底下:

| 例外 | 是 NetworkError 子類 | 原本的待遇 | 為什麼不對 |
|---|---|---|---|
| `RateLimitExceeded` (429) | ✓ | 短退避重試 | 被限流還一直打,只會讓封鎖更久 |
| `DDoSProtection` | ✓ | 短退避重試 | 同上,而且更嚴重 |
| `InvalidNonce` | ✓ | 短退避重試 | 時鐘偏移造成,重試一百次也沒用 |

`agmcis/exchange/error_policy.py` 把錯誤分成幾類,每類有自己的動作:

| 動作 | 用於 | 退避倍率 |
|---|---|---|
| `BACKOFF_LONG` | DDoS 保護 / 429 | 8x / 4x,並讓限流器進入冷卻 |
| `RESYNC_TIME` | `InvalidNonce` | 先對時再重試一次,失敗就放棄 |
| `RETRY` | 逾時 / 一般網路錯誤 / 維護 | 1x / 1x / 4x |
| `FAIL` | 認證、保證金不足、精度錯誤、symbol 不存在 | 不重試 |
| `RECONCILE` | **寫入操作的逾時** | 不重試,必須先查詢對帳 |

### `RECONCILE` 是最重要的一條

讀取失敗最多就是沒資料。但**送出訂單之後**失敗完全是另一回事:
交易所可能已經收到、也可能沒有。這時候重送是重複開倉最常見的來源。

所以 `create_order` / `cancel_order` 走 `is_write=True`,
逾時與網路錯誤一律轉成 `OrderStateUnknownError`,訊息直接寫明必須對帳不可重送。
對照組:同一個 `RequestTimeout` 在讀取時只是正常重試。兩者都有測試。

未分類的例外一律保守處理:不重試。

---

## 3. Rate Limiter

ccxt 的 `enableRateLimit` 只是「兩次請求至少間隔 N 毫秒」的固定節流。
它不知道我們有幾個 process 共用同一把 API Key,也不會在 429 之後退讓。

`agmcis/exchange/rate_limiter.py` 補上:

- **Token bucket**(每 N 秒最多 M 次),允許短暫突發 ——
  一次掃描 50 檔需要突發能力,固定間隔會讓掃描變得極慢
- **冷卻期**:收到 429 就 `penalise()`,期間所有請求先等待。
  重複懲罰只會延長不會縮短冷卻時間
- 節流次數與冷卻次數計入 `status()`,供健康檢查觀察

⚠️ **這是行程內的限制器。** 多個 process(scheduler / web / telegram)
共用同一把 Key 時,每個 process 各有自己的額度。
真正的跨行程限流需要共用狀態(Redis),排在 Phase 16。
這一點寫在程式碼註解裡,不是隱藏的假設。

---

## 4. 伺服器時間同步

時鐘偏移是簽章失敗的經典生產事故,而且**錯誤訊息看起來像 API Key 有問題**,
很容易被誤判成金鑰設定錯誤,然後浪費很多時間重新申請金鑰。

`sync_server_time()` 用 ccxt 的 `fetch_time`(`has['fetchTime']` 為 True)
取得交易所時間,記錄偏移量,超過容許值(預設 3000ms)就用 ERROR 級別
明確指出要檢查 NTP。`clock_status()` 供健康檢查使用。

---

## 5. Trading Rules Registry

`agmcis/exchange/trading_rules.py`。合約規則一律動態取得(Master Prompt 第 11 條),
但每次下單都打 `load_markets` 太慢,所以這一層做快取(預設 TTL 一小時)。

`(exchange, market_type, symbol)` 是快取鍵 —— Standard 與 Perpetual 的
同一個 symbol 規則不同,不能共用。

### 進位方向是有意義的

```
做多停損 65000.37 -> 65000.3   (往下取,更早離場)
做空停損 65000.33 -> 65000.4   (往上取,更早離場)
```

數量一律無條件捨去 —— 超出的部分交易所會直接拒單。

### 驗證一次回報所有問題

```
BTC/USDT:USDT (perpetual): 價格 65000.37 不是 tick_size 0.1 的整數倍;
數量 0.0001 低於最小下單量 0.001;
數量 0.0001 不是 step_size 0.001 的整數倍;
槓桿 200x 超過該合約上限 125x
```

下單被拒之後一次修好,比來回試三次好。

`quantity_for_notional()` 把「我想開多少 USDT」換算成合約數量,
下不了單就明確回 `None`,不回一個會被拒單的數字。

⚠️ 這一層只判斷「交易所收不收」。**要不要開、開多大是 Risk Engine 的職責**(Phase 5)。

---

## 6. 唯讀驗證腳本

`scripts/verify_bingx.py`。在**金鑰所在的 VPS** 上執行:

```
/root/AGMCIS_APP/.venv/bin/python scripts/verify_bingx.py
```

金鑰只從 `.env` 讀,不需要也不應該貼到任何對話或 log。

**全程唯讀** —— 已用 grep 驗證腳本內不含任何
`create_order` / `cancel_order` / `set_leverage` / `set_margin_mode` /
`withdraw` / `transfer` 呼叫。

檢查 7 組:公開行情、伺服器時間偏移、合約規則與名目換算、
私有端點(餘額 / 持倉 / 持倉模式 / 槓桿)、金鑰提款權限提醒、
Standard Futures 支援狀況、限流器狀態。

有任何 FAIL 就回傳離開碼 1,並印出「在這些項目修好之前,不要進入下一個 Phase」。

---

## 7. 其他發現

### BingX 測試環境可用(Phase 11 會用到)

ccxt 的 `set_sandbox_mode(True)` 會把 base URL 切到
`https://open-api-vst.bingx.com/openApi`(BingX 的 VST 模擬盤)。
已接成 `EXCHANGE_USE_TESTNET` 設定,開啟時會用 WARNING 級別
明確記錄「這不是真實市場」。

### WebSocket 可行但尚未實作

`ccxt.pro` 隨 ccxt 4.5.78 一起安裝,bingx 支援
`watchTicker` / `watchOHLCV` / `watchOrderBook` / `watchTrades` /
`watchBalance` / `watchOrders` / `watchPositions`。

**這個 Phase 沒有做 WebSocket。** 理由是它需要一個常駐的 async 事件迴圈,
而目前的排程器是同步的 —— 硬接會變成第二套並行模型。
排到 Phase 12/13(執行與對帳)一起做比較合理,那時才真的需要即時訂單狀態。

### 測試改用真實的 ccxt 例外階層

原本 `conftest.py` 一律把 `ccxt` 換成 `fake_ccxt`。但錯誤分類是建立在
ccxt **真實的**例外階層上(`RateLimitExceeded` 與 `InvalidNonce` 都是
`NetworkError` 的子類)。跑在假階層上,ccxt 哪天改了階層,
production 會壞但測試還是綠的。

改成:有裝 ccxt 就用真的(它是 requirements.txt 裡的硬相依),
沒裝才退回 `fake_ccxt`,而 `fake_ccxt` 的階層也改成與真實一致。
測試仍然不連網路 —— 交易所實體一律用 MagicMock 注入。

---

## 驗證

```
$ pytest tests/ -q
302 passed
```

| 檔案 | 測試數 | 鎖住什麼 |
|---|---|---|
| `tests/test_error_policy.py` | 15 | 429 退避比逾時久、時鐘偏移先對時、寫入逾時必須對帳、未知例外不重試 |
| `tests/test_rate_limiter.py` | 10 | 允許突發、額度用盡會等、429 冷卻、重複懲罰只延長 |
| `tests/test_trading_rules.py` | 26 | 快取與 TTL、市場型態不共用快取、進位方向、一次回報所有問題 |
| `tests/test_exchange_adapter.py` | +20 | 重試策略真的被套用、對時、Standard 每條路徑都被擋 |

---

## 已知仍未解決

- **Standard Futures 無法支援**(見第 1 節,需要你決定 A/B/C)
- **WebSocket 未實作**(改排 Phase 12/13)
- **跨行程限流**需要 Redis(Phase 16)
- **私有端點尚未對真實帳戶驗證** —— 這個容器沒有也不該有金鑰。
  需要你在 VPS 上跑 `scripts/verify_bingx.py`
- **下單路徑不可使用**:`create_order` 可以呼叫但沒有精度驗證的自動套用
  (Phase 4)、沒有風控 sizing(Phase 5)、沒有狀態機(Phase 12)
- 兩條訊號管線仍未合併(Phase 6);回測仍有 look-ahead bias(Phase 7)
- 歷史 `pnl_usdt` 仍是舊基準,已標記 `LEGACY_UNLEVERAGED`,等你決定

---

## Sources

- [BingX-API/BingX-Standard-Contract-doc — REST API.md](https://github.com/BingX-API/BingX-Standard-Contract-doc/blob/main/REST%20API.md)
- [BingX-API/BingX-swap-api-doc](https://github.com/BingX-API/BingX-swap-api-doc)
- [BingX API Docs](https://bingx-api.github.io/docs/)
