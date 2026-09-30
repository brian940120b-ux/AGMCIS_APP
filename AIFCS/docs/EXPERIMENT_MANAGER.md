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
- 同一個 ID 不能宣告兩次；`result` 的 decision 只接受三個值。

## 目前的紀錄

| ID | 狀態 | 內容 |
|---|---|---|
| `EXP-001-official-sac-baseline` | PLANNED | PART 11.1 的 OFFICIAL_SAC_BASELINE，零旗標 |
