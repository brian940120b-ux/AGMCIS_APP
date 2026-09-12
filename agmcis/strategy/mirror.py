"""
策略註冊表的資料庫鏡像(Master Prompt 第六十四節的 strategies 表)。

## 這張表不是權威來源

生命週期狀態的權威來源是檔案(agmcis/strategy/health.py 的 StatusStore)。
這一點不是實作偏好,是安全考量:

    資料庫掛掉 -> 讀不到狀態 -> 如果程式去讀資料庫,它會拿到「沒有紀錄」
                            -> 而「沒有紀錄」最容易被寫成 LIVE 或 APPROVED

檔案儲存讀不到時退回 PAPER(能模擬、不能碰真錢),方向是對的。
所以判斷「這個策略能不能下單」永遠走檔案,**沒有任何程式碼會讀這張表
來做那個判斷**。

那為什麼還要這張表?因為人要查。策略清單、每個策略需要什麼資料、
現在是什麼狀態 —— 這些用 SQL 問比讀 JSON 檔方便,而且可以跟
backtests、trades 一起 join。

## 鏡像會落後

鏡像是排程工作推的,不是狀態變更時同步推的。所以它可能落後一個週期。
這是刻意的:狀態變更的路徑上多一個資料庫寫入,等於多一個
「策略停不下來」的失敗點。落後一個週期的查詢表,比一個會讓 PAUSE
失敗的同步寫入好。

`updated_at` 讓落後看得見。
"""
import logging

logger = logging.getLogger("agmcis.strategy.mirror")


def snapshot(registry=None, store=None):
    """
    現在的策略清單與狀態。純讀取,不碰資料庫 —— 測試可以直接比對。
    """
    from agmcis.strategy import health
    from agmcis.strategy.registry import get_registry

    registry = registry if registry is not None else get_registry()
    store = store if store is not None else health.get_store()

    rows = []
    for strategy in registry.strategies:
        status = store.get(strategy.name)
        rows.append({
            "name": strategy.name,
            "status": getattr(status, "value", str(status)),
            # 說明取類別的 docstring 第一行。策略的說明本來就寫在那裡,
            # 再維護一份字串對照表只會讓兩邊不一致。
            "description": _first_line(type(strategy).__doc__),
            "suitable_regimes": sorted(
                getattr(r, "value", str(r))
                for r in (strategy.suitable_regimes or ())
            ),
            "needs_candles": bool(strategy.needs_candles),
            "needs_order_book": bool(strategy.needs_order_book),
        })

    return rows


def _first_line(text):
    if not text:
        return None
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            return line
    return None


def mirror_strategies(registry=None, store=None):
    """
    排程工作。把目前的策略清單寫進 strategies 表。

    寫入失敗不影響任何交易 —— 見模組說明。但一定寫 log。
    """
    try:
        rows = snapshot(registry=registry, store=store)
    except Exception as exc:
        logger.exception("Strategy Mirror | SNAPSHOT_FAILED | %s", exc)
        return {"status": "SNAPSHOT_FAILED", "error": str(exc), "mirrored": 0}

    try:
        from database_service import upsert_strategy
    except Exception as exc:
        logger.warning("Strategy Mirror | IMPORT_FAILED | %s", exc)
        return {"status": "UNAVAILABLE", "error": str(exc), "mirrored": 0}

    mirrored, failed = 0, []
    for row in rows:
        try:
            upsert_strategy(
                row["name"], row["status"],
                description=row["description"],
                suitable_regimes=row["suitable_regimes"],
                needs_candles=row["needs_candles"],
                needs_order_book=row["needs_order_book"],
            )
            mirrored += 1
        except Exception as exc:
            # 一個策略寫不進去不該讓其他的也不寫。逐一記名字 ——
            # 「有些失敗了」不足以查問題。
            failed.append(row["name"])
            logger.warning(
                "Strategy Mirror | ROW_FAILED | %s | %s: %s",
                row["name"], type(exc).__name__, exc,
            )

    return {
        "status": "OK" if not failed else "PARTIAL",
        "mirrored": mirrored,
        "failed": failed,
        "total": len(rows),
    }
