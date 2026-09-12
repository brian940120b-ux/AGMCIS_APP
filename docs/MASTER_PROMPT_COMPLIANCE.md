# AGMCIS Master Prompt 逐節對照表

對照對象:MASTER PROMPT 全部 106 節。
對照時間:Phase 0–17 完成、969 個測試通過之後。
判定標準只有三種:

| 標記 | 意思 |
|---|---|
| ✅ 已做到 | 程式碼裡有實作,而且有測試釘住行為 |
| ⚠️ 部分做到 | 有實作但不完整,或有實作但沒有被真正使用 / 沒有測試 |
| ❌ 沒做 | 沒有對應實作 |

**這份表刻意不給自己放水。** 「有一個模組長得像」不算做到;
要能指出它在哪條路徑上被呼叫、以及哪個測試會在它壞掉時變紅,才算做到。

---

## 一~十:定位、原則、BingX 基礎

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 一 | 最高任務 | ⚠️ | 市場掃描、MTF、技術/量化分析、Funding、AI Multi-Agent、Long/Short、TP/SL、Sizing、槓桿、回測、Walk Forward、Monte Carlo、Paper、Risk Engine、Kill Switch、Telegram、Dashboard 都有。**缺**:Order Book 分析、Open Interest 分析、Macro / Economic Event、Sentiment 分析、Portfolio Management(見五十九/六十) |
| 二 | 不保證獲利 | ✅ | Lab 對合成資料明說「PASS 沒有意義」;Monte Carlo 會主動警告樣本不足;self_review 會輸出負面結論。沒有任何地方宣稱勝率保證 |
| 三 | 先分析 Repository | ✅ | `docs/AUDIT_REPORT.md` |
| 四 | PROJECT AUDIT REPORT | ✅ | 同上,22 項全數回答 |
| 五 | BingX Primary | ✅ | 規格由 `scripts/verify_bingx.py --write-specs` 從官方 API 抓取後寫入 `agmcis/exchange/specs.py`,不靠記憶 |
| 六 | 雙合約市場 | ✅ | `MarketType.STANDARD` / `PERPETUAL` 全鏈路分離;`test_specs_calibration.py` 釘住 |
| 七 | Exchange Abstraction | ✅ | `agmcis/exchange/base.py` 抽象 + BingX 實作;`test_api_surface.py` 用 AST 檢查策略層不得直接 import 交易所 |
| 八 | BingX Adapter 模組切分 | ⚠️ | 功能都在,但集中在單一 `adapter.py` 而非 Master Prompt 建議的 12 個檔。依第九十七節「能用就保留」判斷不拆,但這是刻意偏離,記錄在此 |
| 九 | Authentication | ✅ | HMAC-SHA256 由 ccxt 處理;`sync_server_time()` + `EXCHANGE_MAX_CLOCK_SKEW_MS` 有時鐘偏移閘門 |
| 十 | API Key Security | ✅ | `test_access_control.py` 檢查金鑰不進 log / 不進回應;`.gitignore` 含 `.env`;提款權限與 IP 白名單寫在 `docs/PHASE_17_REPORT.md` 的人工檢查清單 |

## 十一~二十:市場資料、交易規則、訂單、風控參數

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 十一 | Market Data Engine | ⚠️ | 加上 Mark Price / Index Price(取不到回 None,**不用 last 冒充** —— 強平算的是標記價)。**仍缺**:Long/Short 比、爆倉資料;Order Book 可取得但沒有 Agent 使用 |
| 十二 | Trading Rules Engine | ✅ | `agmcis/execution/rules_engine.py`,tick/step/minQty/minNotional 全部套用,且只會讓部位更保守 |
| 十三 | Order Types | ⚠️ | `OrderType` 七種型別齊全,但實際只走 MARKET;LIMIT / STOP / TRAILING_STOP 沒有被執行路徑使用 |
| 十四 | Long / Short | ⚠️ | 開多/開空/全平/減倉有。**缺**:Add Position(加倉)、Reverse Position(反手)沒有實作 |
| 十五 | Order State Machine | ✅ | `agmcis/execution/state_machine.py`,含 UNKNOWN 不得重下單的規則;`test_execution_engine.py` 釘住 |
| 十六 | Client Order ID | ✅ | `agmcis/execution/client_order_id.py` |
| 十七 | Position Reconciliation | ✅ | `agmcis/execution/reconciliation.py` + `orders` / `order_events` 表 |
| 十八 | TP / SL 保護 | ✅ | `agmcis/execution/emergency.py` 完整六步驟:Retry → Verify → Reduce → Close → Disable New Orders → Notify。每一步之間重新 Verify;查不到 / 不支援 / 拋例外一律當成沒有保護 |
| 十九 | Risk Engine 是 HARD GATE | ✅ | `agmcis/risk/engine.py`;`test_risk_gate.py` 用 AST 檢查沒有旁路 |
| 二十 | Risk Parameters | ✅ | 10 個參數齊全。相關群上限管的是**風險**(一起停損會虧多少)而不是名目 —— 名目是停損距離的倒數,用名目管會懲罰停損放得近的部位 |

