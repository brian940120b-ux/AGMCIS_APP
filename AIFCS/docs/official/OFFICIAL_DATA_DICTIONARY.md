# OFFICIAL_DATA_DICTIONARY

## OBS — 主辦方 → 參賽者，每個 Frame 一包

| 項目 | 值 | 來源 | 狀態 |
|---|---|---|---|
| 通訊 | UDP | 公告 p.2 一.2.(3)「UDP封包收送」；指引03 p.5「通訊方式：UDP」 | `[VERIFIED]` |
| 欄位數 | 26（本機 20 + 敵機 6） | 公告 p.4 二.1；表 1 | `[VERIFIED]` |
| 型態 | 每欄 double | 公告 表 1「型態 double」；指引03 p.5「皆為 double」 | `[VERIFIED]` |
| 封包大小 | 208 bytes = 8 × 26 | 指引03 p.5；`player1_Loadmodel.py:7`、`:279`（`expected_value_count * 8`） | `[VERIFIED]` |
| 位元組順序 | Little-endian | 指引03 p.5；`player1_Loadmodel.py:290` `struct.unpack("<26d", …)` | `[VERIFIED]` |
| 非 208 bytes 的封包 | 範例程式忽略（印 WARN） | `player1_Loadmodel.py:283-288`；指引03 p.6 | `[VERIFIED]`（範例行為；Host 端是否會送其他長度：`[UNKNOWN]`） |
| 頻率 | 模擬時脈 60 Hz；每個 Frame 收到一包 | 公告 p.2 圖 1「模擬時脈：60Hz」、p.3 (1)「以 60HZ 進行核心 6-DOF 數值計算」、p.4 二.1「每個 Frame 會收到」；`Setting.txt:16` `RUN_FPS: 60` | `[VERIFIED]` 60 Hz 運算；「每幀一包 = 60 包/秒」為 `[INFERRED]` |
| 座標系 | 緯經度 (deg)、高度 ft（海平面）、NED 速度 fps、機體軸 u/v/w fps、機體角速度 rad/s | 公告 表 1 各欄「變數單位」 | `[VERIFIED]` |

### 表 1 逐欄（ID、型態、變數名稱、說明、單位 — 全部出自公告 p.4–5 表 1）

| ID | Type | 官方變數名稱（JSBSim property） | 官方說明 | 單位 | 範例程式索引名（`player1_Loadmodel.py:59-86`，非官方名稱） |
|---|---|---|---|---|---|
| 1 | double | `ownship: position/lat-gc-deg` | 本機緯度 | Deg | `own_lat_deg` [0] |
| 2 | double | `ownship: position/long-gc-deg` | 本機經度 | Deg | `own_lon_deg` [1] |
| 3 | double | `ownship: position/h-sl-ft` | 本機高度 | Ft | `own_alt_ft` [2] |
| 4 | double | `ownship: attitude/phi-deg` | 本機滾轉角 | Deg | `own_roll_deg` [3] |
| 5 | double | `ownship: attitude/theta-deg` | 本機俯仰角 | Deg | `own_pitch_deg` [4] |
| 6 | double | `ownship: attitude/psi-deg` | 本機頭向角 | Deg | `own_yaw_deg` [5] |
| 7 | double | `ownship: velocities/v-north-fps` | 本機朝北速度 | Ft/sec | `own_vn_fps` [6] |
| 8 | double | `ownship: velocities/v-east-fps` | 本機朝東速度 | Ft/sec | `own_ve_fps` [7] |
| 9 | double | `ownship: velocities/v-down-fps` | 本機朝下速度 | Ft/sec | `own_vd_fps` [8] |
| 10 | double | `ownship: velocities/p-rad_sec` | 本機 X 軸角速度 | Rad/sec | `own_p_radps` [9] |
| 11 | double | `ownship: velocities/q-rad_sec` | 本機 Y 軸角速度 | Rad/sec | `own_q_radps` [10] |
| 12 | double | `ownship: velocities/r-rad_sec` | 本機 Z 軸角速度 | Rad/sec | `own_r_radps` [11] |
| 13 | double | `ownship: velocities/vc-fps` | 本機校正空速 | Ft/sec | `own_vc_fps` [12] |
| 14 | double | `ownship: velocities/vt-fps` | 本機真實空速 | Ft/sec | `own_vt_fps` [13] |
| 15 | double | `ownship: accelerations/n-pilot-z-norm` | 本機飛行員 Z 軸 G 值 | G | `own_g_acc` [14] |
| 16 | double | `ownship: velocities/u-fps` | 本機 X 軸速度 | Ft/sec | `own_u_fps` [15] |
| 17 | double | `ownship: velocities/v-fps` | 本機 Y 軸速度 | Ft/sec | `own_v_fps` [16] |
| 18 | double | `ownship: velocities/w-fps` | 本機 Z 軸速度 | Ft/sec | `own_w_fps` [17] |
| 19 | double | `ownship: aero/alpha-deg` | 本機攻角 | Deg | `own_alpha_deg` [18] |
| 20 | double | `ownship: aero/beta-deg` | 本機側滑角 | Deg | `own_beta_deg` [19] |
| 21 | double | `target: position/lat-gc-deg` | 敵機緯度 | Deg | `enemy_lat_deg` [20] |
| 22 | double | `target: position/long-gc-deg` | 敵機經度 | Deg | `enemy_lon_deg` [21] |
| 23 | double | `target: position/h-sl-ft` | 敵機高度 | Ft | `enemy_alt_ft` [22] |
| 24 | double | `target: velocities/v-north-fps` | 敵機朝北速度 | Ft/sec | `enemy_vn_fps` [23] |
| 25 | double | `target: velocities/v-east-fps` | 敵機朝東速度 | Ft/sec | `enemy_ve_fps` [24] |
| 26 | double | `target: velocities/v-down-fps` | 敵機朝下速度 | Ft/sec | `enemy_vd_fps` [25] |

