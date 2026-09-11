# PHASE 10 — Paper Trading 2.0

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:模擬損益扣掉成本,而且與回測用同一套成本模型。** 已達成。

---

## 1. 為什麼要做

Phase 10 之前模擬盤**完全沒有成本**:沒有手續費、沒有滑點、沒有點差、
沒有資金費用、沒有強平。

那種損益不只是「數字偏高」。真正危險的是它會讓**本來沒有優勢的策略
看起來有優勢** —— 一天進出好幾次的策略,光手續費就能吃掉全部價差。
然後你會拿一個沒有優勢的東西去開實單。

---

## 2. 與回測共用同一個成本模型

`agmcis/execution/paper_costs.py` 直接用 `agmcis/backtest/costs.py` 的
`CostModel`,費率從 settings 讀。

這不是為了省程式碼。兩邊用不同的成本假設,**模擬盤就沒辦法拿來驗證回測**
—— 而那正是模擬盤的用途:回測說這個策略有優勢,模擬盤要在同樣的成本條件下
重現它。費率不同時,你分不出「策略不行」和「成本假設不同」。

| 項目 | 預設值 | 環境變數 |
|---|---|---|
| Maker 手續費 | 0.02% | `PAPER_MAKER_FEE` |
| Taker 手續費 | 0.05% | `PAPER_TAKER_FEE` |
| 滑點 | 0.05% | `PAPER_SLIPPAGE_PCT` |
| 點差 | 0.02% | `PAPER_SPREAD_PCT` |
| 資金費率 / 8h | 0.01% | `PAPER_FUNDING_RATE_8H` |
| 清算費 | 0.5% | `PAPER_LIQUIDATION_FEE` |
| 維持保證金率 | 10% | `PAPER_MAINTENANCE_MARGIN_RATIO` |

---

## 3. 納入的四件事

### 3.1 成交價含點差與滑點,永遠在不利的一邊

下單看到 100,做多實際成交在 100.06;平倉時再被扣一次。
這不是悲觀,是市價單的實際行為。

`requested_entry_price` 與 `entry_price` 分開存,兩者的差就是進場滑價 ——
分開存才看得出成本跑到哪裡去了。

### 3.2 手續費

依**名目價值**(保證金 × 槓桿)計算,不是依保證金。
3x 槓桿的 1000 USDT 保證金,手續費是按 3000 USDT 算的。

### 3.3 資金費用

依實際持倉時數累計。費率為正時**做多付、做空收** ——
方向搞反會讓空單的成本被系統性高估,有測試釘住。

### 3.4 強制平倉

`position_monitor` 現在會檢查強平。判定順序與回測引擎一致:
**停損與強平之中,離進場價較近的那個先觸發**,不是無條件先看強平。
做多停損 99、強平 91 時,價格是先經過 99 的,那筆是正常停損。

強平時在**強平價**成交,另收清算費,而且**不套用滑點** ——
強平是在強平價成交的,再加一次滑點等於把同一件事算兩遍。
虧損上限是保證金。

---

## 4. 開倉時新增的兩道守門

滑價與強平價都是開倉當下就能算出來的,所以在開倉就擋:

1. **滑價後停損跑到錯邊** → 拒絕開倉。那張單一開就會被停掉。
2. **強平價比停損更接近進場價** → 拒絕開倉。那個倉位實際上根本用不到停損。

第 2 點與 Phase 5 的風控、Phase 7 的回測引擎是同一條規則,現在模擬盤
也守同一條。

配套有一個測試專門防止守門過嚴:`test_a_sane_combination_still_opens`。

---

## 5. 歷史資料的處理

Migration `003_paper_trading_costs.sql` 加欄位,既有資料**一律保留原值**
並標記 `cost_basis = 'LEGACY_NO_COSTS'`。不回頭改寫任何歷史數字 ——
與 Phase 0.5 處理 `pnl_basis` 的做法一致。

`get_paper_summary()` 的成本統計**只算 `WITH_COSTS` 的交易**,
並另外回報有多少筆是舊資料。混在一起平均,「加了成本之後績效差多少」
這個問題就永遠問不出答案。

---

## 6. 又一次 SELECT 漏欄位的預防

Phase 0.5 抓到過:`get_trades()` 沒有 SELECT `leverage`,於是每一筆交易
不管實際槓桿多少都變成預設的 3x。

同一類錯在 Phase 10 會更嚴重:漏掉 `liquidation_price`,
`position_monitor` 就**永遠不會觸發強平**,而且不會有任何錯誤訊息。

所以加了三個測試把 `TRADE_COLUMNS` 與 `_row_to_trade()` 的索引綁在一起:
欄位清單加了東西但 mapper 沒跟上,測試就會紅。

---

## 7. 測試

```
全部:656 passed
本階段新增:tests/test_paper_costs.py  34
```

其中幾個是針對「方向搞反也不會報錯」這類無聲錯誤:

- `test_shorts_receive_funding_when_the_rate_is_positive`
- `test_liquidated_closes_do_not_also_pay_slippage`
- `test_a_normal_close_does_pay_slippage`
- `test_it_matches_the_backtest_formula`(強平價公式必須與回測一致)

`tests/test_pnl_leverage.py` 與 `tests/test_position_monitor.py` 的
fixture 跟著 schema 與回傳型別更新。

---

## 8. 已知的樂觀偏誤(寫在程式裡,不是藏著)

- **`position_monitor` 比對的是輪詢當下的單一價格,不是這段期間的
  high / low。** 兩次輪詢之間穿刺停損又彈回來的行情,模擬盤看不到,
  但交易所的觸發單會成交。**這會讓模擬勝率偏高。**
  要修掉需要 WebSocket 逐筆價格(Phase 12 / 13)。
- **成本在平倉時一次結算**,不是在發生的當下逐筆扣。總額正確,時點簡化。
  對「這個策略扣掉成本還剩多少」沒有影響,但資金曲線的形狀會略有差異。
- **維持保證金率用固定 10%。** BingX 實際依合約分層,精確值要等 Phase 11
  用真實合約規格校準。
- **資金費率用固定 0.01% / 8h。** 真實費率每 8 小時浮動,而且極端行情下
  差很多。接真實 funding 要等有歷史費率資料。
- **假設全部是 Taker。** 限價單成交會拿到 Maker 費率,成本會低一些。
  `CostModel` 支援 maker,但目前模擬盤一律用 taker —— 保守的一邊。

---

## 9. 下一步

Phase 11 — BingX Test Validation:用 VST 測試網驗證合約規格、
最小下單量、精度、實際費率與強平規則,把上面幾個「保守固定值」換成真實值。
