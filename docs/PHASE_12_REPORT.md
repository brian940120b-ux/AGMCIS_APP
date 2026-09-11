# PHASE 12 — Execution Engine

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:開倉後沒有停損保護的部位會被立刻平掉,而且狀態機擋得住非法轉移。** 已達成。

---

## 1. 這一層不做判斷

```
Agents -> TradeIntent -> Risk Engine -> Trading Rules -> Execution Engine -> Broker
```

| 誰 | 回答什麼 |
|---|---|
| Agent + Consensus | 要不要交易?往哪邊? |
| Risk Engine | 可以開嗎?開多大?幾倍? |
| Trading Rules | 交易所收不收這組數字? |
| **Execution Engine** | **把批准的決定安全地送出去,並且知道現在到哪一步。** |

執行層**不自己算倉位大小**。`size_usdt` 或 `leverage` 缺了就拒絕執行,
不會憑空生一個出來 —— 有測試釘住。

---

## 2. 最重要的一條:裸倉

**開倉成功、但停損沒有掛上去 = 裸倉。** 部位已經存在,但沒有虧損上限。

那不是待辦事項,是必須**立刻**處理的狀態。做法是:成交後檢查保護,
沒有保護就**馬上平掉**,用 ERROR 記錄,並且讓 `run_auto_trader()`
**中止這一輪**,不是換下一個候選。

> 寧可平掉一個可能會賺的倉位,也不要留一個沒有停損的倉位過夜。

這條沒有例外,也沒有設定可以關掉。

三種情況都處理了:

| 情況 | 行為 |
|---|---|
| 有保護 | `PROTECTED`,正常開倉 |
| 沒有保護 | 立即平倉,回報 `NAKED_POSITION_CLOSED` |
| **檢查保護時出錯** | 當作沒有保護 —— 反過來會讓裸倉安靜地留在帳上 |
| 有裸倉又平不掉 | `logger.critical` + `NAKED_POSITION_STUCK`,要求人工介入 |

另外有一層安全網:`run_naked_position_sweep()` 定期掃所有部位,
把沒有停損的平掉。停損可能因為別的原因消失(手動改、對帳修正、資料庫問題)。

---

## 3. 訂單狀態機

非法的狀態轉移在交易系統裡不是小 bug:把一張已經 FILLED 的單當成 CREATED
重送,就是憑空多一個部位。

```
CREATED -> VALIDATING -> RISK_CHECK -> SUBMITTING -> ACCEPTED
        -> PARTIALLY_FILLED -> FILLED -> PROTECTED -> CLOSING -> CLOSED
```

三條規則:

1. **允許的轉移寫死在表裡。** 不在表裡就拋例外,不是印個警告繼續跑。
   連「同狀態轉移」也拒絕 —— 那通常代表呼叫端搞不清楚自己在哪一步。
2. **終止狀態不可再轉移。**
3. **需要對帳的狀態不可重送。** `UNKNOWN` / `TIMEOUT` / `SUBMITTING` 代表
   系統不知道交易所收到了什麼。

第 3 點在送單例外時就用上了:送單過程炸掉時狀態是 `UNKNOWN`,**不是**
`REJECTED`。兩者差很多 —— `REJECTED` 是交易所明確說「不收」,可以確定沒有倉位;
`UNKNOWN` 是不知道,直接重送可能開出第二張。

還有一個測試確保 `OrderState` 的**每一個**狀態都在轉移表裡:漏掉一個會讓它
變成死路,而且是在執行當下才發現。

---

## 4. Broker 介面

`Broker` 這道縫存在的理由有兩個:

1. **Paper 與 Live 走同一條執行路徑。** 兩套各自實作的下單流程,
   模擬盤驗證過的東西在實盤不算數。Phase 6 與 Phase 9 已經收掉過兩次
   重複的評分路徑,執行層不要再長出第三次。
