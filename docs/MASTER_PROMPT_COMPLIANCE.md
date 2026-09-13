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
| 一 | 最高任務 | ✅ | 全部項目都有實作。Order Book / Macro / Sentiment 由 `agmcis/agents/flow.py` 補上;Portfolio Management 見五十九 / 六十 |
| 二 | 不保證獲利 | ✅ | Lab 對合成資料明說「PASS 沒有意義」;Monte Carlo 會主動警告樣本不足;self_review 會輸出負面結論。沒有任何地方宣稱勝率保證 |
| 三 | 先分析 Repository | ✅ | `docs/AUDIT_REPORT.md` |
| 四 | PROJECT AUDIT REPORT | ✅ | 同上,22 項全數回答 |
| 五 | BingX Primary | ✅ | 規格由 `scripts/verify_bingx.py --write-specs` 從官方 API 抓取後寫入 `agmcis/exchange/specs.py`,不靠記憶 |
| 六 | 雙合約市場 | ✅ | `MarketType.STANDARD` / `PERPETUAL` 全鏈路分離;`test_specs_calibration.py` 釘住 |
| 七 | Exchange Abstraction | ✅ | `agmcis/exchange/base.py` 抽象 + BingX 實作;`test_api_surface.py` 用 AST 檢查策略層不得直接 import 交易所 |
| 八 | BingX Adapter 模組切分 | ✅ | 照第八節建議的佈局拆完。`adapter.py` 從 711 行變成 108 行的**組裝點**,用 mixin 讓檔案分開而物件不變 —— 公開 API 與拆之前一模一樣,25 個方法一個都沒少。第八節說「已有類似模組就不要重複建立」,所以 contracts / websocket / executor / reconciler / rate_limiter / errors 是指路的 re-export,實作留在原處。金鑰收斂到 `auth.py`,有測試掃「只有它能碰 EXCHANGE_CREDENTIALS」;`signer.py` 說明簽章為什麼交給 ccxt(第五節:不要猜 API),並有測試擋自己寫 HMAC |
| 九 | Authentication | ✅ | HMAC-SHA256 由 ccxt 處理;`sync_server_time()` + `EXCHANGE_MAX_CLOCK_SKEW_MS` 有時鐘偏移閘門 |
| 十 | API Key Security | ✅ | `test_access_control.py` 檢查金鑰不進 log / 不進回應;`.gitignore` 含 `.env`;提款權限與 IP 白名單寫在 `docs/PHASE_17_REPORT.md` 的人工檢查清單 |

## 十一~二十:市場資料、交易規則、訂單、風控參數

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 十一 | Market Data Engine | ✅ | Mark / Index Price、Long/Short 比、爆倉資料都有(交易所不支援回 None,**不回 1.0**);OrderBookAgent 與 OrderFlow 策略真的在用訂單簿 |
| 十二 | Trading Rules Engine | ✅ | `agmcis/execution/rules_engine.py`,tick/step/minQty/minNotional 全部套用,且只會讓部位更保守 |
| 十三 | Order Types | ✅ | MARKET 立即成交;LIMIT / STOP / TAKE_PROFIT 進 `agmcis/execution/pending.py` 的掛單簿,由排程檢查觸價。掛單有有效期 —— 一張掛三天的單當初的訊號早就過期了 |
| 十四 | Long / Short | ✅ | 開多/開空/全平/減倉/加倉/反手都有。反手是**先平再開**,平不掉就不開 —— 一個「開了新倉但舊倉還在」的反手,是同時持有兩個方向 |
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
| 三十 | Multi-Agent 架構 | ✅ | 15 個投票 Agent + 6 個刻意**不是** Agent 的子系統。對照表在 `docs/AGENT_ROLES.md`,而且有測試在盯它沒說謊。Risk Manager / Execution / Supervisor / Portfolio / Quant Research / Performance Analyst 不投票:把 Risk Engine 變成一票,就是把第十九節的否決權降級成十五分之一的意見。Agent 10 缺的 Sharpe / Sortino / MFE / MAE / Holding Time 全部補上(`agmcis/review/live_metrics.py` + migration 010)|

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
| 三十八 | Order Flow | ⚠️ | 逐筆成交的 volume delta 已實作(`get_trade_flow`),OrderFlow 優先用它、信心上限 80;拿不到才退回訂單簿代理、上限 65,而且 reasons 會標明用的是哪一種。**留在 ⚠️ 是因為那條路在真的 BingX 上還沒跑過** —— `scripts/verify_bingx.py` 會告訴你端點通不通 |
| 三十九 | 避免 Overfitting | ✅ | `agmcis/lab/scoring.py` 有 OVERFITTED 標記與 Strategy Health Score |
| 四十 | Strategy Ensemble | ✅ | `agmcis/lab/ensemble.py`,含權重上限 |

