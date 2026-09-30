# CHANGELOG

版本標記（brief PART 34）。**標籤的 SHA 寫在這裡**，因為標籤 ref 從雲端環境推不上
遠端（代理在 refs/tags 更新時切斷連線，分支本身推得上去）。有這張表，回滾點不依賴
標籤是否存在於遠端 —— `git checkout <sha>` 永遠有效。

在筆電上重建標籤（若遠端沒有）：

```
git tag -a AIFCS_V1_STABLE 436d372 -m "AIFCS 1.x, last state before the 2.0 work"
git tag -a AIFCS_V2_AUDIT_COMPLETE 33c5be9 -m "PHASE 0 audit accepted; P0 fixed; experiments in place"
git push origin --tags
```

| 標籤 | Commit | 日期 | 意義 |
|---|---|---|---|
| `AIFCS_V1_STABLE` | `436d372` | 2026-09-30 | 2.0 動工前的最後狀態。程式碼與 `016432c` 相同（該 commit 只加了稽核報告）。已知：`competition/trace.py` 遮蔽 stdlib（潛在，當時未觸發）。**回滾點。** |
| `AIFCS_V2_AUDIT_COMPLETE` | `33c5be9` | 2026-09-30 | 稽核被接受；P0 已修（`trace.py` → `roundtrace.py`）；Experiment Manager、來源登錄、`EXP-001` 就位。完整套件 1,107 通過 / 0 失敗。 |

## 2026-09-30 — H4：`--reward potential`（位能差 shaping）

- `rewards.py` 新 `RewardMode.POTENTIAL` / `PotentialReward`：margin + deck 不變，`shaped` 的追蹤項改付 γΦ(s′)−Φ(s)，Φ = 2000 × aim × range_factor（我方減敵方）。測試證明 telescoping（路過六次 = 一次）、待在錐裡 shaping 為零、首幀不付差。tier = research。
- 紀錄 `EXP-005-potential-shaping`：EXP-002 配方只換獎勵；判定看 cone+ 與錐內比例，不看 won。

## 2026-09-30 — H3：訓練中的勝率與 exploiter 停止規則

- `gym_env`：每回合結束 `info["verdict"]` = 表 3 判定（有沒有池都給）。`competition/winrate.py`：`WinRate` 回呼，滾動勝率（整體、每對手）寫進 logger `league/*`；`--stop-at-win-rate RATE --win-window ROUNDS` 到達就停，`SessionState.stopped_at_win_rate` 記下原因、target 設為已達成。預設 None，不影響任何既有訓練。
- 紀錄 `EXP-004-exploiter`：v6 配方、池只有 v6、70% 停。Dataset 要加 v6。

## 2026-09-30 — 訓練模組：對手分佈、EMA 聯賽、鏡像增強（H1、H2）

- **腳本對手進池**：`--opponent-pool` 接受名字（`break` `energy` `scissors` `wanderer` `pursuit` `reference` `level`）與 checkpoint 混用；環境每回合重建名字對應的腳本，和存好的策略共用一個抽樣分佈。`environment.opponent_names()` 是唯一的名單；`evaluate.OPPONENT_NAMES` 由測試鎖定與它相等。Kaggle 腳本的 `split_pool` 只對 session 名字查 Dataset。
- **`--league ema`**（預設仍是 `paper`）：SRC-012 的抽樣器 p = 0.5·均勻 + 0.5·softmax(−EMA/0.3)。發現：PHANG-MAN 規則的閘門（每對手 100 場/worker）在 200 萬步 8 worker 內永遠不會開，之前所有有池的訓練都是均勻抽樣。
- **`--mirror`**（預設關，SAC 限定，PPO 直接拒絕）：`competition/mirror.py` 的 `MirroredReplayBuffer` 每筆經驗存原本與左右鏡像；20/30 維觀測各有一組符號向量，由編碼器本身驗證；動作在縮放空間鏡像，固定的方向舵保持固定。**實測** JSBSim F-16 每幀不對稱 < 0.005° 滾轉（10 秒隨機滿舵後 12°，是混沌放大不是偏差），測試鎖住界線。旗標記進 card 與 session，續練自動繼承。
- 實驗紀錄：`EXP-002-opponent-distribution`（v6 配方 + 池 + ema）、`EXP-003-mirror`（EXP-002 + `--mirror`）。`docs/TRAINING_ROADMAP.md`：逐步做法與判定規則。
- 端到端煙霧測試：SAC + `--mirror` + `--opponent-pool break scissors --league ema` 跑 240 步、存檔、無旗標續練繼承 `mirror=True, league=ema`，buffer 類別為 `MirroredReplayBuffer`、600 筆（300 步 × 2）。

