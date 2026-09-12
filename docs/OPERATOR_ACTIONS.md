# 只有你能做的事

這份清單列的是**我做不到、或依 Master Prompt 不該由我做**的事。

分成兩類:

* **技術上做不到** —— 開發容器連不到 BingX(網路政策擋掉),
  而且沒有你的 API 金鑰、沒有 production 資料庫。
* **不該由我做** —— 第一百零二節:Live Trading、API Key、
  Risk Limit 需要人工確認。第七十八節:AI 不得自我修改 → 自我測試
  → 自我批准 → 自我上線。

⚠️ **不要把 API 金鑰貼進對話。** 它只該存在 VPS 的 `.env` 裡
(第十 / 八十四節:金鑰不得出現在原始碼、Git、Log、例外訊息、
資料庫、前端、瀏覽器、Telegram 或 AI Prompt)。

---

## 一、現在就可以做(唯讀,不碰真錢)

全部在 VPS 上:

```
cd /root/AGMCIS_APP
git pull
.venv/bin/python scripts/migrate.py
```

Migration 現在到 `009_research_tables.sql`。**先跑 migration 再重啟
服務** —— 新程式碼配舊 schema 會在第一次查詢時炸掉。

```
.venv/bin/python scripts/verify_bingx.py --write-specs
```

抓真實的合約規格(tick size、step size、最小名目、槓桿上限)。
在這之前,強平價與成本都是保守猜測值,而 `/api/calibration`
會顯示問號。這一步是唯讀的:它只是把 `fetch_*` 的結果寫成 JSON。

```
.venv/bin/python scripts/run_strategy_lab.py --candles 5000
```

**這是目前最重要的一步。** 到現在為止沒有任何策略通過完整驗證,
而那不是程式的問題 —— Lab 早就寫好了,缺的是真實資料。1h 的
5000 根約 208 天,樣本外交易筆數才有機會湊到 30 筆門檻。

它會跑很久。K 棒會快取到 `data/history/`,第二次快很多。

```
.venv/bin/python scripts/tune_exits.py --candles 5000
```

出場參數調校(第五十六節)。`exit_plan.py` 的 1R / 2R / 3R 與
30/30/40 從來沒有被驗證過 —— 它們是起點不是結論。

看報告的時候,**最重要的數字是「最佳與中位數的差距」**:
差距大代表參數面崎嶇,而崎嶇參數面上的最高點幾乎一定是過度擬合。
那時正確的動作是**維持現狀**,不是換成那個最高點。

這份報告不會自己套用。要改預設值走提案流程(第七十八節)。

```
.venv/bin/python scripts/preflight.py
.venv/bin/python scripts/live_gate.py
```

前者回答「這套系統現在可以跑在生產環境嗎」,後者回答
「現在可以用真錢交易嗎」。兩支都是唯讀的,不會打開任何東西。

---

## 二、需要你決定的設定

這些是 Risk Limit,第一百零二節要求人工確認。我把預設值設在保守側,
但**那些是佔位值不是建議值** —— 正確的數字取決於你的實際帳戶規模,
而我不知道那個數字。

在 `.env` 裡設:

| 變數 | 現在的預設 | 你要決定什麼 |
|---|---|---|
| `MAX_RISK_PER_TRADE_PCT` | 1.0 | 單筆最多虧掉權益的百分之幾 |
| `MAX_DAILY_LOSS_USDT` | 見 settings.py | 一天虧多少就停手 |
| `MAX_WEEKLY_LOSS_USDT` | 見 settings.py | 一週虧多少就停手 |
| `MAX_LEVERAGE` | 見 settings.py | 槓桿上限 |
| `MAX_OPEN_POSITIONS` | 見 settings.py | 同時最多幾個部位 |

改完之後排程的 config_audit 會寫一列稽核紀錄,而 `/api/config_changes`
看得到「從多少變成多少」。**那一層答不出「是誰改的」** ——
設定來自環境變數,環境變數沒有作者。

---

## 三、需要你授權才能繼續的事

**我不會自己做這些,也不會催。**

### Phase 18:小額實單

前提:上面第一節全部跑完,而且 `scripts/live_gate.py` 除了「人工確認」
之外全部通過。

```
.venv/bin/python scripts/live_confirm.py
```

這支精靈會逐項問第九十二節列的七件事(Account / Exchange / Market /
Risk / Leverage / Daily Loss / API),每一項顯示**當下的實際值**,
最後要你逐字輸入:

```
I UNDERSTAND LIVE TRADING RISK
```

產生的確認檔:

* **24 小時後失效。** 「上個月批准過」不等於「現在批准」。
* **指名批准金額**,而且首次不得超過 50 USDT。
  第一次用真錢跑的規模應該小到虧光也不影響任何事。
* **設定改了就作廢。** 確認檔存一份設定指紋,之後有人動了風控參數
  就對不上。「批准過一次」不等於「批准所有設定」。

⚠️ 閘門開啟**不代表系統會下實單**:`LiveBroker` 目前不存在。
實單程式碼本身還要再經過一次審視,而那一次審視也需要你點頭。

### Phase 19:實單監控

小額實單跑起來之後,盯 `/health`、`/api/risk_events`、
`/api/system_events`。要跑多久才算通過,是你的判斷 ——
我可以建議(至少 30 天、至少 100 筆),但不能替你決定。

### Phase 20:放大規模

只有在 Phase 19 的結果誠實地支持它時才做。第二節:
**70-80% 勝率是研究目標,不是保證**。誠實的 55-65% 配 PF > 1.5
是好結果;為了讓數字好看而調整,是把問題往後推。

---

## 四、為什麼有幾節永遠停在「部分做到」

`docs/MASTER_PROMPT_COMPLIANCE.md` 有完整的 106 節對照。其中五節
(三十八 / 五十六 / 八十三 / 一百零四 / 一百零六)的程式碼都寫完了,
但它們留在 ⚠️,理由是同一個:

**沒有跑過真實資料或真錢的東西,不能宣稱做到。**

那不是謙虛,是第二節與第九十九節的要求。等你跑完第一節的四支腳本,
其中幾項就會有答案 —— 而答案可能是「這個策略沒有優勢」。
那也是一個結果,而且比一份看起來很漂亮但沒驗證過的報告有用。
