# PHASE 16 — Production Safety

完成日期:2026-09-11
分支:`claude/resume-session-0mcy96`

**完成判準:緊急按鈕真的會動、限流跨行程成立、上線前有一個檢查指令。** 已達成。

---

## 1. Kill Switch 之前按下去不會平倉

Phase 5 建了 kill switch 的骨架,但 `order_canceller` 與 `position_closer`
一直是 `None`。`panic()` 會記錄「未接上平倉能力,部位沒有被平掉」然後結束。

**一個按下去不會平倉的緊急按鈕,比沒有按鈕更危險:你會以為自己按過了。**

現在 `build_wired_kill_switch()` 把平倉接到 Execution Engine。
理由跟 Phase 12 一樣:不要在系統裡長出第二條平倉路徑。

平倉失敗時 closer **拋例外**,讓 `panic()` 把它記進 `errors`。
安靜地當作成功,報告會說「已平倉」而部位還在。

撤單**沒有接**,而且 `status()` 據實回報 `can_cancel_orders = False`。
模擬盤沒有掛在市場上的單,假裝有這個能力比沒有更糟。

---

## 2. 跨行程限流:用 PostgreSQL,不加 Redis

`RateLimiter` 一直是**行程內**的。scheduler、web、telegram 三個 process
共用同一把 API Key,但各有自己的額度 ——
設定 100 次 / 10 秒,實際上會打到 300 次 / 10 秒。

原本的註解寫「需要 Redis,等 Phase 16」。改用 PostgreSQL:

- 資料庫已經是必要相依。再加 Redis 等於多一個會壞、要監控、要備份的元件。
- 限流的寫入量完全在 Postgres 的能力內。
- 少一個「只有這一件事需要」的基礎設施。

冷卻期也共用:一個 process 被限流了,其他 process 繼續打只會讓封鎖時間變長。

### 共用狀態拿不到時怎麼辦

這是這一階段最重要的決定。兩個選擇:

| 選擇 | 後果 |
|---|---|
| (a) 退回行程內限流 | 系統繼續跑,但限流變鬆 |
| (b) 擋住所有請求 | 安全,但資料庫一抖整個系統就停擺 |

**選 (a),而且大聲記錄。** 限流變鬆的後果是被交易所暫時封鎖,那是可回復的;
整個系統停擺會讓已有部位失去監控,那更危險。

但降級必須看得見:

- 每次失敗記一次 `degraded_count` 與原因。
- 連續失敗三次就暫時停用共用層 60 秒(每次失敗都是一次資料庫往返)。
- 停用時用 `logger.error` 明說「多個 process 共用同一把 API Key 時,
  實際請求量會是設定值的倍數」。
- `status()` 回報 `shared` 與 `last_degrade_reason` ——
  降級不能只出現在 log 裡。

### 預設關閉

`EXCHANGE_SHARED_RATE_LIMIT` 預設 `False`,因為它需要 migration 006 的資料表。
表不存在時每次呼叫都會先失敗一次再降級,**那比不開更慢**。

上線前檢查會偵測「開了但表不存在」這個組合並判為 BLOCKER。

---

## 3. `scripts/preflight.py`

一個指令回答一個問題:**這套系統現在可以跑在生產環境嗎?**

六個區塊:金鑰、交易模式、風控、規格校準、資料庫、自我檢討。
三個等級:`BLOCKER`(會導致資金損失或系統失控)、`WARNING`、`INFO`。

幾個值得一提的檢查:

- **`TRADING_MODE=live` 是 BLOCKER。** 系統裡沒有 LiveBroker,
  設成 live 沒有意義,而且會讓人以為真的在下實單。
- **`broker` 模組出現任何 live 命名的東西也是 BLOCKER** ——
  實單路徑不該在 Phase 17 之前存在。
- **Kill Switch 沒有平倉能力是 BLOCKER**(見第 1 節)。
- **自我檢討判定 LOSING 是 BLOCKER。** 期望值為負的系統不該加大規模。

腳本本身唯讀,而且**不印出任何金鑰的值** —— 它會被貼到聊天視窗裡。
兩件事都有測試:AST 掃描確認沒有寫入呼叫,字串掃描確認沒有讀秘密值。

---

## 4. 測試

```
全部:817 passed
本階段新增:tests/test_production_safety.py  22
```

重點:

- `test_a_failed_close_raises_so_panic_records_it`
- `test_it_does_not_pretend_to_cancel_orders`
- `test_shared_usage_counts_against_the_budget`
- `test_a_broken_store_does_not_block_everything`
- `test_repeated_failures_stop_hammering_the_database`
- `test_status_says_whether_the_shared_layer_is_working`
- `test_it_never_prints_a_secret`

---

## 5. 尚未做的事

- **設定變更沒有稽核。** 改了 `MAX_LEVERAGE` 之後沒有紀錄是誰改的、什麼時候。
  這需要把設定從環境變數搬進資料庫,是個獨立的重構。
- **`rate_limit_calls` 沒有自動清理排程。** `cleanup()` 寫好了但沒有人叫它。
  表會一直長。
- **跨行程限流沒有在真實多 process 環境下驗證過。** 邏輯與降級行為有測試,
  但實際的競態要在 VPS 上跑才知道。
- **Kill Switch 的撤單能力要等實盤。**

---

## 6. 下一步

Phase 17 — LIVE SAFETY GATE。這是唯一一個**需要人明確確認才能通過**的階段
(Master Prompt 第一百零二節)。我會把閘門本身與檢查清單做出來,
但不會、也不能代替你按下那個確認。
