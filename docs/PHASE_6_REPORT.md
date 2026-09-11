# PHASE 6 — Strategy Engine

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:Dashboard 顯示的訊號 = 實際下單依據的訊號;支援做空。** 已達成。

---

## 1. 兩條互相矛盾的管線,合併成一條

這是 Phase 0 稽核最根本的發現。原本:

| | 管線 A | 管線 B |
|---|---|---|
| 幣種池 | `market_universe`(12 檔硬編碼) | `exchange_universe`(成交量前 50) |
| 指標 | `technical_service`(EMA20/**60**) | `strategy.analyze_symbol`(EMA20/**50**) |
| 評分 | 基準 50,趨勢 ±25、MACD ±20 | 從 0 累加,EMA 35、RSI 15、MACD 20、量 15、ADX 15 |
| 訊號詞彙 | emoji(`🟢 Strong Buy`) | 中文(`做多`) |
| 用途 | **Dashboard 顯示** | **實際開倉** |

連 EMA 週期都不一樣,所以同一檔標的在兩條管線會得到**不同的趨勢判斷**。
結果就是 Dashboard 上看到的訊號,不是實際下單所依據的訊號。

現在只有一條:

```
universe -> 資料品質 gate -> 指標 -> 市況 -> 策略集成 -> 評分 -> Signal
```

`scanner_service.py` 與 `smart_ranking.py` 都變成薄 shim,把統一管線的
`Signal` 轉回各自的舊格式,呼叫端(api、v2、v3、auto_trader、
opportunity_scanner、rebalance_engine)完全不用改。

有測試鎖住:兩個 shim 都必須 import `agmcis.signal.pipeline`,
而且沒有任何活的模組還在 import 舊的第二套評分 `strategy.analyze_symbol`
(用 AST 檢查真正的 import,不是檢查字串是否出現在註解裡)。

---

## 2. 策略層:每個策略都支援做空

舊 `strategies/*.py` 的 `sell_signal` 是「平多」不是「做空」。
所以系統宣稱支援 Long/Short,但**策略層根本產不出空單**。

新的四個策略每一個都同時支援兩個方向:

| 策略 | 做多條件 | 做空條件 | 適用市況 |
|---|---|---|---|
| `trend_following` | EMA20>EMA50 且 MACD 多 | EMA20<EMA50 且 MACD 空 | 僅趨勢市 |
| `breakout` | 突破布林上軌 | 跌破布林下軌 | 不挑 |
| `momentum` | MACD 柱正 且 RSI 50-70 | MACD 柱負 且 RSI 30-50 | 不挑 |
| `mean_reversion` | RSI<30 且觸下軌 | RSI>70 且觸上軌 | **僅盤整市** |

有一個測試用 AST 檢查每個內建策略的原始碼都同時出現
`Direction.LONG` 與 `Direction.SHORT`。

### 策略知道市況

`trend_following` 在盤整市不出手 —— 那是它最容易虧錢的環境。
`mean_reversion` 只在盤整市出手 —— 單邊趨勢市裡「超賣」可以一路更超賣。
舊策略完全不知道市況,這是它們在錯誤環境下被反覆洗出場的原因。

### 停損依方向擺放

舊 scanner 一律算 `price - atr*2`,做空時停損會被放在進場價**下方** ——
那在開倉的瞬間就會觸發。現在 `Strategy.atr_levels()` 依方向計算,
有跨方向的不變量測試。

---

## 3. 一個我自己造成、實測才發現的嚴重問題

我第一版把共識規則的 `min_agreeing` 設成 **2**,理由是「不要只依賴一個策略」。

用合成資料端到端測試時,**三種市況全部回傳 WAIT**。診斷後發現:

```
市況 STRONG_BULL, ADX 70.5
  trend_following  -> 做多  conf=60
  breakout         -> 觀望  價格在布林通道內
  momentum         -> 觀望  RSI 87.3 已超出 50-70 動能區
  mean_reversion   -> 觀望  只在盤整市出手
```

只有一個策略出手,所以 `min_agreeing=2` 把它擋掉了。
**強趨勢正是順勢系統最該交易的情境**,結果我用一條看起來保守的規則
把系統的核心功能關掉了。

根因是我把「棄權」當成「反對」。四個策略在找的是**不同的 setup**:
`breakout` 沒出手不代表它反對趨勢,只代表它沒看到突破;
`mean_reversion` 在趨勢市棄權是它的設計,不是它有意見。

### 修正

- `min_agreeing` 預設改為 **1**
- **反對票仍然是硬否決** —— 有策略投反方向一律 WAIT,這條不變
- 佐證強度改由**分數**反映:`scorer` 的 `strategy_consensus` 項目
  用同向比例加權,只有一個策略同向的訊號自然拿不到高分
- 真正的品質門檻是 `MIN_SIGNAL_SCORE`(預設 70)

修正後的實測:

| 市況 | 方向 | 分數 | 是否通過門檻 |
|---|---|---|---|
| 強上升趨勢 | 做多 | 72.33 | ✓ |
| 強下降趨勢 | **做空** | 72.33 | ✓ |
| 盤整 | 做空(動能) | 47.58 | ✗ 被分數擋掉 |

有 5 個迴歸測試鎖住「管線真的能交易」—— 一個永遠不交易的管線跟壞掉沒兩樣。

---

## 4. Market Regime Engine

`agmcis/analysis/regime.py`。ADX < 20 盤整、≥ 40 強趨勢、中間趨勢成形,
再由 EMA 決定多空。波動度依 ATR 佔價格比例分四級。

兩個設計決定:

- **極端波動(ATR ≥ 5%)視為不可交易。** 那種環境下停損很容易被無意義地掃到。
- **BTC 當大盤參考。** 個別標的看起來多頭但 BTC 正在跌時,那個訊號降一級。
  加密貨幣的個股大多跟著 BTC 走,逆著大盤做需要更強的理由。

`UNKNOWN` 代表「不知道」,不是「中性」—— 而且不可交易。

---

## 5. 單一 0-100 評分

`agmcis/signal/scorer.py`。權重加總 100:

| 項目 | 權重 | 為什麼 |
|---|---|---|
| 策略共識 | 30 | 多個獨立方法同意最有價值 |
| 趨勢一致 | 15 | |
| 動能 | 15 | |
| 市況支持 | 15 | |
| 量能 | 10 | |
| 波動度適中 | 10 | 太低沒行情,太高停損容易被掃 |
| 風報比 | 5 | |

**缺資料的項目給 0 分,不是給一半。** 沒有資料不等於中性 ——
那正是 Phase 0 稽核抓到的根本問題(舊系統遇到 None 會給 50 分的「中性」分數,
讓系統分不出市場中性與資料壞掉)。

`ScoreBreakdown` 保留每一項的得分與 `coverage_pct`,
讓 Dashboard 可以回答「為什麼是 87 分」而不只是顯示 87。

⚠️ **權重是起始值,不是回測結果。** Master Prompt 第 27 條明確說
「實際權重由 Quant Research Agent 研究,不要固定認為這一定最佳」。
所有策略門檻(RSI 30/70、ADX 25、量能 1.2 倍)同樣是業界慣例值。
Phase 7 的回測與 Phase 8 的 Walk Forward 之後才會知道哪些真的有效。

---

## 6. 其他

- **TOP N 不硬選。** `top_opportunities()` 會過濾分數門檻,
  回傳數量可能少於要求,也可能是空的(Master Prompt 第 53 條)。
- **並行掃描。** 原本逐檔同步跑,50 檔要等很久。現在用 ThreadPool。
- **單一策略出錯不影響整輪**,但一定會記錄。
- **Signal 的形狀在所有路徑上一致** —— WAIT 路徑也會設好附加欄位,
  否則消費端要對每個欄位做 getattr 防禦,遲早有人漏掉。

---

## 驗證

```
$ pytest tests/ -q
433 passed
```

| 檔案 | 測試數 | 鎖住什麼 |
|---|---|---|
| `tests/test_strategy_pipeline.py` | 42 | 指標只有一份實作、市況判斷、四個策略都能做空、策略知道市況、共識規則、評分、TOP N 不硬選、管線真的能交易 |
| `tests/test_data_quality_gate.py` | 12(改寫) | 壞資料在管線中變成 WAIT Signal、停損方向不變量 |

端到端用合成資料驗證過三種市況,並確認產出的 Signal 能轉成 `TradeIntent`
餵進 Risk Engine。

---

## 已知仍未解決

- **回測仍有 look-ahead bias**(Phase 7)。在回測修好之前,
  所有策略參數與評分權重都只是慣例值,沒有證據支持
- **Execution Engine 尚未實作**(Phase 12)
- **12 個 Agent 尚未存在**(Phase 9)。訊號層已經就位,
  Agent 層要做的是在這之上加入新聞、情緒與總經判斷
- `ai_decision_service.py` 仍是舊的加權指標計分,尚未併入統一管線
- 私有端點仍待在 VPS 上用 `scripts/verify_bingx.py` 驗證