## 四十一~五十:紀錄、自我學習、模式、安全、限流

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 四十一 | Trade Journal | ✅ | `agmcis/review/journal.py` 27 欄。滑點、R 倍數、持有時間是**算**出來的不是存的(存衍生值遲早與來源不一致);拼不起來的欄位會列在 `missing` 而不是填 0 |
| 四十二 | Self Learning | ✅ | `agmcis/review/proposals.py`。狀態不能跳、每一步要附數字、**批准只有人做得到**(看起來像系統的名字會拋 NotAHuman) |
| 四十三 | Paper Trading | ✅ | `agmcis/execution/paper_costs.py` 含手續費/滑點/資金費用/強平 |
| 四十四 | Trading Modes | ✅ | `TradingMode` MANUAL/PAPER/TEST/LIVE |
| 四十五 | LIVE SAFETY GATE | ✅ | `agmcis/safety/live_gate.py` 13 項檢查,任何檢查不到都算「未通過」而非「略過」 |
| 四十六 | Live 初期限制 | ✅ | `agmcis/safety/safe_live.py`。四個實單上限 + SAFE LIVE MODE(預設開啟)。與一般上限取較嚴格的那一個 —— 這一層只能收緊,不能放寬。LIVE GATE 多一項檢查 |
| 四十七 | Emergency Kill Switch | ✅ | `agmcis/risk/kill_switch.py` 含 audit log |
| 四十八 | API Rate Limit | ✅ | `agmcis/exchange/rate_limiter.py` + 跨行程 `shared_rate_limit.py`,有限次重試 |
| 四十九 | WebSocket | ✅ | `agmcis/exchange/bingx/stream.py` 用 ccxt.pro。**過期的報價等於沒有報價**,退回 REST。重連有上限,放棄時 `/health` 會變 error。預設關閉 |
| 五十 | 資料品質 | ✅ | `agmcis/data/quality.py` 檢查缺 K、重複、時間戳、離群、Gap、Stale;不合格 → 不交易 |

