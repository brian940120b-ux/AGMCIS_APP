# AGMCIS Roadmap

## 已完成:Phase 0 - 17

| Phase | 主題 | 報告 |
|---|---|---|
| 0 | 專案稽核 | `docs/AUDIT_REPORT.md` |
| 0.5 | 止血:風控 hard gate、靜默失敗、損益基準 | `docs/PHASE_0.5_REPORT.md` |
| 1 | 套件化、設定集中、排程器收斂 | `docs/PHASE_1_REPORT.md` |
| 2 | 市場資料層與品質 Gate | `docs/PHASE_2_REPORT.md` |
| 3 | BingX 整合、錯誤分類、限流 | `docs/PHASE_3_REPORT.md` |
| 4 | Trading Rules Engine | `docs/PHASE_4_REPORT.md` |
| 5 | Risk Engine、部位大小、波動度槓桿、Kill Switch | `docs/PHASE_5_REPORT.md` |
| 6 | 統一訊號管線、做空支援 | `docs/PHASE_6_REPORT.md` |
| 7 | 回測引擎(修掉前視偏誤、成本、強平) | `docs/PHASE_7_REPORT.md` |
| 8 | Strategy Lab:OOS、Walk Forward、Monte Carlo | `docs/PHASE_8_REPORT.md` |
| 9 | Multi-Agent:12 Agents + Consensus + Supervisor | `docs/PHASE_9_REPORT.md` |
| 10 | Paper Trading 2.0:成本、資金費用、強平 | `docs/PHASE_10_REPORT.md` |
| 11 | 合約規格校準 | `docs/PHASE_11_REPORT.md` |
| 12 | Execution Engine:狀態機、裸倉保護 | `docs/PHASE_12_REPORT.md` |
| 13 | 對帳:訂單持久化、狀態不明訂單 | `docs/PHASE_13_REPORT.md` |
| 14 | 透明度面板 | `docs/PHASE_14_REPORT.md` |
| 15 | 績效歸因與自我檢討 | `docs/PHASE_15_REPORT.md` |
| 16 | Production Safety | `docs/PHASE_16_REPORT.md` |
| 17 | LIVE SAFETY GATE | `docs/PHASE_17_REPORT.md` |

## 已完成:Master Prompt 106 節逐節補齊

Phase 0–17 之後,把 Master Prompt 全部 106 節重新對照了一次,
把 12 個「完全沒做」的節補完。對照表在
[`docs/MASTER_PROMPT_COMPLIANCE.md`](docs/MASTER_PROMPT_COMPLIANCE.md),
目前是 **84 已做到 / 22 部分做到 / 0 沒做**。

| 節 | 補了什麼 |
|---|---|
| 十八 | SL 失敗六步驟緊急保護(先重試、再縮倉、才平倉、停新單、通知) |
| 二十 / 五十九 / 六十 | 組合相關性風險與週虧損上限 |
| 四十六 | SAFE LIVE MODE(只能收緊不能放寬) |
| 五十一 | 重大事件時間窗封鎖 + 未排程衝擊偵測 |
| 七十三 ~ 七十六 | 策略生命週期、回撤自動停用、績效與市況漂移偵測 |
| 五十五 ~ 五十八 | 分批停利、移到成本、ATR / 結構型移動停損、時間出場 |
| 六十四 / 六十九 ~ 七十一 | 決策持久化與「為什麼開這一單」 |
| 六十五 / 六十六 | 稽核與不需金鑰的 `/health` |
| 三十三 / 三十七 | 回測 Partial Fill / Partial Close / Trailing,成本敏感度 |
| 二十三 ~ 二十五 / 三十八 | 市場結構、MTF 階層、PANIC 市況、五個新策略 |
| 八十六 ~ 九十二 / 一百零五 | 新首頁、模式徽章、Agent 面板、TOP 機會與 WHY |
| 四十一 / 六十七 / 八十一 / 八十二 / 八十五 | 完整日誌、模擬測試、環境分離、Docker、API 補齊 |

測試從 969 增加到 1415。

---

## 需要人操作才能繼續

### 在 VPS 上(需要 API 金鑰)