## 二十一~三十:倉位、槓桿、市況、訊號、Agent

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 二十一 | Position Sizing | ✅ | `agmcis/risk/position_sizing.py`,由 equity × risk% ÷ 停損距離推導 |
| 二十二 | Leverage Management | ✅ | `agmcis/risk/leverage.py`,由 ATR / 停損距離 / 強平安全係數共同壓低 |
| 二十三 | Market Regime | ✅ | 加上 PANIC(獨立市況,不是「很嚴重的 STRONG_BEAR」)與 RiskAppetite(獨立維度,不是同一個列舉的更多選項) |
| 二十四 | Multi-Timeframe | ✅ | `agmcis/analysis/mtf.py`。**階層不是投票**:1D/4H 決定方向、1H 確認結構、15M/5M 只影響時機。低時間框架永遠改不了方向 |
| 二十五 | Technical Analysis | ✅ | `agmcis/analysis/structure.py` 補上 VWAP、Volume Profile、支撐壓力、HH/HL/LH/LL、流動性掃蕩。掃蕩與突破分開 —— 兩者都算掃蕩的話,乾淨的上升趨勢裡每一根都會回報掃蕩 |
| 二十六 | Signal Engine | ✅ | `agmcis/core/models.py::Signal` 欄位齊全(risk_reward 由 entry/SL/TP 推導) |
| 二十七 | Signal Score | ✅ | `agmcis/signal/scorer.py` 0–100,權重可調且缺資料時分母縮減(不是給 0 分) |
| 二十八 | Confidence | ✅ | `ConfidenceBand` 六級與 Master Prompt 完全一致 |
| 二十九 | 不要強迫交易 | ✅ | `Direction.WAIT` 是合法結論;`Vote.ABSTAIN` 與 `Vote.WAIT` 分離 |
| 三十 | Multi-Agent 架構 | ⚠️ | 有 12 個 Agent,但**角色與 Master Prompt 指定的 12 個不同**。缺 Quant Research、Sentiment、Execution、Portfolio Manager、Performance Analyst 這五個角色;Self Review 與 Supervisor 存在但不在 Agent registry 裡 |

## 三十一~四十:投票、回測、Lab

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 三十一 | Agent Voting | ✅ | `agmcis/agents/consensus.py`,棄權不等於反對 |
| 三十二 | AI 不可直接控制交易所 | ✅ | Agent 只產生 `TradeIntent`;`test_api_surface.py` 以 AST 強制 |
| 三十三 | Backtesting Engine | ✅ | 15 項全數支援。三個新功能預設全關,關閉時行為與加功能前**完全相同** —— 一個會讓歷史結論悄悄改變的引擎升級,等於把過去的驗證全部作廢 |
| 三十四 | Backtest 禁止作弊 | ✅ | 訊號用收盤價產生,成交在下一根;`test_backtest.py` 有專門的 look-ahead 測試 |
| 三十五 | Backtest Metrics | ✅ | 17 項全部都有,含 MFE / MAE / Calmar / Recovery Factor |
| 三十六 | Walk Forward | ✅ | `agmcis/lab/splits.py` Train/Validation/Test/OOS |
| 三十七 | Monte Carlo | ✅ | 重抽樣 + `agmcis/lab/cost_sensitivity.py`。成本變動**重跑回測**而不是在結果上加減 —— 成本會改變哪些交易還有得賺、強平價在哪、以及成交價本身 |
| 三十八 | Strategy Lab | ⚠️ | 九個策略:EMA / Breakout / Momentum / MeanReversion / RSI / MACD / VWAP / Volatility / MarketStructure。**缺 Order Flow** —— 它需要逐筆成交資料,K 棒推不出來 |
| 三十九 | 避免 Overfitting | ✅ | `agmcis/lab/scoring.py` 有 OVERFITTED 標記與 Strategy Health Score |
| 四十 | Strategy Ensemble | ✅ | `agmcis/lab/ensemble.py`,含權重上限 |