## 五十一~六十:新聞、掃描、出場、組合風險

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 五十一 | News Risk | ✅ | `agmcis/risk/news_risk.py`。已排程事件走時間窗封鎖(HIGH 不開新倉 / MEDIUM 倉位減半),未排程衝擊走標題關鍵字。**日曆過期會主動說話**(`calendar_watch` 排程 + `/health` + Telegram,只在轉態時發),而不是等到想開實單被閘門擋住才發現。`scripts/calendar.py` 讓維護是一行指令,而且任何寫入都會重蓋時間戳 |
| 五十二 | Opportunity Scanner | ✅ | `exchange_universe.py` 動態取得合約清單 + 流動性/量/波動度過濾,沒有寫死 symbol |
| 五十三 | TOP 3 | ✅ | 首頁在沒有合格機會時顯示 NO HIGH QUALITY SETUP,並把掃過但沒入選的列出來。合格的定義是**共識層自己產生得出 TradeIntent**,不是比對一個 Agent 管線根本不產生的 score —— 那個 bug 讓首頁**永遠**顯示沒有機會,而那個畫面跟系統壞掉長得一樣 |
| 五十四 | 每個訊號解釋原因 | ✅ | `Signal.reasons` + `/transparency` 顯示每個 Agent 的理由 |
| 五十五 | Exit Intelligence | ✅ | `agmcis/execution/exit_plan.py`:分批停利、移到成本、移動停損、時間出場走同一條判斷鏈,一次只做一個動作 |
| 五十六 | Dynamic TP / SL | ⚠️ | 停損可由 ATR 與市場結構推導(`trail_structure`),停利用 R 倍數。**驗證工具做好了**(`agmcis/lab/exit_tuning.py` + `scripts/tune_exits.py`):固定進場只換出場,回報每組的期望值 R 與樣本數,並算「最佳與中位數的差距」——差距大代表參數面崎嶇,那時正確的動作是維持現狀。**但還沒跑過真實 K 棒**,所以預設值仍是起點不是結論 |
| 五十七 | Partial Take Profit | ✅ | TP1/TP2/TP3 + TP1 後移到成本價(含手續費緩衝)。分批**不算一筆已平倉交易** —— 否則勝率會衝到接近 100% |
| 五十八 | Trailing Stop | ✅ | ATR / 百分比 / 結構型三種,由排程的 exit_plan 工作執行。停損只能往有利方向移動,有九個測試釘住這一條 |
| 五十九 | Portfolio Risk | ✅ | `agmcis/risk/portfolio.py`。相關群一起停損的總風險有上限;反向部位不給抵銷(爆倉時對沖那一邊不會保護你,而且給抵銷會產生繞過上限的漏洞) |
| 六十 | Correlation Engine | ✅ | `agmcis/risk/correlation.py`。用對數報酬率算 Pearson 與 Beta;樣本不足回 None 而不是 0;算不出來時一律當成相關 |

## 六十一~七十:Dashboard、資料庫、可觀測性、測試

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 六十一 | Performance Dashboard | ✅ | `/trading` 的績效區照第六十一節的四塊排:Account / Trading / Risk / Strategy,並含 Sharpe / Sortino / MFE / MAE / 持倉時間。**樣本不足的策略不參與 Best / Worst 排序**(少於 20 筆);排序用期望值不用總損益 —— 後者偏袒跑得比較多的策略。PF 沒有虧損單時是 None 不是無限大 |
| 六十二 | AI Agent Dashboard | ✅ | 首頁的 Agent 面板顯示每個 Agent 在做什麼與這一輪的票。活躍數是**實際有意見的**數量,不是寫死的 12/12 |
| 六十三 | Agent Interaction Visualization | ✅ | 五關決策鏈 + 動畫。第六十三節列的 animated nodes / data streams / glowing connections / activity indicators / signal pulses / decision timeline 都有,而且**每一個都綁在真實狀態上**:只有通過的關卡會發光、只有上一關通過的那一段連線會流動、被擋下來的會脈動、執行層永遠不動。純 CSS 不佔主執行緒(第六十三節:不得影響交易核心),`prefers-reduced-motion` 會停。關卡名稱用系統裡真的存在的那些 —— 把示意的 Quant Agent 畫進去但系統沒有,是在編造 |
| 六十四 | Database | ✅ | migration 007–009。ai_decisions / risk_events / audit_logs / market_regimes / trade_exits / strategies / backtests / backtest_runs / news / system_events / jobs 全部建好**而且有東西寫進去** —— 排程的 news_archive、strategy_mirror、回測任務、排程失敗事件。strategies 是**鏡像不是權威**:能不能下單看檔案,資料庫掛掉時「全部看起來是 LIVE」的方向是錯的。缺 users(單人系統,見九十七) |
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
| 七十七 | Research Loop | ✅ | 排程的 research_loop 把檢討結論變成有編號、有狀態的提案草稿。它**只做草稿** —— 第七十八節禁止的是整條鏈 |
| 七十八 | 禁止 AI 無限自改 | ✅ | 沒有任何自動改策略的路徑。實單那條鏈的最後一步也擋住了:`LiveBroker` 寫好之後,LIVE SAFETY GATE 的實單路徑那一項要人逐檔讀過原始碼、簽下每個檔案的 SHA-256 才放行(`agmcis/safety/live_path.py`)。雜湊我算得出來,「我讀過了」不行。改一個字雜湊就變,舊的簽章作廢。網頁精靈簽不了這一項,會直接拒絕 |
| 七十九 | Live 絕對安全原則 | ✅ | `live_gate.py` 九項任一失敗即 NO TRADE,且失敗預設是「未通過」 |
| 八十 | Configuration | ✅ | `agmcis/config/settings.py` 全部走環境變數;Standard/Perpetual 靠設定切換 |

