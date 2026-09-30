# OFFICIAL_PROGRAM_MAP

每個程式：用途、輸入、輸出、依賴、執行方式、官方地位。行號出自 zip 內的檔案。

## A. 訓練環境 — `envs/jsbsimEnv/jsbsimEnv.py`（515 行）

| 項目 | 內容 | 行 |
|---|---|---|
| 用途 | gymnasium `Env`：我方 FDM1（RL）對敵方 FDM2（PID 平飛），1v1 | 18–36 |
| 輸入 | `action`：4 維（aileron, elevator, rudder, throttle） | 436–441 |
| 輸出 | 20 維 `state`（float32）、`reward`、`terminated`、`truncated`、`info={attack_frames, distance}` | 452–470 |
| 呼叫 | `jsbsimFdm.JsbsimFdm`（`Fdm`）；`utils.CalculateCoordinates`；`stable_baselines3.SAC`（import 未用） | 10–13 |
| 套件 | gymnasium、numpy、（jsbsim 經 Fdm） | 6–7 |
| 網路 | 無 | — |
| 幀率 | `sim_freq=60`、`interact_step=1`（每步 1 幀）；`max_steps=18000`（5 分鐘） | 43, 64–65, 447–449 |
| 動作空間 | Box：[−1,1]、[−1,1]、**[0, 1e-17]**、[0,1] | 68–81 |
| 觀測空間 | Box(20)，±500，float64 | 83–138 |
| 整形 | deadbands [0.01,0.03,0.06]；exponents [1,3,3]；max_change [0.050,0.026,0.013]；`elevator_limit = 1.0 if mach>0.8 else 1.0`（**兩邊都是 1.0**）；rudder 上限 0.2；throttle 每步 0.004 | 144–182 |
| reset | 敵機 FDM2 固定在 (23.060552N, 121.948555E)；我方在敵機方位 brg3、距離 `randint(0,12000)*0.3048` m 處；雙方航向、高度 1000–22000 ft、速度 340–400 kt 各自隨機；每次重建兩個 Fdm | 184–270 |
| 20 維狀態 | [0] 距離/5000 [1] 高度差/5000 [2] 仰角/90 [3] 方位角/180 [4] 敵機看我 3D 角/180 [5] sin φ [6] cos φ [7] sin θ [8] cos θ [9] α/30 [10] β/30 [11] 我高度(m)/5000 [12] vt·0.3048/340 [13] Vc/340 [14–16] u,v,w·0.3048/340 [17–19] p,q,r | 331–351 |
| 幾何 | NED 相對向量用球面近似 R=6378137；旋轉 Rx(φ)Ry(θ)Rz(ψ)；`own_3d_angle = arccos(xb/d)`；敵機 3D 角 = 敵機速度向量與「敵→我」視線的夾角 | 273–365 |
| 獎勵/終止 | 我高 ≤ 50 → (−10, True)；敵高 ≤ 50 → (0, True)；deck；接近率；`own_3d_angle ≤ 1.0` → +(2−angle)；方位/仰角超 1° 各 −0.01/度 | 367–425 |
| 擊殺 | **未實作**：`accumulated_attack_frames` 只歸零與回報 | 44–45, 191, 469 |
| render | `render_modes='txt'` 時寫 ACMI（見 SPEC） | 472–508 |
| 可直接執行 | 否（模組） | — |
| 官方地位 | 主辦方「範例」，「後續需要各參賽者自行修改」（公告 p.11 五.3.(2).A） | — |

## B. FDM 包裝 — `envs/jsbsimEnv/jsbsimFdm.py`（386 行）

| 項目 | 內容 | 行 |
|---|---|---|
| 用途 | 一架 JSBSim F-16 的初始化、屬性讀取、動作寫入、以及敵機用的 PID 自動飛行 | 7–386 |
| 初始化 | `FGFDMExec(None)`；`load_model('f16')`；`set_dt(1/60)`；`ic/vc-kts`（先）→ lat/long/h → psi/theta/phi → `run_ic()` → starter_cmd=1 → refuel=1 → `run()` → active_engine=True、set-running=−1 | 52–95 |
| 屬性群 | `velocity` = [v-north, v-east, v-down, vc-fps, vt-fps, ve-fps, u, v, w, p, q, r, mach]（索引 12 = mach）；`pose` = [lat-gc, long-gc, h-sl-**meters**, psi, theta, phi, heading-true-rad, lat-geod, alpha, beta]；`all` 含 `accelerations/n-pilot-z-norm` | 121–224 |
| 敵機 PID | roll kp 0.02 kd 0.01；pitch kp 0.05 kd 0.02，elevator 取負再減 (0.05 + 0.2·\|sin roll\|)；throttle base 0.5 + kp 0.1 kd 0.01，追 `target_speed_kts` | 30–46, 276–332 |
| auto_run | 高度保持：pitch = clip(alt_error·0.01, −5, 10)；每 10–30 s `random.choice(["Straight"])`（Turn Left/Right 分支存在但未列入） | 334–386 |
| 網路 | 無 | — |
| 官方地位 | 範例 | — |

## C. 工具 — `envs/jsbsimEnv/utils/utils.py`（423 行）

`get_AO_TA_R`、`get2d_AO_TA_R`（AO/TA 幾何）、`LLA_to_XYZ`、`dist`、`bearing`、**`CalculateCoordinates`（Vincenty 正算，reset 用）**（104–151）、`in_range_deg/rad`、`LookVector`、`orientationDifference`、`LatLng_rotate`、`CartesianToSphericalWGS84`、`calculate_3d_aspect_angle`、`ecef_to_enu`、`get_aircraft_direction`、`calculate_angle_to_target`。只有 `CalculateCoordinates` 被 `jsbsimEnv.py:10,210` 使用。`[VERIFIED]`

