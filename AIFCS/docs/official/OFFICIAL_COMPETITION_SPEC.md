# OFFICIAL_COMPETITION_SPEC

每一條都附來源；沒有來源的不寫。頁碼慣例見 `README.md`。

## Competition

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 名稱 | 2026 神盾盃國際邀請賽暨國防 AI 競賽「AI 飛行員擂台賽」 | 公告封面；附件2 標題 | `[VERIFIED]` |
| 主辦 | 國家中山科學研究院 | 公告封面 | `[VERIFIED]` |
| 日期 | 民國 115 年 11 月 7 日(六)–8 日(日) | 附件2 壹；公告封面「115 年 11 月 7-8 日」 | `[VERIFIED]` |
| 地點 | 國立成功大學國際會議廳 B1 第 3 研討室 | 附件2 貳；公告封面 | `[VERIFIED]` |
| 資格 | 中華民國國民；學校單獨或與 1 間法人/企業組隊；每隊 ≤ 4 人；大陸/港澳相關主體排除條款 | 附件2 參、肆 | `[VERIFIED]` |
| 報名 | 官網 https://defense-ai.tw | 附件2 伍 | `[VERIFIED]` |
| 規則依據 | 「依『AI 空戰競賽』公告文件之規定辦理」 | 附件2 陸 | `[VERIFIED]` |
| 模式 | 1 vs 1；兩隊各扮演參賽者 1 或 2 | 公告 p.6 三.1.(1) | `[VERIFIED]` |
| 場／回合 | 以場為單位；每場回合數可為 5 戰 3 勝、3 戰 2 勝、循環賽，依隊伍數由主辦方調整 | 公告 p.7 三.2.(1) | `[VERIFIED]`（賽制未定 → 具體值 `[UNKNOWN]`） |
| 回合時間 | 5 分鐘 | 公告 p.6 三.1.(2)；p.10 四.6 | `[VERIFIED]` |
| 回合切換 | 準備時間 3 分鐘為限；可微調 AI／更換演算法 | 公告 p.10 四.8；p.12 六.3 | `[VERIFIED]` |
| 允許的控制技術 | Rule-Base、行為樹、決策樹、貝葉斯、AI、ML、DL、RL、LLM 等非人工干預 | 公告 p.2 一.1.(2) | `[VERIFIED]` |

## Environment（模擬）

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 模擬器 | JSBSim 中的 F-16 模型，主辦方電腦統一計算雙方 6-DOF | 公告 p.3 一.2.(1) | `[VERIFIED]` |
| 時脈 | 60 Hz | 公告 p.2 圖 1、p.3 (1)；`Setting.txt:16` | `[VERIFIED]` |
| 網路 | 封閉式架構，不連外部公用網路；參賽者不得遠端連線參賽 | 公告 p.3 一.2.(3) | `[VERIFIED]` |
| 顯示 | 主辦方另傳資料到即時戰況顯示電腦 | 公告 p.3 (4)、圖 2 | `[VERIFIED]` |
| 主辦方 Host 是否配平 | — | 無任何文件提及 | `[UNKNOWN]` |

## Aircraft

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 機型 | JSBSim `f16`（`load_model('f16')`）；機體檔自述 F-16A Block-32 | `jsbsimFdm.py:12,57`；`aircraft/f16/f16.xml`（description） | `[VERIFIED]`（訓練程式）；Host exe 載入哪一份 `[UNKNOWN]` |
| 套件內機體檔 vs pip JSBSim 1.3.1 | `f16.xml` 只差空白/授權標記/一個 `name="POINTMASS"`（氣動資料相同）；`Engines/F100-PW-229.xml` **不同**：bypassratio 0.360 vs 0.4、bleed 0.03 vs 無、idlen1/n2 30/60 vs 40/53、推力表數值不同 | diff（本次 audit） | `[VERIFIED]`（差異存在）；哪一份是比賽用 `[UNKNOWN]` |
| 訓練程式用哪一份 | `jsbsim.FGFDMExec(None)` → JSBSim 套件預設資料根目錄，**不是**套件內的 `aircraft/` | `jsbsimFdm.py:52` | `[VERIFIED]`（程式）；效果 `[INFERRED]` |
| 控制面 | aileron / elevator / rudder / throttle（`fcs/*-cmd-norm`） | 公告 表 2；`jsbsimFdm.py:232-237` | `[VERIFIED]` |
| 初始化順序（訓練程式） | `ic/vc-kts` 先於 `ic/lat`、`ic/long`、`ic/h-sl-ft`、`ic/psi-true-deg`、theta、phi；`run_ic()`；starter_cmd=1；refuel=1（flight_mode 0）；`run()`；active_engine、set-running=-1 | `jsbsimFdm.py:66-95` | `[VERIFIED]`（程式順序）；Host 是否相同 `[UNKNOWN]` |
| dt | 1/60 | `jsbsimFdm.py:20,63-64` | `[VERIFIED]` |