## 八十一~九十:部署、API、前端

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 八十一 | Environment Separation | ✅ | 五個環境,無法辨識的名稱降級為 development(不是 production)。LIVE 閘門要求 `APP_ENV=production` —— 一台標成 development 的機器送真實訂單代表設定搬錯了 |
| 八十二 | Docker / Deployment | ✅ | 十項全部交代,對照表在 `deploy/README.md`。Nginx(限流 / HSTS / WebSocket upgrade / health 不限流)、HTTPS(certbot + acme 路徑不導向)、systemd 三個 unit(非 root、加固、反覆失敗會停)、備份 timer(關機補跑)、監控文件。**Redis 刻意不用**且說得出門檻:快取是 3-60 秒的行程內字典、任務刻意一次跑一個、跨程序限流已經用 PostgreSQL。Production 仍在 systemd 上 —— 這些是要比對過再放上去的參考設定,不是一鍵部署 |
| 八十三 | Deployment Strategy | ⚠️ | 五個環境的定義、`docker-compose.staging.yml`、以及 `docs/DEPLOYMENT.md` 寫清楚「不直接覆蓋 Production」具體是哪五步(migration 一律先跑再重啟)。staging 把 `TRADING_MODE` **寫死**成 paper 並用獨立的資料庫 volume —— 共用 production 資料庫的 staging 是第二個 production。**還缺一台實際的機器** |
| 八十四 | Secrets | ✅ | 全部走環境變數,`test_access_control.py` 釘住 |
| 八十五 | API Design | ✅ | `/api/backtest` 與 `/api/paper` 做成**任務式**端點(POST 送出 → 拿 job_id → GET `/api/jobs/{id}` 查),不是會逾時的同步請求。一次只跑一個 —— 併發回測會讓停損檢查延遲,而那是拿真錢換一份報告。查詢類再補 `/api/system_events`、`/api/backtests`、`/api/news`、`/api/strategies` |
| 八十六 | Frontend Dashboard | ✅ | 首頁 + 完整儀表板 + 透明度面板。Top Opportunities、News、AI Agents 三個區塊都有 |
| 八十七 | Trading Panel | ✅ | `/trading` 的開倉預覽走**與自動交易相同的鏈**(`_agent_intent` → `evaluate_intent`),在 Execution Engine 前停下。含被風控擋下的那些。**不下單、不寫決策紀錄** —— 看一眼不是打算。Score 那一格是空的:Agent 管線不產生分數,拿信心冒充會讓系統看起來有兩個獨立指標 |
| 八十八 | Open Position Panel | ✅ | Dashboard 表格含 Entry/Current/SL/TP/Leverage/Notional/PnL/PnL% |
| 八十九 | News Center | ✅ | Impact / Direction / 強度 / 相關標的都有。UI 明說**強度是關鍵字命中的強度,不是價格影響機率** |
| 九十 | System Status UI | ✅ | 首頁狀態列九個元件。降級時把壞掉的元件**列出來** —— 只顯示 DEGRADED 等於要人去翻 log |

## 九十一~一百零六:模式 UI、品質、流程、最終原則

