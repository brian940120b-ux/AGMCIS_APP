# OFFICIAL COMPETITION KNOWLEDGE BASE — 索引

> 純官方資料模型。這個資料夾裡的每一條陳述都必須能回溯到主辦方提供的檔案；
> AIFCS 的設計、獎勵、觀測、架構**不在**這裡（只在 `OFFICIAL_VS_AIFCS_MATRIX.md` 的右欄出現）。
> Audit 日期：2026-09-30。主辦方檔案**不在**這個公開 repo 裡，只有引用。

## 狀態標記

| 標記 | 意思 |
|---|---|
| `[VERIFIED]` | 官方檔案明文支持，附檔名 + 頁/行 |
| `[PARTIAL]` | 只有部分資訊 |
| `[NOT VERIFIED]` | 找到了但沒能在這次 audit 驗證（例：二進位 exe 的內部行為） |
| `[UNKNOWN]` | 官方資料沒有說 |
| `[CONFLICT]` | 不同官方來源互相矛盾 |
| `[INFERRED]` | 由官方資料推論，不是明文 |

## 官方來源階層（OFFICIAL SOURCE HIERARCHY）

| 級 | 來源 | 檔案 | 版別 |
|---|---|---|---|
| 1 | 正式競賽規則／公告 | `AI空戰飛行競賽公告文件_0918.pdf`（14 頁，以下簡稱 **公告**） | 版別 2026/09/18 |
| 1 | 活動辦法附件 2 競賽規則 | `2026 AI空戰競賽活動辦法_1150909.pdf`（1 頁，**附件2**）— 規則條文「依公告文件之規定辦理」 | 115/09/09 |
| 2 | 主辦方技術文件 | `0.AI空戰資料說明文件.pdf`（NCSIST-AIPilot-**01**）、`2.比賽用參賽方連線程式.pdf`（**03**）、`3.比賽用主辦方連線程式.pdf`（**04**）、`4.訓練階段軟體訓練程式.pdf`（**05**）、`5.訓練階段軟體測試程式.pdf`（**06**） | 版別 2026/09/03 |
| 3 | 主辦方提供程式 | `AirCombat_Train_Test/`：`envs/jsbsimEnv/{jsbsimEnv.py, jsbsimFdm.py, utils/utils.py}`、`train.py`、`test.py`、`player1_Loadmodel.py`、`D.…/JSB_host_GUI_publish.exe`、`Setting.txt` | zip 內時間戳 2026-08-25～09-03 |
| 4 | 安裝文件 | `1.從0開始AI飛行員-環境安裝.pdf`（**02**） | 版別 2026/09/03 |
| 5 | 範例程式 | 同第 3 級（主辦方自己稱之為「範例」） | — |
| 6 | README／說明文字 | `D.…/readme_part1.txt`、`readme_part2.txt`、`aircraft/f16/README` | 2026-08-27 |
| 7 | 其他 | `JSBSimRecording.txt.acmi`（範例輸出）、`player2_runtime.log`（主辦方測試時的 log）、`model/jsbsim_sac_314400000_steps.zip`（範例模型） | — |

頁碼慣例：**公告** 引用印在頁面上的「第 k 頁，共 13 頁」的 k（PDF 第 k+1 頁）；指引文件引用印上的「第 k 頁」。程式引用 `檔名:行號`。

## 文件清單

| 檔案 | 內容 |
|---|---|
| `OFFICIAL_FILE_INVENTORY.md` | 55 個檔案的清單、類型、sha256、是否已讀 |
| `OFFICIAL_COMPETITION_SPEC.md` | 規格總表（Competition → Conflicts） |
| `OFFICIAL_DATA_DICTIONARY.md` | OBS 26 欄、CMD 6 欄，逐欄附來源 |
| `OFFICIAL_PROGRAM_MAP.md` | 每個官方程式：用途、輸入輸出、依賴、執行方式 |
| `OFFICIAL_REQUIREMENTS.md` | REQUIRED / RECOMMENDED / OPTIONAL / PROVIDED / UNKNOWN |
| `OFFICIAL_WORKFLOW.md` | 八個流程（安裝、訓練、測試、Player、Host、UDP、比賽、重播） |
| `OFFICIAL_TRACEABILITY.md` | 每條需求 → 來源 → 頁/行 → 狀態 |
| `OFFICIAL_AIFCS_REFERENCE.md` | **總報告** A–T，含 Unknowns、Conflicts、34 題 Summary |
| `OFFICIAL_VS_AIFCS_MATRIX.md` | 官方需求 vs AIFCS 實作，MATCH / PARTIAL / MISSING / CONFLICT / NOT VERIFIED |
