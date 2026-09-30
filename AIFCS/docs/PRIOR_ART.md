# 這個領域別人做到哪裡,我們還能做什麼

2026-09-28 整理。距離比賽 40 天。

寫這份的理由:到目前為止我們一直在解眼前的 bug,而參賽的其他隊伍裡一定有
本科背景的人。與其自己摸,不如先看這題**已經被解過**的部分。

---

## 一、最重要的事實:這題不是新題

主辦方的射擊條件是 **DARPA AlphaDogfight 的 WEZ,一字不改**:

| | 論文(arXiv 2105.00990) | 主辦方 | 我們 `AttackEnvelope` |
|---|---|---|---|
| 錐角 | 2° aperture | 2°(半角 1°) | `half_angle_deg = 1.0` |
| 距離 | 500–3000 ft | 500–3000 ft | `500.0` / `3000.0` |
| 持續 | (無) | 累積 3 秒 | `kill_seconds = 3.0` |

唯一的加法是「累積 3 秒」。**所以 AlphaDogfight 八支隊伍兩年的工作,對我們幾乎
全部適用。**

---

## 二、我們最弱的一環,不是演算法

量到的事實:

```
do nothing vs v5     80% won     ← 一根不動的搖桿,贏我們自己訓練的對手
do nothing vs v4     60% won
```

**我們的對手池裡有一個成員,連中立搖桿都打不贏。** 而整個 v6/v7p 的訓練與評分
都建立在這兩個對手上。

對照文獻:

| 來源 | 對手數量與種類 |
|---|---|
| PHANG-MAN(AlphaDogfight 亞軍) | **30 個** —— 主辦方腳本、自己的舊版本、單一低階策略、模仿對手行為的、自對弈變體 |
| BVR Sim(2026 環境論文) | **6 個腳本對手**:straight-line、random、aggressive、tactical、standoff、MAD |
| AlphaStar | main agents + **main exploiters** + **league exploiters** |
| 我們 | **2 個**,都是學出來的,其中一個是壞的 |

PHANG-MAN 的對手抽樣規則寫得很具體:勝率過 50% 才加入「聰明」對手,之後依
**最近 100 場勝率**加權抽樣,機率夾在 **0.2%–11.7%**。

BVR Sim 把腳本對手的用途講成三件事,第三件我們完全沒有:

> provide curriculum opponents, **reproducible evaluation references**,
> and expert actions without requiring a separately trained checkpoint

我們的評分基準是 v4/v5 —— 兩個會變動、而且其中一個是壞的目標。**連「這一代比上
一代好」這句話,現在都沒有穩固的量尺。**

---

## 三、排序過的待辦(影響 ÷ 成本)

### 1. 建一組腳本對手 ★★★ 最高優先

不用 GPU,不用訓練,一天之內做得完,同時修好**三件事**:課程的簡單起點、
可重現的評分基準、對手多樣性。

現有三個(`reference`、`level`、`pursuit`)是起點,照 BVR Sim 的清單補齊:
定速直飛、隨機、純追擊、純防禦(不停轉彎)、能量戰(爬升脫離)、鏡像。

### 2. 課程順序 ★★★

PHANG-MAN:**先簡單腳本,勝率過 50% 才加難的**。我們是第一步就上池。
而 v6k(只打 drone)殺 55%、v6(直接上池)殺 0%,資料跟文獻一致。

### 3. 回放緩衝區裡沒有「錐」★★☆

PHANG-MAN 把雙方血量調高 **10 倍**,理由原文:

> to further increase the ratio of WEZ to non-WEZ memories stored in the
> replay buffer

我們的對應做法是**初始條件偏向射擊區** —— 讓一部分回合直接從接近開火的幾何
開始。SAC 學不到它從沒進過的狀態。`cone 0.38 秒` 就是這件事的數字。

相關技術:Prioritized Experience Replay、Hindsight Experience Replay,都是
針對稀疏獎勵的標準工具,我們一個都沒用。

### 4. 比賽當天的工程風險 ★★★ 但不是 ML

**這是比賽最常輸掉的地方,而且跟策略好壞無關。** 60 Hz UDP、208 位元組 OBS、
掉包、重連、第一幀的時序。策略再好,客戶端在當天掉一包就是零分。