| 節 | 主題 | 判定 | 說明 |
|---|---|---|---|
| 九十一 | Trading Modes UI | ✅ | 四種模式 MANUAL / PAPER / TEST / LIVE 在 `/modes` 全部列出並標明現在是哪一個 —— 只顯示現在這一個的話,使用者看不出「我以為在 PAPER 但其實在 MANUAL」。模式是從三個既有設定**推導**的,不是第四個真相來源。**TEST 蓋過 LIVE**:測試網上沒有真錢在動,反過來判定的話使用者會學會忽略紅色警告。LIVE 的橫幅會呼吸 —— 靜態的紅框會被看習慣 |
| 九十二 | LIVE Confirmation | ✅ | 兩條路徑,同一套驗證:`scripts/live_confirm.py`(CLI)與 `/modes` 的網頁精靈都走 `agmcis/safety/live_confirm.py`,四道保護一個都沒少(七項齊全 / 設定指紋 / 24 小時 / 指名金額且不超過首次上限)。網頁版加一條:**送出時比對前端拿到的指紋**,擋「使用者在確認的過程中設定被改過」。它**不會讓系統下實單** —— 確認檔只是 LIVE GATE 其中一項檢查,而 LiveBroker 不存在,最壞情況是磁碟上多一個檔案 |
| 九十三 | No Hidden Trading | ✅ | 所有下單進 DB + log + journal;`/transparency` 可查 |
| 九十四 | No Silent Failure | ✅ | `test_production_safety.py` 以 AST 掃描 `except: pass` |
| 九十五 | Code Quality | ✅ | God Function 全部處理完:Dashboard Lite(藏著損益公式的第二份拷貝)、`analytics.get_trade_analytics`、`risk_control.get_risk_control_status`(九項限制變成九個可單獨測的函式)、`paper_trading.create_paper_trade`(驗證 / 成本 / 成本後驗證分開)、`auto_trader.run_auto_trader`(逐檔評估拆出來)。`strategy.analyze_symbol` 是**死碼,直接刪掉** —— 拆開一段沒有人呼叫的評分公式只會讓它看起來更值得保留。`tests/test_god_function_split.py` 用**敘述數**(不是行數,註解密度會騙人)擋回歸,例外只有兩個 atomic 交易函式且要寫理由 |
| 九十六 | Coding Rule | ✅ | Phase 0–17 每階段都有 BUILD→TEST→VERIFY→REPORT 報告 |
| 九十七 | 不要一次重寫 | ✅ | 舊模組用 shim 轉接,沒有大爆炸式重寫 |
| 九十八 | 每次修改必須說明 | ✅ | `docs/PHASE_*_REPORT.md` |
| 九十九 | 錯誤不能隱藏 | ✅ | 測試失敗時明說 FAILED,本表本身就是這條原則的產物 |
| 一百 | 開發順序 | ✅ | Phase 0–17 依序完成;18–20 需要真實資金,必須人工授權 |
| 一百零一 | 第一個任務 | ✅ | `docs/AUDIT_REPORT.md` 17 問全數回答 |
| 一百零二 | 不問不必要的問題 | ✅ | 只在真實資金 / Live / API Key 相關時要求人工確認。實單原始碼的逐檔審視也算在這一類 —— 它問的次數等於實單檔案數,不多問一次 |
| 一百零三 | 真正的成功標準 | ⚠️ | Reliable / Testable / Explainable / Risk Controlled / BingX Compatible / Research Driven 六項達成。**Operationally Safe 要跑過真錢才算數** —— 這一項先前標成 ✅ 是不對的,第二節與第九十九節不允許在沒驗證的情況下宣稱完成 |
| 一百零四 | 最終架構目標 | ⚠️ | 主幹完成,Portfolio 分支也完成 —— `agmcis/risk/portfolio.py` 是 Risk Engine 裡的 hard gate(`engine.py` 算完倉位之後才評估,因為曝險要用實際名目不是意圖),41 個測試釘住。**這一列先前寫「Portfolio 分支缺席」是錯的**,而且與這份文件後面的說法自相矛盾。它留在 ⚠️ 的真正原因只有一個:**沒有跑過真實的多倉情境** |
| 一百零五 | 最終使用者體驗 | ✅ | 首頁就是第一百零五節描述的樣子。完整儀表板移到 `/dashboard`,沒有刪掉 —— 第一眼要回答「健康嗎、是不是真錢、有沒有機會」,不是三十個數字 |
| 一百零六 | 最終原則 17 條 | ⚠️ | 第 1–16 條達成,含第 12 條(Standard/Perpetual 分離)與組合層風險(見一百零四那一列 —— 先前把它算成缺口是錯的)。缺口只剩第 17 條「任何開倉都必須有風險保護」的六步驟緊急保護:程式碼寫完了,`LiveBroker` 也把停損掛單那條路做出來了,但**沒有在真的下單失敗時觸發過** |

