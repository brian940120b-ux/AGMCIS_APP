# PHASE 4 — Trading Rules Engine

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:不合規的 intent 一律被擋並記錄原因。** 已達成。

---

## 0. 三個懸而未決的問題,已依建議定案

使用者指示「以我傳的為基準,你給予建議,直接做」。三個問題的定案:

| 問題 | 定案 | 理由 |
|---|---|---|
| Standard Futures | **選項 A + B**:現在只做 Perpetual,`MarketType` 架構保留 | Standard 在 ccxt 沒有可用的 API 面(Phase 3 實測),硬做就是猜 |
| 風控參數 | **保守預設值,全部可由 `.env` 覆寫** | Paper 模式下安全;上 Live 前必須由使用者依帳戶規模重新決定 |
| 歷史損益 | **保留原值不改寫,但統計把兩種基準分開** | 改寫歷史財務紀錄不可逆,且會讓過去的決策記錄失去意義 |

---

## 1. Trading Rules Engine

`agmcis/execution/rules_engine.py`。

### 職責分界

```
Signal -> Risk Engine -> **Trading Rules Engine** -> Execution Engine -> Exchange
```

| 層 | 回答什麼問題 |
|---|---|
| Risk Engine | 要不要開?開多大?幾倍槓桿? |
| **Trading Rules** | **交易所收不收這組數字?** |
| Execution Engine | 真的送出去,並管理狀態機 |

這一層**刻意不知道**帳戶回撤多少、今天虧了多少、連續虧幾筆。
有兩個測試鎖住這個分界:模組原始碼不得出現 `risk_control` 或 `risk_limits`,
而 `TradingContext` 不得有 `drawdown` / `daily_loss` / `equity` 這類欄位。

### 驗證項目(Master Prompt 第 12 條)

| 檢查 | 行為 |
|---|---|
| 合約是否存在 / 規則拿不拿得到 | 拿不到就乾淨地拒絕,不繼續往下 |
| 方向 | 必須是明確的多或空 |
| Order side | 由方向推導,不讓呼叫端自己填 |
| **Position side** | One-Way 用 `BOTH`,Hedge 用 `LONG`/`SHORT` |
| 訂單型別 | 比對交易所支援清單 |
| 價格 | 進位到 `tick_size`;市價單不帶價格 |
| 數量 | 由名目換算後無條件捨去到 `step_size` |
| 最小 / 最大量、`step` 倍數 | 全部檢查 |
| 最小名目 | 檢查 |
| 槓桿 | 不得超過該合約上限 |
| 保證金 | 不得超過可用餘額 |
| 停損 | 進位後**重新確認方向仍然正確** |
| 停利 | 同上 |
| 重複倉位 | 已有未平倉位就不重複開 |
| 重複訂單 | client order id 不得重複 |
| Reduce only | 沒有部位就不能送 reduce-only |

### 最重要的不變量:調整只會讓倉位變小

自動進位很方便,但它**不可以變成偷偷放寬風控的決定**。

```
要求名目 5000 USDT  ->  實際 4940.03 USDT
(0.0769... 張捨去到 step 0.001 -> 0.076 張)
```

數量一律無條件捨去。停損只往「更早離場」的那一側進位:
做多往下、做空往上。有測試鎖住 `notional <= size_usdt × leverage`。

### 能修的就修,不能修的才拒絕

| 情況 | 處理 |
|---|---|
| 數量不是 `step` 的倍數 | **自動進位**,並把調整列在 `adjustments` |
| 價格不是 `tick` 的倍數 | **自動進位** |
| 數量低於最小下單量 | **拒絕** —— 硬補上去等於偷改風控決定的倉位大小 |
| 保證金超過餘額 | **拒絕** |

調整永遠會被回報,不會靜默發生。

### 一次回報所有問題

```
槓桿 200x 超過該合約上限 125x;
保證金 9999 超過可用餘額 10;
BTC/USDT 已有未平倉位,不重複開倉
```

下單被拒之後一次修好,比來回試三次好。

---

## 2. Client Order ID

`agmcis/execution/client_order_id.py`。格式 `AGMCIS-20260911-BTC-000001`。

用途(Master Prompt 第 16 條)中**防重複下單最重要**:
送單後如果連線斷掉,我們不知道交易所收到沒有。帶著同一個 id 去查就知道那筆存不存在,
而不是盲目重送 —— 重送是重複開倉最常見的來源。這與 Phase 3 的 `RECONCILE` 策略是一組的。

`is_ours()` 讓對帳時能分辨哪些單是 AGMCIS 開的、哪些是使用者手動開的。

⚠️ 長度上限與允許字元採保守策略(32 字元,只用英數與連字號),
因為 BingX 的實際限制在這個環境無法完整查證。上線前請用
`scripts/verify_bingx.py` 對真實 API 確認。

---

## 3. 歷史損益基準分離

Phase 0.5 之前寫入的已平倉資料,損益漏乘了槓桿。

**原值一律保留,不回頭改寫。** 改寫歷史財務紀錄不可逆,
而且會讓當時的決策記錄失去意義。

但也不能讓兩種基準混在同一個勝率裡 ——
一筆 5x 的交易在舊基準下損益只有實際的五分之一,混著算出來兩邊都不是。

所以 `get_trade_analytics()`:

- 預設**只納入 `LEVERAGED`(基準正確)的交易**
- 舊基準的交易列在 `legacy` 區塊,帶 `comparable: False` 與說明文字
- 兩者筆數都回報 —— 排除不等於隱藏,使用者要看得到那些交易還在
- `pnl_basis` 欄位標明這組數字是 `LEVERAGED` 還是 `MIXED`
- `include_legacy=True` 可以選擇混合計算,但會被標成 `MIXED`
- 未標記 `pnl_basis` 的資料保守假設為舊基準

有測試鎖住:兩筆新資料全贏、兩筆舊資料全輸時,預設勝率是 100% 而不是 50%。

---

## 驗證

```
$ pytest tests/ -q
342 passed
```

| 檔案 | 測試數 | 鎖住什麼 |
|---|---|---|
| `tests/test_rules_engine.py` | 31 | 驗證鏈、調整只會縮小倉位、positionSide、一次回報所有問題、職責分界 |
| `tests/test_pnl_basis.py` | 9 | 舊基準不污染勝率、排除但不隱藏、opt-in 混合計算 |

### 一個測試自己的錯誤

`test_limit_order_carries_a_rounded_price` 原本用 `price % 0.1 == 0` 判斷是否為
tick 的整數倍,結果失敗。實際檢查後價格是正確的 `65000.4`,
問題在於浮點數取餘:`65000.4 % 0.1` 得到 `0.0999...` 而不是 `0`。
是測試寫錯不是程式錯,改用 `validate_price()` 判斷。

---

## 已知仍未解決

- **Risk Engine 尚未接上**(Phase 5)。目前 `size_usdt` 與 `leverage`
  仍由呼叫端傳入,Position Sizing(`equity × risk% ÷ 停損距離`)還沒實作
- **Execution Engine 尚未實作**(Phase 12)。訂單狀態機、開倉後強制 SL 保護、
  對帳都還沒有。`create_order` 可以呼叫但不該呼叫
- **兩條訊號管線仍未合併**(Phase 6)
- **回測仍有 look-ahead bias**(Phase 7)
- **Standard Futures 不支援**(定案為選項 A,只做 Perpetual)
- 私有端點仍待在 VPS 上用 `scripts/verify_bingx.py` 驗證
