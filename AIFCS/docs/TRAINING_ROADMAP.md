# 訓練路線圖 — 從研究結果到下一個模型（2026-09-30）

給一個要在 38 天內把模型練到能贏的人看的。每一步都寫：**做什麼、在哪裡打、成功長什麼樣、
失敗怎麼辦、下一步**。不做的事也寫，免得回頭又問。

規則不動：OBS/CMD/計分/射擊錐是主辦方的，這裡一個字都不改。所有改動都在「怎麼練」，
而且都是旗標 —— `train.py` 不加旗標就是主辦方原配方（EXP-001）。

---

## 0. 今天做好的東西（`a4ffdbb` 之後）

| 東西 | 是什麼 | 來源 | 預設 |
|---|---|---|---|
| **腳本對手進池** | `--opponent-pool` 現在可以混：`v4 v5 break energy scissors wanderer pursuit`。名字每回合重建，checkpoint 是存好的策略，**同一個抽樣分佈** | SRC-001 PHANG-MAN、SRC-012 韓國冠軍 | 關（不給池就是內建 drone） |
| **`--league ema`** | 對手抽樣 = 一半均勻 + 一半 softmax(−EMA 勝率 / 0.3)，**從第一回合就開始偏向打不過的對手** | SRC-012 `train.py` 預設值 | `paper`（PHANG-MAN 規則，不變） |
| **`--mirror`** | SAC 的 replay buffer 每筆經驗存兩份：原本的 + 左右鏡像。同樣步數，兩倍幾何 | SRC-012 mirror augmentation | 關 |
| 實驗紀錄 | `experiments/EXP-002-opponent-distribution.yaml`、`EXP-003-mirror.yaml` | PART 12 | — |

**為什麼 `--league ema` 不是可有可無**：PHANG-MAN 的規則要「每個對手各打滿 100 場 + 整體勝率 > 50%」
才開始加權。我們 200 萬步分 8 個 worker，一個 worker 整場只看到幾十回合 —— **舊規則在我們的預算內
永遠不會啟動**，所以到今天為止所有「有池」的訓練，對手都是均勻抽的。這是讀了韓國程式才發現的。

**`--mirror` 是量過的，不是假設的**：JSBSim F-16 左右**不完全**對稱 —— 隨機滿舵 10 秒後兩邊差 12° 滾轉，
但方向盤置中時每秒只差 0.001°，所以不是固定偏差，是混沌放大。一筆經驗只跨 1 幀，
1 幀內的不對稱 < 0.005°。測試 `test_competition_mirror.py` 鎖住這個界線。

---

## 1. 現在馬上做：EXP-001（主辦方原配方基準）

還沒跑。它是所有研究層獎勵要跨過的地板，PART 11.1 說一定要有。

**Kaggle：**
1. 開你的 notebook → 確認 `kaggle/aifcs_train.py` 裡 `EXPERIMENT = "EXP-001-official-sac-baseline"`（目前就是）
2. **不用**掛 Dataset（pool 是空的）
3. Save Version → Save & Run All
4. 成功的樣子：log 出現 `experiment EXP-001-official-sac-baseline`、`2,000,000 / 2,000,000 steps`、然後一張 evaluate 表
5. 下載 Output → `sessions/official_sac_baseline/` 放到筆電 `models/competition/official_sac_baseline/`

**筆電：**
```
cd %USERPROFILE%\AGMCIS_APP\AIFCS
scripts\scoreboard.bat models\competition\official_sac_baseline --json results\exp001.json
scripts\experiment.bat result EXP-001-official-sac-baseline --scoreboard results\exp001.json --decision keep --notes "baseline recorded"
```
（decision 填 `keep` 是「留下當基準」，不是「它很好」。）

## 2. 接著：EXP-002（對手分佈）— 今天的主菜

v6 的配方一個字不動，只換「跟誰打、怎麼抽」。

**Kaggle：**
1. `kaggle/aifcs_train.py` 第 60 行改成 `EXPERIMENT = "EXP-002-opponent-distribution"`，commit + push（在筆電 `git pull` 後改，或直接在 GitHub 網頁改）
2. Input → Add Input → **`aifcs_pool` Dataset**（裡面要有 `v4/`、`v5/`，跟上次 v7p 一樣）
3. Save & Run All
4. 成功的樣子：log 有 `league:   ema over break, energy, pursuit, scissors, v4, v5, wanderer`；
   失敗的樣子：`!! 對手池少了 v4, v5` → Dataset 沒掛到，回第 2 步

**筆電（下載後）：**
```
scripts\scoreboard.bat models\competition\v8_pool --json results\exp002.json
```
判定規則（寫在紀錄裡）：6 個腳本對手，**won ≥ v6 的 4 個以上、cone+ 比 v6 的 2.22 s 進步 2 個以上 → keep**；
won 輸給 v6 3 個以上 → reject。其他 → inconclusive。
```
scripts\experiment.bat result EXP-002-opponent-distribution --scoreboard results\exp002.json --decision keep --notes "..."
```

