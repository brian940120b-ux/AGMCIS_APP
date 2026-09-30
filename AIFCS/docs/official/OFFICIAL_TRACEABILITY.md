# OFFICIAL_TRACEABILITY

| # | Requirement | Source | Page / Line | Official Status |
|---|---|---|---|---|
| T01 | 比賽日期 115/11/7–8、地點成大國際會議廳 | 附件2；公告封面 | 附件2 壹、貳 | `[VERIFIED]` |
| T02 | 每隊 ≤ 4 人；資格條款 | 附件2 | 參、肆 | `[VERIFIED]` |
| T03 | 規則以公告文件為準 | 附件2 | 陸 | `[VERIFIED]` |
| T04 | 1v1；參賽者 1 / 2 | 公告 | p.6 三.1.(1) | `[VERIFIED]` |
| T05 | 回合 5 分鐘 | 公告 | p.6 三.1.(2)；p.10 四.6 | `[VERIFIED]` |
| T06 | 初始距離 3,000/6,000/9,000 呎 | 公告 | p.6 三.1.(3)；圖 3 | `[VERIFIED]` |
| T07 | 初始高度 10,000–20,000 呎隨機 | 公告 | p.6 三.1.(4) | `[VERIFIED]` |
| T08 | 初始速度 340 節 | 公告 | p.6 三.1.(5) | `[VERIFIED]` |
| T09 | 有效攻擊範圍 鼻軸線 2 度、500–3000 呎 | 公告 | p.6 三.1.(6)；圖 4 | `[VERIFIED]`；半角/全角 `[INFERRED]` |
| T10 | 擊殺 = 累積射擊滿 3 秒 | 公告 | p.7 三.2.(2).A；表 3 | `[VERIFIED]` |
| T11 | 墜毀 = 高度低於 50 公尺 | 公告 | p.7 三.2.(2).B | `[VERIFIED]` |
| T12 | 相撞 = 距離小於 15 公尺 | 公告 | p.7 三.2.(2).B | `[VERIFIED]` |
| T13 | 判定順序（擊殺→毀機→積分；擊殺無視分數） | 公告 | 表 3（p.7 圖） | `[VERIFIED]` |
| T14 | 完全平手 → 無效局重比 | 公告 | p.7；表 3 | `[VERIFIED]` |
| T15 | S_kill = W_base + (300 − T_kill)，W_base 參考 1000 | 公告 | p.8 三.3.(2) | `[VERIFIED]` |
| T16 | S_advantage = W_time·T_att − W_G·T_G + W_pos·ΣP_t（2000/1000/10 參考值） | 公告 | p.8 三.3.(3) | `[VERIFIED]` |
| T17 | T_G = 超過 9G 秒數 | 公告 | p.8 | `[VERIFIED]` |
| T18 | TA_norm / AA_norm / P(t) 公式 | 公告 | p.9 三.3.(4) | `[VERIFIED]` |
| T19 | Distance_Factor 五段 | 公告 | 表 4（p.9 圖） | `[VERIFIED]` |
| T20 | 模擬 60 Hz、JSBSim F-16、主辦方統一運算 | 公告 | p.2 圖 1；p.3 一.2.(1) | `[VERIFIED]` |
| T21 | OBS 26 × double | 公告；指引03 | 表 1（p.4–5）；指引03 p.5 | `[VERIFIED]` |
| T22 | OBS 208 bytes、little-endian | 指引03；player1 | 指引03 p.5；`player1_Loadmodel.py:279,290` | `[VERIFIED]` |
| T23 | 非 208 bytes 忽略 | 指引03；player1 | 指引03 p.6；`:283-288` | `[VERIFIED]`（範例） |
| T24 | CMD 5 float + Char[10] "PLAYER_CMD"，30 bytes | 公告；指引03；player1 | 表 2；指引03 p.6；`:325` | `[VERIFIED]` |
| T25 | CMD 順序 roll, pitch, yaw, throttle, state | 指引03；player1 | p.6；`:325` | `[VERIFIED]` |
| T26 | 範圍 aileron/elevator/rudder −1~+1、throttle 0~+1 | 公告 | 表 2 | `[VERIFIED]` |
| T27 | state 0/1/2 意義 | 公告；指引03 | 表 2；指引03 p.6 | `[VERIFIED]` |
| T28 | state 切換條件（第一筆有效 → 1；>60 包 → 2） | 指引03；player1 | p.4, p.6；`:436-445` | `[VERIFIED]`（範例）；Host 判定條件 `[UNKNOWN]` |
| T29 | 本機測試 port：P1 收 8199、送 8099；P2 收 8201、送 8101 | Setting.txt；player1；指引03 | `Setting.txt:1-9`；`:25-29`；p.4 | `[VERIFIED]` |
| T30 | 比賽日 IP 由主辦方指定；不得要求 | 公告 | p.9 四.3；p.12 六.4 | `[VERIFIED]` |
| T31 | 初始化指令含經緯度、高度、速度；回傳準備狀態 | 公告 | p.10 四.4 | `[VERIFIED]`（內容）；封包格式 `[INFERRED]` = OBS |
| T32 | 手動開始回合 | 公告 | p.10 四.5 | `[VERIFIED]` |
| T33 | 回合切換 ≤ 3 分鐘 | 公告 | p.10 四.8 | `[VERIFIED]` |
| T34 | 積分項須等經緯度變化後才開始 | 公告 | p.12 六.2 | `[VERIFIED]` |
| T35 | 只有 4+1 參數；不得要求 PitchRate/RollRate 介面 | 公告 | p.12 六.1 | `[VERIFIED]` |
| T36 | 單一運算環境；不得分散式 | 公告 | p.12 六.5 | `[VERIFIED]` |
| T37 | 不得外部橋接；檢入檢查 | 公告 | p.12 六.6 | `[VERIFIED]` |
| T38 | 不得干預/攻擊/掃描 | 公告 | p.13 六.7 | `[VERIFIED]` |
| T39 | 開賽後不得外部介入；離開設備 | 公告 | p.13 六.8 | `[VERIFIED]` |
| T40 | 不得改/轉/偽造封包 | 公告 | p.13 六.9 | `[VERIFIED]` |
| T41 | 自行處理封包異常；不得要求暫停 | 公告 | p.13 六.10 | `[VERIFIED]` |
| T42 | 概括條款；最終解釋權 | 公告 | p.13 六.11–12 | `[VERIFIED]` |
| T43 | 硬體建議 i5/R5、RTX30、16G | 公告 | p.11 五.1 | `[VERIFIED]` RECOMMENDED |
| T44 | Windows/Linux 皆可 | 公告 | p.11 五.2 | `[VERIFIED]` |
| T45 | Python 3.10、SB3 2.4.0、jsbsim、gymnasium、tensorboard、torch cu128/121/118 | 指引02 | p.10–13 | `[VERIFIED]`（範例環境） |
| T46 | 範例模型環境：Py 3.10.20、SB3 2.4.0、torch 2.12.0.dev+cu128、gymnasium 1.0.0、numpy 1.26.4 | 模型 zip | `system_info.txt` | `[VERIFIED]` |
| T47 | 訓練動作空間 rudder [0, 1e-17] | jsbsimEnv | `:68-81` | `[VERIFIED]` |
| T48 | 整形常數 [0.01,0.03,0.06] / [1,3,3] / [0.05,0.026,0.013] / rudder 0.2 / throttle 0.004 | jsbsimEnv；player1 | `:148-178`；`:336-370` | `[VERIFIED]` |
| T49 | 高速升降舵限制：client 0.4（Mach>0.8）；env 1.0 | player1；jsbsimEnv | `:341-344`；`:153-156` | `[CONFLICT]` C1 |
| T50 | 訓練 reset 分佈（0–12000 ft、1000–22000 ft、340–400 kt、航向隨機） | jsbsimEnv；指引05 | `:205-234`；p.6 | `[VERIFIED]`（與比賽規則不同，文件自述） |
| T51 | 獎勵函數五項 | jsbsimEnv；指引05 | `:367-425`；p.4–6 | `[VERIFIED]` |
| T52 | 訓練環境不實作擊殺 | jsbsimEnv | `:44-45,191,469` | `[VERIFIED]` |
| T53 | SAC 超參 | train.py | `:40-64` | `[VERIFIED]` |
| T54 | ACMI 格式（訓練端） | jsbsimEnv；.acmi | `:472-508`；檔頭 | `[VERIFIED]` |
| T55 | Host 狀態機與按鈕 | 指引04；readme | p.3–6；part1/2 | `[VERIFIED]`（文件） |
| T56 | Host 輸出 CSV → ACMI → output_acmi | 指引04 | p.4, p.6 | `[VERIFIED]`（存在）；格式 `[NOT VERIFIED]` |
| T57 | 內建 Player2 = 平飛 PID，SAC disabled | player2_runtime.log | 頭部 | `[VERIFIED]`（公告版） |
| T58 | 套件內引擎檔與 pip 不同；訓練程式用 pip 根目錄 | diff；jsbsimFdm | `:52` | `[VERIFIED]`；Host 用哪份 `[UNKNOWN]` |
