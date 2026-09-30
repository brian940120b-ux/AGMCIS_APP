# 官方資料全面 Audit — 怎麼做（2026-09-30）

> **2026-09-30 已完成**：主辦方的檔案上傳到本 session 後逐檔讀完，結果在 `docs/official/`（`README.md` 是索引，`OFFICIAL_AIFCS_REFERENCE.md` 是總報告）。下面保留做法，供日後主辦方更新檔案時重做。

## 先講清楚一件事

主辦方的資料夾「AI飛行員競賽辦法」在**你的桌面**，不在雲端這台機器上，也**不能**放進這個公開 repo。
所以「讀每一頁、讀每一行程式」這一步只能在筆電做。這裡沒有那些檔案，我不會憑記憶或之前的對話
寫 OFFICIAL_* 文件 —— 那正是這份 audit 要禁止的事。

兩條路，選一條：

| 路 | 做法 | 適合 |
|---|---|---|
| **A（最忠實）** | 在筆電開 Claude Code（桌面版或終端機），`cd` 到桌面那個資料夾，把整份 MASTER PROMPT V1.0 貼進去 | 你的筆電有 Claude Code |
| **B（用這個 session）** | 在筆電跑下面的**唯讀抽取工具**，把它產生的文字貼回來，我依實際內容寫 OFFICIAL_* 文件 | 想在同一個對話裡做完 |

工具本身**不改、不搬、不刪、不改名**任何官方檔案；只讀。輸出放在 `official_audit/`，`.gitignore` 已擋住，永遠不會進 git。

## 路 B 的步驟

1. 筆電 Git Bash 或 CMD：
   ```
   cd %USERPROFILE%\AGMCIS_APP\AIFCS
   git pull
   scripts\official_audit.bat "%USERPROFILE%\Desktop\AI飛行員競賽辦法"
   ```
   （Git Bash 用：`scripts/official_audit.bat "$USERPROFILE/Desktop/AI飛行員競賽辦法"`）

2. 成功的樣子：
   ```
   wrote C:\Users\...\AIFCS\official_audit
     files: N
     pdf: N
     python: N
     ...
   ```
   失敗的樣子：`!! ... is not a folder` → 路徑打錯，資料夾名字要跟桌面上一模一樣。
   如果印 `pypdf is not installed`：PDF 沒抽到。要不要裝由你決定（`.venv\Scripts\pip install pypdf`，小套件）；不裝就把 PDF 截圖貼過來。

3. `official_audit/` 裡會有：

   | 檔案 | 內容 |
   |---|---|
   | `INVENTORY.md` | 每個檔案：路徑、大小、類型、sha256、分類**猜測**、有沒有抽出 |
   | `EXTRACT/…txt` | 每個可讀檔案的**全文**（PDF 逐頁標 `===== PAGE n =====`） |
   | `IMAGES/` | PDF 內嵌的圖片（表 3、表 4、圖 4 這種嵌成圖的） |
   | `ARCHIVES.md` | zip 的目錄（只列，不解壓） |
   | `GREP.md` | `socket`、`struct.pack`、`PLAYER_CMD`、port、`jsbsim`… 出現在哪個檔第幾行 |
   | `SUMMARY.md` | 機械計數 + 沒抽出的檔案與原因 |

4. 貼回來的順序（一次一個，大的分段）：
   1. `SUMMARY.md` 和 `INVENTORY.md`
   2. `GREP.md`
   3. `EXTRACT/` 裡的 PDF 全文（公告說明、競賽規則、安裝文件）
   4. `EXTRACT/` 裡的 Python 全文（Host / Player / 訓練環境 / 測試）
   5. `IMAGES/` 裡的表格圖片（直接貼圖）

5. 我拿到之後才寫：`OFFICIAL_COMPETITION_SPEC.md`、`OFFICIAL_DATA_DICTIONARY.md`、`OFFICIAL_PROGRAM_MAP.md`、
   `OFFICIAL_REQUIREMENTS.md`、`OFFICIAL_WORKFLOW.md`、`OFFICIAL_TRACEABILITY.md`、`OFFICIAL_AIFCS_REFERENCE.md`，
   每一條都附檔名 + 頁碼或行號，沒有來源的標 `[UNKNOWN]` / `[NOT VERIFIED]`，衝突標 `[CONFLICT]`。
   這些文件**不引用**官方原文超過必要的片段，也不會把官方檔案本身放進 repo。

## 這裡已經有的、可以先對照的東西（不是官方資料本身）

- `backend/tests/test_competition_spec.py`：2026-09-26 在筆電讀原文後釘住的數字，每條註明是哪一節
- `backend/tests/test_competition_parity.py`：主辦方 `jsbsimEnv.py` 與 client 的幾個函式**原封 vendored** 當 oracle
- `docs/CONFORMANCE.md`：A/B/C/D/E 五級證據表
- `docs/COMPETITION.md`：對真實 HOST 的實測（2026-09-25/26）

Audit 完成後，這些會被 `OFFICIAL_VS_AIFCS_MATRIX.md` 逐條對到官方來源，狀態 MATCH / PARTIAL / MISSING / CONFLICT / NOT VERIFIED。
