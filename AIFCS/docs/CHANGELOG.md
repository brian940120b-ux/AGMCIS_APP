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
