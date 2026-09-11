# PHASE 9 — Multi-Agent

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:十二個 Agent 各自出意見,彙總成 TradeIntent,而且 Agent 構不到下單。** 已達成。

---

## 1. 架構鐵律

Master Prompt 第三十二節:**AI / Agent 不得直接呼叫下單 API。**

```
Agents -> AgentOpinion -> Consensus -> TradeIntent
       -> Supervisor(可否決) -> Risk Engine(hard gate) -> Execution Engine
```

這條規則不是靠自律維持的:

- `agmcis/agents/` 底下**不得 import** exchange、execution、ccxt、database。
- Agent 模組裡**不得出現** `create_order` / `cancel_order` / `set_leverage` /
  `withdraw` / `transfer` 等呼叫。
- `AgentContext` 裡沒有交易所連線、沒有 DB handle —— 給不到的東西才是真的拿不到。
- `AgentOpinion` 裡沒有 size、沒有 leverage、沒有 order type。

四條都有測試用 **AST 掃描**釘死。LLM 與多因子評分都會出錯,而且會用很有
說服力的語氣出錯。把下單能力放在它們構不到的地方,錯誤就只會變成一個
被拒絕的 intent,而不是一張真的送出去的單。

---

## 2. 十二個 Agent

| 組別 | Agent | 職責 |
|---|---|---|
| 方向 | trend | EMA 排列 + ADX 強度 |
| 方向 | momentum | MACD 柱狀圖方向與強度 |
| 方向 | mean_reversion | RSI 極端 + 布林,**只在盤整發言** |
| 方向 | breakout | 通道壓縮後的突破 |
| 方向 | volume | 量能確認,不主張方向 |
| 方向 | btc_correlation | 山寨幣看 BTC 臉色 |
| 環境 | volatility | 極端波動時投 WAIT,**從不主張方向** |
| 環境 | regime | 市況不可交易時投 WAIT |
| 環境 | funding | 極端資金費率是反向訊號 |
| 管理 | position_risk | 相關部位集中度 |
| 管理 | exit | 既有部位,**不參與進場投票** |
| 管理 | news | 外部消息面 |

---

## 3. 棄權不是反對票

這是 Phase 6 用血換來的教訓,在 Agent 層再次出現,所以寫進型別:

```
WAIT    = 「我看得懂這個盤,而我的結論是不要進場。」
ABSTAIN = 「這不是我負責判斷的東西,別把我算進去。」
```

`Vote.opposes()` 只有在兩票都是明確且相反的方向時才回 True。
棄權不對立任何人。

把棄權當反對票,專職抓特定型態的 Agent 在型態沒出現時就變成反對者,
整個系統會永遠不交易 —— 而一個永遠不交易的系統,跟一個會崩潰的系統一樣是壞的。

配套的實際案例:`VolumeAgent` 原本在量能不足時投一張**信心 0** 的 WAIT。
信心 0 的票在共識裡權重是 0,等於投了一票沒有力道的反對 —— 那不是它的本意。
現在明確給了信心值,而「信心 0 的 WAIT 沒有否決力」這個設計本身也有測試說明。

---

## 4. Consensus

| 規則 | 理由 |
|---|---|
| 棄權不計入任何一邊 | 見上 |
| WAIT 權重 ≥ 35% 直接否決 | 環境型 Agent 說「這盤不能碰」時,方向票再強也不該進場 |
| 至少 2 張方向票 | 單一 Agent 的判斷不構成共識 |
| 勝方需佔方向票權重 60% 以上 | 多空分歧過大時不交易 |
| 權重 = Agent 權重 × 該 Agent 信心 | 信心 30 的票不該和信心 90 的等重 |
| `exit` 不參與進場投票 | 它的職責是既有部位,不是要不要開新倉 |
| **停損取最保守的一個** | 調整只能讓部位更安全,不能更寬鬆(Phase 4 起的規則) |
| 沒有 Agent 給停損時用預設距離 | **絕不允許沒有停損的 intent** |
| Consensus 不決定 size 與 leverage | 那是 Risk Engine 的職責 |

---

## 5. Supervisor

Consensus 回答「這些意見加起來是什麼結論」。
Supervisor 回答另一個問題:**這些意見本身可不可信?**

它只能做兩件事:**否決**一個已成立的決議(降級成 WAIT),以及**回報**健康狀態。

它**不能**製造交易。一個能把 WAIT 變成進場的監督者就不是監督者 ——
這條有專門的測試。