## 3. 然後：EXP-003（鏡像）

EXP-002 **有結果之後**才跑，因為它是 EXP-002 + 一個旗標，比較對象是 EXP-002，不是 v6。
步驟同上，`EXPERIMENT = "EXP-003-mirror"`。成功的樣子：log 有 `mirror:   on`。

注意：`replay_buffer.pkl` 會是原本的兩倍大（每筆存兩份），Kaggle Output 500 MB 左右，正常。

## 4. 之後的順序（先不要做，等 2、3 的數字）

| 順序 | 假設 | 要做的事 | 為什麼排這 |
|---|---|---|---|
| H3 | **Exploiter**：凍結最好的模型，從零練一個專打它的，進池 | **已做**：`--opponent-pool v6 --stop-at-win-rate 0.7 --win-window 50`；紀錄 `EXP-004-exploiter`（Dataset 要加 v6） | 韓國冠軍每 500 輪做一次；揭露固定弱點最直接的方法 |
| H4 | **位能差 shaping** Φ(s′)−Φ(s) | **已做**：`--reward potential`；紀錄 `EXP-005-potential-shaping`（= EXP-002 只換獎勵） | 三個來源都用；但 v7p 的教訓是獎勵改動最容易白跑，所以排在對手之後 |
| H5 | 視線系/速度系觀測 | **已做**：`--observation frames`（105 維）；紀錄 `EXP-006-frames-observation`（= EXP-002 只換觀測） | 改觀測 = 跟舊 pool 不相容，代價最大 |
| H6 | 課程初始分佈 | **已做**：`--geometry published/abreast/headon/offensive/defensive/mix`；續練可以換（= 課程），card 記歷史；紀錄 `EXP-007-start-geometry` | 只影響訓練，評測仍用規則的隨機起始 |
| H7 | **錐邊階躍獎勵**（ADT 兩隊都用，SRC-018 表 I） | **已做**：`--reward gunsnap` = `shaped` 但錐內付 HOST 量到的 10,000/s（公告 2,000）；紀錄 `EXP-013-gunsnap-reward` | ADT 的 gun-snap 就是 HOST 的錐內指示項；我們的獎勵本來就含它，差在付多少 |
| H8 | **WEZ 起始課程**（射手從錐內／被咬住開始） | **已做**：`--geometry wez`／`wezdef`；紀錄 `EXP-009-wez-start` | ADT 射手策略 100% 這樣練；PRIOR_ART 七.5 |
| H9 | **寬淺網路**（單層 12,288） | 未做：`--hidden 8192` 既有旗標即可 | 最便宜；推論時間要量 |
| H10 | **狀態／動作截斷**求 GPU／CPU 一致 | 未做 | 可能解釋 Kaggle 55% vs 筆電 60%；PRIOR_ART 七.5 |
| H11 | 對手門：腳本勝率過 50% 才抽 session | 未做：`league.py` | PHANG-MAN 的課程 |
| H12 | **手冊腳本對手**：flare、jinker（出平面 duckunder）、reversal、leadturn | **已做**：`adversaries.DOCTRINE`，池與評測可用名字；bench 不變；紀錄 `EXP-008-doctrine-pool` | AFTTP 3-3 4.3.10–11、APL BUD FSM；`docs/TACTICS.md` 六 |
| H13 | **固定起始集**：攻／守／中立／高角度各 N seed，加 `cz` 幾何（2,500–4,500 ft、25–45°） | 一半：`--geometry cz` 已做；evaluate 的固定集未做 | ADT 的 benchmark 做法 |
| H14 | 閉合率不超過距離 5% 的平滑懲罰 | 未做：研究層獎勵 | 手冊 ROT；先量 v6 的 overshoot |
| H15 | 出平面防守對手（3,000–4,000 ft 且對方拉 lead 才 jink） | **已做**：= `jinker`（觸發 1,200 m、對方機頭 10° 內） | 比 `break` 像真的 |
| H16 | **預測未來軌跡**：1 s／3 s 等速推算的幾何進觀測（lead computing） | **已做**：`--observation lookahead`（40 維）；紀錄 `EXP-010-lookahead-observation` | Heron 3 秒、手冊「先放 lead」、韓國冠軍 margin 特徵 |

**不做的**：HP 模型、200 秒、放寬的錐、離散動作、任何改 OBS/CMD/計分的東西。

## 4.1 H3 已做好的部分：訓練中就看得到勝率

