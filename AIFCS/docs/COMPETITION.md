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
