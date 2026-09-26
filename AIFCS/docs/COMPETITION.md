# 2026 神盾盃「AI 飛行員擂台賽」— 對接主辦方 HOST

主辦單位：國家中山科學研究院 · 競賽日期：2026/11/07–08 · 地點：成功大學國際會議廳

這份文件只講一件事：**怎麼把我們的程式接上主辦方的 HOST，並且取得三個還沒有答案的問題的答案。**

---

## 為什麼要做這件事

有三件事讀規格讀不出來，只有真實 HOST 的封包能回答：

| 問題 | 為什麼重要 | HOST 怎麼回答 |
|---|---|---|
| 一回合從幾節開始？ | 規格寫 340 節。主辦方的程式因為設定順序，實際只有 **234 節**（差 106 節）。如果 HOST 也有這個問題，那 234 才是真的，我們照 340 訓練就錯了 | OBS 封包第 13、14 個值就是 `vc-fps` 和 `vt-fps` |
| HOST 用哪一份 F-16？ | 主辦方附的引擎和 pip 裝的**不一樣**（旁通比、引氣、慢車轉速），飛出來的軌跡會分岔 | 錄下油門階躍後的空速曲線，事後比對兩份 |
| 回合邊界偵測對不對？ | 我們從「位置凍結後開始移動」推斷回合開始。這是推論，不是實測 | 錄下 INIT 和 START 前後的封包 |

---

## 你要做什麼（約 15 分鐘）

### 準備

1. 主辦方的資料夾放在你找得到的地方，例如 `C:\AirCombat_Train_Test`
2. 確認裡面有 `D.比賽用主辦方連線程式.../JSB_host_GUI_publish.exe`

### 步驟 0 — 先確認探針本身沒問題（10 秒）

```bash
cd ~/AGMCIS_APP/AIFCS
.venv/Scripts/python.exe backend/competition/probe.py --selftest
```

要看到：

```
  OK  listening on 127.0.0.1:8199
  OK  120 of 120 synthetic frames were answered
PASS  the probe listens, decides and replies.
```

**看到 PASS 之後，任何「收不到封包」就一定是 HOST、port 或防火牆，不是這支程式。**
這一步存在是因為第一次實測時收到 0 個封包，而當下沒有辦法分辨是哪一邊的問題。

### 步驟 1 — 先開我們的程式

開一個 **Git Bash**，貼：

```bash
cd ~/AGMCIS_APP/AIFCS
.venv/Scripts/python.exe backend/competition/probe.py --seconds 420
```

會看到：

```
listening on 127.0.0.1:8199
replying to  127.0.0.1:8099
recording for up to 420 s — start the host and press INIT, then START

  waiting for the host… 12s (start it and press INIT, then START)
```

**這個視窗不要關。** 它會一直等到 420 秒為止，慢慢來沒關係 —— 早期版本會在安靜 60 秒後自己收工，而那比啟動 HOST、按 INIT、等雙方初始化、再按 START 需要的時間還短。

### 步驟 2 — 開主辦方的 HOST

雙擊 `JSB_host_GUI_publish.exe`。確認 `HOST STATE` 顯示 `STOPPED`。

> 防火牆如果跳出詢問，**兩個程式都要按「允許」**。

### 步驟 3 — 按 INIT

按 `INIT PLAYERS STATE`。

等右邊 Player2 亮起（主辦方內建的），再等左邊 **Player1 亮起** —— 那就是我們的程式回報初始化成功了。

**如果 Player1 一直不亮**，就是沒接上。跳到下面的「接不上怎麼辦」。

### 步驟 4 — 按 START

倒數完顯示 GO 之後，按 `START`。`HOST STATE` 會變成 `RUNNING`。

我們的視窗會開始跑數字：

```
  12345 packets, round 1, frame 12345
```

### 步驟 5 — 讓它跑完五分鐘

時間到 HOST 會自動 STOP。

### 步驟 6 — 再跑一回合（重要）

`HOST STATE` 回到 `STOPPED` 後，**再按一次 INIT，再按 START**。

> 第二回合是關鍵 —— 回合邊界偵測只有在第二回合才驗得到。

### 步驟 7 — 停止並把結果給我

回到 Git Bash 視窗按 `Ctrl + C`（或等它自己到 420 秒）。

它會印出一份 JSON 報告，並存兩個檔案：