要做的是**全程彩排**:對主辦方的 host 連續跑完整的 300 秒回合,量抖動、
量延遲、量掉包,而不是只驗協定解析。

### 5. 階層式架構 ★☆☆ 這次不做

PHANG-MAN 用高階選擇器(10 Hz)+ 多個專門低階策略(50 Hz),其中
**Aggressive Shooter / Conservative Shooter** 專門負責近戰。LAG / CloseAirCombat
也是低階飛行控制器 + 高階指令。

效果好,但這是**重寫架構**,40 天內做這個會賠掉已經有的東西。記下來,不做。

### 6. 控制頻率 ── **不要做**

量過了:`deg/s 3.2`,10 Hz 下每個指令之間視線移動 0.32 度,是錐寬的三分之一。
控制有餘裕。文獻用 50 Hz,但那是它們的飛機和它們的任務。**我們量過,不是這個。**

---

## 四、參考資料

### 書

| 書 | 為什麼 |
|---|---|
| **Robert L. Shaw, _Fighter Combat: Tactics and Maneuvering_**(Naval Institute Press, 1985) | 空戰機動的聖經。lead / lag / pure pursuit、角力與能量戰、corner speed、槍砲追瞄的幾何。獎勵函數要編碼的「戰術知識」全在這裡,而且是**不用 GPU 就能取得的優勢** |
| **Stevens & Lewis, _Aircraft Control and Simulation_** | 它的 F-16 模型和 JSBSim 的 `f16.xml` 同源 —— 都來自 NASA TP-1538（1979）的風洞資料;`f16.xml` 檔頭引用的是 TP-1538 本身。要理解我們飛的是什麼、為什麼升降舵正負號會反,看這本;機體的整理見 `AIRCRAFT_KNOWLEDGE_BASE.md` |

### 論文(依實用程度)

1. **Pope, Ide et al., _Hierarchical Reinforcement Learning for Air Combat at
   DARPA's AlphaDogfight Trials_**, arXiv 2105.00990 —— 就是我們這一題。獎勵表
   (table I)、課程、對手抽樣、50 Hz / 10 Hz 分層,全部可抄
2. **_Deep reinforcement learning-based air combat maneuver decision-making:
   literature review, implementation tutorial and future direction_**,
   Artificial Intelligence Review (2023) —— 綜述 + **實作教學**,有付費牆
3. **BVR Sim**, arXiv 2608.25419 —— 2026 的環境論文,組合式獎勵的分類、六個
   腳本對手的設計理由、多速率決策(戰術 0.4 s / 積分 0.02 s)
4. **_Within-visual-range air combat maneuver decision-making ... via a
   curriculum self-play soft actor-critic with an attention mechanism_** ——
   跟我們的技術棧(SAC + 自對弈 + 課程)最接近

### 開源環境

