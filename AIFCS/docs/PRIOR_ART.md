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
