# PHASE 0 — FULL PROJECT AUDIT

稽核日期：**2026-09-30**　距離比賽（2026-11-07/08）**38 天**
稽核環境：雲端容器（Linux、Python 3.11.15、無 GPU），**不是**使用者的筆電
分支：`claude/aifcs-flight-simulation-u32h56`　HEAD：`016432c`　工作目錄乾淨

本階段**只做檢查與規劃，未修改任何程式**。

---

## 最重要的三件事

### 1. 🔴 P0 缺陷：`competition/trace.py` 遮蔽標準庫的 `trace`

完整測試套件（**1,107 個測試，1 失敗 1,106 通過**）跑出來的唯一失敗，而它是嚴重的：

```
FAILED tests/test_competition_timing.py::test_no_module_here_shadows_one_the_standard_library_needs
AssertionError: these shadow the standard library for a script run: ['trace']
```

這是**這個專案發生過一次的同一個病**。`competition/profile.py` 曾經遮蔽標準庫的
`profile`，torch → cProfile → `import profile` 撞上它，訓練整個死掉，錯誤訊息在
`torch._dynamo` 裡面六層深，既不提檔名也不提衝突。那次之後加了這個守門測試。

**守門測試有效，是我繞過了它。** `trace.py` 是我 2026-09-28（commit `1d8c5b8`）加的，
當時我只跑了針對性的測試檔案，沒跑完整套件。

實際重現（模擬 `train.py` 以腳本執行時 `competition/` 進入 `sys.path[0]` 的情況）：

```
import trace  ->  /home/user/.../backend/competition/trace.py
ModuleNotFoundError: No module named 'competition'
```

比測試說的更糟：不只是被遮蔽，**被遮蔽的那個還會 import 失敗**，因為它
`from competition.scoring import ...` 而那條路徑上沒有 `competition` 套件。

| | |
|---|---|
| 嚴重度 | **P0** — 可能重演一次訓練整個掛掉 |
| 修法 | 更名為 `roundtrace.py`，連同 `evaluate.py`、`trace_html.py` 與測試的 import |
| 成本 | 10 分鐘 |
| 狀態 | **已修（2026-09-30，使用者確認進入實作後）**。實際重現驗證：`import trace` 現在解析到 `/usr/lib/python3.11/trace.py` |

### 2. 🟠 8 個 mypy 錯誤，同樣是我引入且沒發現

```
backend/tests/test_competition_rewards.py:175,176  (7)
backend/tests/test_competition_trace.py:37         (1)
```

`**kwargs` 展開進有型別的 dataclass，以及一個鴨子型別的 `_Geometry` 替身。
全在我最近兩次 commit 新增的測試裡，原因一樣：**我跑了 `ruff` 和針對性的
pytest，沒跑 `mypy` 和完整套件。**

**已修（2026-09-30）**：改為明確引數與真正的 `Geometry`，mypy 全綠。

### 3. 🔴 官方稽核（PHASE 1）在這台機器上做不到

主辦方的 PDF、Host 程式、Player 連線程式、Training/Testing 程式、安裝文件
**都不在 repository 裡** —— 這是**正確的**，因為這個 repo 是**公開**的，不能放
主辦方的資料。

搜尋結果：repo 內沒有任何 official / host / player 的主辦方檔案。

**所以 PART 40 的 PHASE 1「OFFICIAL SPECIFICATION AUDIT」必須在你的筆電上做，
不能在這裡做。** 這是規劃上必須先處理的限制。

---

## 1. Project Overview

| | |
|---|---|
| Repository | `brian940120b-ux/AGMCIS_APP`（**公開**） |
| 內含兩個無關系統 | 根目錄 = AGMCIS 加密貨幣交易平台；`AIFCS/` = 本專案 |
| 邊界文件 | `docs/TWO-SYSTEMS.md`（連接埠、venv、資料庫、執行方式全部分離） |
| 本分支 commit 數 | 164（其中 AIFCS 相關 113） |
| Git 標籤 | **無** — master prompt PART 34 要求的版本標記一個都還沒建 |
| 後端 Python | 24,602 行（不含測試） |
| 測試 | 68 個檔案、14,923 行、**1,107 個測試**（本次 1 失敗） |
| 前端 | React + TypeScript + Vite + Three.js + R3F + Zustand |

## 2. Current Architecture