| 專案 | 值得看什麼 |
|---|---|
| [LAG / CloseAirCombat](https://github.com/liuqh16/LAG) | JSBSim 1v1,階層式控制器,PPO/MAPPO 基線,獎勵實作可讀 |
| [BVR Gym](https://arxiv.org/pdf/2403.17533) | 超視距,但獎勵拆解的方式值得參考 |
| [gym-jsbsim](https://github.com/Gor-Ren/gym-jsbsim) | 最早的 JSBSim RL 包裝,結構簡單 |

---

## 五、誠實的評估

我們現在有的:一個對 v4 拿 90%、+5,501 分差的策略,最好的一回合累積到
**2.22 / 3.00 秒**。這不差 —— 但**從來沒有殺掉過一個會閃的對手**。

比賽當天的對手是別隊的 AI,會閃。所以真正的問題不是「我們的分數好不好看」,
是「對上一個沒見過的、會閃的對手,我們還剩多少」。

而那個問題,現在**沒有任何證據可以回答**,因為我們的對手池只有兩個,還有一個
是壞的。

**所以下一件事是第 1 項,不是再練一代。**

---

## 六、2026-09-30 補查：同一題目，兩個月前在韓國比過了，冠軍程式是公開的

上一輪標成「找不到」的，換路線再找的結果：

| 來源 | 上次 | 這次 | 怎麼找到的 |
|---|---|---|---|
| SRC-006 Drones 2025（階層 RL + WGAN 課程） | 403 unreachable | **verified，全文** | MDPI/DOI/OA PDF 都 403，改走 r.jina.ai 讀取代理 |
| SRC-007 SP-PSRO | abstract-only | **verified，PDF 抽文** | 直接下 arXiv PDF 本地抽字 |
| SRC-003 Shaw《Fighter Combat》 | abstract-only | 仍讀不到（archive.org 只借閱） | **改用 SRC-016** 美國海軍 T-45 ACM 講義（公開、有數字） |
| SRC-017 Wang 2024 AI Review 綜述 | — | abstract-only（Springer 付費、RG 403） | 作者 gitee 程式庫公開，未讀 |

**最大的發現**：搜「JSBSim F-16 1v1 dogfight RL github」找到 **韓國航空大學 2026 AI Pilot Top Gun Challenge**
（初賽 8/27–28、決賽 9/17，290 隊 873 人，16 隊決賽）—— 同一架 JSBSim F-16、同樣 1v1 純機砲。
**冠軍隊把完整程式公開了**（SRC-012），另有兩隊公開設計摘要（SRC-013、SRC-014）。

### 6.1 他們的規則 vs 我們的（SRC-011）

| | 韓國 KAU | 我們（中科院） |
|---|---|---|
| 機體 / 物理 | JSBSim F-16 | JSBSim F-16 |
| 回合 | **200 s** | 300 s |
| 射擊錐 / 距離，前段 | **2° / 500–3,000 ft**（0–100 s） | **2° / 500–3,000 ft**，整場 |
| 後段 | 4° / 3,500 ft（100–150 s）、6° / 4,000 ft（150–200 s） | 不變 |
| 擊殺判定 | **HP 模型**：錐內每秒扣血，500 ft 時 1.0/s 線性降到最遠距離 0（後段 0.3、0.1） | **累積 3 秒** |
| 沒擊殺時 | 剩餘 HP 高者勝 | S_advantage 積分 |
| 「2 度」怎麼讀 | 冠軍程式 `TIER1_CONE_DEG = 1.0` —— **半角 1°** | 我們也讀成半角 1°（CONFORMANCE D.1） |

第一階段的幾何**一模一樣**；差在時間長度和擊殺的積分方式。他們的東西**不能**改我們任何官方層的東西
（規則、OBS/CMD、計分），但這是「這個幾何上什麼有效」最接近的公開證據。

> 一個獨立的比賽、獨立的隊伍，把同樣的「鼻軸線 2 度」寫成半角 1°。D.1 的判讀多了一份旁證。

### 6.2 冠軍隊的作法（SRC-012，已 clone 讀碼，commit e677b4b）

**沒有 LICENSE 檔 → 程式碼不能複製進本 repo，只能讀設計、註明出處、自己重寫。**

| 面向 | 他們 | 我們現在 | 差距 |
|---|---|---|---|
| 演算法 | PPO，MLP 512×512×512 tanh，**離散化動作**每軸 21 格 | SAC 連續 | 不同路線；他們的 GPU 版一次跑幾千個環境 |
| 超參 | lr 1e-4、γ 0.98、GAE 0.95、clip 0.2、5 epoch、minibatch 512、entropy 1e-4、target KL 0.05、每輪 80,000 步、觀測正規化、**左右鏡像增強** | — | 鏡像增強是免費的樣本翻倍，我們沒有 |
| 觀測 | **214 維**：50 個座標無關純量 + 7 個方向向量 × 6 個座標系（世界、我機體、敵機體、我速度系、敵速度系、視線系）；角速度從連續姿態用 SO(3) log 重建 | 20 維參考編碼 / 30 維 shaped | 他們把「幾何」在觀測裡展開，而不是讓網路自己學旋轉 |
| 獎勵 | (敵 HP 損 − 我 HP 損 × w) × 10，w 由 0.5 排程到 1.0；加一項**位能差** shaping（距離 + 雙方 ATA）×5e-5，整場加起來約等於一次命中；**勝負終端 = 0**；掉出高度下限 = 一次扣光剩餘 HP；**高度 shaping 全部拿掉** | 官方分數 + 追蹤項 + 地板罰 | 他們讓「打中」幾乎是唯一的錢 |
| 對手池 | 7 格：行為樹 DLL 永不淘汰、隊內 MPC、主辦方 cut-off 程式固定；學習快照在最低 EMA 勝率 > 0.6 時晉升；抽樣 = 0.5 均勻 + 0.5 softmax(−ema/0.3)，**輸得多的對手抽得多** | pool v4+v5 + 6 個腳本對手 | 概念相同，他們有「輸誰就多打誰」 |
| Exploiter | 每 500 輪凍結主策略，從零練一個專打它的，打到 70% 就進池永不淘汰 | 無 | AlphaStar 那一套，他們真的做了 |
| 初始分佈 | **3-9 線（並排反向）: 迎頭 = 4 : 1**，迎頭 5,539 m | 規格 3/6/9 千呎 | 他們的初始不是規則給的，是自己挑的訓練分佈 |
| 評測 | power_test（N 局 + 顯著性）、final_power_test（對全部 8 個基準）、league 熱圖；10/60 Hz、argmax/隨機各自可選；Tacview CSV + 重播器 | scoreboard 6 對手 + trace/viewer | 同構，他們多了顯著性檢定和聯賽熱圖 |

### 6.3 另兩隊（SRC-013、SRC-014）

- **junomanversion99**：三層（1 Hz 離散 SAC 選 10 種機動 → 虛擬點 → 60 Hz 指向控制），位能式 shaping。
  自報 97.3% 勝率，**其中 91.6% 是靠 HP 撐到時間到，只有 2.3% 是真的打掉** —— 在那套規則下，
  「不被打中」比「打中」值錢。我們的規則沒有 HP，S_advantage 是位置積分，這點**不可直接搬**。
- **TopGun-BT**：手寫行為樹，門檻：< 1,500 m 高度緊急、< 914 m 開火、< 1,100 m 受威脅就防禦、
  6,000 m 內尾追轉換、6,000 m 外預測攔截。可拿來對照我們六個腳本對手的門檻。

### 6.4 LAG（SRC-015，GPL-3.0，已 clone 讀碼）

同一架 F-16、1v1 無武器「姿態」任務、60 Hz 每 12 幀動一次、200 s。
PostureReward = 方位函數 × 距離函數，位能式，尺度 100；Elo 加權的自我對弈池。**GPL：引用與重寫，不 vendor。**

### 6.5 海軍 T-45 ACM 講義（SRC-016，替代 Shaw）

一圈戰（同一個轉彎圓，半徑小者勝）/ 兩圈戰（各自的圓，轉彎率高者勝）/ 出平面（機頭高收圓、機頭低加率）；
高 yo-yo = 滯後追蹤防 3/9 線衝過頭，低 yo-yo = 前置追蹤加轉彎率。訓練規則：機砲最小距離 1,000 ft、
禁止迎頭機砲；T-45 轉角速度約 410 kt（只當尺度感）。這是腳本對手和看 trace 的共同語言。

### 6.6 從這裡能做的假設（都走 `experiments/`，不直接改模型）

| # | 假設 | 來源 | 官方層動了嗎 |
|---|---|---|---|
| H1 | 左右鏡像增強（觀測和動作同時鏡像）讓同樣步數學到兩倍幾何 | SRC-012 | 否（訓練資料處理） |
| H2 | 對手抽樣「輸誰多打誰」（softmax(−勝率/τ) 混均勻）比均勻抽樣更快壓平弱點 | SRC-012、SRC-001 | 否 |
| H3 | 一個 exploiter 回合（凍結 v6，練專打它的，再把它放進池）能揭露 v6 的固定弱點 | SRC-012、SRC-007 | 否 |
| H4 | 位能差 shaping（Φ(s′) − Φ(s)）比我們的逐幀追蹤項更不會被「路過」騙到 | SRC-012、SRC-013、SRC-015 | 否（research tier） |
| H5 | 觀測裡直接給視線系 / 速度系分量，比 20 維參考編碼學得快 | SRC-012 | 否（`--observation` 選項，state.py 不動） |
| H6 | 初始分佈混入非規格的相對幾何（並排反向）當課程，正式評測仍用 3/6/9 千呎 | SRC-012、SRC-006 | 否（訓練分佈；評測不變） |

**不搬的**：HP 模型、200 s、後段放寬的射擊錐、離散 21 格動作（我們的 CMD 是 float，SAC 連續沒理由換）。

**下一步的排序建議**：H1 和 H2 最便宜（不碰獎勵、不碰觀測、不碰環境），先寫成 EXP-002 / EXP-003 的紀錄。