## Initial conditions（比賽）

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 兩機距離 | 3,000 / 6,000 / 9,000 呎 | 公告 p.6 三.1.(3)；圖 3 | `[VERIFIED]` |
| 初始高度 | 10,000～20,000 呎隨機 | 公告 p.6 三.1.(4)；圖 3 | `[VERIFIED]` |
| 初始速度 | 340 節 | 公告 p.6 三.1.(5)；圖 3 | `[VERIFIED]` |
| 初始航向 | — | 公告未定義（圖 3 只畫兩架相向的箭頭，無數字） | `[UNKNOWN]` |
| 兩機高度是否相同 | — | 未定義 | `[UNKNOWN]` |
| 訓練環境（範例）的初始化 | 距離 `randint(0,12000)*0.3048` m、方位隨機、雙方航向隨機、高度 `randint(1000,22000)` ft、速度 `randint(340,400)` kt，各自獨立 | `jsbsimEnv.py:205-234`；指引05 p.6 | `[VERIFIED]`（且與比賽規則**不同**，指引05 自己說明是為泛化） |

## Attack envelope / Kill

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 有效攻擊範圍 | 鼻軸線 2 度、距離 500–3000 呎 | 公告 p.6 三.1.(6)；圖 4（箭頭橫跨整個錐，標 2°） | `[VERIFIED]`；2° 是全錐或半角：文字未明說，圖示為全錐 → `[INFERRED]` 半角 1° |
| 擊殺 | 攻擊方累積射擊滿 3 秒 → 提前結束，達成方勝 | 公告 p.7 三.2.(2).A；表 3 | `[VERIFIED]` |
| 同時擊殺 | 同一物理運算幀內雙方皆滿 3 秒 → 作戰優勢分高者勝 | 公告 表 3（圖） | `[VERIFIED]` |
| 「累積」是否可中斷後續算 | 文字為「累積射擊滿 3 秒」 | 公告 p.7 | `[INFERRED]` 累積（非連續）；Host 實作 `[NOT VERIFIED]` |
| 訓練環境的對應 | `target_attack_frames = 300`（註解「總和5秒」），**從未累加、從未用於終止** | `jsbsimEnv.py:44-45,191,469` | `[VERIFIED]`（程式不實作擊殺）；與公告 3 秒 `[CONFLICT]`（見 Conflicts） |

## End conditions

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 墜毀 | 高度低於 50 公尺即判定墜毀；存活方勝 | 公告 p.7 三.2.(2).B；表 3 | `[VERIFIED]` |
| 相撞 | 兩機距離小於 15 公尺；作戰優勢分高者勝 | 公告 p.7 三.2.(2).B；表 3「物理座標重疊」 | `[VERIFIED]` |
| 時間耗盡 | 雙方皆存活 → 作戰優勢分高者勝 | 公告 p.7 三.2.(2).C；表 3 | `[VERIFIED]` |
| 完全平手 | 擊殺分與作戰優勢分皆完全相同 → 平手（無效回合）再比一場 | 公告 p.7；表 3 | `[VERIFIED]` |
| 訓練環境 | 我方高度 ≤ 50 → −10 終止；敵方 ≤ 50 → 0 終止；18,000 步截斷 | `jsbsimEnv.py:376-382,43,466-467` | `[VERIFIED]`；單位見 Conflicts C4 |