否決條件:

- 出錯的 Agent > 25%:半個系統壞掉時的「共識」沒有意義
- 棄權的 Agent > 75%:沒人看得懂這個盤,那就不要碰
- 十個以上 Agent 意見**完全一致**:獨立的 Agent 不會完全同意,
  完全同意通常代表它們在看同一個東西,或全部壞掉

另外它會追蹤跨次數的投票紀錄,連續 20 次投同一票的 Agent 會被標記為
「可能已經卡住或邏輯失效」。

---

## 6. 接線:又一次把重複的評分收掉

### 6.1 auto_trader

```
之前:scan_market() -> 從掃描結果的欄位組 TradeIntent -> Risk Engine
現在:scan_market() -> Agent 共識 -> TradeIntent -> Supervisor -> Risk Engine
```

掃描層現在只負責「要看哪些標的」,不再負責「要不要進場」。
**TradeIntent 的產生者只有一個。** Phase 6 合併過兩條互相矛盾的訊號管線,
不能在這裡又長出第二條。

Agent 層炸掉時該標的直接跳過,**絕不退回「用掃描結果直接開倉」**——
有測試鎖住這件事。

### 6.2 ai_decision_service(持倉評估)

這是系統裡的**第三套**獨立評分:自己一套 `score_rsi` / `score_trend` /
`score_macd` / `score_atr` 加權出 confidence。那套權重跟訊號管線不一樣,
也跟風控不一樣 —— 同一個部位在三個地方會得到三種不同的結論。

現在它跑同一套 Agent 群,只是多帶部位資訊。Dashboard 看到的評估,
就是系統內部真正在用的評估。翻譯規則裡有一條刻意的優先順序:
**風險先於機會** —— 停損快到了這件事,比「共識還是看多」重要。

### 6.3 decision_engine.py 已移除

它是**第四套**評分(把 confidence 翻成 emoji 字串)。接上 Agent 層之後
沒有任何呼叫端,留著只會誘使人把它再接回去。

---

## 7. 資料缺失一律棄權,不填 0

`build_context()` 取不到的東西一律留 `None`。
用 0 填補會讓「沒資料」看起來像「數值正常」:`FundingAgent` 看到 `None`
會棄權,看到 `0` 會說「費率正常」—— 完全不同的兩件事。

資料品質不合格時整個 context 的 `data_ok` 是 False,十二個 Agent 全部棄權,
Consensus 回「所有 Agent 都棄權,沒有可用意見」。

---

## 8. 測試

```
全部:616 passed
本階段新增:
  tests/test_agents.py               50
  tests/test_ai_decision_service.py  12
```

最重要的四個:

- `test_no_agent_module_imports_the_exchange_or_execution_layer` —— AST 掃描
- `test_no_agent_module_calls_an_order_function` —— AST 掃描
- `test_the_supervisor_cannot_turn_a_wait_into_a_trade`
- `test_every_agent_survives_a_context_with_almost_no_data` —— 缺資料是常態不是例外

同時保留「該進場時會進場」:`test_a_clean_bull_setup_produces_a_trade_intent`。

`tests/test_risk_gate.py` 的接縫跟著架構移動了:原本 patch 掃描結果的欄位,
現在 patch `agent_pipeline`。這也讓其中兩個測試從「跑真網路而且為了錯的理由
通過」變回真正的單元測試(該檔案執行時間 5.5 秒 -> 0.014 秒)。

---

## 9. 尚未做的事

- **Agent 之間沒有辯論。** 目前是一輪投票。多輪辯論(Agent 看到別人的理由
  後修正意見)留給後面的 Phase,而且要小心:多輪會放大群體迷思,
  Supervisor 的「一致到不正常」檢查就是為了這個。
- **沒有 LLM Agent。** 十二個都是規則型。接 LLM 時介面不用改
  (照樣只能回 `AgentOpinion`),但要加上輸出驗證與成本控制。
- **Agent 權重是寫死的常數**,不是學出來的。自我學習排在 Phase 15。
- **ExitAgent 只會提示,不會平倉。** 真正的出場動作要等 Phase 12 的
  Execution Engine。
- **相關標的清單要由呼叫端傳入**,系統還沒有自動算相關性。

---

## 10. 下一步

Phase 10 — Paper Trading 2.0:把 Phase 7 的成本模型
(手續費、滑點、資金費用、強平)接進模擬盤,讓模擬損益與實盤可比。
