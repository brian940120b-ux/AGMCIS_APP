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

| ID | 狀態 | 內容 |
|---|---|---|
| `EXP-001-official-sac-baseline` | DONE / keep | PART 11.1 的 OFFICIAL_SAC_BASELINE，零旗標。筆電配對比較：vs v4 5%／墜毀 75%／−721，vs v5 10%／90%／+96，cone 0。六對手板 cone 全 0、分差與置中搖桿幾乎相同。地板，永不晉升 |
| `EXP-002-opponent-distribution` | DONE / reject | v6 配方 + 腳本對手進池 + `--league ema`。H2。筆電配對：vs v4 60%／+585／cone+ 0.62（v6 90%／+5,501／2.22），vs v5 75%／+246／0.00（v6 75%／+1,001／1.10）。六對手板：won ≥ v6 只有 3/6、cone 只贏 1 個（vs reference 0.72，全板唯一非零）。不取代 v6；仍是 EXP-003/005/006/007 的比較基準 |
| `EXP-003-mirror` | PLANNED | EXP-002 + `--mirror`。H1；等 EXP-002 有結果再跑 |
| `EXP-004-exploiter` | PLANNED | v6 配方、池只有 v6、`--stop-at-win-rate 0.7`。H3；Dataset 要加 v6 |
| `EXP-005-potential-shaping` | DONE / reject | Kaggle V8：vs v4 65%／−321／cone+ 0.00／best 12°，vs v5 60%／−66／0.23。錐沒動，對 v4 根本沒瞄。跑在位置項修正前（fda84e3），margin 不可比。 EXP-002 配方，`--reward potential`。H4；看 cone+ |
| `EXP-006-frames-observation` | PLANNED | EXP-002 配方，`--observation frames`。H5 |
| `EXP-007-start-geometry` | PLANNED | EXP-002 配方，`--geometry mix`。H6 |
| `EXP-008-doctrine-pool` | DONE / keep | Kaggle：vs v4 **100%、一次擊殺、cone+ 3.00**、錐內平均 0.88 s、5° 內 17.7 s；vs v5 100%／0.37。六對手板：cone reference 2.42／wanderer 1.63（v6 全 0），分差五項贏 v6；**弱點 pursuit 17%**（v6 50%）。新基準、冠軍候選，當天模型要先補 pursuit |
| `EXP-009-wez-start` | DONE / keep（課程階段） | Kaggle：vs v4 95%、一次擊殺、cone+ 3.00、5° 內 21.5 s；vs v5 85%、墜毀 15%、cone+ 0.70。六對手板：cone reference 3.00、wanderer 3.00，**各擊殺 1/6**（bench 上第一次擊殺）；但 pursuit 33%（分差負）、energy 50%。當課程第一階段，接著用 published 幾何續練 |
| `EXP-010-lookahead-observation` | RUNNING（Kaggle） | EXP-008 配方，`--observation lookahead`（預測 1 s／3 s 幾何）。H16 |
| `EXP-011-defensive-starts` | PLANNED | EXP-008 配方，`--geometry defmix`（50% published／25% defensive／25% wezdef）。補 pursuit：v9_doctrine 訓練期對 pursuit 只有 50%，板上 17% |
