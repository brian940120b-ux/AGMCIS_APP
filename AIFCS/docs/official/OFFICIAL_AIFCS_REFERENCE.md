# OFFICIAL_AIFCS_REFERENCE — 官方資料全面 Audit 總報告

Audit 日期 2026-09-30。讀的是主辦方發布的原始檔案（上傳到本 session 的副本，與筆電 `AI飛行員 競賽辦法\` 內容 sha256 逐檔相同）。
本文件只寫官方資料；AIFCS 只在 §T 出現。狀態標記見 `README.md`。

==================================================
## A. Official Competition Identity
==================================================
2026 神盾盃國際邀請賽暨國防 AI 競賽「AI 飛行員擂台賽」；主辦：國家中山科學研究院；115/11/7–8；成大國際會議廳 B1 第 3 研討室；每隊 ≤ 4 人；報名 defense-ai.tw。`[VERIFIED]`（附件2；公告封面）

==================================================
## B. Official Documents（版本）
==================================================
| 文件 | 版別（印在頁面） | PDF metadata 建立時間 | 備註 |
|---|---|---|---|
| 公告說明（0918） | 2026/09/18 | 2024-12-27（Word 2013） | metadata 早於版別 → 模板日期，`[INFERRED]`；以印刷版別為準 |
| 附件2 競賽規則（1150909） | 115/09/09 | 2026-09-09 | — |
| 指引 01–06 | 2026/09/03 | 2026-09-03 | 六份同日 |
| zip 內程式 | — | 2026-08-25 ～ 09-03（zip 時間戳）；`jsbsimEnv.py` 最新 09-03 | — |
最新的規則文件 = 公告 2026/09/18。指引（09/03）早於公告；兩者未見矛盾的地方以公告為準。

==================================================
## C. Official Programs
==================================================
見 `OFFICIAL_PROGRAM_MAP.md`。八個 Python、三個 bat、一個 exe、Setting.txt、兩個 readme、範例模型、範例 ACMI、F-16 機體檔。程式全部是「範例」，公告 p.11 五.3.(2) 明言可修改或以其他語言重寫。

==================================================
## D. Environment Requirements
==================================================
規則層：能 UDP 交換即可，Windows/Linux 不限（公告 五.2）；硬體只有「建議」（五.1）。
範例層：Anaconda + Python 3.10 + jsbsim + gymnasium + SB3 2.4.0 + tensorboard + torch(cu128/121/118)（指引02）。範例模型實際環境：Python 3.10.20、SB3 2.4.0、torch 2.12.0.dev20260408+cu128、gymnasium 1.0.0、numpy 1.26.4、Windows 10.0.26100（模型 `system_info.txt`）。

==================================================
## E. Training Environment
==================================================
`JsbsimEnv`：60 Hz、每步 1 幀、18,000 步截斷；動作 Box(4) rudder 鎖 [0,1e-17]；觀測 Box(20)；reset 分佈 0–12,000 ft / 1,000–22,000 ft / 340–400 kt / 航向隨機（**與比賽規則不同**，指引05 p.6 自述為泛化）；敵機 PID 平飛；**不實作擊殺**；獎勵五項。`[VERIFIED]`（`jsbsimEnv.py`、`jsbsimFdm.py`、指引05）

==================================================
## F. Model Training
==================================================
`train.py`：SAC、8 環境、lr 2e-4、buffer 5M、batch 256、γ 0.99、learning_starts 1M、Tanh [256,256]、SDE；`learn(5e8)`；checkpoint `save_freq=50000*2`（8 環境 → 每 800,000 timesteps，範例模型 314,400,000 = 393 × 800,000）。`[VERIFIED]`

==================================================
## G. Model Testing
==================================================
`test.py`：載入 `model/jsbsim_sac_314400000_steps`，deterministic，每步 render 成 ACMI；只在 terminated 停。`[VERIFIED]`

==================================================
## H. Player
==================================================
`player1_Loadmodel.py`：見 PROGRAM_MAP §F 與 WORKFLOW §4。三組網路設定擇一；208-byte 檢查；`<26d`；20 維 state 與訓練環境同式；整形（升降舵 Mach>0.8 限 0.4）；`<fffff10s`+"PLAYER_CMD"；state 0→1→2。`[VERIFIED]`

==================================================
## I. Host
==================================================
公告版 `JSB_host_GUI_publish.exe`：STOPPED → INIT → (雙方初始化) → READY/GO → 手動 START → RUNNING → FREEZE/RESUME → STOP → CSV→ACMI → STOPPED；內建 Player2 為平飛 PID；`Setting.txt` 定 IP/PORT/60 FPS。exe 內部 `[NOT VERIFIED]`。比賽日 Host 是否同版 `[UNKNOWN]`。

==================================================
## J. UDP
==================================================
Host→Player OBS（P1 8199 / P2 8201）；Player→Host CMD（8099 / 8101）；監控 8000/8010；比賽日 IP 由檢入指定（程式註解範例 192.168.1.1/.3/.4）。`[VERIFIED]`（Setting.txt、player1、指引03、公告 四.3）

==================================================
## K. OBS
==================================================
26 × double、208 bytes、little-endian、順序 = 表 1（本機 20 + 敵機 6）；單位 deg / ft / ft/s / rad/s / G。`[VERIFIED]`。逐欄見 `OFFICIAL_DATA_DICTIONARY.md`。

==================================================
## L. CMD
==================================================
5 × float32 + Char[10] "PLAYER_CMD" = 30 bytes；順序 aileron(roll), elevator(pitch), rudder(yaw), throttle, state；範圍 ±1 / ±1 / ±1 / 0–1 / {0,1,2}。`[VERIFIED]`。endian：程式 `<`；文件未明說 → 文件層 `[PARTIAL]`。

==================================================
## M. Scoring
==================================================
表 3 判定順序；S_kill = W_base + (300 − T_kill)；S_adv = W_time·T_att − W_G·T_G + W_pos·ΣP_t；P(t) = (TA_norm + AA_norm) × DF；DF 表 4；W 皆「參考值，由主辦方定義」。`[VERIFIED]`（公告 p.7–9）

==================================================
## N. ACMI / Tacview
==================================================
訓練端 ACMI 由 `render()` 寫（FileVersion 2.1，A0001/B0001）；Host 端 CSV→ACMI 到 `output_acmi/`；Tacview 為「建議」的分析工具，不是規則要求。`[VERIFIED]`

==================================================
## O. Competition Workflow
==================================================
分組 → 抽籤 → 檢入（IP/PORT、實體線、連線測試）→ 初始化（Host 送初始化指令，參賽者回準備狀態）→ 手動開始 → 5 分鐘 → 自動終止 → 3 分鐘切換 → 賽局結束。`[VERIFIED]`（公告 四）

==================================================
## P. Restrictions
==================================================
R0–R12，見 `OFFICIAL_COMPETITION_SPEC.md` §Restrictions。要點：封閉網路、單一機器、4+1 參數、不得外部介入/橋接/攻擊/改封包、自行處理封包異常、主辦方最終解釋權。`[VERIFIED]`

==================================================
## Q. Official Unknowns（OFFICIAL_UNKNOWN_LIST）
==================================================
| # | 沒有說的事 | 影響 |
|---|---|---|
| U01 | 比賽日的初始**航向**（相對幾何）；兩機高度是否相同 | 訓練分佈、開局策略 | 公開版 HOST 一回合（2026-10-01）：航向 303°/123° 相反、並排、同高、1,702 m；比賽日版本未知（`CONFORMANCE.md` F） |
| U02 | 「鼻軸線 2 度」是全錐還是半角（圖 4 畫全錐） | 擊殺判定 | **公開版 HOST 實測：半角 1°**，HP 每幀扣 1，與 `AttackEnvelope` 逐幀對齊（`CONFORMANCE.md` F2） |
| U03 | 「累積」3 秒是否允許中斷後續算；Host 幀內如何計 | 擊殺判定 |
| U04 | 超過 9G 的「超過」用 n-pilot-z-norm 的哪個方向/絕對值；G 值符號慣例 | T_G |
| U05 | ΣP_t 的累加頻率（每幀？） | 位置優勢分尺度 | 公開版 HOST：每幀，且含 START 前保持幀；有效 W_pos = 1/幀（`CONFORMANCE.md` F） |
| U06 | 當天的 W_base / W_time / W_G / W_pos 實際值（皆「參考值」） | 分數 | 公開版 HOST：W_time = 10,000／秒、W_pos = 1／幀（`ScoringWeights.host_measured()`）；W_G、W_base 未觸發（`CONFORMANCE.md` F、F2） |
| U07 | 賽制（5戰3勝／3戰2勝／循環） | 戰略 |
| U08 | Host 是否對飛機配平；Host 的 JSBSim 初始化順序；Host 用套件內引擎還是 pip 引擎 | 訓練用機體是否等於比賽用機體 |
| U09 | Host 每秒送幾包 OBS（60 Hz 運算 ≠ 明文 60 包/秒）；封包時序/抖動 | 決策節奏 |
| U10 | Host 收到非法 CMD（長度錯、NaN、尾碼錯、state 值錯）怎麼處理 | 穩健性 |
| U11 | Host 判定 state=2「已備便」的精確條件；60 包只是範例 | 初始化流程 |
| U12 | 初始化指令的封包格式（是否就是 OBS） | 握手 |
| U13 | 回合邊界怎麼通知（位置凍結？特定封包？） | 積分項重置（六.2 只說經緯度開始變化後才算） |
| U14 | Host CSV 欄位；比賽日是否給參賽者 ACMI | 賽後分析 |
| U15 | 比賽日 Host 版本是否 = 公告版 exe | 一切 |
| U16 | 「JSBGYM」「Socket」在下載清單裡指什麼 | 環境 |
| U17 | 封包遺失/延遲時 Host 用上一筆 CMD 還是歸零 | 穩健性 |
| U18 | 主辦方是否會在比賽中 FREEZE/RESUME | 時序 |

==================================================
## R. Official Conflicts（CONFLICT REPORT）
==================================================
| # | Source A | Source B | 差異 | 可能原因 | 較新？ | 需主辦方確認 |
|---|---|---|---|---|---|---|
| C1 | `player1_Loadmodel.py:341-344`：Mach>0.8 時升降舵限 0.4 | `jsbsimEnv.py:153-154`：兩分支皆 1.0 | 比賽 client 與訓練環境的整形不同；訓練出的模型在 client 端會被額外限舵 | 訓練環境該行原本應為 0.4 被改成 1.0（或反之） | 皆 08/31–09/03 | 是 |
| C2 | 公告 p.7：擊殺 = 累積 3 秒 | `jsbsimEnv.py:45` `target_attack_frames = 300`，註解「總和5秒」；且從未使用 | 訓練環境不實作擊殺，常數還寫 5 秒 | 程式為早期版本 | 公告較新 | 否（以公告為準） |
| C3 | 公告 表 2：rudder −1～+1 | `jsbsimEnv.py:72,78`：動作空間 [0, 1e-17]；`player1:344` clip ±0.2 | 規則允許全舵，範例鎖舵/限舵 | 範例的設計選擇 | — | 否（公告 五.3.(2).C 允許改） |
| C4 | 公告 p.7：墜毀 = 低於 50 **公尺** | 指引05 p.4：「低於 50 **英尺**」；`jsbsimEnv.py:376` 比較的是公尺（`alt1 = h-sl-ft*0.3048`） | 文件寫錯單位；程式與公告一致 | 指引筆誤 | 公告較新 | 否 |
| C5 | 指引02 p.10：`conda create -n f16_ai` | 三個 bat：`conda activate F16_ai` | 環境名大小寫不同 | 筆誤；Windows 檔案系統不分大小寫多半仍可用 | — | 否 |
| C6 | 指引05 p.7：「大約每 10 萬步」存一個模型 | `train.py:25` save_freq=100,000 **回呼次數** × 8 環境 = 800,000 timesteps；模型檔名 314,400,000 = 393×800,000 | 文件把回呼次數當步數 | 文字近似 | — | 否 |
| C7 | 公告 p.9：「距離因子…如表 3.所示」 | 同頁表格標題「表 4. 距離因子」 | 編號筆誤 | — | — | 否 |
| C8 | 公告 p.6 三.1：初始距離 3/6/9 千呎、高度 10–20 千呎、速度 340 節 | `jsbsimEnv.py:205-218`：0–12,000 呎、1,000–22,000 呎、340–400 節 | 訓練分佈 ≠ 比賽分佈 | 指引05 p.6 明說是為泛化 | — | 否（不是矛盾，是設計） |
| C9 | `player1_Loadmodel.py:4` docstring 檔名 `player_Loadmodel.py`、`:384` 印 `player_connect.py` | 實際檔名 `player1_Loadmodel.py` | 名稱不一致 | 改名遺留 | — | 否 |
| C10 | 公告 表 2 ID 5 名稱 `state`；指引03 稱 `player_state` | — | 同一欄兩個名字 | — | — | 否 |
| C11 | 公告 p.9：TA_norm = (90−\|TA\|)/90 | 公開版 HOST CSV（2026-10-01 實測）：DF×(180−TA)/180，≥90° 為 0；AA 同 | 同門檻、不同斜率；Final 每幀累加 Att+Pos 含 START 前保持幀 | HOST 是比賽當天算分的程式；公告可能是簡寫 | `CONFORMANCE.md` F | **是**（影響分差尺度與訓練獎勵形狀；`scoring.Normalisation`） |

==================================================
## S. Official Source Traceability
==================================================
見 `OFFICIAL_TRACEABILITY.md`（T01–T58）。

==================================================
## T. Official vs AIFCS
==================================================
見 `OFFICIAL_VS_AIFCS_MATRIX.md`。

==================================================
## Summary — 34 題
==================================================
1. 官方資料夾實際路徑：`C:\Users\user\Desktop\AI飛行員 競賽辦法\`（名字中有空格）；本次讀的是同內容的上傳副本，sha256 逐檔相同。
2. 總檔案數：55（含 12 個 .pyc、2 個筆電自行產生的 Host 輸出）。
3. PDF：10（公告 1、附件2 1、報名文件 2、指引 6）。
4. 程式：Python 8 + bat 3 + exe 1。
5. 官方工具：1（`JSB_host_GUI_publish.exe`）。
6. 訓練相關：6（`train.py`、`jsbsimEnv.py`、`jsbsimFdm.py`、`utils.py`、`A.bat`、指引05）。
7. 測試相關：3（`test.py`、`B.bat`、指引06）。
8. 有 Host：有，公告版 exe（內部 `[NOT VERIFIED]`）。
9. 有 Player：有，`player1_Loadmodel.py`；Player2 只有 Host 內建的 PID 版（無原始碼）。
10. 完整 Training Environment：有（gymnasium 環境 + SAC 訓練 + 測試），但**不含擊殺判定、不含比賽的初始條件**。
11. Python：範例用 3.10（指引02）；規則不限語言。
12. Conda：Anaconda，環境 `f16_ai`（bat 寫 `F16_ai`）。
13. GPU：建議 RTX30 以上；非必要。
14. CUDA：「依實際規格自行配置」；torch 提供 cu128 / cu121 / cu118 三選一。
15. Packages：jsbsim、gymnasium、stable-baselines3[extra]==2.4.0、tensorboard、torch/torchvision/torchaudio；範例模型另見 numpy 1.26.4、gymnasium 1.0.0。
16. OBS：26 × double、208 bytes、little-endian、表 1 順序。
17. CMD：5 × float32 + "PLAYER_CMD"、30 bytes、little-endian（程式）。
18. IP/Port：本機 P1 收 8199 送 8099、P2 收 8201 送 8101；Host 收 8099/8101、送 8199/8201；監控 8000/8010；比賽日由檢入指定（範例 192.168.1.1/.3/.4）。
19. Player 連 Host：UDP；bind LISTEN；收到第一筆有效 OBS 回 state=1；>60 包回 state=2；之後每包回 CMD。
20. Host 啟動：雙擊 exe → STOPPED → INIT PLAYERS STATE → 雙方初始化 → START。
21. Training 啟動：`A.一鍵啟動train.bat`（`conda activate F16_ai; python train.py`）。
22. Test 啟動：改 `test.py` 模型名 → `B.一鍵啟動test.bat`。
23. ACMI 產生：訓練/測試端 `render()` 寫 `JSBSimRecording.txt.acmi`；Host 端回合結束 CSV→ACMI 到 `output_acmi/`。
24. Tacview：安裝後雙擊 .acmi；只是建議的分析工具。
25. 比賽開始：檢入連線測試 → Host 送初始化 → 雙方回準備狀態 → 主辦方手動 START。
26. 比賽結束：擊殺（3 秒）/ 墜毀（<50 m）/ 相撞（<15 m）/ 5 分鐘到 → Host 自動終止。
27. 評分：表 3 判定順序；S_kill、S_advantage、P(t)、表 4（見 §M）。
28. 禁止：R0–R12（封閉網路、單機、4+1 參數、不得外部介入/橋接/攻擊/改封包、自行處理封包異常）。
29. 最容易誤解：「2 度」半角/全角（U02）；50 公尺 vs 指引寫的 50 英尺（C4）；範例的 rudder 鎖舵不是規則（C3）；訓練環境不判擊殺（C2）；範例 60 包不是 Host 的規則（U11）；client 與訓練環境的升降舵限制不同（C1）；「每 10 萬步」其實是 80 萬（C6）。
30. 官方沒說：U01–U18。
31. 互相衝突：C1–C10（其中 C1、C2、C4 有實質影響）。
32. 需主辦方確認：C1（哪一個整形才是「比賽用」）、U02、U03、U04、U06、U08、U09、U10、U11、U13、U15。
33. 是否足以建立完整 Official Adapter：**封包層足夠**（OBS/CMD/state/port 全部 `[VERIFIED]`）；**握手與回合邊界只有範例行為**（U11–U13）；**計分可完整實作但有 U02–U06 的解讀**；**機體有 U08 的不確定**。結論：Adapter 可建，但要把 U02/U03/U08/U11–U13 當成「需在真 HOST 上實測」的項目。
34. 最大相容性風險：**U08 + C1** —— 我們訓練的飛機（引擎檔、初始化順序、升降舵限制）是否等於比賽日 Host 算的那架；其次 U02/U03（擊殺的幾何與累積定義）。
