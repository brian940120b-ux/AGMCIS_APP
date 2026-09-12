# 監控(Master Prompt 第八十二節)

## 一個原則:安靜不等於正常

這個系統最危險的失效方式不是掛掉,是**繼續跑但少了一層保護**:

* 排程器還在,但某個工作每次都拋例外
* WebSocket 連著,但一直回傳三十分鐘前的價格
* 事件日曆過期,而 Macro Agent 從「沒有事件」變成「不知道」
* 合約規格沒校準,強平價全部是估計值

四個都不會讓服務停止回應。所以監控要問的不是「還活著嗎」,
是「還在做它該做的事嗎」。

## 三層

### 一、`/health`(給機器)

**不需要金鑰** —— 一個要金鑰才打得到的健康端點等於沒有健康端點,
因為真正需要它的東西(uptime 監測、負載平衡器、Docker healthcheck)
不會帶金鑰。

它只回狀態不回內容:回「資料庫 error」,不回連線字串。

```
curl -s localhost:8000/health | jq
```

三種狀態,而且 **HTTP 狀態碼也對**(監控看的是狀態碼不是 JSON):

| status | 碼 | 意思 |
|---|---|---|
| `healthy` | 200 | 十個元件都正常 |
| `degraded` | 200 | 還能交易,但有東西壞了或快壞了 |
| `unhealthy` | 503 | `database` / `risk_engine` / `trading_engine` 其中之一掛了 |

外部監控最少要做兩件事:

1. 每分鐘打一次 `/health`,**非 200 就告警**。
2. 解析 JSON 的 `degraded` 陣列 —— degraded 不會讓狀態碼變,
   但它代表某一層保護沒了。

### 二、系統事件(給人查)

`/api/system_events`(要金鑰)。排程工作**從正常變成失敗**、
從失敗恢復、日曆過期、系統啟動,都各寫一列。

只在轉態時寫。一個每分鐘失敗一次的任務會在一天內寫一千四百列,
而那一千四百列講的是同一件事。

### 三、Telegram(給人立刻知道)

`risk_alert` 排程(每 5 分鐘)與 `calendar_watch`(每小時)會推。
同樣只在轉態時推 —— 一份每小時響一次的警報,只會讓人把它靜音,
而那正好讓下一個真正的警報也被靜音。

## 要盯的指標

| 看什麼 | 在哪裡 | 不對的樣子 |
|---|---|---|
| 服務活著 | `/health` 狀態碼 | 非 200 |
| 有沒有降級 | `/health` 的 `degraded` | 陣列不是空的 |
| 排程有在跑 | `/health` 的 `scheduler` | `error`(狀態檔超過 5 分鐘沒更新) |
| **裸倉** | `/api/positions` 的 `naked_count` | **> 0,任何時候** |
| 對帳差異 | `/api/reconciliation` | `critical` 不是 0 |
| 事件日曆 | `/health` 的 `news_calendar` | `error` 或 `degraded` |
| 訂單狀態不明 | `/api/orders` 的 `unresolved_count` | > 0 |

**裸倉那一列是唯一需要立刻行動的。** 沒有停損的部位沒有虧損上限。

## systemd

```
systemctl status agmcis agmcis-scheduler
journalctl -u agmcis-scheduler -f
journalctl -u agmcis-scheduler --since "1 hour ago" -p err
```

服務反覆重啟時 `StartLimitBurst` 會讓它停下來 —— 那是刻意的。
`systemctl status` 會顯示 `start-limit-hit`,而原因在最上面那幾行。

## 磁碟

備份與 log 會長。`backup_system.py` 有 `prune()`,但 log 靠 journald
自己輪替。確認一下:

```
journalctl --disk-usage
df -h /
```

磁碟滿了的時候,PostgreSQL 會停止寫入 —— 而那時候平倉會失敗。
