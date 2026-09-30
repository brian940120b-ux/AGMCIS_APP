# OFFICIAL_VS_AIFCS_MATRIX

左欄只寫官方需求（附來源）；右欄是 AIFCS 目前的實作（`backend/competition/`）。Status：MATCH / PARTIAL / MISSING / CONFLICT / NOT VERIFIED。這份表**不修改** AIFCS。

| Official Requirement（來源） | AIFCS Implementation | Status | Evidence |
|---|---|---|---|
| OBS 26 × double、208 B、LE（表 1；指引03 p.5；player1:290） | `protocol.OBS_STRUCT = "<26d"`，208 | MATCH | `protocol.py:21-22`；`test_competition_spec.py::test_the_observation_is_table_one` |
| OBS 欄位順序 = 表 1 | `OBS_FIELDS` 26 名，順序對過 | MATCH | `test_competition_spec.py`（IDs 1,15,19-23） |
| CMD `<fffff10s` + "PLAYER_CMD"、30 B（表 2；player1:325） | `protocol.CMD_STRUCT = "<fffff10s"`；尾碼檢查 | MATCH | `protocol.py:25-27,124` |
| state 0/1/2（表 2） | `PlayerState.NOT_READY/INITIALISED/CONNECTED` = 0/1/2 | MATCH | `test_competition_spec.py::test_the_command_is_table_two` |
| state 切換：第一筆有效 → 1；>60 包 → 2（指引03 p.4、p.6；範例） | `client.py`：INITIALISED 後 `frames_this_round > CONNECTED_AFTER_FRAMES(60)` → CONNECTED | MATCH（對範例）；Host 真正條件 U11 | `client.py:60,224-229` |
| 非 208 B 封包忽略（player1:283） | 對真 HOST 33,090 幀 0 格式錯誤（AIFCS 自測） | PARTIAL（行為未逐行比對） | `docs/CONFORMANCE.md` A 級表 |
| Port：P1 收 8199 / 送 8099（Setting.txt；player1:25-29） | 可設定；預設同範例 | NOT VERIFIED（本次未讀 client 預設值） | — |
| 60 Hz（公告 p.3；Setting RUN_FPS 60） | `SIM_HZ = 60`；每幀決策；決策延遲實測 0.20 ms | MATCH | `state.py:34`；CONFORMANCE A |
| 初始距離 3/6/9 千呎、高度 10–20 千呎、340 節（公告 三.1） | `RoundSetup()` 預設同；`--geometry` 只改訓練用航向 | MATCH（評測）；訓練可選其他分佈 | `test_competition_spec.py::test_the_round_is_rule_one` |
| 初始航向（公告未定）U01 | 隨機 | NOT VERIFIED（官方未說） | — |
| 攻擊範圍 2°、500–3000 ft（公告 三.1.(6)；圖 4） | `AttackEnvelope(half_angle_deg=1.0, 500, 3000)` | PARTIAL：半角 1° 是對圖 4 與 `jsbsimEnv.py:406`（`≤ 1.0`）的解讀，U02 | `scoring.py:87-89`；CONFORMANCE D.1 |
| 擊殺 累積 3 秒（公告 三.2） | `kill_seconds = 3.0`，累積不需連續 | MATCH（累積解讀 U03） | `scoring.py`；spec test |
| 墜毀 < 50 m；相撞 < 15 m（公告 三.2） | 50.0 m / 15.0 m | MATCH | `scoring.py:55,57` |
| 表 3 判定順序、完全平手重比 | `decide_round`；`Verdict.REPLAY` | MATCH | CONFORMANCE C |
| S_kill、S_advantage、P(t)、表 4（公告 三.3） | `ScoringWeights` 1000/2000/1000/10 可換；`DISTANCE_FACTOR_BANDS` 五段；(TA+AA)×DF | MATCH（參考值） | `scoring.py:46-80`；spec test |
| T_G = 超過 9G 秒數（U04 方向未定） | `abs(g) > 9.0`（雙向） | PARTIAL（解讀） | `scoring.py:59`；TUNING「正負號」 |
| ΣP_t 每幀（U05） | 每幀累加 | PARTIAL（解讀） | CONFORMANCE D.2 |
| 搖桿整形常數（player1:336-370；jsbsimEnv:148-178） | 差分測試逐點相等 | MATCH | `test_competition_parity.py`（41 測試） |
| 升降舵高速限制：client 0.4 vs env 1.0（C1） | 採 client 的 0.4（比賽跑的是 client）；`--g-limit` 可改用 G 限制（研究層） | PARTIAL（選了一邊；官方衝突） | `action.py`；CONFORMANCE B 表 |
| Mach 的算法：client `vt*0.3048/340`（player1:341）；env 用 JSBSim `velocities/mach`（jsbsimFdm velocity[12]） | 採 client：真空速/340 | PARTIAL（同 C1 的一體兩面） | `state.py:85-94` |
| Rudder：規則 ±1；範例 0.2 / 鎖舵（C3） | 預設 0.2（`--rudder` 可開） | MATCH（規則允許） | `action.py:RUDDER_LIMIT`；spec test |
| 4+1 參數、不得要求其他介面（六.1） | 只送表 2 的 5 欄 | MATCH | `protocol.py` |
| 積分項須在經緯度變化後才開始（六.2） | 回合邊界由「位置凍結後再變化」偵測；對真 HOST 兩回合皆偵測到 | MATCH（對測試 HOST）；比賽日 U13 | CONFORMANCE A「回合邊界」 |
| 單機、封閉網路、不得外部介入（六.5–6、六.8） | 訓練在 Kaggle（**賽前**）；比賽日 client 單機執行，無外連 | MATCH（設計）；當天執行紀律由人 | `docs/RULES.md` |
| 不得改/轉/偽造封包、不得掃描/干預（六.7、六.9） | 一律不做 | MATCH | 專案原則 |
| 自行處理封包遺失/延遲/重複（六.10） | 保持上一筆命令、每幀回覆 | PARTIAL（未對遺失情境壓力測試） | `client.py` |
| 訓練環境 reset 分佈（jsbsimEnv:205-234） | `RoundSetup.reference()` 可重現；預設用比賽規則 | MATCH（兩者都有） | `environment.py:82-89` |
| 訓練程式 `ic/vc-kts` 先於高度（jsbsimFdm:67-72） | 預設高度先（實測 HOST 340 KCAS）；`--reference-speed-order` 可重現範例順序 | CONFLICT（有意）：與範例程式順序不同，與 HOST 實測一致 | `environment.py:150-160`；CONFORMANCE A |
| 訓練程式用 pip 資料根（jsbsimFdm:52）；套件另附引擎檔（U08） | `--jsbsim-root` 可二選一；預設 pip；兩者 30 秒差 2.4 ft | PARTIAL（哪份是比賽用未知） | `environment.py` 模組說明；AIRCRAFT_KNOWLEDGE_BASE §三 |
| 敵機 PID 平飛（jsbsimFdm:334-386） | `reference_opponent` 重現 + 腳本對手集 | MATCH（重現）+ 擴充 | `environment.py:266-`；`adversaries.py` |
| 獎勵五項（jsbsimEnv:367-425） | `ReferenceReward` 逐項移植 | MATCH | `rewards.py:62-115`；rewards tests |
| 20 維 state（jsbsimEnv:331-351） | `StateEncoder` 差分相等；`extended`/`frames` 為選項 | MATCH（reference） | parity tests |
| 訓練不實作擊殺（C2） | AIFCS 每幀計分並判擊殺 | 差異（AIFCS 依公告，不依範例） | `gym_env.py` |
| SAC 超參（train.py:40-63） | `train.py` 無旗標 = 主辦方配方（EXP-001） | MATCH（可重現） | `experiments/EXP-001` |
| Python 3.10 / SB3 2.4.0（指引02；模型 metadata） | `requires-python >= 3.11`；SB3 未釘版（雲端 2.9.0） | CONFLICT（環境）：無法用官方環境載入我方模型；但規則不要求語言/版本 | `pyproject.toml:5` |
| ACMI 輸出（指引04/05；非規則） | 無 ACMI 寫入器；有自己的 replay（jsonl/SQLite/HTML） | MISSING（OPTIONAL 項） | grep 無 acmi |
| Host GUI 狀態機（指引04） | 由 `probe.py` 對真 HOST 實測握手與回合邊界 | PARTIAL（只測了 INIT/START/回合） | CONFORMANCE A；`probe.py` |
| Host 不配平（U08） | 對測試 HOST 實測：不配平，18,084→94 ft | MATCH（對測試 HOST）；比賽日 U15 | `docs/COMPETITION.md` |
