# 部署(Master Prompt 第八十二 / 八十三節)

## 五個環境,以及它們真正的差別

第八十三節要求 Development → Testing → Staging → Paper → Production。
它們的差別不是名字,是**什麼可以自動發生**:

| 環境 | `APP_ENV` | 連真實行情 | 送真實訂單 | 自動交易 |
|---|---|---|---|---|
| development | `development` | 可以(測試網) | 否 | 隨意 |
| testing | `testing` | 否 | 否 | 否 |
| paper | `paper` | 是 | 否 | 是 |
| staging | `staging` | 是 | 否 | 否 |
| production | `production` | 是 | 需要 LIVE GATE | 是 |

`agmcis/config/settings.py` 的 `_normalise_env()` 把**無法辨識的名稱
降級成 development**。當成 production 會讓一個打錯字的環境變數解鎖
正式環境的行為;拋例外會讓系統在可以繼續跑的情況下起不來。
原始值留在 `APP_ENV_RAW` 供稽核。

`staging` 與 `production` 共用 `IS_PROTECTED_ENV`,所以寬鬆的預設值
在 staging 就會被擋下來,而不是等到 production 才發現。

## Production 目前跑在 systemd 上,不是 Docker

第八十二節要 Docker,但同一節也說「不要破壞目前正在運作的
Production」。所以:

* `Dockerfile` 與 `docker-compose.yml` 是給 development / testing /
  staging 用的,讓那三個環境可重現。
* Production 仍是 DigitalOcean VPS 上的 systemd(`agmcis`、
  `agmcis-opportunity`、`agmcis-position`、`agmcis-report`)。
* 切換到 Docker 必須是一次**有計畫的遷移**,不是某次部署的副作用。

這是刻意偏離,不是還沒做完。

## 起一個 staging

```
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d
docker compose -f docker-compose.yml -f docker-compose.staging.yml exec web \
    python scripts/migrate.py
```

Staging 與 production 的差別只有兩件事:**帳戶不同**(這是它存在的
全部理由)與**永遠不是實單**(compose 檔把 `TRADING_MODE` 寫死成
paper、`AUTO_TRADING` 關閉)。資料庫是獨立的一份 —— 共用 production
資料庫的 staging 不是 staging,是第二個 production。

埠是 `127.0.0.1:8010`,所以它可以與 production 在同一台機器上並存。

## 「不直接覆蓋 Production」具體是什麼意思

一次重大更新的順序:

1. **本機**:`python3 -m pytest tests/ -q` 全綠。紅的不要往下走
   (第九十九節:測試沒過不要說完成)。
2. **staging**:起上面那組 compose,跑 migration,讓它跑滿一個
   完整的排程週期(至少一小時,含一次對帳與一次 drift monitor)。
3. **檢查 staging 的 `/health`**:`degraded` 也要看清楚是哪一項。
4. **production**:`git pull` → 跑 migration → `systemctl restart`。
   **先重啟 scheduler 再重啟 web** —— 反過來的話,web 已經在用新
   schema 而 scheduler 還在用舊的。
5. **回頭看 production 的 `/health` 與 `/api/system_events`**:
   排程工作壞掉會寫一列 `SCHEDULER_JOB_FAILED`。

Migration 一律**先跑再重啟**。新程式碼配舊 schema 會在第一次查詢
時炸掉,而舊程式碼配新 schema 通常沒事 —— 那個不對稱決定了順序。

## 回滾

`git checkout <上一個 tag>` 然後重啟。**Migration 不回滾** ——
這個系統的 migration 全部是 `CREATE TABLE IF NOT EXISTS` 與
`ADD COLUMN IF NOT EXISTS`,舊程式碼看不到新欄位但不會壞。
寫一個會刪欄位的 migration 之前,先想清楚回滾要怎麼辦。

## 金鑰

`.env` 只在機器上,不進 Git(第十 / 八十四節)。`docker-compose.yml`
用 `env_file: .env` 而不是 `environment:` —— compose 檔會進 Git。

提款權限必須是關的。`scripts/preflight.py` 會檢查這一項。
