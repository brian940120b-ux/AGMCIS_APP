# AGMCIS Architecture

BingX 合約交易系統。Phase 0 到 17 重寫後的樣子。

---

## 一條路徑,不是三條

Phase 0 的稽核在這個系統裡找到**兩條互相矛盾的訊號管線**與**四套獨立評分**。
同一個標的在不同地方會得到不同的結論,而實際下單依據的是哪一條,沒有人說得清楚。

現在只有一條:

```
Universe          決定看哪些標的
    ↓
資料品質 Gate      資料不合格一律 NO TRADE(資料壞掉 ≠ 市場中性)
    ↓
Indicators        指標
    ↓
Market Regime     市況
    ↓
12 個 Agent        各自出意見(AgentOpinion)
    ↓
Consensus         彙總 → TradeIntent(建構時強制驗證停損)
    ↓
Supervisor        Agent 群本身可不可信?可否決,不可製造交易
    ↓
Risk Engine       HARD GATE:要不要開?開多大?幾倍?
    ↓
Trading Rules     交易所收不收這組數字?
    ↓
Execution Engine  送單、狀態機、**開倉後必須有停損保護**
    ↓
Broker            PaperBroker(LiveBroker 不存在)
```

每一層的職責是排他的:

| 層 | 回答 | **不**回答 |
|---|---|---|
| Agent | 要不要交易?往哪邊? | 開多大 |
| Risk Engine | 可以開嗎?多大?幾倍? | 交易所收不收 |
| Trading Rules | 格式合不合法? | 風險大不大 |
| Execution | 現在到哪一步了? | 以上任何一項 |

---

## 接線測試

每一層都有單元測試,但層與層之間的介面錯誤**不會讓任何單元測試變紅** ——
它們只會讓系統安靜地少做一件事。

`tests/test_end_to_end.py` 走完整條鏈路,只 mock 三個邊界
(市場資料、合約規則、資料庫寫入),中間全部是真的。

它抓到過的真實錯誤:Trading Rules Engine 會把數量往下對齊到 step size,
但 PaperBroker 忽略了那個調整、直接用未對齊的名目價值建倉 ——
模擬盤開出來的倉位因此比「實際送得出去的訂單」略大,而且每一筆都被
誤判成部分成交。單元測試看不到,因為兩邊各自都是對的。

---

## 幾條寫進測試的鐵律

| 規則 | 守在哪裡 |
|---|---|
| Agent 不得直接呼叫下單 API | `tests/test_agents.py` 用 AST 掃描 `agmcis/agents/` |
| 沒有停損的 TradeIntent 建不出來 | `TradeIntent.__post_init__` |
| 開倉後沒有停損保護 → 立刻平倉 | `ExecutionEngine._ensure_protected` |
| 調整只能讓部位更安全,不能更寬鬆 | Phase 4 起,每一層的四捨五入都往保守方向 |
| 狀態不明的訂單不可以重送 | `state_machine.can_resubmit` |
| 對帳只更正紀錄,絕不下單 | `tests/test_reconciliation.py` 掃描原始碼 |
| Lab 驗證的必須是 live 實際在用的訊號 | `LiveGate.check_strategy_validation` 只認 `is_live_pipeline` |
| 棄權不是反對票 | `Vote.opposes` |
| 資料壞掉不等於市場中性 | 資料品質 Gate、`data_ok=False` |
| 安全檢查壞掉時算「沒通過」 | `LiveGate.evaluate` |

---

## 套件

```
agmcis/
  core/        型別與錯誤(不 import 任何 exchange / DB / web)
  config/      設定集中在 settings.py
  data/        市場資料 + 品質 Gate
  analysis/    指標、市況
  strategy/    策略與 registry
  signal/      統一訊號管線、Agent 管線接線
  agents/      12 個 Agent + Consensus + Supervisor(碰不到交易所)
  risk/        部位大小、槓桿、Risk Engine、Kill Switch
  exchange/    ExchangeAdapter、BingX、交易規則、規格快照、限流
  execution/   Trading Rules Engine、Execution Engine、狀態機、對帳、出場管理
  backtest/    回測引擎、成本模型、指標
  lab/         OOS、Walk Forward、Monte Carlo、過擬合偵測、Ensemble
  review/      績效歸因、Agent 貢獻度、自我檢討
  safety/      LIVE SAFETY GATE
  scheduling/  排程器(四個 service 共用一套)
```

根目錄的模組(`auto_trader.py`、`paper_trading.py`、`scanner_service.py` 等)
大多是薄 shim,把舊的 import 路徑轉接到套件內的實作。
生產環境的 import 路徑因此完全沒有改過。

---

## 資料流的三個閘門

1. **資料品質 Gate**(Phase 2)—— 資料不合格回 `NO TRADE`,不是回一個大概的數字。
2. **Risk Engine**(Phase 5)—— 架構上不可繞過。Agent 產不出 size,
   只有 Risk Engine 能決定。
3. **LIVE SAFETY GATE**(Phase 17)—— 需要人手動簽署的確認檔,24 小時過期。

---

## 目前的狀態

- **只有模擬盤。** `LiveBroker` 不存在,系統沒有任何一條路徑能送出真實訂單。
- **合約規格尚未校準。** 維持保證金率、費率用的是保守猜測值,
  強平價與成本都是估計。校準要在 VPS 上執行(見 `docs/PHASE_11_REPORT.md`)。
- **還沒有策略通過完整的樣本外驗證。** 資料量不夠。

詳細狀態見 `docs/PHASE_17_REPORT.md` 第 7 節。

---

## 各階段報告

`docs/AUDIT_REPORT.md` 是起點,之後每個階段一份:

| Phase | 主題 |
|---|---|
| 0 / 0.5 | 稽核 / 止血 |
| 1-3 | 套件化、市場資料、BingX 整合 |
| 4-6 | 交易規則、風控、統一訊號管線 |
| 7-8 | 回測引擎、Strategy Lab |
| 9-10 | Multi-Agent、Paper Trading 2.0 |
| 11-13 | 規格校準、Execution Engine、對帳 |
| 14-15 | 透明度面板、績效歸因 |
| 16-17 | Production Safety、LIVE SAFETY GATE |