```
data/probe/probe-20261107-xxxxxx.json    ← 報告，把這個貼給我
data/probe/probe-20261107-xxxxxx.jsonl   ← 每一幀的原始記錄
```

**把螢幕上印出來的那整段 JSON 貼給我就好。**

---

## 報告會告訴你什麼

```json
"speed_verdict": "host starts at 340 KCAS — the published figure, ordering correct"
```
或
```json
"speed_verdict": "host starts near 234 KCAS — it has the reference's ordering,
                  so train with --reference-speed-order"
```

```json
"boundary_verdict": "2700 frames repeated the previous position exactly;
                     2 round(s) were detected from that pattern"
```

`rounds_detected` 應該是 **2**（你跑了兩回合）。如果是 1 或 3，回合偵測要修。

---

## 接不上怎麼辦

**Player1 的燈不亮：**

1. 先確認我們的視窗有在跑，而且印出 `listening on 127.0.0.1:8199`
2. 檢查 HOST 那邊的 `Setting.txt`：
   ```
   PLAYER1_INPUT_PORT: 8099    ← HOST 收我們命令的 port
   PLAYER1_OUTPUT_PORT: 8199   ← HOST 送 OBS 給我們的 port
   ```
   如果數字不一樣，用參數改：
   ```bash
   .venv/Scripts/python.exe backend/competition/probe.py \
     --listen-port <PLAYER1_OUTPUT_PORT> --host-port <PLAYER1_INPUT_PORT>
   ```
3. Windows 防火牆：控制台 → Windows Defender 防火牆 → 允許應用程式，確認 `python.exe` 和 HOST 都被允許

**報告說 `no packets arrived`：** 先跑步驟 0 的 `--selftest`。

- `--selftest` **PASS** → 探針正常，問題在 HOST／port／防火牆，照上面三項查
- `--selftest` **FAIL** → 問題在我們這邊，把輸出貼給我

`--seconds` 不夠長也會這樣。要更多時間就加大，例如 `--seconds 900`。

---

## 比賽當天的設定

主辦方會在檢入時指定 IP。根據 `Setting.txt`，預期是：

| 你抽到 | 你的電腦 IP | 指令 |
|---|---|---|
| P1 | 192.168.1.3 | `--listen-ip 192.168.1.3 --listen-port 8199 --host-ip 192.168.1.1 --host-port 8099` |
| P2 | 192.168.1.4 | `--listen-ip 192.168.1.4 --listen-port 8201 --host-ip 192.168.1.1 --host-port 8101` |

**以檢入時主辦方當場給的為準**，上面只是預期值。

---

## 這個探針不是參賽程式

它飛的是**維持高度、機翼水平**，不會追擊、不會閃躲。它的工作是**把資料帶回來**，不是比賽。

比賽用的程式是同一套 `CompetitionClient`，只是換上訓練好的策略。


---

## 實測結果（2026-09-25，對真實 HOST，兩回合）

| 問題 | 答案 |
|---|---|
| 初始速度 | **339.9 KCAS / 444.5 KTAS / Mach 0.673 @ 19,116 ft** —— HOST 沒有設定順序的缺陷，規格寫的 340 節就是真的 |
| 回合邊界 | **6,729 幀位置完全不動，兩回合都偵測到** —— PHASE 2 從規則推論的機制就是 HOST 的實際行為 |
| 決策延遲 | 平均 **0.20 ms**、最差 **1.11 ms**，預算 16.67 ms（用掉 1.2% / 6.7%） |
| 封包品質 | 33,090 幀，**0 個格式錯誤、0 個 NaN** |

**最重要的推論：** 主辦方那個 3.14 億步的模型是在 **Mach 0.47** 練出來的，而比賽跑 **Mach 0.67**。那是他們訓練環境的問題，不是我們的 —— 訓練時**不要**加 `--reference-speed-order`。

### 初始條件：測試 HOST 跟規格不一樣

| | 高度 | 水平距離 | 航向 | 校正空速 | 高度差 |
|---|---|---|---|---|---|
| 回合 1 | 19,116 ft | **3,295.0 ft** | 340.00° | 339.9 kt | 0 |
| 回合 2 | 14,659 ft | **4,850.2 ft** | 55.00° | 339.9 kt | 0 |

距離**不是** 3,000 / 6,000 / 9,000，而是**隨機整數呎**（3,295.013 離整數只差 4 公釐）。航向也是隨機整數度。這正是主辦方參考環境 `randint(0,12000)*0.3048` 和 `randint(0,359)` 的輸出。

