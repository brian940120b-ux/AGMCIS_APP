# 手動檢查腳本

這些檔案原本放在根目錄、命名為 `test_*.py`,但**它們不是自動化測試**:
沒有 assert、沒有 test function、import 即執行,而且需要一個活的資料庫與網路連線。

留在根目錄有兩個問題:
1. 在專案根目錄跑 `pytest` 會把它們收集進來,然後因為連不上資料庫而失敗。
2. `check_paper.py`(原 `test_paper.py`)會**真的建立模擬倉位**,
   看起來像測試但實際會改動資料。

真正的自動化測試在 `tests/`,不連網路、不需要資料庫,用 `pytest tests/ -q` 執行。

## 使用方式

需要先設定好 `.env`(至少 `DB_PASSWORD`),然後單獨執行:

```
/root/AGMCIS_APP/.venv/bin/python scripts/manual/check_dashboard.py
```

## ⚠️ 會改動資料的腳本

- `check_paper.py` — 會開倉與平倉,並改動 accounts 餘額
- `check_rebalance.py` — 只讀,但會打交易所 API 掃描 50 檔
- `check_okx_bingx.py` — 會打交易所 API