## 2026-09-30 — PART 18 補查：找不到的換路線找

- **發現同題目的公開冠軍程式**：韓國航空大學 2026 AI Pilot Top Gun Challenge（9/17 決賽，290 隊），JSBSim F-16 1v1 純機砲，前段射擊錐 **2° / 500–3,000 ft 與我們相同**；冠軍隊程式已 clone 讀碼（無 LICENSE → 只讀設計不複製）。`research/sources.yaml` 新增 SRC-011～017；SRC-006（改走讀取代理）與 SRC-007（PDF 抽文）升為 verified；Shaw 讀不到，改以美國海軍 T-45 ACM 講義（SRC-016）替代。
- `docs/PRIOR_ART.md` 第五節：規則對照表、冠軍作法對照表、六個可驗證假設（H1 鏡像增強、H2 輸誰多打誰、H3 exploiter、H4 位能差 shaping、H5 座標系觀測、H6 課程初始分佈），全部走 `experiments/`，官方層不動。
- 旁證：冠軍程式把「2 度」寫成 `TIER1_CONE_DEG = 1.0`（半角 1°），與 CONFORMANCE D.1 一致。

## 2026-09-30 — PART 18/19 飛航資料研究

- **新**：`docs/AIRCRAFT_KNOWLEDGE_BASE.md` —— 機體身分（JSBSim F-16A Block-32，源自 NASA TP-1538 1979 公開風洞資料）、幾何、兩顆引擎的差別、HOST 實測初始條件、plant-vs-HOST 10 秒差 3 ft、G 正負與升降舵整形約定、Boyd E-M 只當讀圖語言、未驗證清單。**不引入任何新數據到模型。**
- `research/sources.yaml`：SRC-008 NASA TP-1538（verified，NTRS）、SRC-009 Boyd/Christie/Gibson APGC-TR-66-4（verified，原文 PDF 已核對 Ps 定義）、SRC-010 `f16.xml` 出處（verified）。SRC-004 的來源說法更正：Stevens & Lewis 與 `f16.xml` 同源於 TP-1538，`f16.xml` 引用的是 NASA 論文本身。
- `docs/PRIOR_ART.md` 同一處更正。

## 2026-09-30 — AIFCS 2.0 step 1（`4097529` + `33c5be9`）

- **修**：`competition/trace.py` 更名 `roundtrace.py`，消除 stdlib 遮蔽（同 `profile.py` 事件）。實測 `import trace` 解析回 `/usr/lib/python3.11/trace.py`。
- **修**：8 個 mypy 錯誤（皆在近兩次新增的測試）。mypy 173 檔全綠。
- **新**：`competition/experiments.py` + `experiments/`（PART 15）。
- **新**：獎勵三層標籤 `REWARD_TIERS`（PART 13）。
- **新**：`research/sources.yaml`，7 筆來源含驗證狀態（PART 18.3）。
- **新**：`experiments/EXP-001-official-sac-baseline.yaml`（PART 11.1）。
- **改**：`kaggle/aifcs_train.py` 改讀實驗紀錄，不再硬編 NAME/POOL/FLAGS。
- **新**：`scripts/experiment.bat`、`docs/EXPERIMENT_MANAGER.md`、`docs/RESEARCH_ENGINE.md`。
- 註：`4097529` 只含 7 個檔案（更名、型別修正、experiments.py 與其測試）；`experiments/`、`research/`、文件、Kaggle 腳本與 `.bat` 在 `33c5be9` 才進入版本庫。V2 標籤指向 `33c5be9`。

## 2026-09-30 — PHASE 0 audit（`436d372`）

- `docs/PROJECT_AUDIT.md`：22 項盤點。完整套件 1,107 個測試，1 失敗（即上述 P0）。