readme 標題是「**民眾公告版**」—— 給大家測連線用的，初始條件不必照比賽設定。所以：

- **測試 HOST 無法用來驗證回合初始設定**
- **訓練仍以規格的 3,000 / 6,000 / 9,000 為準** —— 那是目前唯一關於比賽當天的正式陳述

兩件測試 HOST 跟規格一致的事：**速度固定 340 KCAS**、**兩機起始高度相同**。後者參考環境是各自獨立隨機的，已照 HOST 的行為修正。

> 探針原本報告的 3,604 呎是**我的計算錯誤** —— 經度換算漏了 `cos(緯度)`，在北緯 25 度造成東向分量 10% 誤差。已修並用 HOST 的真實數字釘成測試。


---

# 訓練：關機也不會損失進度

## 先建桌面捷徑（做一次就好）

用檔案總管走到 `C:\Users\user\AGMCIS_APP\AIFCS\scripts\`，雙擊 **`install_shortcuts.bat`**。

桌面會出現兩個圖示：

| 圖示 | 做什麼 |
|---|---|
| **AIFCS 平台** | 開啟模擬平台和網頁介面 |
| **AIFCS 訓練** | 開始或**繼續**競賽訓練 |

## 訓練

雙擊 **AIFCS 訓練**。就這樣。

- **要停就按 `Ctrl+C` 或直接關掉視窗** —— 進度不會遺失
- **要繼續就再雙擊一次** —— 它會從停的地方接下去，不是重來
- 電腦關機、當機、沒電，最多只損失**一個存檔點**（預設 25,000 步）

畫面會這樣顯示：

```
resuming run1: 1,250,000 / 5,000,000 steps (25.0%) — about 4.2 hr left
training 3,750,000 more steps
```

## 它為什麼能這樣

訓練每 25,000 步把**模型、優化器狀態、SAC 的經驗回放緩衝區、已訓練步數**全部寫到硬碟：

```
models/competition/run1/
    checkpoint.zip        策略和優化器
    replay_buffer.pkl     SAC 看過的經驗
    state.json            步數，和這些步數是對著什麼練的
    card.json             完成後的模型卡片
```

`state.json` 一定**在模型之後**才寫。中間當掉會損失一個存檔點，但留下的是一對一致的檔案；順序相反的話，狀態檔會宣稱一個模型沒有的步數。

**沒有任何程式能在關機的電腦上執行。** 能跨越關機的只有硬碟上的東西。

## `--timesteps` 是總數，不是增量

```bash
train.bat --timesteps 5000000
```

意思是「**練到總共五百萬步為止**」。已經練了三百萬就再練兩百萬；已經到了就直接說做完了。重複執行同一行是安全的。

## 筆電不會睡著

訓練期間會要求 Windows **不要休眠**（螢幕還是可以關）。不然設定好整晚訓練，早上起來會發現它只跑了三分鐘。

Linux/macOS 上這一步是空操作 —— **不能防止休眠不是拒絕訓練的理由**。

## 換了設定會被擋下來

```
session 'run1' has 20,480 steps trained against something else:
  reward reference -> score
