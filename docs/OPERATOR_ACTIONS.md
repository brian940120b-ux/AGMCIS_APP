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

Migration 現在到 `010_excursions.sql`。**先跑 migration 再重啟
服務** —— 新程式碼配舊 schema 會在第一次查詢時炸掉。

010 加的是 MFE / MAE 兩欄(第三十節 Agent 10)。跑完之後
position_monitor 會開始記錄每一筆持倉「最多曾經賺到多少 / 虧到多少」,
而那兩個數字回答一個總損益答不出來的問題:**停損是不是設得太緊。**
Migration 之前開的倉沒有這些數字,統計會把它們排除而不是當成 0。

```
.venv/bin/python scripts/calendar.py
```

事件日曆的狀態(第五十一節)。**現在一定是紅的** —— 檔案還沒建立。
日曆是人工維護的:FOMC 的日期是公布的不是算出來的,CPI 與 NFP 會因為
假日與日光節約時間移動,所以我不會替你生一份。從官方來源複製:

    FOMC       federalreserve.gov 的 FOMC calendar
    CPI / PPI  bls.gov 的 release schedule
    NFP        bls.gov 的 Employment Situation release schedule

然後一筆一筆加(時間用 UTC):

```
.venv/bin/python scripts/calendar.py --add "FOMC 利率決議" <UTC時間> HIGH --before 60 --after 90
```

日曆沒建立或過期(超過七天)時,消息面風險那一層**沒有在保護任何東西**——
FOMC 當天系統會照常開倉。排程每小時檢查一次,狀態改變時會發 Telegram,
`/health` 也看得到。

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

⚠️ 閘門的**實單路徑**那一項要你逐檔讀過實單原始碼。

```bash
.venv/bin/python scripts/verify_live_broker.py   # 印出檔案與 SHA-256,跑行為測試
.venv/bin/python scripts/live_confirm.py         # 逐檔問「你讀過了嗎」,簽下雜湊
```

那個簽章我產不出來:雜湊我算得出來,「我讀過了」不行(第七十八節)。
而且簽的是**那一份原始碼** —— 改一個字雜湊就變,舊的簽章作廢。

網頁精靈簽不了這一項,它會直接拒絕並請你改用終端機。那是刻意的:
一個網頁按鈕只證明有人點過按鈕,證明不了有人讀過程式碼。

⚠️ 閘門開啟**仍然不代表系統會下實單**:`LiveBroker` 沒有被接進
Execution Engine。接上是另一個決定,也需要你點頭。

而且接上之前還有一件事要在 VPS 上確認:**用 clientOrderId 查訂單要放進
哪一個 params 欄位。** 對帳靠它,而猜錯的後果是一張已經成交的單被標成
REJECTED。`agmcis/execution/live_broker.py` 的 `CLIENT_ID_LOOKUP_PARAM`
目前是 `None`,`fetch_order()` 在那個狀態下會拋例外而不回答 —— 對帳因此
記成「狀態不明,不可重送」,那是安全的方向。

對著 BingX 官方 API 確認欄位名稱之後填進那個常數。填上它等於宣告
「我確認過了」,所以那一行要你來改。

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