---

## 統計

| 判定 | 節數 |
|---|---|
| ✅ 已做到 | 100 |
| ⚠️ 部分做到 | 6 |
| ❌ 沒做 | 0 |

## 還沒補完的 6 節,以及為什麼

原本的 12 個「❌ 沒做」全部補完了。剩下的 6 個全部是同一類:**沒有跑過真實資料或真錢的東西不能宣稱做到。**
**這一份不含「快做完了」這種說法** —— 每一條都寫缺什麼,而不是缺多少。

### 一、刻意不做(0 節)

原本有四節列在這裡:八(Adapter 拆檔)、六十三(動畫)、
八十二(Docker / 部署)、九十二(LIVE 網頁確認)。

**那四個是我的判斷,不是規章的要求。** 使用者要求照 106 節做,
而回頭讀原文之後有兩件事要更正:

  * 第八節本來就寫著「REFACTOR EXISTING CODE」—— 我的顧慮是
    **怎麼拆**的問題,不是拆不拆的問題。
  * 第九十二節沒有提到介面。我說它「要求網頁按鈕」是誤讀 ——
    要求多重確認的是第九十一節,而七個確認畫面加上逐字輸入
    一句話本來就不是誤觸。

四節都做完了。

### 二、需要真實環境或真錢才能算數(6 節)

這六節的程式碼都寫完了。它們留在 ⚠️ 是因為**沒有跑過真實資料或真錢的東西不能宣稱做到**
(第二節、第九十九節)。

| 節 | 還缺什麼 |
|---|---|
| 三十八 | **這一列先前的說法是錯的。** 它寫「要升級需要 BingX 的逐筆資料」,暗示拿不到 —— 但 BingX 有公開的 Recent Trades 端點,ccxt 也支援(`fetchTrades`),而它從 `isBuyerMaker` 推出的 `side` 就是主動方。真正的 volume delta 已經實作了(`get_trade_flow`),OrderFlow 優先用它(信心上限 80),拿不到才退回訂單簿代理(上限 65)。留在 ⚠️ 的理由換成:**那條路在真的 BingX 上還沒跑過** |
| 五十六 | 1R / 2R / 3R 與 30/30/40 是**起點不是結論**。調校工具寫好了(`scripts/tune_exits.py`),但它要真實 K 棒才有意義,而這個容器連不到 BingX。跑完之後結果也不會自動套用 —— 走提案流程,由人批准(第七十八節)|
| 八十三 | compose 與 runbook 都寫好了(`docker-compose.staging.yml` + `docs/DEPLOYMENT.md`),**但沒有第二台機器**。起一個 staging 是一行指令,不是一個設計問題了 |
| 一百零三 | 前六項達成。**Operationally Safe 要跑過真錢才算數** |
| 一百零四 | 組合風險與相關性是 Risk Engine 的 hard gate,41 個測試釘住,但**沒有跑過真實的多倉情境** |
| 一百零六 | 第 1–16 條達成。第 17 條「任何開倉都必須有風險保護」的六步驟緊急保護寫完了,但**沒有在真的下單失敗時觸發過** |