Use a different --name, or delete the session directory to start again.
```

同一個 session 裡混兩種訓練條件，產出的模型卡片只能對其中一個誠實。所以它拒絕，並告訴你怎麼辦。

要跑不同設定就換名字：

```bash
train.bat --name run2 --reward score
```

---

# 開不起來：`系統資源不足`

換到筆電後出現過這個。`start.bat` 印出 `VITE v6.4.3 ready in 2794 ms`，下一行卻說前端沒啟動 —— 兩句話互相矛盾。直接跑 `npm run dev` 才看到真正的原因：

```
X [ERROR] Cannot read file "node_modules/@react-three/drei/core/TrailTexture.js":
系統資源不足，無法完成要求的服務。
Error: Build failed with 1 error
```

這是 Windows 的 **ERROR_NO_SYSTEM_RESOURCES (1450)**：handle 或 paged pool 用完了。Vite 會先說 ready，再在背景做套件預先打包（pre-bundle），esbuild 在那一步同時開非常多檔案，開到一半開不下去就整個死掉。所以「ready」是真的，「死掉」也是真的。

## 我們這邊改了什麼

3D 畫面只用到 `@react-three/drei` 裡的兩個元件（`Html`、`OrbitControls`），但原本是從套件的總入口 import，會把 320 個檔案整包拉進來。改成直接指名那兩個模組：

```tsx
import { Html } from '@react-three/drei/web/Html'
import { OrbitControls } from '@react-three/drei/core/OrbitControls'
```

預先打包從 **28.0 MB 降到 18.3 MB（−35%）**。

**這是減輕壓力，不是解除上限。** Windows 的 handle 上限還在那裡，只是現在離它比較遠。所以下面這幾件事還是要做。

## 你要做的（Windows 這邊）

1. **把資料夾加進 Defender 排除清單。** 設定 → 隱私權與安全性 → Windows 安全性 → 病毒與威脅防護 → 管理設定 → 排除項目 → 新增排除項目 → 資料夾 → 選 `C:\Users\user\AGMCIS_APP`。即時掃描會替每個被開啟的檔案多佔一份 handle，`node_modules` 有幾萬個檔案。
2. **重新開機。** handle 洩漏是會累積的，開機久了本來就比較容易撞到。
3. **關掉吃記憶體的程式**再開 —— 瀏覽器分頁、Docker Desktop、其他 IDE。

## 確認修好了

```bash
cd ~/AGMCIS_APP && git pull --ff-only origin claude/aifcs-flight-simulation-u32h56
cd AIFCS/frontend && rm -rf node_modules/.vite && npm run dev
```

`rm -rf node_modules/.vite` 是把上次沒做完的預先打包結果丟掉，不然它會沿用壞掉的快取。

**成功**：停在 `ready`，而且**不會**再往下吐錯誤。然後 Ctrl+C，改用 `start.bat` 正常開。

**失敗**：錯誤訊息會不一樣（不同檔名、或不同錯誤）。把那幾行貼出來 —— 檔名會指出還有誰在拉整包。


---

# 開不起來（二）：`後端 30 秒內沒有啟動成功`

這是另一個原因，跟上面那個不一樣。**同一份程式、同一台機器，連續兩次**：

```
!!  Backend did not become healthy in 30s.      ← 第一次
    Backend ready — http://127.0.0.1:8080/docs  ← 第二次，2 秒
```

後端啟動時會去問「這台機器能不能訓練」，那一步要 import PyTorch。CUDA 版的
PyTorch 第一次載入要讀好幾百 MB 的函式庫；檔案還沒進到系統快取，或防毒軟體
逐一掃描，就會遠超過 30 秒。

**後端沒有壞，是啟動腳本等不夠久就放棄了。** 這是腳本的問題，不是後端的問題。

## 改了什麼

等待上限從 30 秒（PowerShell 版是 60 秒）改成 **180 秒**，前端也一樣。而且
不再靜靜地等：

```
==> Starting simulation backend / 啟動模擬引擎…
    Still starting — the first run loads PyTorch, which is slow.
    第一次啟動要載入 PyTorch，比較久，請等一下。
    …still waiting (30s of 180s)
    Backend ready — http://127.0.0.1:8080/docs
```

等三分鐘卻什麼都不印，跟當掉沒兩樣。所以超過 20 秒它就會說自己在等什麼，之後
每 30 秒報一次時間。

**第一次啟動慢是正常的，第二次就會變快**（東西進了系統快取）。

## 覺得太久想改短

```bash
AIFCS_BACKEND_TIMEOUT_S=60 AIFCS_FRONTEND_TIMEOUT_S=60 ./scripts/start.sh
```

Windows 上在 PowerShell 裡設環境變數，或直接用參數：

```powershell
.\scripts\start.ps1 -BackendTimeoutSeconds 60 -FrontendTimeoutSeconds 60
```


---

# 開不起來（三）：前端根本沒綁上埠

筆記本上後端修好之後，剩下這個：

```
Backend ready - http://127.0.0.1:8080/docs
==> Starting dashboard / 啟動儀表板…
    ...still waiting (149s of 180s)
    Listening on 5173:          ← 這行後面是空的
