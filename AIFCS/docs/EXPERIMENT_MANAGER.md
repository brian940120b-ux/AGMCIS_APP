# Experiment Manager（brief PART 15）

一個實驗 = 一個問題、一個改動、一次訓練、一個決定 —— **寫下來**。

## 為什麼

以前一次訓練只留下 `models/competition/<name>/card.json`。card 能說飛機設定是什麼，
**不能說這次跑是為了什麼、預期看到什麼、最後結論是什麼**。v7p 就是例子：它因為
一個「錐附近獎勵梯度太平」的假設而存在，假設錯了，而這兩件事的唯一紀錄是聊天記錄。

## 檔案

```
experiments/<ID>.yaml        一個實驗一個檔
backend/competition/experiments.py
scripts/experiment.bat       Windows 入口
```

## 一筆紀錄的欄位（對應 brief 的九個問題）

| 欄位 | 回答 |
|---|---|
| `question` | 為什麼做這次實驗 |
| `hypothesis` | 預期看到什麼 |
| `flags` / `pool` / `session` | 改了什麼、用什麼對手、用什麼模型 |
| `reward_tier` | `official` / `official-derived` / `research` / `experimental`（PART 13 三層） |
| `git_commit` | 可重現 |
| `results.summary` | 固定計分板的四個數字：won / margin / cone+ / killed |
| `decision` | `keep` / `reject` / `inconclusive` |
| `status` | `PLANNED` → `RUNNING` → `DONE` |

## 流程

```
scripts\experiment.bat new EXP-00N --session s --question "..." --hypothesis "..." --flags "..." --pool v4 v5
   ↓  kaggle/aifcs_train.py 的 EXPERIMENT = "EXP-00N"，commit，Save & Run All
   ↓  （Kaggle 端把紀錄標成 RUNNING；除此之外不寫入）
下載 sessions/<s>/ → models/competition/<s>/
scripts\scoreboard.bat models/competition/<s> --json results\exp00N.json
scripts\experiment.bat result EXP-00N --scoreboard results\exp00N.json --decision keep|reject|inconclusive --notes "..."
```

## 邊界

- 這裡**不做**模型晉升。結果是 registry gate 的證據，不是裁決（PART 14.2）。
- 空的 `flags` 代表 `train.py` 預設值 = **主辦方的配方**；`reward_tier` 會自動標成 `official`。
- `pool` 可以混：session 名字（要在 Kaggle Dataset 裡）和腳本對手名字（`reference` `pursuit` `break` `energy` `scissors` `wanderer`，不需要任何檔案）。`kaggle/aifcs_train.py` 只會去找前者。
- 同一個 ID 不能宣告兩次；`result` 的 decision 只接受三個值。

## 目前的紀錄

> **2026-10-01 的分差注意事項**：這一天之前所有 `margin` 欄（evaluate／scoreboard／Kaggle）都是用位置項反向的計分引擎算的
> （`CONFORMANCE.md` C 表 AA 列）。`won`、`cone`、`killed` 不受影響。EXP-001／002 的 margin 數字留著當歷史，不要和之後的比。
> v6 與 v8_pool 的 `shaped` 獎勵也含同一個反向項；EXP-008 起修正。

> **2026-10-03 的決策率注意事項**：這一天之前 `evaluate`／`scoreboard` 把受測方的策略每一幀（60 Hz）問一次，
> 池對手和當天的 client 則照卡片的 `action_repeat`（6 → 10 Hz）持住。所以受測方多了六倍反應速度：v6 自打 85%、
> v9 自打 80%、v9 vs v6 與 v6 vs v9 都 90%。已修（`play_round` 持住決策）；之前所有 scoreboard／配對列都偏好受測方，
> 名次要用修正後的評測器重量。真 HOST 預演不受影響。
>
> **同日第二個原因**：`PolicyOpponent` 只在決策幀編碼，`extended` 的速率欄被放大 6 倍、時鐘慢 6 倍，所以**池對手一直比它訓練出來的樣子弱**。
> 影響所有 `--opponent-pool` 列和訓練時的池（EXP-002、EXP-008）；腳本對手的六對手板不受影響。已修。
>
> **結案（同日）**：兩個修正後，`actorparity` 證明兩條推論路徑相同（差 ≤ 5e-6），v9 自打 `--both-seats` 60 回合 48%。評測器公平。
> 混沌讓同 seed 的局不會剛好抵銷，所以 20 回合的勝率有 ±11% 誤差；比模型一律 `--rounds 30 --both-seats`。