```
AIFCS/
├── backend/
│   ├── competition/   8,138 行  ← 官方介面與比賽用的一切
│   ├── core/                     模擬引擎、設定、run manager
│   ├── simulation/               JSBSim adapter、physics、scenario
│   ├── training/                 pipeline、jobs、registry、environment
│   ├── agents/                   rule agent、commander agent
│   ├── scoring/ analytics/ replay/ storage/ api/
│   ├── main.py cli.py
│   └── tests/        68 檔
├── frontend/          React，2 頁（BootScreen、CommandCenter）+ 23 元件
├── docs/              11 份
├── configs/           5 份 YAML
├── scripts/           27 個（.bat / .sh）
├── kaggle/            雲端訓練
└── plans/             ladder.yaml
```

`competition/` 是官方邊界層，24 個模組。**三個 host-owned 檔案自始至終未改**：
`protocol.py`、`scoring.py`、`state.py`。

## 3. Current File Structure

最大的後端模組（行數）：

```
805  competition/evaluate.py      743  competition/environment.py
681  competition/probe.py         647  training/pipeline.py
628  core/simulation_engine.py    615  competition/train.py
585  core/config.py               562  cli.py
543  training/jobs.py             448  storage/repository.py
```

## 4. Git Status

- 工作目錄**乾淨**，沒有未提交的使用者工作
- 分支：`claude/aifcs-flight-simulation-u32h56`
- **無標籤** — 需要建立 `AIFCS_V1_STABLE` 作為回滾點（PART 34）

## 5. Current Environment

**⚠️ 三個 Python 版本並存，這是最大的環境風險：**

| 位置 | Python | SB3 | 備註 |
|---|---|---|---|
| 官方安裝文件（依 master prompt 轉述） | **3.10** | **2.4.0** | 需在筆電上以原文確認 |
| 本雲端容器 | **3.11.15** | **2.9.0** | torch 2.14.0+cu130、jsbsim 1.3.1、無 GPU |
| 使用者筆電 | **3.14** | 未確認 | 由先前 traceback 得知 |

`requirements*.txt` **全部是 `>=` 範圍，沒有任何 pin**：

```
stable-baselines3>=2.3     torch>=2.4      gymnasium>=0.29
jsbsim>=1.2                numpy>=1.26     fastapi>=0.115
```

**這代表三台機器裝出來的環境幾乎一定不同**，而我們已經被咬過一次
（numpy 新版 stub 用 PEP 695 語法，導致筆電上 mypy 整個停擺）。

## 6. Official Files Inventory

| 項目 | 在 repo 裡？ | 說明 |
|---|---|---|
| 公告說明 PDF（2026/09/18，13 頁） | ❌ | 正確——公開 repo 不放 |
| 附件2 競賽規則（115/09/09） | ❌ | 同上 |
| Host Connection Program | ❌ | 同上 |
| Player Connection Program | ❌ | 同上 |
| 官方 Training / Testing 程式 | ❌ | 同上 |
| 環境安裝文件 | ❌ | 同上 |

**但內容已經被萃取進 repo**：`test_competition_spec.py` 把公告裡每個數字連同
它出自的條款釘在測試裡；`test_competition_parity.py` 把主辦方的函式**原封不動
vendored** 進來當 oracle。

## 7. Official Compatibility Report

**這一塊比 master prompt 預期的成熟很多。** `docs/CONFORMANCE.md` 已經是一份
帶證據等級的相容性矩陣：

### A 級 — 對真實 HOST 實測（2026-09-25，兩回合，33,090 幀）

| 項目 | 實測 | |
|---|---|---|
| OBS 封包 208 bytes / 26 double | 33,090 幀，**0 格式錯誤、0 NaN** | ✅ |
| CMD 封包 30 bytes | HOST 全部接受 | ✅ |
| 更新率 60 Hz | 符合 | ✅ |
| 握手 `player_state` 0→1→2 | 符合 | ✅ |
| 初始速度 | 339.9 KCAS / Mach 0.673 @ 19,116 ft | ✅ |
| 回合邊界（位置凍結） | 6,729 幀完全不動，兩回合都偵測到 | ✅ |
| **決策預算 16.67 ms** | 平均 **0.20 ms**、最差 **1.11 ms** | ✅ 用掉 **1.2%** |

### B 級 — 對主辦方原始碼差分測試

20 維狀態編碼、死區、指數曲線、每幀變化上限、Mach 0.8 升降舵限制，**逐點相等**。

文件並記錄了**主辦方自己兩份程式不一致**之處（高速升降舵限制、Mach 怎麼算、
double/float32），以及我們選了哪一邊和理由。

### C 級 — 對原文逐字核對

`S_kill`、`S_advantage`、權重、`P(t)`、`TA_norm`、距離因子分段，全部吻合。

**結論：OBS / CMD / UDP / 握手 / 決策預算 = PASS（A 級證據）。**
這是整個專案最硬的部分，**不應該動它**。

## 8. OBS / CMD / UDP Report