## 這些缺口要怎麼補

上面第二類的六節,答案都在同一個地方:**跑真實資料**。
`docs/OPERATOR_ACTIONS.md` 列了要跑哪幾支腳本、順序是什麼,
以及哪幾件事需要你授權才能繼續(Phase 18–20 的小額實單、監控、放大)。

我不會自己做那些,也不會催。

## 明確不做的事

* **Phase 18–20(小額實單 / 監控 / 放大)**:需要真實資金,依第一百零二節必須人工授權。

## 實單路徑(LiveBroker)

`agmcis/execution/live_broker.py` 寫好了,45 個行為測試釘住它。
它**沒有被接進 Execution Engine** —— 沒有任何一條路徑會實例化它。

寫它的理由:實單與模擬盤真正不同的地方(停損是交易所那邊一張獨立的
掛單、部分成交會真的發生、查詢失敗與查無此單是兩件事)在模擬盤裡
一條都驗不到,而它們每一個都會在實盤的第一週發生。那些分支要先存在
才驗得到。

測試問的都是同一句話:**這裡壞掉的時候,它往哪一邊倒?**

| 情況 | 它往哪邊倒 |
|---|---|
| 讀不到實單名目上限 | 不送單。讀不到上限不等於沒有上限 |
| 算不出這筆訂單的名目 | 不送單。不知道多大就不下單 |
| 設定槓桿失敗 | 不送單。用上一次的槓桿開倉,倉位大小就不是我們算的那個 |
| 交易所回應少了成交量欄位 | 算「狀態不明」交給對帳,不算「成交 0」 |
| 查不到掛單 | 當作**沒有**停損保護 |
| 部位方向不明 | 不動它。猜錯是把倉位開成兩倍,不是平掉 |
| 沒看過的訂單狀態 | 翻成 `unknown`,不猜一個樂觀的 |
| 訂單查詢失敗 | 拋例外。回 `None` 會讓成交的單被標成 REJECTED |
| 送單過程拋例外 | **不接**。Execution Engine 要看到它才會標 UNKNOWN 交給對帳 |

那道名目上限是重複的 —— Risk Engine 已經擋過一次。重複是刻意的:
上游任何一段被繞過、被改壞、被新的呼叫路徑跳過,這裡還會擋。
平倉不受它限制,因為上限的目的是擋住「開太大」,而一個因為超過上限
而平不掉的部位是這道上限能造成的最壞結果。

### 用 clientOrderId 查訂單:為什麼不能直接查

對帳是拿 `client_order_id` 去問交易所「這張單怎麼了」,而 ccxt 的
`fetch_order(id, ...)` 那個 id 是**交易所的訂單編號**。

第五節說 BingX 的 API 要照官方文件不要靠記憶猜。官方文件站被這個容器的
網路政策擋掉,但**真正決定行為的是本機那份 ccxt**(4.5.78)—— 它才是
實際發出請求的程式碼,讀它不是猜。讀出來的結果是:

  * `create_order` 與 `cancel_order` 都認得 clientOrderId,送給 BingX
    永續的欄位是 `clientOrderID`(大寫 ID),現貨是 `newClientOrderId`。
  * `cancel_order` 有 client-id 分支:帶了 `clientOrderID` 就**不送
    `orderId`**。
  * `fetch_order` **沒有那個分支** —— 它一律送 `orderId: id`,再把
    params merge 進去。

所以把 client id 塞進 `fetch_order` 會同時送出一個假的 `orderId`。
問題從來不是「欄位名不知道」,是那條路徑本身不對。

現在的做法是**列舉再比對**:先查未結掛單(沒有時間窗),沒有再查歷史
(`fetch_canceled_and_closed_orders`,七天視窗),兩邊都比對 ccxt 統一
後的 `clientOrderId`。全走有文件的路徑,不送任何假欄位。