!!  Dashboard did not answer in 180s.
```

`Listening on 5173:` 後面**什麼都沒有** —— 沒有任何程式在聽那個埠。Vite 連
「開始服務」都沒走到，180 秒內完全沒動靜。

## 換一個做法：不要 dev server

Vite 的 dev server 是**開發用的工具**，它的工作是熱重載和即時打包。但你不是在
改前端，你是在**用**這個平台。那台筆電做不到的那一步（預先打包），對「用平台」
這件事完全沒有必要。

所以現在預設改成：**後端直接把打包好的前端送出去**。

| | 舊的（dev） | 新的（built，預設） |
|---|---|---|
| 程式數量 | 2 個（Python + Node） | **1 個**（只有 Python） |
| 執行時需要 Node | 要 | **不要** |
| Vite 預先打包 | 每次都跑 | **完全沒有** |
| 網址 | `:5173` | **`:8080`**（跟 API 同一個） |
| 熱重載 | 有 | 沒有 |

前端本來就用相對路徑 `/api`、`/ws` 連後端，所以同源之後**連代理都不需要**。

## 你會看到什麼

第一次（要打包，這一步還是需要 Node）：

```
==> Building the dashboard / 打包前端（no bundle yet）…
    This happens once. Later starts reuse it. 只有這次要等，之後會直接用。
    ✓ built in 6.80s
==> Starting simulation backend / 啟動模擬引擎…
    Backend ready — http://127.0.0.1:8080/docs
      http://127.0.0.1:8080
```

第二次之後：

```
    Dashboard bundle is up to date / 前端已是最新，不用重新打包
    Backend ready — http://127.0.0.1:8080/docs
```

**實測第二次啟動 3 秒。** 改了前端原始碼它會自己發現並重新打包，不用手動清。

## 還是要用 dev server（改前端的時候）

Windows：

```powershell
.\scripts\start.ps1 -Dashboard dev
```

Git Bash / Linux / macOS：

```bash
AIFCS_DASHBOARD=dev ./scripts/start.sh
```

## 誠實說一句

打包（`vite build`）跟 dev server 的預先打包**一樣要讀很多檔案**，所以打包那一步
在你的筆電上也可能撞到同一個 Windows 上限。差別在兩點：

1. 打包用的是 Rollup，一次開的檔案數比 esbuild 少很多。
2. **只要成功一次就夠了** —— 之後每次啟動都只是送出已經在硬碟上的檔案。

如果打包那一步失敗，錯誤訊息會直接印出來，貼給我。


## 只要 API，不要網頁

訓練用的機器、或比賽當天的機器，根本用不到網頁：

```bash
AIFCS_SERVE_DASHBOARD=0 ./scripts/start.sh
```

後端照常跑，`/` 會回一段 JSON 說明而不是網頁。測試套件用的也是這個設定 ——
不然「找不到的網址回什麼」就會取決於這台機器有沒有打包過前端，同一份程式在兩台
機器上跑出不同結果。

## 「我的顯示卡到底有沒有在用？」

續練的時候看不到 `Using cuda device` —— **這是正常的**。Stable-Baselines3 只在
「建立」模型時印那行，「載入」時什麼都不印。而續練走的就是載入那條路，所以大部分
的執行都看不到它。少了那一行，不代表沒用到 GPU。

現在訓練器自己會說，兩條路都會印：

```
resuming run1: 1,921,544 / 5,000,000 steps (38.4%)
training 3,078,456 more steps
replay buffer restored (348 MB)
device:   cuda:0          ← 就是這行
workers:  8 parallel simulations
```

`cuda:0` = 有用到顯示卡。`cpu` = 沒有。

想從外面確認，另開一個視窗執行 `nvidia-smi`，清單裡會有 `python.exe`。

## 訓練中看不到進度？

**舊版確實沒有印。** 從 `Logging to ...` 到跑完為止，訓練器自己一行都不印 ——
中間那幾個小時只有 Stable-Baselines3 偶爾自己吐一張表。你找不到進度是對的。

現在每次 checkpoint（預設每 25,000 步）會印一行：

```
device:   cuda:0
workers:  8 parallel simulations
1,946,544 / 5,000,000 steps (38.9%) — about 2.1 hr left
1,971,544 / 5,000,000 steps (39.4%) — about 2.1 hr left
```

印的是**剛剛寫進硬碟的那個數字**，不是估的。

### 正在跑的訓練不想中斷，又想看進度

進度本來就會寫進檔案。另開一個 Git Bash：

```bash
cd ~/AGMCIS_APP/AIFCS && cat models/competition/run1/state.json
```

`timesteps_done` 就是目前進度，每 25,000 步更新一次。

## `wall_clock_s` 這個數字以前是錯的

同一個 run 裡，每次 checkpoint 都把「從開始到現在」的秒數**再加一次**到總計上。
三次 checkpoint 的五分鐘會記成十分鐘；77 次 checkpoint 會記成大約 39 倍。

已經修好：現在是「設定總時間」而不是「累加」，而且會正確帶上之前幾輪的時間。

**但是已經寫壞的舊數值不會自己變對。** 如果你的 session 是在這個修正之前開始
練的，`state.json` 和模型卡上的 `wall_clock_s` 都偏大。步數（`timesteps_done`）
**沒有受影響**，那個一直是對的。


---

# 一鍵驗收:`tryout.bat`

新加的東西全部在你的機器上跑一遍,**每個數字都是當場量的**。

## 怎麼開

用檔案總管走到 `C:\Users\user\AGMCIS_APP\AIFCS\scripts\`,雙擊 **`tryout.bat`**。

或在 Git Bash:

```bash
cd ~/AGMCIS_APP/AIFCS && scripts/tryout.bat
```

**大約兩分鐘。什麼都不會存,現有的訓練不受影響。**

## 它會檢查六件事

| # | 檢查 | 看什麼 |
|---|---|---|
| 1 | **防墜地板** | 墜毀率應該從 100% 掉到 0% |
| 2 | **四種獎勵** | `score` 對墜毀給正分,`margin` 給負分 |
| 3 | **觀測特徵** | 20 維 vs 30 維,以及十個新輸入的實際值 |
| 4 | **對手** | 靶機五分鐘後在 45 km 外,`pursuit` 轉向我方 |
| 5 | **Action repeat** | 兩種設定都是每幀回一個封包 |
| 6 | **訓練** | 全部選項打開跑一段,確認你的 GPU 吃得下 |

## 最後會給結論

```
=============================================
  OK    ground avoidance
  OK    reward modes
  OK    observation
  OK    opponents
  OK    action repeat
  OK    training
  Everything works here. 全部正常。