| | 實作 | 測試 |
|---|---|---|
| OBS 解碼 | `competition/protocol.py`（132 行） | spec + parity + client 測試 |
| CMD 編碼 | 同上 | 同上 |
| 搖桿整形 | `competition/action.py` | parity（逐點相等） |
| UDP runtime | `competition/client.py`（432 行） | `test_competition_client.py` |

**缺口：沒有 `tests/golden/` 目錄。** master prompt PART 7 要求的 golden packet
檔案不存在。目前的等價物是 parity 測試（用主辦方函式當 oracle，隨機輸入比對），
**強度其實更高**，但少了「固定的官方樣本封包 + 預期解碼結果」這種可追溯的檔案。

## 9. Host / Player Integration Report

`docs/COMPETITION.md`（1,087 行）記錄了完整的對接流程與三個只有真實 HOST 能回答
的問題，且三個都已在 2026-09-25 得到答案。

**未驗證項目**：測試用 HOST 的初始距離與航向是**隨機**的（3,295 ft / 340°，
4,850 ft / 55°），不是規格寫的 3000/6000/9000。該 readme 標題是「民眾公告版」，
所以**不能**用來驗證回合初始設定。→ 標記 **UNKNOWN**。

## 10. Training / Testing Report

| | 狀態 |
|---|---|
| 自研訓練 | `competition/train.py` + `gym_env.py` + SAC，可續跑、可停、有 session 狀態 |
| 雲端訓練 | `kaggle/` — bootstrap + 一鍵，7.5 小時預算 |
| 對手 | 內建 3 個 + 腳本 4 個（`adversaries.py`，2026-09-30 新增） |
| **官方 SAC baseline** | ❌ **不存在** — master prompt PART 11.1 要求的 `OFFICIAL_SAC_BASELINE` 沒有建立 |
| 官方 Testing 流程 | ❌ 未整合 |

## 11. Model Registry Report

`backend/training/registry.py`（349 行）存在，有相容性檢查。

**但**：
- 比賽用的模型（v4–v7p）走的是 `competition/session.py` 的 card.json，**沒有進 registry**
- 沒有 master prompt PART 14 的狀態機（TRAINING → … → COMPETITION_READY）
- 沒有 promotion gate（`promotion` 在程式碼裡 0 個檔案提及）
- `models/*` 被 gitignore → **雲端這台機器上 `models/competition/` 是空的**，模型只在筆電

## 12. Evaluation / Replay Report

| | 狀態 |
|---|---|
| 評估 | `competition/evaluate.py`（805 行）— 勝率/擊殺/分差/錐內秒數/瞄準速率 |
| 固定計分板 | `competition/scoreboard.py`（2026-09-30 新增）— 六個固定對手 |
| 逐幀軌跡 | `competition/trace.py` + `trace_html.py`（2026-09-30 新增）⚠️ 見 P0 |
| 平台 replay | `backend/replay/`（recorder/reader/player/format）存在 |
| **ACMI / Tacview** | ❌ **完全沒有** — 全 repo 0 個檔案提及 |

## 13. Frontend / API Report

**API**：10 個 router（health, simulation, agents, coordination, physics,
analytics, replay, scenarios, training, telemetry）。
**缺**：competition、experiment、research、dataset、model registry 的端點。

**前端**：只有 **2 頁**（`BootScreen.tsx`、`CommandCenter.tsx`）+ 23 個元件。
master prompt PART 30 列的 14 頁，目前是**一頁 command center**。
9 個 e2e 腳本（`frontend/e2e/*.mjs`）。
**沒有 Tailwind、沒有 Recharts** — 圖表是 `src/charts` 自刻的。

## 14. Research Capability Report

| master prompt 要求 | 狀態 |
|---|---|
| Public Research Engine | ❌ 不存在 |
| Aircraft Knowledge Base | ❌ 不存在（`knowledge_base` 0 個檔案） |
| Dataset Manager | ❌ 不存在（`dataset` 0 個檔案） |
| Source Management | ❌ 不存在 |
| 研究成果 | ✅ **有**，但是文件形式：`docs/PRIOR_ART.md` 已整理 AlphaDogfight、BVR Sim、LAG、PSRO 與書單 |

## 15. Existing Features to Preserve（KEEP，不要動）

1. **`protocol.py` / `scoring.py` / `state.py`** — host-owned，A/B 級證據
2. **`test_competition_parity.py`** — 主辦方函式當 oracle，41 測試
3. **`test_competition_spec.py`** — 每個數字連同出處
4. **`competition/client.py`** — 對真實 HOST 實測過 33,090 幀
5. **`docs/CONFORMANCE.md` / `RULES.md` / `COMPETITION.md`** — 已是高品質的相容性稽核
6. **`competition/action.py`** — 搖桿整形，逐點相等
7. **守門測試**（stdlib shadow、script encoding）— 它們**有效**，剛抓到我的 bug