補充（`[VERIFIED]`，出自程式）：範例程式把 26 個 double 轉成 `np.float32` 再使用（`player1_Loadmodel.py:291`）—— 這是範例的選擇，公告沒有要求。
G 值符號慣例：公告只說「本機飛行員 Z 軸 G 值」；正負方向 `[UNKNOWN]`（公告未定義）。

## CMD — 參賽者 → 主辦方，每個 Frame 一包

| 項目 | 值 | 來源 | 狀態 |
|---|---|---|---|
| 欄位 | 4 個控制參數 + 1 個狀態參數 + 尾碼 | 公告 p.5 二.2；表 2 | `[VERIFIED]` |
| 型態 | 5 × float + Char[10] | 公告 表 2；指引03 p.6「5 個 float 變數，加上 10 個 char 的尾碼」 | `[VERIFIED]` |
| 封包長度 | 30 bytes = 5×4 + 10 | 指引03 p.6；`player1_Loadmodel.py:325` `struct.pack("<fffff10s", …)` | `[VERIFIED]` |
| 位元組順序 | Little-endian（`<`） | `player1_Loadmodel.py:325` | `[VERIFIED]`（程式）；指引03 只寫「數值順序」未明說 endian → 文件層 `[PARTIAL]` |
| 順序 | roll, pitch, yaw, throttle, player_state, "PLAYER_CMD" | 指引03 p.6；`player1_Loadmodel.py:325` | `[VERIFIED]` |
| 尾碼 | `"PLAYER_CMD"`，為檢查字串，總長度必須正確 | 公告 表 2 ID 6；指引03 p.6 備註 | `[VERIFIED]` |

### 表 2 逐欄（公告 p.5 表 2）

| ID | Type | 官方變數名稱 | 官方說明 | 範圍 | 範例程式來源 |
|---|---|---|---|---|---|
| 1 | float | `fcs/aileron-cmd-norm` | 滾轉控制命令 | −1～+1 | `roll_cmd = action[0]`（`:319`） |
| 2 | float | `fcs/elevator-cmd-norm` | 俯仰控制命令 | −1～+1 | `pitch_cmd = action[1]`（`:320`） |
| 3 | float | `fcs/rudder-cmd-norm` | 偏航控制命令 | −1～+1 | `yaw_cmd = action[2]`（`:321`）；範例自行 clip 到 ±0.2（`:344`） |
| 4 | float | `fcs/throttle-cmd-norm` | 油門控制命令 | 0～+1 | `throttle_cmd = action[3]`（`:322`） |
| 5 | float | `state` | 系統狀態參數 | 0:未備便 1:已完成初始化 2:已備便 | `player_state`（`:442-445`） |
| 6 | Char[10] | N/A | 封包尾碼 | `"PLAYER_CMD"` | `extra_bytes = b"PLAYER_CMD"`（`:324`） |

### player_state 的官方定義與範例行為

| 值 | 公告 表 2 | 指引03 p.6 | 範例程式 |
|---|---|---|---|
| 0 | 未備便 | 尚未收到有效初始化資料 | 初始值（`:402`） |
| 1 | 已完成初始化 | 已收到有效經緯度及初始化資料 | 第一筆 lat∈[−90,90] 且 lon∈[−180,180] 的封包後（`:436-443`） |
| 2 | 已備便 | 已進入持續連線模式；條件為已進入 state 1 且總封包數超過 60 | `player_state == 1 and packet_count > 60`（`:444-445`） |

公告 p.5 原話：「該狀態參數為 INIT 完成後需要回傳的必要參數，詳細回傳數值可參考範例。」→ 60 包這個門檻是**範例**的選擇，不是規則；Host 判定「已備便」的精確條件 `[UNKNOWN]`。

## 初始化指令（主辦方 → 參賽者）

公告 p.10 四.4：「主辦方…會傳遞初始化指令給競賽雙方，包含發送參賽者各自的經度、緯度、高度位置與速度等資料。」
封包格式**沒有另外定義**；範例程式只收 208-byte OBS，並在第一筆有效 OBS 後回 state=1（`player1_Loadmodel.py:436-443`）→ `[INFERRED]` 初始化指令即為同格式的 OBS 封包。是否有其他格式 `[UNKNOWN]`。