之前訓練過程只看得到 reward，看不到「贏幾成」—— 每個 worker 各自記帳，主程序不知道。現在每回合結束時
環境把表 3 的判定放進 `info["verdict"]`，訓練主程序算滾動勝率（整體 + 每個對手），寫進 TensorBoard 的
`league/win_rate`、`league/win_rate_vs_<對手>`，跑完也印一行 `won:  62% of the last 50 rounds (break 80%, v5 40%)`。

`--stop-at-win-rate 0.7 --win-window 50`：最近 50 回合勝率到 70% 就停，card 記 `stopped_at_win_rate`，
再跑一次會說 target already met。這就是 exploiter 的停止規則。**預設不停**（None）。

## 4.2 H4 已做好的部分：`--reward potential`

`shaped` 的追蹤項是**每幀**付：機頭靠近目標的每一幀都給錢，所以「路過錐六次」領六次 ——
v6 量出來就是這樣（1° 內的時間只有立體角的機率值）。`potential` 把同一個追蹤項改成
**位能差** Φ(s′) − Φ(s)：一整回合加總只等於 Φ(終) − Φ(始)，路過幾次都一樣，**待在錐裡的錢只剩官方
的 2000/秒** —— 讓官方分數自己當老師。Φ 滿刻度 = 2000 = 一秒攻擊時間（韓國冠軍也是這樣定尺度：
「整場 shaping 約等於一次命中」）。兩個模式只差這一件事，deck 罰則、margin、擊殺都一樣。

## 4.3 H5 已做好的部分：`--observation frames`

`extended` 30 維之外再加 75 維：6 個向量（重力、視線、我方速度、敵方速度、相對速度、我方角速度）
× 5 個座標系（世界 NED、我機體、我速度系、敵速度系、視線系），去掉常數或已經有的組合。
封包**沒有**敵機姿態與角速度，所以沒有「敵機體座標系」和「敵角速度」—— 不編造。
鏡像符號表也涵蓋 105 維（普通向量 y 翻號、角速度 x/z 翻號），由編碼器本身驗證，所以 `--mirror` 可以疊加。

## 4.4 H6 已做好的部分：`--geometry`

只改**訓練回合**開始時兩機怎麼面對：`published`（規則：隨機方位、隨機航向，預設）、`abreast`（3/9 線並排反向，
韓國冠軍的主分佈）、`headon`（迎頭）、`offensive`（目標在前方飛離，我們一開始就在錐附近）、`defensive`（目標在後面追）、
`mix`（abreast : headon = 4 : 1，SRC-012 的訓練分佈）。距離 3/6/9 千呎、高度 10–20 千呎、340 節**不變**。
評測器從不讀 card 的 setup（測試鎖住），所以訓練用什麼幾何都不會改變考試。

**課程 = 同一個 session 續練時換幾何**：`--geometry offensive` 練 50 萬步，再用 `--geometry published` 續練，
不會被擋（geometry 是 growable），card 的 `geometry_history` 記錄每一段從第幾步開始。

## 4.5 四個假設全部做完之後：跑的順序

| 順序 | 實驗 | 比誰 | 改了什麼 |
|---|---|---|---|
| 1 | EXP-001 | — | 主辦方原配方（地板） |
| 2 | EXP-002 | v6 | 對手分佈（池 + ema） |
| 3 | EXP-003 | EXP-002 | + `--mirror` |
| 4 | EXP-005 | EXP-002 | 獎勵 `potential` |
| 5 | EXP-006 | EXP-002 | 觀測 `frames` |
| 6 | EXP-007 | EXP-002 | 起始 `mix` |
| 7 | EXP-004 | v6 的勝率 | exploiter（Dataset 要加 v6） |

3–6 各自只跟 EXP-002 差一件事，可以**平行跑**（Kaggle 一次一個 notebook version，開四個 version 就是四個實驗）。
不用再改第 60 行：在 Kaggle 那個 cell 最上面加一行 `import os; os.environ["AIFCS_EXPERIMENT"] = "EXP-008-doctrine-pool"`（換名字就是換實驗），再 Save & Run All。一個實驗一個 version。
贏的那些旗標最後疊在一起，變成 EXP-009（那時再寫紀錄）。

## 5. 每次跑完都要看的三個數字

`scoreboard.bat` 的 **won**（贏幾成）、**margin**（S_advantage 差）、**cone+**（最好一回合在射擊帶累積幾秒；3.00 = 擊殺）。
v6 的：90% / +5,501 / 2.22（對 v4）。任何新模型先跟這三個比，再談別的。

看單一回合為什麼沒擊殺：`scripts\evaluate.bat models\competition\v8_pool --trace results\traces` 錄下每回合，再
`.venv\Scripts\python backend\competition\roundtrace.py results\traces\*.jsonl.gz --html results\trace_html` 出 HTML，
看它是「路過錐一次」還是「進出六次」。