## Scoring（公告 p.8–9 三.3；表 4）

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 總分 | S = S_advantage（T_att < 3 s）；S = S_advantage + S_kill（T_att = 3 s） | 公告 p.8 (1) | `[VERIFIED]` |
| S_kill | W_base + (300 − T_kill)；W_base 參考值 1000，由主辦方定義；T_kill 擊殺時間(秒) | 公告 p.8 (2) | `[VERIFIED]`；當天實際 W `[UNKNOWN]` |
| S_advantage | W_time·T_att − W_G·T_G + W_pos·ΣP_t；W_time 參考 2000、W_G 參考 1000、W_pos 參考 10 | 公告 p.8 (3) | `[VERIFIED]`（參考值） |
| T_G | 實際超過 9G 值秒數 | 公告 p.8 (3) | `[VERIFIED]`；「超過」用 OBS 第 15 欄的哪個方向 `[UNKNOWN]` |
| P(t) | TA_norm × DF + AA_norm × DF；TA_norm = (90°−\|TA\|)/90°（TA<90°，否則 0）；AA_norm 同理 | 公告 p.9 (4) | `[VERIFIED]` |
| TA / AA 定義 | TA：本機機頭指向與敵機位置的 3D 夾角；AA：本機位置與敵機機尾的 3D 夾角 | 公告 p.9 | `[VERIFIED]` |
| Distance_Factor | <150 m 0.1；150–500 m 1.2；500–1500 m 1.0；1500–3000 m 0.5；>3000 m 0.1 | 公告 表 4（圖，p.9） | `[VERIFIED]`（讀圖） |
| ΣP_t 的累加頻率 | 「即時位置優勢指標」 | 公告 p.8 | `[INFERRED]` 每運算幀；`[NOT VERIFIED]` |
| 表 3 的引用編號 | 內文「距離因子…如表 3.所示」但表格標題是「表 4.」 | 公告 p.9 | `[CONFLICT]`（編號筆誤） |

## Host（主辦方連線程式，公告版）

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 程式 | `JSB_host_GUI_publish.exe`；具 JSBSim F-16 模擬與網路收發；「不飛行控制能力」 | 公告 p.12 五.3.(2).D；指引04 | `[VERIFIED]` |
| 內建 Player2 | 按 INIT 後自動啟動；log 顯示為「Random level-flight PID controller (SAC disabled)」，監聽 127.0.0.1:8201，送 127.0.0.1:8101，Target Vc 339.9 kts | 指引04 p.2；`player2_runtime.log` 頭部 | `[VERIFIED]`（公告版行為）；比賽日對手是另一隊，不適用 |
| 狀態 | STOPPED → INIT PLAYERS STATE → (P1/P2 初始化回傳) → P1/P2 READY 倒數 → GO → 手動 START → RUNNING → FREEZE ⇄ RESUME → STOP → 存檔 + CSV→ACMI → STOPPED | 指引04 p.3–6；readme_part1/2 | `[VERIFIED]`（公告版 GUI）；比賽日 Host 是否同一程式 `[UNKNOWN]` |
| 倒數計時 | 預設啟用、5 分鐘；歸零自動 STOP；停用則正向計時需手動 STOP | 指引04 p.5；readme_part2 | `[VERIFIED]` |
| 輸出 | 回合結束自動存 CSV，轉成 ACMI 放 `output_acmi/`；兩機在 Tacview 均顯示 F-16 | 指引04 p.6；readme_part2 五 | `[VERIFIED]`（存在）；CSV 欄位 `[NOT VERIFIED]`（zip 未附範例；筆電上有 2026-09-26 產生的檔案未讀） |
| Setting.txt | `PLAYER1_INPUT 0.0.0.0:8099`、`PLAYER2_INPUT 0.0.0.0:8101`（Host 收 CMD）；`PLAYER1_OUTPUT 127.0.0.1:8199`、`PLAYER2_OUTPUT 127.0.0.1:8201`（Host 送 OBS）；監控 8000 / 8010；`RUN_FPS: 60`；`ENABLE_REALTIME_ANALYSIS: 0` | `Setting.txt:1-18` | `[VERIFIED]` |
| exe 內部 | 打包二進位；`strings` 無可讀協定字串 | 本次 audit | `[NOT VERIFIED]` |

## Player（參賽方連線程式範例）

見 `OFFICIAL_PROGRAM_MAP.md` 與 `OFFICIAL_WORKFLOW.md`。要點：`player1_Loadmodel.py` 監聽 LISTEN_IP:LISTEN_PORT 收 OBS，`SAC.load` 模型（`:387`），`get_state` 26→20 維（`:118-263`），`process_joystick_action` 整形（`:331-374`），`send_control_command` 送 30 bytes（`:304-327`），state 0→1→2（`:436-445`）。公告 p.11 五.3.(2).C：「並無強制限制需要按照本範例進行開發」；「本程式不具備 F-16 之運動模擬」。

