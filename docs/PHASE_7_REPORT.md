# PHASE 7 — Backtesting Engine

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:回測不偷看未來、算成本、有停損與強平、報告誠實的指標。** 已達成。

---

## 1. 舊回測的五個偏誤

Phase 0 稽核把「回測偏誤」列為第二大問題。逐一列出,以及現在的做法:

| # | 舊做法 | 後果 | 現在 |
|---|---|---|---|
| 1 | 第 i 根收盤產生訊號,**同一根**收盤價成交 | 前視偏誤。實際交易不可能用剛知道的收盤價成交 | 第 i 根收盤決定,**第 i+1 根開盤價**成交 |
| 2 | 停損停利只比對 `close` | 盤中穿刺停損的 K 棒不算停損,勝率被系統性高估 | 用 `high` / `low` 判定 |
| 3 | 沒有成本模型(或只有固定 0.1%) | 手續費、滑點、點差、資金費用全部忽略 | `CostModel` 全部納入 |
| 4 | 只做多 | 空方機會與空方風險都看不到 | 多空對等 |
| 5 | 每筆押上 100% 資金完全複利 | 報酬率被指數級放大 | 固定風險部位大小,與 Risk Engine 同一套公式 |

另外舊回測**完全沒有強平概念**,所以高槓桿的災難在報表上看不見。

---

## 2. 新引擎

```
agmcis/backtest/
  costs.py     成本模型(maker/taker、滑點、點差、資金費率、清算費)
  engine.py    逐根 K 棒推進;進場、停損、停利、強平、策略出場
  metrics.py   完整指標與誠實性警告
  legacy.py    把 strategies/ 底下的舊策略模組接上新引擎
```

### 每根 K 棒的處理順序

```
1.  檢查既有部位出場(用這根的 high / low)
1b. 上一根策略喊的出場 -> 這根開盤價成交
2.  上一根掛著的進場訊號 -> 這根開盤價成交
3.  最後才產生新訊號(signal_fn 只看得到 rows[:index+1])
```

順序本身就是不偷看未來的保證:訊號一定比成交晚一根。

### 同根 K 棒同時觸及停損與停利

沒有逐筆成交資料就無法知道誰先到。**一律假設停損先到。**
寧可低估績效,也不要高估 —— 反過來假設會讓結果系統性偏樂觀。

---

## 3. 過程中修掉的三個自己的錯

### 3.1 出場順序寫反了

第一版我無條件「先檢查強平、再檢查停損」。錯的。

> 做多,進場 100,停損 99,槓桿 10x → 強平價 91。
> 一根跌到 85 的 K 棒裡,價格是**先經過 99 才到 91**。

那筆應該是正常停損,舊寫法會回報成強制平倉。修正:

```python
adverse_level = max(stop_price, liquidation_price)      # 做多
adverse_level = min(stop_price, liquidation_price)      # 做空
```

**離進場價較近的那個先觸發**,不是無條件先看強平。

### 3.2 槓桿上限讓強平價剛好等於停損價

我在引擎裡自己算 `(1 - mmr) * 100 / stop%`,結果強平價**剛好等於**停損價,
邊界上誰先觸發取決於浮點誤差。這跟 Phase 5 修過的同一類問題。

改成直接用 Risk Engine 的 `max_leverage_for_stop()`(含
`LIQUIDATION_SAFETY_FACTOR = 0.5`)。理由有兩個:
一是安全邊界本來就是為這件事存在的,二是**回測若允許風控不會放行的槓桿,
結果就沒有參考價值**。

停損距離大到連 1x 都會先被強平(>= 90%)時,直接不開倉。

### 3.3 跳空穿過停損還假設在停損價成交

開盤價已經穿過觸發價時,不可能還在觸發價成交。現在取兩者中較差的一邊,
並且成交價若已穿過強平價,那筆就記為強平而不是停損。

---

## 4. 指標

`metrics.compute()` 輸出:勝率、Profit Factor、**Expectancy(USDT 與 R)**、
平均盈虧、Payoff Ratio、最大回撤、Sharpe、Sortino、Calmar、Recovery Factor、
平均持倉根數、MFE / MAE、總手續費與資金費用、**成本拖累**、出場原因分佈、
多空筆數、強平次數。