```

**任何一項 FAIL,把整段貼給我。** 它會印出失敗的原因,不是只說失敗。

## 跑快一點 / 跑仔細一點

```bash
scripts/tryout.bat --rounds 1 --steps 200     # 快
scripts/tryout.bat --rounds 6 --steps 5000    # 仔細
```

`--rounds` 是每個比較跑幾個回合,`--steps` 是最後那段訓練的決策數。


---

# 量模型:`evaluate.bat`

訓練印的是**獎勵**,比賽判的是**勝負**,兩個不是同一個函數 —— 這就是 `rewards.py`
有四種模式的原因。所以「哪個模型比較好」不能看訓練曲線,要用主辦方的判定表去量。

## 一行搞定

雙擊 **`scripts\evaluate.bat`** —— 不給參數就會把 `models\competition\` 底下
每個練過的 session 都量一遍。

指定要比的:

```bash
scripts/evaluate.bat models/competition/run1 models/competition/v2
```

## 會印什麼

```
每個 session 跑同一批 20 個回合(seed 1000),對手 reference

  run1 (5,000,000 steps, reference, reference, 1x)
  v2   (2,000,000 steps, margin, extended, 6x, floor)

                           won   killed    died  crashed      margin  152-500m
run1                       25%      8%      0%      58%      -1,204       3.1
v2                         70%     45%      0%       5%      +8,910      41.7

  勝率優先、其次分差,最好的是:v2
  相同種子,配對比較 —— 兩邊打的是同一批對戰。