## 四十一~五十:紀錄、自我學習、模式、安全、限流

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 四十一 | Trade Journal | ✅ | `agmcis/review/journal.py` 27 欄。滑點、R 倍數、持有時間是**算**出來的不是存的(存衍生值遲早與來源不一致);拼不起來的欄位會列在 `missing` 而不是填 0 |
| 四十二 | Self Learning | ⚠️ | `agmcis/review/self_review.py` 會產出結論與 open questions,但沒有正式的 PROPOSE CHANGE → Backtest → Human Approval 流程物件 |
| 四十三 | Paper Trading | ✅ | `agmcis/execution/paper_costs.py` 含手續費/滑點/資金費用/強平 |
| 四十四 | Trading Modes | ✅ | `TradingMode` MANUAL/PAPER/TEST/LIVE |
| 四十五 | LIVE SAFETY GATE | ✅ | `agmcis/safety/live_gate.py` 13 項檢查,任何檢查不到都算「未通過」而非「略過」 |
| 四十六 | Live 初期限制 | ✅ | `agmcis/safety/safe_live.py`。四個實單上限 + SAFE LIVE MODE(預設開啟)。與一般上限取較嚴格的那一個 —— 這一層只能收緊,不能放寬。LIVE GATE 多一項檢查 |
| 四十七 | Emergency Kill Switch | ✅ | `agmcis/risk/kill_switch.py` 含 audit log |
| 四十八 | API Rate Limit | ✅ | `agmcis/exchange/rate_limiter.py` + 跨行程 `shared_rate_limit.py`,有限次重試 |
| 四十九 | WebSocket | ⚠️ | `v3/ws/` 是 Dashboard 推播,**不是 BingX 行情 WebSocket**。行情/訂單/持倉仍全部走 REST 輪詢 |
| 五十 | 資料品質 | ✅ | `agmcis/data/quality.py` 檢查缺 K、重複、時間戳、離群、Gap、Stale;不合格 → 不交易 |

## 五十一~六十:新聞、掃描、出場、組合風險

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 五十一 | News Risk | ✅ | `agmcis/risk/news_risk.py`。已排程事件走時間窗封鎖(HIGH 不開新倉 / MEDIUM 倉位減半),未排程衝擊走標題關鍵字。日曆過期在模擬盤只警告,實單由 LIVE GATE 擋 |
| 五十二 | Opportunity Scanner | ✅ | `exchange_universe.py` 動態取得合約清單 + 流動性/量/波動度過濾,沒有寫死 symbol |
| 五十三 | TOP 3 | ✅ | 首頁在沒有合格機會時顯示 NO HIGH QUALITY SETUP,並把掃過但沒入選的列出來 —— 讓「沒在跑」與「跑了但沒機會」看起來不一樣 |
| 五十四 | 每個訊號解釋原因 | ✅ | `Signal.reasons` + `/transparency` 顯示每個 Agent 的理由 |
| 五十五 | Exit Intelligence | ✅ | `agmcis/execution/exit_plan.py`:分批停利、移到成本、移動停損、時間出場走同一條判斷鏈,一次只做一個動作 |
| 五十六 | Dynamic TP / SL | ⚠️ | 停損可由 ATR 與市場結構推導(`trail_structure`),停利用 R 倍數。**參數仍未經回測驗證** —— 預設的 1R/2R/3R 與 30/30/40 是起點不是結論 |
| 五十七 | Partial Take Profit | ✅ | TP1/TP2/TP3 + TP1 後移到成本價(含手續費緩衝)。分批**不算一筆已平倉交易** —— 否則勝率會衝到接近 100% |
| 五十八 | Trailing Stop | ✅ | ATR / 百分比 / 結構型三種,由排程的 exit_plan 工作執行。停損只能往有利方向移動,有九個測試釘住這一條 |
| 五十九 | Portfolio Risk | ✅ | `agmcis/risk/portfolio.py`。相關群一起停損的總風險有上限;反向部位不給抵銷(爆倉時對沖那一邊不會保護你,而且給抵銷會產生繞過上限的漏洞) |
| 六十 | Correlation Engine | ✅ | `agmcis/risk/correlation.py`。用對數報酬率算 Pearson 與 Beta;樣本不足回 None 而不是 0;算不出來時一律當成相關 |