誠實性警告(不是裝飾,是防止自欺):

- 沒有任何交易 → 明說可能是條件太嚴或資料不足
- 沒有虧損單 → Profit Factor 標記為 **n/a**,不是無限大
- 交易筆數 < 30 → 樣本太小,不足以下結論
- 出現強平 → 明確標記
- 期望值 <= 0 → 明確標記

勝率高但期望值為負的策略照樣會把帳戶打光,所以 Expectancy 與 PF
排在勝率前面呈現。

---

## 5. 舊策略模組接上新引擎

`strategy_lab.py` 與 `strategy_optimizer.py` 的 `from backtest_engine import
load_data, run_strategy` **完全不用改**。`backtest_engine.py` 變成薄 shim,
實作在 `agmcis/backtest/legacy.py`。

橋接層做三件事:

1. 資料來源從 **Binance 換成 BingX**,並且走資料品質 gate。
   舊版用 Binance 的價格回測、在 BingX 下單 —— 價格與流動性都不是成交環境。
2. 舊策略模組沒有停損概念,用 **ATR 停損**(預設 2 × ATR)補上。
   沒有停損的訊號在新引擎會被直接拒絕,這是 Phase 5 定下的鐵律。
3. `sell_signal` 變成引擎的 `exit_fn`,一樣是**第 i 根決定、第 i+1 根開盤成交**。

策略邏輯本身一個字都沒改。改掉的是回測方法。

---

## 6. 順手修掉的資料誠實性問題

`market_center.py` 抓不到行情時把價格與漲跌幅**塞 0**,於是交易所整個掛掉時,
報告會很有自信地說「市場中性」—— 正是 Phase 0 稽核講的
「分不出市場中性與資料壞掉」。而且它的資料來源也是 Binance。

現在:BingX 行情;抓不到就 `available=False` 且不參與排序;
可用比例低於 60% 時 `market_sentiment` 回 `"資料不足"`、`market_score` 回 `None`。
`ai_report.py` 據此輸出「資料不足,無法判斷市場方向」,而不是硬擠一句建議。

`debug_news.py` 原本整段寫在模組層,import 就會打外部 API。已收進 `main()`。

---

## 7. 測試

```
全部:492 passed
本階段新增:
  tests/test_backtest.py                 35
  tests/test_backtest_legacy_bridge.py   13
  tests/test_market_center.py            11
```

其中兩個測試是特意防止「通過了但什麼都沒驗到」:

- `test_real_strategy_modules_actually_produce_trades` —— 參數順序接錯時
  每個 `buy_signal` 都會安靜地回 False,回測回報 0 筆交易,只斷言
  「不拋例外」的測試照樣通過。一筆都不交易的管線跟會崩潰的管線一樣是壞的。
- `test_the_argument_order_matches_what_the_modules_expect` —— 用探針模組
  把十個參數的順序逐一釘死。

另外修掉一個**依測試順序而時好時壞**的既有測試:
`tests/test_exchange_engine.py` 用 `os.environ.setdefault()` 設重試次數,
但 settings 是在模組載入時就把環境變數讀成常數的 —— 整包一起跑時別的模組
可能先載入了 settings,setdefault 就完全沒有作用。改成明確把
`max_retries` 傳進 engine。

`requirements-dev.txt` 新增:`starlette.testclient` 需要 `httpx`,
沒有它 `tests/test_api_surface.py` 會以 RuntimeError 失敗,很容易被誤判成程式壞掉。

---

## 8. 尚未做的事

- **這個引擎一次只持有一個部位。** 多部位與投資組合層級的資金分配是後面的 Phase。
- **OOS / Walk Forward / Monte Carlo / 過擬合偵測** → Phase 8。
- **維持保證金率用保守固定值 0.10。** BingX 實際依合約分層,
  精確值要等 Phase 11 用真實合約規格校準。
- **資金費率用固定 0.01% / 8h。** 歷史 funding 要另外抓。
- 本報告中的任何回測數字都來自合成資料,**不是任何策略有優勢的證據**。

---

## 9. 下一步

Phase 8 — Strategy Lab:OOS、Walk Forward、Monte Carlo、
Strategy Health Score、過擬合偵測、Ensemble。
