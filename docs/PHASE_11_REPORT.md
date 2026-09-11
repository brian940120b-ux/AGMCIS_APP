# PHASE 11 — BingX Test Validation(合約規格校準)

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:系統知道自己哪些數字是真的、哪些是猜的,而且說出來。** 已達成。
**未完成(需要在 VPS 上執行):實際擷取快照、以真實私有端點驗證。** 見第 6 節。

---

## 1. 問題

Phase 7 與 Phase 10 裡有幾個數字是我憑常識填的,不是 BingX 給的:

| 數字 | 我填的值 | 它決定什麼 |
|---|---|---|
| 維持保證金率 | 10% | **強平價** |
| Taker 費率 | 0.05% | 每筆交易的成本 |
| Maker 費率 | 0.02% | 同上 |
| 資金費率 / 8h | 0.01% | 持倉成本 |

BTC 永續在 BingX 的實際維持保證金率是**千分之幾**的等級,不是 10%。
用 10% 算出來的強平價會比真實的近很多。保守方向沒錯,但那是**估計**,
不是規格 —— 而 Master Prompt 第五節講得很直接:
**不要靠模型記憶猜 API**。

---

## 2. 做法

```
agmcis/exchange/specs.py       規格快照的載入、查詢、過期判定、校準報告
scripts/verify_bingx.py        加上 --write-specs(全程唯讀)
scripts/calibration_report.py  現在有多少數字是真的
```

流程:

1. **在 VPS 上**跑 `verify_bingx.py --write-specs`,把交易所實際回應寫成
   `data/bingx_specs.json`。
2. 系統載入那份快照,費率與維持保證金率優先用真實值。
3. **沒有快照時不會安靜地用猜測值。**

---

## 3. 關鍵設計:取值一定帶來源

```python
value, source = store.maintenance_margin_ratio("BTC/USDT")
# -> (0.004, "EXCHANGE")   或   (0.10, "DEFAULT_GUESS")
```

呼叫端**拿不到一個匿名的數字**。這樣就沒辦法把猜測值當成交易所給的值用,
而報告與日誌也永遠說得出「這個數字哪來的」。

`calibration_report()` 把它整理成一份上線前必看的清單:
哪些標的校準過、哪些欄位還是猜的、快照多久沒更新、是不是從測試網抓的。

四種情況都會被講出來,不會被吃掉:

| 情況 | 行為 |
|---|---|
| 沒有快照檔 | 警告 + 全部標記 DEFAULT_GUESS |
| 有快照但缺這個標的 | 該標的全部退回預設並標記 |
| 有標的但缺某個欄位 | 只有那個欄位退回預設並標記 |
| 快照 JSON 壞掉 | **拋例外**,不是當成「沒有快照」 |

最後一條是刻意的:壞掉的檔案被當成「沒有檔案」,系統會用猜測值一路跑下去,
而沒有人發現檔案壞了。

---

## 4. 滑點與點差不在校準範圍

`slippage_pct` 與 `spread_pct` **一律用設定值**,不從快照讀。

那兩個是市場衝擊的估計,交易所不會告訴你,也沒有「正確答案」——
它們取決於下單大小、當下的深度與行情。把它們列進「已校準」會是假的。

---

## 5. 順手抓到一個無聲的錯

Phase 9 的 `build_context()` 我寫成 `funding.get("rate")`,
但 adapter 產生的欄位名是 `funding_rate`。

症狀是:**`FundingAgent` 永遠拿到 `None`、永遠棄權,而且完全不報錯。**
一個 Agent 靜靜地退出系統,整整一個 Phase 沒人知道。

修好之後加了兩個測試釘住:一個比對 adapter 與 pipeline 的欄位名字串,
一個直接驗證 FundingAgent 在極端費率下會投票 —— 因為接錯的症狀就是「永遠棄權」,
只測「不拋例外」抓不到。

---

## 6. 必須在 VPS 上做、這裡做不到的部分

這個容器沒有 BingX 金鑰,也**不應該有** —— API Secret 不得進入對話、
log、程式碼或 AI prompt(Master Prompt 第十、八十四節)。

所以以下留給在 VPS 上執行:

```bash
cd /root/AGMCIS_APP
.venv/bin/python scripts/verify_bingx.py            # 唯讀連線驗證
.venv/bin/python scripts/verify_bingx.py --write-specs   # 產生規格快照
.venv/bin/python scripts/calibration_report.py      # 確認校準結果
```

三件事在那之後才會有答案:

1. **私有端點**(餘額、持倉、槓桿、持倉模式)能不能正常回應。
2. **實際費率**(依帳戶 VIP 等級而異)。
3. **維持保證金率**。BingX 的 API 不一定給得出分層表 ——
   給不出來時快照會**留空**,系統繼續標記為猜測值。
   填一個看起來合理的數字進去,會讓「已校準」這件事變成謊話,
   所以腳本刻意不那樣做,而是在 notes 裡寫明需要依官方合約分層文件手動補入。

`data/bingx_specs.json` 不進版控:不同帳戶費率不同,而且它是可重新產生的資料。

---

## 7. 測試

```
全部:675 passed
本階段新增:tests/test_specs_calibration.py  19
```

重點都在「沒有真值的時候會不會誠實」:

- `test_every_value_is_tagged_as_a_guess`
- `test_a_field_missing_from_a_present_symbol_also_falls_back`
- `test_unreadable_json_raises_instead_of_falling_back_quietly`
- `test_a_testnet_snapshot_is_flagged`
- `test_the_liquidation_price_uses_the_real_maintenance_margin_ratio`
  —— 真實的 0.4% 與猜測的 10% 算出來的強平價差很多,這個差必須真的存在

還有一條守住唯讀:`test_the_script_never_calls_a_write_endpoint`,
用 AST 掃描 `verify_bingx.py`。那支腳本在**有金鑰的機器**上跑,
絕不能出現任何寫入交易所的呼叫。

---

## 8. 尚未接上的部分

- **回測引擎仍用固定的 `MAINTENANCE_MARGIN_RATIO = 0.10`。**
  `BacktestEngine` 已經接受這個參數,但還沒有自動從快照帶入 ——
  回測是跨標的跑的,要接需要一個「每個標的各自的 mmr」的介面。
- **維持保證金分層**(倉位越大、維持保證金率越高)完全沒有處理。
  目前是單一數字。大倉位的強平價會被低估。
- **實際下單路徑尚未在 VST 上驗證。** 那是 Phase 12 的 Execution Engine
  完成之後的事,而且進入實單前還有 Phase 17 的 LIVE SAFETY GATE。

---

## 9. 下一步

Phase 12 — Execution Engine:Order State Machine、強制停損保護
(開倉成功但停損單沒掛上去 = 裸倉,必須立刻處理)、Exit Agent 實際執行出場。