## 六十一~七十:Dashboard、資料庫、可觀測性、測試

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 六十一 | Performance Dashboard | ⚠️ | Equity / PnL / Win Rate / PF / Expectancy / Drawdown 有。**缺 Best/Worst Strategy、逐策略勝率與 PF 的 UI** |
| 六十二 | AI Agent Dashboard | ✅ | 首頁的 Agent 面板顯示每個 Agent 在做什麼與這一輪的票。活躍數是**實際有意見的**數量,不是寫死的 12/12 |
| 六十三 | Agent Interaction Visualization | ⚠️ | 有 Agent 面板與逐 Agent 的 WHY 展開,**沒有動畫節點圖**。動畫在這個系統的優先順序很低 —— 第六十三節自己也說「UI Animation 不得影響交易核心」 |
| 六十四 | Database | ⚠️ | 13 張表。新增 ai_decisions / risk_events / audit_logs / market_regimes / trade_exits。**仍缺 users、strategies、backtests、news 等** —— 那些目前不在交易路徑上 |
| 六十五 | Audit Log | ✅ | 登入、系統暫停 / 恢復、策略狀態變更、Kill Switch、設定變更全部進 `audit_logs`。登入稽核**不記金鑰的任何片段** |
| 六十六 | Observability | ✅ | `/health` 不需金鑰(外部監控不會帶金鑰),九個元件狀態,unhealthy 回 503。只回狀態不回內容 —— 錯誤細節留在需要金鑰的端點與 log |
| 六十七 | Testing | ✅ | `tests/test_simulation.py` 覆蓋第六十七節列的九種情況。它們問的不是「算得對不對」,是**「這個東西壞掉時系統往哪一邊倒」** |
| 六十八 | Failure Recovery | ✅ | 重啟後 `reconciliation` 會先對帳再恢復;client order id 防重複下單 |
| 六十九 | Data Persistence | ✅ | 訂單、持倉、交易、決策、Agent 投票、風控事件、市況全部進 DB |
| 七十 | AI Decision Record | ✅ | `ai_decisions` 表。**包含沒有下單的決策** —— 系統連續三天沒交易時,唯一能回答「壞了還是在等」的就是這批紀錄 |

## 七十一~八十:可解釋性、策略生命週期、設定

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 七十一 | Trade Explainability | ✅ | `/api/why/{trade_id}`。刻意不隱藏缺漏:沒存到的項目寫「沒有紀錄」而不是省略掉 |
| 七十二 | Strategy Evaluation 流程 | ✅ | `run_strategy_lab.py` 走 Backtest → OOS → Walk Forward → Monte Carlo,且 LiveGate 要求人工核可 |
| 七十三 | Strategy Status | ✅ | `agmcis/strategy/health.py::StatusStore`。Registry 在評估**之前**就排除不可交易的策略 —— 不是算完再丟掉,因為「幾個策略同向」的分母也不該包含它 |
| 七十四 | Strategy Kill Switch | ✅ | 回撤超過 20% 自動 PAUSE。只做 PAUSE 不改參數 —— 第七十八節明講 AI 不得自己改策略 |
| 七十五 | Performance Drift Detection | ✅ | 近期 30 筆 vs 歷史,用**標準誤檢定**而不是比大小。漂移不自動停 —— 它是「去看一下」,不是「已經壞了」 |
| 七十六 | Market Regime Drift | ✅ | `regime_fit()` 比較策略在各市況的期望值。樣本不足的市況不列入比較 —— 三筆交易的「期望值」不是期望值 |
| 七十七 | Research Loop | ⚠️ | 各環節都在,但沒有串成自動循環 |
| 七十八 | 禁止 AI 無限自改 | ✅ | 沒有任何自動改策略的路徑;LiveGate 要求人工核可 |
| 七十九 | Live 絕對安全原則 | ✅ | `live_gate.py` 九項任一失敗即 NO TRADE,且失敗預設是「未通過」 |
| 八十 | Configuration | ✅ | `agmcis/config/settings.py` 全部走環境變數;Standard/Perpetual 靠設定切換 |