## 16. Technical Debt

| # | 項目 | 嚴重度 |
|---|---|---|
| 1 | ~~`competition/trace.py` 遮蔽 stdlib~~ 已更名 `roundtrace.py` | ~~P0~~ 已修 |
| 2 | ~~8 個 mypy 錯誤（新測試）~~ | ~~P1~~ 已修 |
| 3 | 依賴無 pin，三個 Python 版本並存 | P1 |
| 4 | 無 git 標籤 / 回滾點 | P1 |
| 5 | 比賽模型不在 model registry 裡 | P2 |
| 6 | 沒有 `tests/golden/` 官方樣本封包 | P2 |
| 7 | 沒有官方 SAC baseline | P2 |
| 8 | ACMI / Tacview 完全缺席 | P2 |
| 9 | 前端只有 1 頁 command center | P3 |
| 10 | `competition/evaluate.py` 805 行，職責過多 | P3 |

## 17. Compatibility Risks

| 風險 | 證據 | 影響 |
|---|---|---|
| **環境漂移** | 3 個 Python、SB3 2.4 vs 2.9、依賴無 pin | 筆電能跑、Kaggle 能跑、當天那台不一定 |
| **回合初始設定 UNKNOWN** | 測試 HOST 用隨機距離/航向 | 訓練的初始條件可能與當天不符 |
| **60 Hz 全程未彩排** | A 級證據只有兩回合 | 掉包、重連、第一幀時序未測 |
| **官方 Testing 流程未整合** | 未做 | 模型當天載不載得起來未驗證 |

## 18. Missing Tests

- `tests/golden/`（官方樣本封包）
- 長時間 UDP 穩定性 / 掉包 / 重連
- 官方 Testing 流程端到端
- Performance regression（啟動時間、推論延遲、封包處理）
- ACMI 輸出

## 19. Recommended Architecture

**不建議大規模重構。** 理由：

1. 官方邊界層已有 A 級證據，動它是純風險
2. 距比賽 38 天
3. 現有 24 個 competition 模組職責清楚
4. master prompt PART 9 的分層，現有結構**已經大致符合**，只是名字不同

建議**只做三件事**：`competition/` 保持不動（除了 P0 更名）；補缺的模組
（golden、official baseline、ACMI）；其餘標記為賽後項目。

## 20. Migration Plan

見下方「建議執行順序」。

## 21. Risk / Rollback Plan

**動任何東西之前**：建立 `AIFCS_V1_STABLE` 標籤。目前**沒有任何標籤**，
等於沒有明確的回滾點。

## 22. Phase-by-Phase Implementation Plan

| 階段 | 內容 | 在哪做 | 估時 |
|---|---|---|---|
| **P0-FIX** | `trace.py` 更名 + 8 個 mypy 錯誤 + 建 `AIFCS_V1_STABLE` 標籤 | 雲端 | 1 小時 |
| **A** | 環境 pin + `aifcs doctor` 擴充成真正的 Environment Validator | 雲端 → 筆電驗證 | 半天 |
| **B** | PHASE 1 官方稽核（**只能在筆電做**）：讀官方安裝文件、確認 Python/SB3 版本、建 `tests/golden/` | **筆電** | 1 天 |
| **C** | 官方 SAC baseline 保存 + 官方 Testing 流程整合 | **筆電** | 1 天 |
| **D** | ACMI 輸出 + Replay 關聯 | 雲端 | 1 天 |
| **E** | Model Registry 狀態機 + promotion gate，把 v4–v7p 登錄進去 | 雲端 | 1 天 |
| **F** | 60 Hz 全程彩排（300 秒 × N 回合，量抖動/掉包/重連） | **筆電 + 官方 Host** | 半天 |
| **G** | 前端擴充（Evaluation / Model / Replay Center） | 雲端 | 2–3 天 |
| **H** | Research Engine / Knowledge Base / Dataset Manager | 雲端 | 賽後 |

**與此同時，訓練不停**：v7p 已在 Kaggle 跑完，該下載評分。

---

## 需要你決定的事

1. **P0 是否現在修？**（`trace.py` 更名，10 分鐘，我建議立刻做）
2. **階段 B / C / F 必須在筆電上做**，因為官方檔案不在這裡。你要先做哪一個？
3. **前端要不要擴充？** 它不影響比賽成績，但影響評審對「平台」的印象。
4. **Research Engine 是否列為賽後？** 我建議是 —— 38 天內它不會改變成績。
