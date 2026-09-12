"""
新聞歸檔(Master Prompt 第六十四節的 news 表)。

## 為什麼要存

RSS 來源不保留歷史。CoinDesk 的 feed 只給最近幾則,今天沒抓下來的
標題,明天就永遠拿不回來了。而事後檢討最常問的一句話是
「那天到底發生了什麼」—— 那句話沒有新聞就答不出來。

## 這裡**不做**情緒分析

只存標題、來源、連結,加上第五十一節那份關鍵字掃描的結果。
理由是情緒分數會隨著模型改版而改變,而一則新聞被存下來的當下
是什麼分數,跟三個月後同一個模型會給什麼分數,是兩件事。
把當下的判斷跟原始文字混在同一列,以後就分不出來哪個是事實。

`impact` 存的是關鍵字掃描的結論(SHOCK / 空),它是規則的輸出,
規則寫在程式碼裡、看得到、不會自己變。

## 失敗不影響交易

這張表不在下單路徑上。抓不到新聞、寫不進資料庫,交易照樣跑 ——
只是少了一份紀錄。所以這裡吞例外,但**一定寫 log**(第九十四節:
沒有靜默失敗),而且回傳的 dict 會說出到底發生了什麼。
"""
import logging

logger = logging.getLogger("agmcis.data.news_archive")

# 抓取失敗時 news_center 會把錯誤訊息當成一則新聞回傳。
# 那不是新聞,不能進資料庫 —— 它會佔掉一個標題的唯一索引,
# 而且事後看起來像那天真的有一則叫「RSS 讀取失敗」的新聞。
ERROR_SOURCE = "SYSTEM"


def _is_real(item):
    if not isinstance(item, dict):
        return False
    if not (item.get("title") or "").strip():
        return False
    return str(item.get("source") or "").upper() != ERROR_SOURCE


def collect(fetch=None):
    """
    抓一輪新聞並標記關鍵字掃描的結果。

    回傳可以直接餵給 database_service.insert_news() 的清單。
    純函式(除了 fetch),所以測試不用碰資料庫。
    """
    if fetch is None:
        from news_center import get_crypto_news as fetch

    items = [item for item in (fetch() or []) if _is_real(item)]

    from agmcis.risk import news_risk

    # scan_headlines 回傳 [(標題, 說明), ...]。用標題對回去,
    # 因為那是唯一在兩邊都存在的欄位。
    shocks = {title: label for title, label in news_risk.scan_headlines(items)}

    rows = []
    for item in items:
        title = item["title"].strip()
        label = shocks.get(title)
        rows.append({
            "title": title,
            "source": item.get("source"),
            "url": item.get("url"),
            # 情緒留空 —— 見模組說明。
            "sentiment": None,
            "impact": "SHOCK" if label else None,
            "score": None,
            "affected_symbols": None,
            "shock_reason": label,
        })

    return rows


def archive_news(fetch=None):
    """排程工作。回傳這一輪抓到幾則、實際新增幾則。"""
    try:
        rows = collect(fetch=fetch)
    except Exception as exc:
        logger.exception("News Archive | FETCH_FAILED | %s", exc)
        return {"status": "FETCH_FAILED", "error": str(exc), "fetched": 0, "inserted": 0}

    if not rows:
        return {"status": "EMPTY", "fetched": 0, "inserted": 0}

    try:
        from database_service import insert_news
        inserted = insert_news(rows)
    except Exception as exc:
        # 表可能還沒 migrate。這不該讓排程器看起來壞掉,
        # 但也不能安靜 —— 少一份紀錄是要有人知道的。
        logger.warning("News Archive | WRITE_FAILED | %s: %s", type(exc).__name__, exc)
        return {
            "status": "WRITE_FAILED", "error": str(exc),
            "fetched": len(rows), "inserted": 0,
        }

    shocks = [r["title"] for r in rows if r["impact"] == "SHOCK"]
    if shocks:
        logger.warning("News Archive | SHOCK_HEADLINES | %s", " | ".join(shocks[:3]))

    return {
        "status": "OK",
        "fetched": len(rows),
        "inserted": inserted,
        "shocks": len(shocks),
    }