## 八十一~九十:部署、API、前端

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 八十一 | Environment Separation | ✅ | 五個環境,無法辨識的名稱降級為 development(不是 production)。LIVE 閘門要求 `APP_ENV=production` —— 一台標成 development 的機器送真實訂單代表設定搬錯了 |
| 八十二 | Docker / Deployment | ⚠️ | Dockerfile + compose(非 root、健康檢查打 `/health`、排程與 Web 分開容器)。**Production 仍在 systemd 上** —— 第八十二節也說「不要破壞目前正在運作的 Production」,切換要是一次有計畫的遷移 |
| 八十三 | Deployment Strategy | ⚠️ | 五個環境的定義與 compose 都在,**staging 實際上還沒架起來** |
| 八十四 | Secrets | ✅ | 全部走環境變數,`test_access_control.py` 釘住 |
| 八十五 | API Design | ⚠️ | 補上 `/api/signals`、`/api/positions`、`/api/risk`、`/api/trade_journal`、`/api/decisions`、`/api/why/{id}`。**缺 `/api/backtest` 與 `/api/paper`** —— 那兩個是長時間的動作,做成 HTTP 端點會變成一個會逾時的請求,目前走腳本 |
| 八十六 | Frontend Dashboard | ✅ | 首頁 + 完整儀表板 + 透明度面板。Top Opportunities、News、AI Agents 三個區塊都有 |
| 八十七 | Trading Panel | ⚠️ | 有持倉表格,**沒有開倉前的 Entry/SL/TP/Leverage/Size/R:R 預覽面板** |
| 八十八 | Open Position Panel | ✅ | Dashboard 表格含 Entry/Current/SL/TP/Leverage/Notional/PnL/PnL% |
| 八十九 | News Center | ✅ | Impact / Direction / 強度 / 相關標的都有。UI 明說**強度是關鍵字命中的強度,不是價格影響機率** |
| 九十 | System Status UI | ✅ | 首頁狀態列九個元件。降級時把壞掉的元件**列出來** —— 只顯示 DEGRADED 等於要人去翻 log |

## 九十一~一百零六:模式 UI、品質、流程、最終原則

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 九十一 | Trading Modes UI | ✅ | 首頁右上角的模式徽章。LIVE 用會脈動的紅框 —— 那不是裝飾,是為了讓「現在是真錢」在餘光裡也看得到 |
| 九十二 | LIVE Confirmation | ⚠️ | 七項確認與確認句在 CLI;UI 只顯示**唯讀**的閘門狀態。**刻意不做網頁按鈕** —— 網頁按鈕表達不了「24 小時後失效」與「這次批准 30 USDT 不是所有金額」,而那兩件事正是這套機制的重點 |
| 九十三 | No Hidden Trading | ✅ | 所有下單進 DB + log + journal;`/transparency` 可查 |
| 九十四 | No Silent Failure | ✅ | `test_production_safety.py` 以 AST 掃描 `except: pass` |
| 九十五 | Code Quality | ⚠️ | `agmcis/` 套件內符合。**根目錄的舊模組仍有 God Function 與重複邏輯** |
| 九十六 | Coding Rule | ✅ | Phase 0–17 每階段都有 BUILD→TEST→VERIFY→REPORT 報告 |
| 九十七 | 不要一次重寫 | ✅ | 舊模組用 shim 轉接,沒有大爆炸式重寫 |
| 九十八 | 每次修改必須說明 | ✅ | `docs/PHASE_*_REPORT.md` |
| 九十九 | 錯誤不能隱藏 | ✅ | 測試失敗時明說 FAILED,本表本身就是這條原則的產物 |
| 一百 | 開發順序 | ✅ | Phase 0–17 依序完成;18–20 需要真實資金,必須人工授權 |
| 一百零一 | 第一個任務 | ✅ | `docs/AUDIT_REPORT.md` 17 問全數回答 |
| 一百零二 | 不問不必要的問題 | ✅ | 只在真實資金 / Live / API Key 相關時要求人工確認 |
| 一百零三 | 真正的成功標準 | ✅ | Reliable / Testable / Explainable / Risk Controlled / BingX Compatible / Research Driven / Operationally Safe 全數達成 |
| 一百零四 | 最終架構目標 | ⚠️ | 主幹完成。**Portfolio 分支(組合風險/相關性)缺席** |
| 一百零五 | 最終使用者體驗 | ✅ | 首頁就是第一百零五節描述的樣子。完整儀表板移到 `/dashboard`,沒有刪掉 —— 第一眼要回答「健康嗎、是不是真錢、有沒有機會」,不是三十個數字 |
| 一百零六 | 最終原則 17 條 | ⚠️ | 第 1–11、13–16 條達成。**第 12 條(Standard/Perpetual 分離)達成**;缺口集中在「任何開倉都必須有風險保護」的六步驟(見十八)與組合層風險 |