```bash
.venv/bin/python scripts/migrate.py                    # 套用 migration 003-008
.venv/bin/python scripts/verify_bingx.py               # 唯讀連線驗證
.venv/bin/python scripts/verify_bingx.py --write-specs # 合約規格快照
.venv/bin/python scripts/calibration_report.py         # 確認校準
.venv/bin/python scripts/run_strategy_lab.py           # 在真實資料上跑驗證
.venv/bin/python scripts/preflight.py                  # 上線前檢查
.venv/bin/python scripts/live_gate.py                  # 實單閘門狀態
```

在校準完成之前,強平價與成本都是估計值。

`run_strategy_lab.py` 是現在最重要的一支:ROADMAP 的第一項待辦
「沒有策略通過完整驗證」不是程式的問題,是缺真實資料。
開發容器連不到 BingX(網路政策擋掉),所以只能在 VPS 上跑。

### Phase 18 - 20

這三個階段需要真錢與人的確認,**不會由 AI 代為執行**:

- **Phase 18 小額實單** —— 需要通過 LIVE SAFETY GATE(`scripts/live_gate.py`),
  而那需要一份你手動簽署的確認檔。
- **Phase 19 監控** —— 實單跑起來之後才有東西可以監控。
- **Phase 20 放大規模** —— 需要 Phase 18/19 累積足夠的實際資料。

---

## 已知的待辦

依重要性排序:

1. **live 訊號管線還沒在真實資料上驗證過。**
   結構性的阻礙已經排除:分頁抓取讓 K 棒數不再受單次請求上限限制。
   合成資料上 1200 根時樣本外 28 筆(REJECT),3000 根時 75 筆(PASS)。
   **那個 PASS 沒有任何意義** —— 合成的波浪資料本來就可預測。
   真實資料上會是什麼結果,只有在 VPS 上跑過才知道。
2. **實單路徑(LiveBroker)不存在。** 刻意的 —— 要寫之前必須單獨審視。
3. **部分成交的實盤路徑仍未驗證。** 處理邏輯與撤單流程已就位並有測試,
   但模擬盤一律全額成交,所以沒有真的走過一次。
4. **停損在模擬盤不是交易所掛單。** 實盤要處理「進場成交但停損單被拒」。
5. **Survivorship bias 還在。** 需要含已下市標的的歷史資料。
6. **舊的 Dashboard Lite 還是一個巨大的 f-string。**
   它已經不是首頁了(移到 `/dashboard`),但還沒重寫。
7. **實單訂單型別只走 MARKET。** LIMIT / STOP / TRAILING_STOP 的型別
   定義好了,執行層還沒用。沒有 LiveBroker 之前也無從驗證。
8. **加倉與反手沒有實作。** 兩者都會改變一個已存在部位的風險,
   而目前的風控是為「開新倉」設計的。要做之前風控要先想清楚。
9. **TP/SL 的參數還沒經過回測驗證。** 1R/2R/3R 與 30/30/40 是起點,
   不是結論 —— 第五十六與五十七節都要求由回測決定。

### 已修掉

- ~~`position_monitor` 用輪詢的單一價格而非 high/low~~ ——
  改成看輪詢間隔內的 1m K 棒 high/low,規則與回測引擎一致
  (含跳空時成交在開盤價、同根同時觸及時假設停損先到)。
- ~~`rate_limit_calls` 沒有自動清理排程~~ —— 已加入排程工作。
- ~~單次請求的 K 棒上限擋住樣本數~~ —— `agmcis/data/history.py` 分頁抓取,
  磁碟快取,**缺口偵測但不補值**(補值會製造一段假歷史)。
- ~~Lab 驗證的是永遠不會下單的策略~~ —— Lab 原本只驗 `strategies/*.py`,
  但 live 走的是 `agmcis/strategy/builtin.py` 的策略集成。
  現在 `LIVE_PIPELINE` 一起驗證並排在第一,**LIVE SAFETY GATE 只認它**。
  舊策略保留為對照。
- ~~設定變更沒有稽核~~ —— 加上設定快照比對,風控參數與成本假設被放寬時
  會警告並浮到透明度面板。**但它答不出「是誰改的」**:設定來自環境變數,
  環境變數沒有作者。
- ~~維持保證金分層沒有處理~~ —— `ContractSpec` 支援分層,強平價依名目價值
  選層。**分層資料仍需在 VPS 上擷取**(API 給不出來時要依官方文件手動補),
  校準報告會逐一列出還缺分層的標的。
