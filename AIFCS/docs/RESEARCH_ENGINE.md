# Research Engine — 最小可驗證版本（brief PART 18）

## 現況

**這不是一個引擎，是一個登錄簿加一條規則。** 38 天內做完整的 Research Engine
不會改變比賽成績，所以先做能驗證的最小版本：

```
research/sources.yaml     每個真的讀過的來源一筆，含驗證狀態
docs/PRIOR_ART.md         整理後的結論與排序過的待辦
experiments/              來源 → 假設 → 實驗紀錄
```

## 規則（PART 18.2）

```
來源 → 驗證狀態 → 適用性 → 假設 → experiments/<ID>.yaml → 訓練 → 固定計分板 → 決定
```

**任何來源都不能直接改官方程式、官方規格或比賽規則。** 來源只能餵給一筆實驗紀錄，
只有實驗結果能動模型。

## 驗證狀態的意思

| 狀態 | 意思 | 能不能引用數字 |
|---|---|---|
| `verified` | 讀過原文 | 可以 |
| `abstract-only` | 只讀到摘要 / 二手整理 | 只能引用方向，不能引用數字 |
| `unreachable` | 找到了但抓不到全文 | **不行**，直到有人讀過 |

## 目前的登錄（2026-09-30）

| ID | 來源 | 驗證 | 適用性 |
|---|---|---|---|
| SRC-001 | PHANG-MAN / AlphaDogfight（arXiv 2105.00990） | verified | **直接**：主辦方的射擊區就是它的 WEZ |
| SRC-002 | BVR Sim（arXiv 2608.25419） | verified | 部分：對手集設計 |
| SRC-003 | Shaw, *Fighter Combat* | abstract-only | 直接：戰術先驗 |
| SRC-004 | Stevens & Lewis | abstract-only | 部分：理解飛機 |
| SRC-005 | DARPA ACE 新聞稿 | verified | **低**：無技術內容 |
| SRC-006 | Drones 2025 階層式 + 自動課程 | **unreachable**（403/405 三次） | 未知 |
| SRC-007 | Self-Play PSRO | abstract-only | 部分 |

SRC-006 是目前最值得在筆電上抓來讀的一篇：標題直指我們的問題（WVR、階層式、
自動課程、有消融實驗），但從雲端三條路都抓不到全文，所以**它的任何數字現在都不能用**。

## 從來源到實驗

| 來源 | 產生的假設 | 實驗 |
|---|---|---|
| SRC-001 陡峭錐邊獎勵 | 加 `pointed` 項 | v7p — **reject**（cone+ 2.22 → 0.33） |
| SRC-001 課程順序 | 先簡單腳本對手，過 50% 才加難的 | 尚未建立紀錄 |
| SRC-001 WEZ 樣本比例 | 初始條件偏向射擊區 | 尚未建立紀錄 |
| brief PART 11.1 | 官方配方基準 | **EXP-001**（PLANNED） |