| ID | 狀態 | 內容 |
|---|---|---|
| `EXP-001-official-sac-baseline` | DONE / keep | PART 11.1 的 OFFICIAL_SAC_BASELINE，零旗標。筆電配對比較：vs v4 5%／墜毀 75%／−721，vs v5 10%／90%／+96，cone 0。六對手板 cone 全 0、分差與置中搖桿幾乎相同。地板，永不晉升 |
| `EXP-002-opponent-distribution` | DONE / reject | v6 配方 + 腳本對手進池 + `--league ema`。H2。筆電配對：vs v4 60%／+585／cone+ 0.62（v6 90%／+5,501／2.22），vs v5 75%／+246／0.00（v6 75%／+1,001／1.10）。六對手板：won ≥ v6 只有 3/6、cone 只贏 1 個（vs reference 0.72，全板唯一非零）。不取代 v6；仍是 EXP-003/005/006/007 的比較基準 |
| `EXP-003-mirror` | PLANNED | EXP-002 + `--mirror`。H1；等 EXP-002 有結果再跑 |
| `EXP-004-exploiter` | PLANNED | v6 配方、池只有 v6、`--stop-at-win-rate 0.7`。H3；Dataset 要加 v6 |
| `EXP-005-potential-shaping` | DONE / reject | Kaggle V8：vs v4 65%／−321／cone+ 0.00／best 12°，vs v5 60%／−66／0.23。錐沒動，對 v4 根本沒瞄。跑在位置項修正前（fda84e3），margin 不可比。 EXP-002 配方，`--reward potential`。H4；看 cone+ |
| `EXP-006-frames-observation` | PLANNED | EXP-002 配方，`--observation frames`。H5 |
| `EXP-007-start-geometry` | PLANNED | EXP-002 配方，`--geometry mix`。H6 |
| `EXP-008-doctrine-pool` | DONE / keep | Kaggle：vs v4 **100%、一次擊殺、cone+ 3.00**、錐內平均 0.88 s、5° 內 17.7 s；vs v5 100%／0.37。六對手板：cone reference 2.42／wanderer 1.63（v6 全 0），分差五項贏 v6；**弱點 pursuit 17%**（v6 50%）。新基準、冠軍候選，當天模型要先補 pursuit。**10-03 決策率修正後重量**：won 100/33/100/100/100/67，cone reference 0.90／energy 1.98／wanderer 3.00（擊殺 1/6），分差五項最高；pursuit 33%（v6 67%）。訓練時的池對手受「池對手只在決策幀編碼」影響偏弱 |
| `EXP-009-wez-start` | DONE / keep（課程階段） | Kaggle：vs v4 95%、一次擊殺、cone+ 3.00、5° 內 21.5 s；vs v5 85%、墜毀 15%、cone+ 0.70。六對手板：cone reference 3.00、wanderer 3.00，**各擊殺 1/6**（bench 上第一次擊殺）；但 pursuit 33%（分差負）、energy 50%。當課程第一階段，接著用 published 幾何續練。**10-03 重量**：won 83/33/100/83/83/100，cone reference 3.00（擊殺 1/6）、wanderer 1.05 |
| `EXP-010-lookahead-observation` | **DONE / keep → 冠軍、暫定當天模型** | EXP-008 配方，`--observation lookahead`（預測 1 s／3 s 幾何）。H16。Kaggle 1,997,568 步在 7.5 h 預算停（74 步/s）；訓練期最後 50 回合 90%（pursuit 94%）。**筆電（修正後評測器）六對手板**：won 100/67/83/100/100/100；cone reference **3.00（擊殺 1/6）**、wanderer **3.00（擊殺 2/6）**；pursuit 分差 +121,365（v9_doctrine +7,050）；唯一失分 break 83%。**配對 vs v9_doctrine，30 seed 兩座 60 回合：67%／+58,815**，兩座各 67%。代價：墜毀 20% |
| `EXP-011-defensive-starts` | **DONE / keep（材料）** | EXP-008 配方，`--geometry defmix`。Kaggle 2,000,000 步 6.4 h；訓練期 96%（pursuit 74%）。**筆電六對手板**：won 100/50/100/100/100/100，**pursuit 33% → 50%**（規則達標）；cone 六個對手中五個非零（0.57/0.90/0.43/1.15/3.00，擊殺 wanderer 1/6），v9_doctrine 只有三個。**配對 vs v9_doctrine 兩座 60 回合：63%／+54,314**。代價：墜毀 27%／17%。不是當天模型（lookahead 在板上和配對都贏它），當 EXP-012 的材料 |
| `EXP-012-lookahead-defmix-v9pool` | RUNNING（Kaggle） | EXP-010 配方 + `--geometry defmix`，池加入 v9_lookahead／v9_defmix／v9_doctrine（池對手修正後第一次正確地飛）。規則：兩座 60 回合 vs v9_lookahead > 56% 且板上沒有對手掉超過一回合 → keep；配對 < 50% 或 cone reference < 1.5 → reject。**Dataset `aifcs_pool` 要先放三個 v9 資料夾（各含 card.json）** |
| `EXP-013-gunsnap-reward` | PLANNED | EXP-010 配方與池，只換 `--reward gunsnap`（錐內 10,000/s，HOST 量到的；其餘與 shaped 相同）。H7。問題：冠軍對真 HOST 射程內 149 s 只咬 1.75 s，穿過去、沒人付它留下。規則：兩座 60 回合 vs v9_lookahead > 56%，或板上兩個對手擊殺上升且沒有對手掉超過一回合 → keep；cone reference < 1.5 或配對墜毀 > 30% → reject。EXP-012 跑完就開 |