「查不到」只有在**兩份清單都成功取得而且都沒有**時才回 None;任何一邊
查詢失敗都拋例外,對帳因此記成 `ORDER_STILL_UNKNOWN`(狀態不明,不可
重送)。有測試把查詢失敗真的餵進對帳,確認落點是 UNKNOWN 不是 REJECTED。

⚠️ 剩下的限制:歷史查詢有七天視窗。一張卡在 UNKNOWN 超過七天的單,
這裡會回 None 而那可能是錯的 —— 不過那種單本身就是更大的問題,
不該靠對帳自動收尾。這一段在 VPS 上還沒對著真的 BingX 跑過。

### 順帶補上的一條:緊急保護的縮倉那一步

第十八節的六步驟緊急保護,中間的「縮倉」那一步在模擬盤**從來沒有被
執行過** —— PaperBroker 的 `reduce_position()` 一律拋 NotImplementedError
(它沒辦法定義「縮掉的那一半用什麼價格結算」),所以模擬盤的緊急流程
永遠是「重試 → 失敗 → 直接平倉」。

LiveBroker 真的實作了縮倉,所以那一步現在有一個會走它的 broker。
`tests/test_live_broker.py` 把六步驟整條跑過:本來就有保護不進流程、
重試掛不上就縮倉再掛、救不回來就平倉、平不掉就 STUCK 而且喊出來。

這**不是**「在真的下單失敗時觸發過」—— 那要真錢,所以一百零六仍然是 ⚠️。
這只是把「六步接得起來、每一步的方向都對」驗掉了。

### SIMULATED LIVE:整條路徑跑得起來,但一毛錢都不動

`agmcis/exchange/simulated.py` 是一個假的 BingX。它不連線、不需要金鑰,
但回傳的每一個欄位都照 ccxt 4.5.78 的 bingx **真正會產生**的形狀 ——
**包括那些坑**:

  * 停損單的 `type` 是 `"market"`(parse_order_type 洗掉了 "stop")
  * 部分成交的 `status` 是 `"open"`
  * 部位的 `hedged` 永遠是 `None`,不管帳戶是不是雙向持倉

保留那些坑是這個模擬器唯一重要的設計決定。一個「乾淨漂亮」的模擬器
會把它們填平,然後測試全綠而實盤照炸 —— 那比沒有模擬器更糟,
因為它給人一種驗過了的感覺。這個專案在實單路徑上找到的四個 bug
全部是綠燈狀態下的 bug,原因就是假物件照著我的假設寫。

`scripts/simulate_live.py` 用它把實單路徑整條跑一次,11 個情境:

| 情境 | 驗的是 |
|---|---|
| 正常路徑 | 開倉 → 掛停損 → 確認 → 平倉,而且平倉前撤掉停損 |
| 停損被拒 | 第十八節六步驟走到平倉,沒有留下裸倉 |
| 停損掛上又被撤掉 | `ensure_stop_loss()` 回 True 與「真的有保護」是兩件事 |
| 部分成交 | 認得出來,剩餘量有人管 |
| 送單後連線中斷 | 例外往上拋,不假裝沒送出去 |
| 中斷之後對帳 | 靠 clientOrderId 在歷史裡找得到 |
| 查詢失敗 | 不等於「交易所沒有這張單」 |
| 雙向 / 單向 / 模式不明 | positionSide 帶或不帶,不知道就不帶 |
| 救不回來又平不掉 | 回報 STUCK 並說明原因,不安靜失敗 |

**這不是「可以下實單」的證據。** 模擬器永遠比真實交易所仁慈:
它不會限流、不會有時鐘偏移、不會在半夜改 API、不會有真實滑點。
它證明的只有一件事:**這條路徑接得起來,而且壞掉的時候往安全的方向倒。**
所以一百零三與一百零六仍然是 ⚠️。

接上它是**另一個決定**,而且在那之前 LIVE SAFETY GATE 的實單路徑
那一項要有人逐檔讀過原始碼並簽下 SHA-256(見第七十八節那一列)。
