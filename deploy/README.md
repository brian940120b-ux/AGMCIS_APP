# 部署產物(Master Prompt 第八十二節)

第八十二節列了十項要考慮的東西。這一份逐項回答**它在哪裡、
現在是什麼狀態**,並說明哪幾項刻意沒做。

| 第八十二節 | 狀態 | 在哪裡 |
|---|---|---|
| Docker | ✅ 開發 / 測試 / staging | `Dockerfile`、`docker-compose.yml`、`docker-compose.staging.yml` |
| Nginx | ✅ 設定檔 | `deploy/nginx.conf` |
| HTTPS | ✅ 步驟 | `deploy/HTTPS.md` |
| Process Manager | ✅ systemd | `deploy/agmcis.service`、`deploy/agmcis-scheduler.service` |
| Database | ✅ PostgreSQL + migration | `migrations/`、`scripts/migrate.py` |
| Redis | ⛔ **刻意不用** | 見下方 |
| Worker | ✅ 排程器獨立程序 | `deploy/agmcis-scheduler.service` |
| WebSocket | ✅ 行情 + 推播 | `agmcis/exchange/bingx/stream.py`、`nginx.conf` 的 `/ws` |
| Monitoring | ✅ `/health` + 系統事件 + Telegram | `deploy/MONITORING.md` |
| Backup | ✅ 每日 timer | `deploy/agmcis-backup.timer`、`backup_system.py` |

⚠️ **這些檔案不會自己套用。** 第八十二節同時寫著「不要破壞目前正在
運作的 Production」,所以它們是要你比對過再放上去的參考設定,
不是一鍵部署。先 `systemctl cat agmcis` 看看現在跑的是什麼。

---

## Redis:第八十二節說「如果必要」,而這裡不必要

Redis 在這種系統上通常負責三件事。三件在這裡都有更好的答案:

**快取。** 行情快取在 `agmcis/config/settings.py` 的 `CACHE_TTL`,
用行程內字典。TTL 是 3 到 60 秒,而這個系統只有兩個程序 ——
跨程序共用一份 3 秒的快取,省下的 API 呼叫少於維護一個 Redis 的成本。

**任務佇列。** 非同步任務(第八十五節)用檔案 + 背景執行緒,一次跑一個。
那是刻意的:回測吃 CPU,而這台機器同時要監控持倉。併發跑五個回測會讓
停損檢查延遲,而那是拿真錢換一份報告。Redis 佇列會讓併發變容易 ——
那不是這裡想要的。

**跨程序限流。** 這一項真的需要共用狀態,而它已經有實作:
`agmcis/exchange/shared_rate_limit.py` 用 **PostgreSQL**。
理由是資料庫本來就在跑,而多一個 Redis 就多一個會掛掉、
會需要備份、會需要監控的東西 —— 而它掛掉的時候,限流會失效。

要引入 Redis 的門檻:**跨程序限流的資料庫寫入成為瓶頸**。
在那之前它是一個沒有付出對應價值的相依。

---

## 順序

改動的順序不是隨便的,每一條都有一個具體的失敗模式:

1. **先跑 migration,再重啟服務。**
   新程式碼配舊 schema 會在第一次查詢時炸掉;舊程式碼配新 schema
   通常沒事(這個系統的 migration 只加不刪)。那個不對稱決定了順序。

2. **先重啟 scheduler,再重啟 web。**
   反過來的話,web 已經在用新 schema 而 scheduler 還在用舊的,
   而 scheduler 是會下單的那一個。

3. **nginx 最後,而且先 `nginx -t`。**
   改壞 nginx 會讓整個系統從外面連不進去 —— 包括儀表板,
   也就是你用來確認剛才那兩步有沒有成功的東西。

完整的升級流程在 `docs/DEPLOYMENT.md`。