## D. 訓練入口 — `train.py`（67 行）

| 項目 | 內容 | 行 |
|---|---|---|
| 用途 | SAC 訓練 | 32–67 |
| 輸入 | 無參數；`JsbsimEnv(config={"rank": i})` × 8（SubprocVecEnv） | 17–35 |
| 輸出 | `./model/jsbsim_sac_<steps>_steps.zip`（CheckpointCallback，save_freq=50000*2）；結束時 `models/jsbsim_sac`、`models/jsbsim_sac_buffer`；TensorBoard `logs/` | 24–30, 43, 61, 65–67 |
| 套件 | gymnasium、torch、stable_baselines3（SAC, PPO import）、logging DEBUG | 1–12 |
| 超參 | 見 SPEC §Training | 40–63 |
| 執行 | `A.一鍵啟動train.bat`：`chcp 65001`、`cd /d %~dp0`、`conda activate F16_ai`、`python train.py`、`pause` | bat 1–13 |
| 官方地位 | 範例 | — |

## E. 測試入口 — `test.py`（33 行）

`JsbsimEnv(render_modes='txt')`；`SAC.load("model/jsbsim_sac_314400000_steps", env)`；迴圈 `predict(deterministic=True)` → `step` → `render()`；`while not done` 只看 terminated；印 totalreward。執行：`B.一鍵啟動test.bat`（同 A，`python test.py`）。輸出 `./JSBSimRecording.txt.acmi`。`[VERIFIED]`

## F. 參賽方連線程式 — `player1_Loadmodel.py`（472 行）

| 項目 | 內容 | 行 |
|---|---|---|
| 用途 | 收 OBS → 20 維 state → SAC 推論 → 整形 → 送 CMD + player_state | 2–10, 380–468 |
| 設定 | LISTEN_IP/PORT、BUFFER_SIZE、target_ip/port；三組（本機 / P1 現場 / P2 現場），同一時間只能啟用一組 | 20–45；指引03 p.5 |
| 模型 | `MODEL_PATH = "model/jsbsim_sac_314400000_steps.zip"`；`SAC.load(MODEL_PATH)`（無 env） | 51, 387 |
| 接收 | `recvfrom(4096)`；長度 ≠ 208 → WARN 忽略；`struct.unpack("<26d")` → float32 | 269–298 |
| 狀態 | `get_state`：與 jsbsimEnv 同式（球面 NED、Rx Ry Rz、`prev_distance_m` 全域、Vc 假設 60 Hz） | 118–263 |
| 整形 | 同 jsbsimEnv，但 `elevator_limit = 0.4 if current_mach > 0.8 else 1.0`，`current_mach = obs[13]*0.3048/340`（真空速/340，非真 Mach） | 331–374 |
| 發送 | `struct.pack("<fffff10s", roll, pitch, yaw, throttle, state, b"PLAYER_CMD")`；`sendto(target)` | 304–327 |
| player_state | 0 → 1（第一筆有效經緯度）→ 2（>60 包） | 402, 436–445 |
| 錯誤處理 | 每包 try/except 印錯誤後繼續；Ctrl+C 關 socket | 404–468 |
| 執行 | `C.一鍵啟動player1_Loadmodel.bat`（同 A，`python player1_Loadmodel.py`） | bat |
| 官方地位 | 範例；公告 p.11 五.3.(2).C 明言可改寫或用其他語言 | — |
| 備註 | 檔頭 docstring 寫檔名 `player_Loadmodel.py`、開機訊息印 `player_connect.py`（`:4, :384`）—— 名稱不一致，無實質影響 | — |

## G. 主辦方連線程式（公告版）— `D.…/JSB_host_GUI_publish.exe`（19,041,280 bytes）

| 項目 | 內容 | 來源 |
|---|---|---|
| 用途 | 供參賽者自測的 Host：JSBSim F-16 模擬 + UDP 收發 + GUI 狀態機 + 內建 Player2 + CSV/ACMI 輸出 | 公告 p.12 五.3.(2).D；指引04 |
| 輸入 | `Setting.txt`（IP/PORT/FPS）；Player1 的 CMD 封包 | `Setting.txt`；指引04 |
| 輸出 | OBS 封包給 P1/P2；監控封包 8000/8010；CSV；`output_acmi/*.acmi`；`player2_runtime.log` | `Setting.txt`；指引04 p.6 |
| 依賴 | 無（獨立 exe）；內建 Player2 以 `--internal-player2` 子程序啟動 | `player2_runtime.log:2` |
| 執行 | 雙擊 exe；SOP 見 WORKFLOW | 指引04 p.2–4 |
| 內部 | `[NOT VERIFIED]`（二進位；strings 無協定字串） | 本次 audit |
| 官方地位 | 主辦方提供的「民眾公告版」測試平台（readme_part1 標題）；比賽日 Host 是否同版 `[UNKNOWN]` | — |

## H. 設定與說明檔

| 檔案 | 內容 | 狀態 |
|---|---|---|
| `Setting.txt` | 見 SPEC §Host | `[VERIFIED]` |
| `readme_part1.txt` / `part2.txt` | 與指引04 第三～六節逐字相同 | `[VERIFIED]` |
| `.vscode/settings.json` | conda 為預設環境管理器 | `[VERIFIED]`，無影響 |
| `aircraft/f16/*` | JSBSim F-16 機體與引擎檔（見 SPEC §Aircraft）；訓練程式**不讀**此資料夾（`FGFDMExec(None)`） | `[VERIFIED]` |
| `model/jsbsim_sac_314400000_steps.zip` | 範例模型；metadata 見 SPEC | `[VERIFIED]` |
| `JSBSimRecording.txt.acmi` | test.py 的範例輸出 | `[VERIFIED]` |
