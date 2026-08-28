# AGMCIS v3 Pro

v3 是 Dashboard 層的重寫版本，**不重寫交易邏輯**：
持倉、掃描、AI 決策一律重用根目錄已驗證的核心模組。

## 架構

```
v3/
├── app.py                  FastAPI 入口（lifespan 啟動單一廣播器）
├── config.py               v3 專用設定
├── api/dashboard.py        REST：/api/dashboard、/api/portfolio、/api/market_scan、/api/ai_decisions
├── services/
│   ├── core.py             資料存取層（延遲載入 + 例外隔離 + 狀態回報）
│   └── dashboard.py        payload 組裝 + 快取
├── ws/
│   ├── manager.py          連線管理 + 單一廣播器
│   └── routes.py           /ws
├── templates/              dashboard.html + components/
└── static/                 css / js
```

## 設計重點

- **REST 與 WebSocket 共用同一份 payload**，前端只寫一份 render 邏輯。
- **全 app 只有一個廣播器**：N 個分頁不會變成 N×N 則訊息、也不會各打一次交易所 API。
- **阻塞操作丟到 thread**：`scan_market()` 會同步呼叫交易所 API，直接跑在事件迴圈會卡死整台伺服器。
- **降級不當機**：DB 或交易所任一不可用時，payload 的 `status.state` 變成 `degraded`，
  Dashboard 顯示紅色提示條，其他區塊照常運作。

## 執行

```bash
cd v3
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

必須從 `v3/` 目錄啟動（`templates` / `static` 使用相對路徑）。

- Dashboard：http://localhost:8000/
- 健康檢查：http://localhost:8000/health（含 WebSocket 連線數與資料源狀態）

## 目前狀態

已完成：KPI 卡片、AI Decision Center、Open Positions、Equity Curve、
Market Scanner Top 10、WebSocket 即時推送、斷線自動重連。

尚未接上：TradingView 圖表切換幣種、下單 / 平倉控制、Telegram 整合。