2. **實單能力必須是可以整個拔掉的。** 現在**沒有 LiveBroker 這個類別** ——
   系統沒有任何一條路徑能送出真實訂單,不是靠設定擋住,是靠沒有那個實作。
   有測試掃描 `broker.py` 確認沒有任何 live / real 命名的東西。

模擬盤的「保護」是交易列上的停損欄位、由 `position_monitor` 執行的。
`has_protection()` 檢查那一欄 —— 沒有就代表這個部位沒有虧損上限,
與實盤「停損單沒掛上去」是同一件事。

---

## 5. 判斷式出場:Exit Agent 真的動手了

Phase 9 的 ExitAgent 到目前為止只會「提示」。`agmcis/execution/exit_manager.py`
讓它真的執行。

兩層出場的分工:

| | position_monitor | exit_manager |
|---|---|---|
| 類型 | 機械式 | 判斷式 |
| 依據 | 價格碰到停損 / 停利 / 強平 | Agent 群看完市況 |
| 頻率 | 60 秒 | 300 秒 |
| 能不能改停損 | 不能 | **不能** |

**機械式出場永遠優先。** 一個會移動停損的「出場管理器」等於把風險上限拿掉了;
一個能開倉的「出場管理器」不是出場管理器。兩件事都有測試掃原始碼確認。

平倉的兩個條件,門檻都比「提示」高:

- ExitAgent 投 WAIT 且信心 ≥ 80
- 共識轉到部位的**反方向**且信心 ≥ 70

一個刻意的否定條件:**所有 Agent 棄權時不做判斷式出場。**
看不見市況就不要憑空決定出場 —— 停損還在,機械式保護不受影響。

`run_exit_manager` 與 `run_naked_position_sweep` 都註冊進排程器的
`position` 任務組。排好的工作沒有被排程器叫到,等於沒有寫;
同時有測試確認 `position` 組裡**仍然沒有任何開倉工作**
(Phase 1 的教訓:合併排程器時把所有工作塞進每個 service,結果四個都在開倉)。

---

## 6. 測試

```
全部:720 passed
本階段新增:
  tests/test_execution_engine.py  27
  tests/test_exit_manager.py      18
```

重點:

- `test_a_position_without_protection_is_closed_immediately`
- `test_a_failing_protection_check_is_treated_as_unprotected`
- `test_a_naked_position_that_cannot_be_closed_is_escalated`
- `test_a_submit_exception_lands_in_unknown_not_rejected`
- `test_every_state_has_an_entry_in_the_transition_table`
- `test_there_is_no_live_broker_implementation_yet`
- `test_all_agents_abstaining_never_triggers_a_discretionary_exit`

`tests/test_risk_gate.py` 的接縫再次跟著架構移動:現在斷言的是
「傳給 Execution Engine 的 RiskDecision 帶著風控算出來的 size 與 leverage」,
比原本斷言 `create_paper_trade` 的參數更貼近真正的保證。

---

## 7. 尚未做的事

- **部分成交沒有完整處理。** 狀態機有 `PARTIALLY_FILLED`,但模擬盤一律
  全額成交,所以那條路徑還沒有真的被走過。實盤會遇到。
- **沒有訂單持久化。** `Order` 目前只活在記憶體裡。程式重啟時
  `UNKNOWN` 狀態的訂單就消失了 —— 那正是最需要被記住的狀態。
  這是 Phase 13 對帳的前提。
- **停損在模擬盤不是交易所的掛單。** 實盤要真的送一張 STOP_MARKET 單,
  而且要處理「進場成交了但停損單被拒」的情況 —— 那時是真正的裸倉,
  而不是資料庫欄位空著。
- **沒有重試。** 送單失敗就是失敗。加重試之前必須先有對帳,
  否則重試就是在製造重複倉位。

---

## 8. 下一步

Phase 13 — Reconciliation:訂單持久化、與交易所對帳、
處理 `UNKNOWN` / `TIMEOUT` 狀態的訂單、偵測本地與交易所的部位差異。