```

| 欄位 | 意思 |
|---|---|
| `won` | 依 `decide_round`(主辦方表 3)的勝率 |
| `killed` | 累積 3 秒追蹤達成率 |
| `died` | 被對方達成 |
| `crashed` | 撞地率 —— **訓練初期最該看這個** |
| `margin` | 我方優勢分減對方,平均。時間到就是靠這個決勝 |
| `152-500m` | 每回合待在甜蜜點的秒數 |

## 兩個設計上的重點

**每個模型飛它自己練的那架飛機。** 觀測維度、決策頻率、防墜地板、方向舵上限
都從它自己的 `card.json` 重建 —— 30 維的策略根本吃不下 20 維的觀測,不可能共用
一套設定。

**但對手和初始條件是共用的。** 那是考題,兩個考生要考同一份。

所以這是在比**兩個系統**,不是比兩組權重 —— 因為上場的是系統。

**`seed` 一定要相同**,不然比的是兩批骰子。

## 換對手再比一次

```bash
scripts/evaluate.bat --opponent pursuit models/competition/run1 models/competition/v2
```

對靶機的勝率會飽和,對會追的對手才看得出差別。


---

# `WinError 1455 分頁檔太小`

```
OSError: [WinError 1455] 分頁檔太小，無法完成操作。
Error loading "...\torch\lib\cufft64_12.dll"
```

**這不是程式壞掉。** 是 Windows 的**分頁檔(虛擬記憶體)**不夠大,載不進 PyTorch
的 CUDA 函式庫。它提到的 `cufft64_12.dll` 只是第一個載不進去的檔案,不是原因。

CUDA 函式庫會一次要求大量的虛擬位址空間,Windows 要用分頁檔去背書。分頁檔設得小
或設成固定大小,就會在這一步失敗 —— 即使實體記憶體還很空。

## 修法(做一次就好)

1. **設定** → **系統** → **系統資訊** → 右邊「**進階系統設定**」
2. 「**效能**」區塊的「**設定**」按鈕
3. 「**進階**」分頁 → 「虛擬記憶體」的「**變更**」
4. 勾選「**自動管理所有磁碟的分頁檔大小**」
5. **確定 → 重新開機**

如果已經是自動管理還是失敗,就手動設:初始 **8192 MB**、最大 **32768 MB**。

## 先試這個(30 秒)

分頁檔是設定問題,但**當下記憶體不夠也會觸發同一個錯誤**:

- **關掉還開著的訓練視窗** —— 一個 8 worker 的訓練在 Windows 上會開 8 個完整的
  Python,每個都載了 torch
- 關掉瀏覽器分頁、Docker Desktop、其他編輯器

然後重試。這樣就好的話,就是當下記憶體不夠,不是分頁檔設定。

## 訊息改好了

以前你只會看到一整串 traceback。現在會直接告訴你怎麼辦:

```
!!  OSError: [WinError 1455] 分頁檔太小，無法完成操作。

    The Windows paging file is too small to load PyTorch's CUDA libraries.
    Windows 的分頁檔(虛擬記憶體)太小,載不進 PyTorch 的 CUDA 函式庫。
    修法:設定 → 系統 → 系統資訊 → 進階系統設定 → 效能「設定」→
          進階 → 虛擬記憶體「變更」→ 勾選「自動管理」→ 重新開機。
```

另外兩個在這個專案上真的發生過的 Windows 錯誤也一併處理了:

| 代碼 | 意思 | 修法 |
|---|---|---|
| **1455** | 分頁檔太小 | 上面那段 |
| **1114** | DLL 初始化失敗 | 裝 Visual C++ 可轉散發套件 |
| **1450** | 系統資源不足(handle 用完) | 重開機 + Defender 排除 |


---

# 續練不用重打一長串旗標

v2 是這樣開的:

```bash
scripts/train.bat --name v2 --reward margin --ground-avoidance --action-repeat 6 \
  --gamma 0.995 --gradient-steps -1 --observation extended --timesteps 2000000
```

**七個旗標。** 少打一個,續練會被擋下來(這是對的 —— 那確實是不同的實驗),
但你要的只是接著跑。

## 現在續練只要這樣

```bash
scripts/train.bat --name v2 --timesteps 2000000
```

它會從 session 自己讀回來,並印出來:

```
inherited:  reward=margin, gamma=0.995, gradient_steps=-1, observation=extended,
            action_repeat=6, ground_avoidance=True, ...
```

## 哪些會繼承、哪些不會

| 會繼承(定義這個實驗是什麼) | 不繼承(只是怎麼跑) |
|---|---|
| `--reward` | `--timesteps` |
| `--gamma`、`--learning-rate`、`--batch-size` | `--workers` |
| `--gradient-steps`、`--sde`、`--hidden` | `--device` |
| `--observation`、`--action-repeat` | `--checkpoint-every` |
| `--ground-avoidance`、`--rudder`、`--rudder-limit` | `--no-save-buffer` |
| `--opponent`、`--opponent-aggression` | |

**明確打出來的旗標永遠優先** —— 然後如果和 session 不一致,照樣會被擋下來。
繼承不是讓你不小心改掉一個實驗的方法。

## 對手池會長大,那不算改變實驗

self-play 就是**把自己的快照丟進池子裡**,所以池子變大是課程在運作,不是換了題目。
`opponent_pool` 因此不列入相容性檢查。

換**對手種類**(`reference` → `pursuit`)還是會被擋,那確實是不同的題目。
