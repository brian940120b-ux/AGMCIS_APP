# OFFICIAL_WORKFLOW

## 1. Installation Flow（指引02，版別 2026/09/03）

```
Anaconda（註冊帳號 → 下載 → Next → 勾 Add to PATH → Install）        p.2–4
→ VS Code（建議）+ Python 擴充                                          p.5–7
→ Tacview（建議）                                                        p.8
→ NVIDIA 驅動                                                            p.9–10
→ Anaconda Prompt
→ conda create -n f16_ai python=3.10 ；conda activate f16_ai             p.10–11
→ pip install jsbsim ；pip install gymnasium
→ pip install "stable-baselines3[extra]==2.4.0" ；pip install tensorboard p.12
→ pip uninstall torch torchvision torchaudio -y
→ pip install torch torchvision torchaudio --index-url …/whl/cu128|cu121|cu118  p.12–13
→ 把 AirCombat_Train_Test 複製到欲執行位置（例 C:\）                     p.13
```
注意：bat 檔用 `conda activate F16_ai`（大寫 F）—— 見 Conflicts C5。

## 2. Training Flow（指引05；`train.py`）

```
A.一鍵啟動train.bat → conda activate F16_ai → python train.py
→ 8 個 SubprocVecEnv(JsbsimEnv) → SAC(…) → learn(500,000,000)
→ 每 save_freq 存 ./model/jsbsim_sac_<steps>_steps.zip → Ctrl+C 可中斷
→ finally: models/jsbsim_sac + replay buffer
```

## 3. Test Flow（指引06；`test.py`）

```
改 test.py 的模型名 → B.一鍵啟動test.bat → python test.py
→ JsbsimEnv(render_modes='txt') → 迴圈 predict/step/render
→ ./JSBSimRecording.txt.acmi → 雙擊用 Tacview 開
→ 只有撞地才停；否則 Ctrl+C
```

## 4. Player Flow（指引03；`player1_Loadmodel.py`）

```
設定 LISTEN_IP/PORT、target_ip/port（三組擇一，其餘註解）   p.5
→ C.一鍵啟動player1_Loadmodel.bat → SAC.load(model)
→ bind(LISTEN) → 等 OBS
→ 每包：len==208? → unpack <26d → get_state(20) → predict → process_joystick_action
→ state: 第一筆有效經緯度 → 1；>60 包 → 2
→ pack <fffff10s + "PLAYER_CMD" → sendto(target)              每包一回
→ Ctrl+C 關 socket
```

## 5. Host Flow（指引04；readme_part1/2；公告版 GUI）

```
STOPPED
  │ 按 INIT PLAYERS STATE（只在 STOPPED 可用）：重設回合、背景啟動內建 Player2
  ▼
等待 P1 / P2 回傳初始化完成（兩個狀態方塊亮起）
  │ 顯示 P1/P2 READY 倒數 → GO（仍需手動 START）
  ▼
按 START → RUNNING（計時器開始；預設倒數 5 分鐘）
  │ FREEZE ⇄ RESUME（可選）
  ▼
STOP（手動 / 倒數歸零 / 系統完成仲裁）
  → 停內建 Player2、清狀態、存檔、CSV → ACMI（output_acmi/）、印完成訊息
  ▼
STOPPED（等下一次 INIT）
```
只有文件裡出現的狀態：STOPPED、RUNNING、FREEZE/RESUME（按鈕）、STOP；「INIT」「READY」「GO」是階段/顯示，不是 HOST STATE 的值（指引04 p.3 只寫 HOST STATE 為 STOPPED / RUNNING）。`[VERIFIED]`（文件）；exe 內部 `[NOT VERIFIED]`。

## 6. UDP Flow（Setting.txt + 指引03 + player1_Loadmodel.py）

```
Host ──OBS 208 B ──▶ Player1 LISTEN 8199      （Setting: PLAYER1_OUTPUT 127.0.0.1:8199）
Host ──OBS 208 B ──▶ Player2 LISTEN 8201      （PLAYER2_OUTPUT 127.0.0.1:8201）
Player1 ──CMD 30 B ──▶ Host 8099              （PLAYER1_INPUT 0.0.0.0:8099）
Player2 ──CMD 30 B ──▶ Host 8101              （PLAYER2_INPUT 0.0.0.0:8101）
Host ──監控封包 ──▶ 顯示電腦 8000 / 8010       （FlightTrace_monitor…）
比賽日範例值（程式註解）：Host 192.168.1.1；P1 192.168.1.3；P2 192.168.1.4 —— 以檢入指定為準
```

## 7. Competition Flow（公告 p.9–10 四）

```
1 分組（報名順序、現場抽籤；單數隊伍抽保送）
2 抽籤（扮演參賽者 1 或 2）
3 檢入（主辦方指定 IP/PORT → 自行輸入 → 實體線連 HUB → 連線測試；失敗遞補）
4 初始化（Host 送初始化指令：經緯度、高度、速度 → 參賽者初始化 → 回傳準備狀態 → Host 等雙方）
5 回合開始（Host 手動開始）
6 回合過程（5 分鐘；即時計分顯示）
7 回合終止（Host 判定，自動結束，顯示結果）
8 回合切換（≤ 3 分鐘；可微調 AI / 換演算法）
9 賽局結束（依賽制決定晉級）
```

## 8. Replay Flow

```
訓練/測試端：test.py render() → JSBSimRecording.txt.acmi（每幀兩機 lon|lat|alt(m)|roll|pitch|yaw）→ Tacview
Host 端（公告版）：回合結束 → CSV → 自動轉 ACMI → output_acmi/ → 雙擊開 Tacview（兩機皆顯示 F-16）
```
Tacview 為分析工具，不是比賽要求（指引02 p.8「建議」）。
