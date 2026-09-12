# 第三十節的 12 個 Agent 角色,對應到系統的哪裡

第三十節列了 12 個 Agent 角色。這個系統有 15 個**投票 Agent**,
但它們的名字跟那 12 個不一樣 —— 因為那 12 個裡有一半**不該是投票者**。

這份文件把兩邊對起來,並說明哪幾個是刻意不當 Agent 的。

## 對照表

| 第三十節 | 這個系統 | 是投票者嗎 |
|---|---|---|
| 01 Market Analyst | `regime`、`btc_correlation`、`volatility`、`volume` | ✅ |
| 02 Technical Analyst | `trend`、`momentum`、`mean_reversion`、`breakout` | ✅ |
| 03 Quant Research | `agmcis/lab/`(OOS / Walk Forward / Monte Carlo / 過擬合偵測) | ❌ 離線 |
| 04 News Agent | `news` + `agmcis/risk/news_risk.py` + `macro` | ✅ |
| 05 Sentiment Agent | `sentiment` | ✅ 只投 WAIT |
| 06 Strategy Agent | `agmcis/agents/consensus.py` | ❌ 它是彙總者 |
| 07 Risk Manager | `agmcis/risk/engine.py` | ❌ **它是 hard gate** |
| 08 Execution Agent | `agmcis/execution/engine.py` | ❌ 它不做判斷 |
| 09 Portfolio Manager | `agmcis/risk/portfolio.py` + `correlation.py` | ❌ 風控的一部分 |
| 10 Performance Analyst | `agmcis/review/live_metrics.py` + `attribution.py` | ❌ 事後分析 |
| 11 Self Review | `agmcis/review/self_review.py` + `decision_log.py` | ❌ 事後分析 |
| 12 Supervisor | `agmcis/agents/supervisor.py` | ❌ 它審查投票者 |

其餘投票 Agent(`funding`、`position_risk`、`exit`、`order_book`)
在第三十節沒有對應的角色,但第一節與第十一節要求那幾件事。

⚠️ `order_book` 是 **Agent**,`order_flow` 是 **策略**
(`agmcis/strategy/extra.py`)。兩者名字很像但在不同的層:
Agent 投票,策略產生 verdict。搞混會讓人以為訂單簿的意見被算了兩次。

## 為什麼有六個刻意不是投票者

**Risk Manager 不能投票。** 第十九節說 Risk Engine 是 hard gate,
沒有 Agent 可以繞過它。把它變成一票,就是把一個否決權降級成
「十五分之一的意見」—— 而那正是它存在的理由被取消的那一刻。

**Execution Agent 不能投票,而且不能被 AI 呼叫。** 第三十二節:
AI / LLM 不得直接呼叫 BingX 下單 API。它只執行已經通過風控的裁決。

**Supervisor 不能投票。** 它的工作是檢查投票者可不可信。一個
會投票的監督者,在檢查自己的票。它**只能把 actionable 變成
不 actionable**,不能反過來 —— 那條規則寫在
`agmcis/agents/supervisor.py` 的 `review()` 裡。

**Quant Research、Performance Analyst、Self Review 是離線的。**
它們吃的是已平倉交易,產出的是報告與提案草稿。第七十八節禁止
「自己修改 → 自己測試 → 自己批准 → 自己 Live」,所以研究的結論
只能變成提案(`agmcis/review/proposals.py`),由人批准。

**Portfolio Manager 是風控的一部分。** 組合層的曝險與相關性檢查
發生在 Risk Engine 裡面(第五十九 / 六十節),與單筆風險同一個閘門。
拆成一個會投票的 Agent,會讓「這一籃子已經太集中了」變成一票
而不是一個封鎖。

## Agent 05 Sentiment 只投 WAIT

它有資料來源(新聞標題的關鍵字掃描),但**不投方向**。

理由:社群情緒與價格的關係在不同的市況下會反轉 —— 極度貪婪
有時是續漲有時是頂部。一個會投方向的情緒 Agent,是在把一個
沒有穩定符號的訊號當成有方向的證據。

同樣的規則套用在 `macro`:FOMC 前十分鐘的問題不是方向猜錯,
是波動大到停損沒有意義 —— 那是一個「不要進場」的理由,
不是一個「往哪邊」的理由。

## 這份對照表怎麼驗證

`tests/test_agent_roles.py` 會檢查:

* 表上列的每一個模組路徑都存在
* 標成「不是投票者」的那幾個,**確實不在 Agent registry 裡**
* `sentiment` 與 `macro` 的原始碼裡沒有 `Vote.LONG` / `Vote.SHORT`

第三個是最重要的:一個「只投 WAIT」的約定,如果只寫在 docstring
裡,下一次有人加功能的時候會消失。