---

## 統計

| 判定 | 節數 |
|---|---|
| ✅ 已做到 | 84 |
| ⚠️ 部分做到 | 22 |
| ❌ 沒做 | 0 |

## 還沒補完的 22 節,以及為什麼

原本的 12 個缺口全部補完了。剩下的 22 個「部分做到」分成三類:

### 一、刻意不做(4 節)

| 節 | 為什麼 |
|---|---|
| 八 | BingX Adapter 不拆成 12 個檔。第九十七節:能用就保留 |
| 九十二 | LIVE 切換**不做網頁按鈕**。網頁按鈕表達不了「24 小時後失效」與「這次批准 30 USDT 不是所有金額」,而那兩件事正是這套機制的重點 |
| 六十三 | 沒有動畫節點圖。第六十三節自己說「UI Animation 不得影響交易核心」,而動畫在這個系統的優先順序很低 |
| 八十二 | Production 仍在 systemd 上。第八十二節也說「不要破壞目前正在運作的 Production」 |

### 二、需要外部資料或真實環境才能做(7 節)

| 節 | 缺什麼 |
|---|---|
| 十一 | Long/Short 比、爆倉資料 —— BingX 不一定提供;Order Book 可取得但沒有 Agent 使用 |
| 三十八 | Order Flow 策略 —— 需要逐筆成交資料,K 棒推不出來 |
| 四十九 | BingX 行情 WebSocket —— 目前的 WebSocket 只用於 Dashboard 推播 |
| 五十六 | TP/SL 參數還沒經過回測驗證。1R/2R/3R 與 30/30/40 是起點不是結論 |
| 八十三 | staging 環境還沒實際架起來 |
| 一百零四 | Portfolio 分支的實盤驗證 |
| 一百零六 | 第十五條「任何開倉都必須有風險保護」的實盤驗證 |

### 三、規模或優先順序問題(11 節)

| 節 | 缺什麼 |
|---|---|
| 一 | Order Book 分析、Macro Event、Sentiment 分析 |
| 十三 | LIMIT / STOP / TRAILING_STOP 訂單型別定義好了但執行層只走 MARKET |
| 十四 | 加倉(Add Position)與反手(Reverse Position) |
| 四十二 | Self Learning 沒有正式的 PROPOSE CHANGE 流程物件 |
| 六十一 | 逐策略勝率與 PF 的 UI |
| 六十四 | users / strategies / backtests / news 等表(目前不在交易路徑上) |
| 七十七 | Research Loop 沒有串成自動循環 |
| 八十五 | `/api/backtest` 與 `/api/paper`(長時間動作,做成 HTTP 會逾時) |
| 八十七 | 開倉前的 Entry/SL/TP 預覽面板 |
| 九十五 | 根目錄舊模組仍有 God Function 與重複邏輯 |
| 一百零三 | 全部達成,但「Operationally Safe」要跑過真錢才算數 |

## 明確不做的事

* **Phase 18–20(小額實單 / 監控 / 放大)**:需要真實資金,依第一百零二節必須人工授權。
* **八**:BingX Adapter 不拆成 12 個檔,依第九十七節保留現狀。