## Network / IP / Port

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 本機測試 | Player1 監聽 127.0.0.1:8199；送 Host 127.0.0.1:8099 | 指引03 p.4；`player1_Loadmodel.py:25-29`；`Setting.txt` | `[VERIFIED]` |
| 比賽當天（程式註解的範例值） | P1：LISTEN 192.168.1.3:8199 → Host 192.168.1.1:8099；P2：LISTEN 192.168.1.4:8201 → Host 192.168.1.1:8101 | `player1_Loadmodel.py:32-45`（註解） | `[PARTIAL]`：只是範例；公告 p.9 四.3 說檢入時由主辦方指定 IP/PORT；p.12 六.4 不得要求指定 |
| BUFFER_SIZE | 4096 | `player1_Loadmodel.py:27`；指引03 p.5 | `[VERIFIED]`（範例值） |
| 連線流程 | 檢入區指定 IP/PORT → 參賽者自行輸入 → 實體網路線連 HUB → 連線測試；失敗由下一組遞補 | 公告 p.9–10 四.3 | `[VERIFIED]` |

## Training（範例）

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 框架 | gymnasium `Env` 子類 `JsbsimEnv`；SB3 `SAC` | `jsbsimEnv.py:8,18`；`train.py:7,49` | `[VERIFIED]` |
| 動作空間 | Box(4)：aileron [−1,1]、elevator [−1,1]、rudder **[0, 1e-17]**、throttle [0,1] | `jsbsimEnv.py:68-81`；指引05 p.2「方向舵下限為 0」 | `[VERIFIED]` |
| 觀測空間 | Box(20)，±500，float64 | `jsbsimEnv.py:83-138` | `[VERIFIED]` |
| 20 維狀態 | 見 `OFFICIAL_PROGRAM_MAP.md`（`jsbsimEnv.py:331-351` 與 `player1_Loadmodel.py:235-261` 同式） | | `[VERIFIED]` |
| 搖桿整形 | deadbands [0.01,0.03,0.06]、exponents [1,3,3]、每步變化上限 [0.050,0.026,0.013]、rudder 上限 0.2、油門每步 0.004 | `jsbsimEnv.py:148-178`；指引05 p.3 | `[VERIFIED]` |
| 獎勵 | 墜機 −10 終止；敵墜 0 終止；deck −5·(1−Sigmoid(h,1/20,1300))；接近率 clip(Δd·0.5,±2)·Sigmoid(d_ft,1/500,2900)；`own_3d_angle ≤ 1.0` 時 +(2−angle)；方位/俯仰超出 1° 死區各 −0.01/度 | `jsbsimEnv.py:367-425`；指引05 p.4–6 | `[VERIFIED]` |
| 敵機（FDM2）行為 | `auto_run`：高度保持 PID，`random.choice(["Straight"])`（轉彎分支存在但未列入選擇）、每 10–30 s 重新決策、油門 PID 追目標速度 | `jsbsimFdm.py:334-386`；指引05 p.2「預設為簡單的平飛模式」 | `[VERIFIED]` |
| SAC 超參 | lr 2e-4、buffer 5,000,000、batch 256、tau 0.005、gamma 0.99、learning_starts 1,000,000、target_entropy auto、Tanh [256,256]、use_sde、log_std_init −2、8 個 SubprocVecEnv、`learn(500000000)`、device cuda | `train.py:33-64` | `[VERIFIED]` |
| Checkpoint | `CheckpointCallback(save_freq=50000*2, …)` → 檔名 `jsbsim_sac_<steps>_steps.zip` | `train.py:24-30` | `[VERIFIED]`；指引05 p.7「大約每 10 萬步」與 8 個環境的實際間隔 800,000 timesteps（314,400,000 = 393 × 800,000）→ `[CONFLICT]` C6 |
| 提供的模型 | `jsbsim_sac_314400000_steps.zip`；metadata：SB3 2.4.0、Python 3.10.20、PyTorch 2.12.0.dev20260408+cu128、Gymnasium 1.0.0、Numpy 1.26.4、Windows 10.0.26100、GPU Enabled | zip 內 `_stable_baselines3_version`、`system_info.txt` | `[VERIFIED]` |

## Testing（範例）

`test.py`：`JsbsimEnv(render_modes='txt')`、`SAC.load("model/jsbsim_sac_314400000_steps", env)`、`deterministic=True`、每步 `render()` 寫 `JSBSimRecording.txt.acmi`；只有 terminated 才停（指引06 p.2：除撞地墜毀外不會自動停止，Ctrl+C 中斷）。`[VERIFIED]`（`test.py:11-33`）。注意 `while not done` 只看 terminated，truncated 被丟棄（`test.py:27`）。

## ACMI / Tacview

| 項目 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| 訓練/測試端 ACMI | 檔頭 `FileType=text/acmi/tacview`、`FileVersion=2.1`、`0,ReferenceTime=2020-04-01T00:00:00Z`；每幀 `#t`、`A0001,T=lon|lat|alt|roll|pitch|yaw,Name=F16,Color=Blue`、`B0001,…Color=Red`；alt 為公尺（`position/h-sl-meters`） | `jsbsimEnv.py:472-508`；`JSBSimRecording.txt.acmi` 檔頭 | `[VERIFIED]` |
| Host 端 ACMI | 由 CSV 轉換，存 `output_acmi/` | 指引04 p.4,6 | `[VERIFIED]`（存在）；格式 `[NOT VERIFIED]` |
| Tacview | 建議安裝（https://www.tacview.net/），雙擊 .acmi 開啟；「建議」不是要求 | 指引02 p.8；指引04 p.4 | `[VERIFIED]` RECOMMENDED |

## Software / Installation（詳見 `OFFICIAL_REQUIREMENTS.md`）

Anaconda；`conda create -n f16_ai python=3.10`；`pip install jsbsim`、`pip install gymnasium`、`pip install "stable-baselines3[extra]==2.4.0"`、`pip install tensorboard`；`pip uninstall torch torchvision torchaudio -y` 後依顯卡 `--index-url …/whl/cu128 | cu121 | cu118`（指引02 p.10–13）。公告 p.11 五.3.(1)：自行下載 CUDA、Python、JSBGYM(JSBSIM F-16)、Socket。作業系統：Windows/Linux 皆可，只要能 UDP 交換（公告 p.11 五.2）。硬體建議 i5/R5+、RTX30+、16G+（公告 p.11 五.1）。

## Restrictions（公告 p.3 一.2.(3)、p.12–13 六）— 逐條

| # | 內容（摘要） | 來源 |
|---|---|---|
| R0 | 封閉網路；不與外部公用網路連線；不得遠端連線參賽 | p.3 一.2.(3) |
| R1 | Host 僅接受 4 個控制參數 + 1 個狀態參數；不得要求新增/刪除/更換 PitchRate、RollRate 等介面；需要者於自己軟體內部算 | p.12 六.1 |
| R2 | 內部積分/累計必須在「經緯度開始變化且不等於初始化位置」後才算正式開始；自行處理初始化 | p.12 六.2 |
| R3 | 只能在回合之間切換模型/演算法；切換時間含軟體重置 | p.12 六.3 |
| R4 | 不得要求指定 IP、PORT、網段、路由；依主辦方設定 | p.12 六.4 |
| R5 | 單一運算環境；不得多電腦、多網路、多網域、多 IP、多網卡、分散式、額外節點 | p.12 六.5 |
| R6 | 不得橋接外部（WiFi、藍芽、紅外線…）或非現場設備；檢入時檢查網路 | p.12 六.6 |
| R7 | 不得干預/探測/破壞他隊設備網路程式資料；防火牆規則、攻擊、DDOS、異常/大量封包、Port Scan、掃描、封包偽裝、欺騙、阻斷、占用資源等 | p.13 六.7 |
| R8 | 比賽開始後不得以遠端桌面、駕駛桿、滑鼠、控制器、SSH、手機等外部介入；必須離開操控設備 | p.13 六.8 |
| R9 | 不得修改、轉送、偽造雙方控制封包；不得影響對方接收 | p.13 六.9 |
| R10 | 自行處理封包遺失/延遲/重複/丟失；不得要求 HOST 暫停或改流程 | p.13 六.10 |
| R11 | 概括禁止規避限制、取得額外資訊、增加主機負荷、干預他人、不公平優勢 | p.13 六.11 |
| R12 | 主辦方最終解釋權：重賽、棄賽、不計分 | p.13 六.12 |

## Unknowns → `OFFICIAL_AIFCS_REFERENCE.md` §Q；Conflicts → §R
