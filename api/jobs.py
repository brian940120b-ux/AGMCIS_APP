"""
非同步任務端點(Master Prompt 第八十五節的 /api/backtest 與 /api/paper)。

這兩條**不在** api/transparency.py 裡,而那是刻意的:
那個模組有一條「所有路由都是 GET、而且原始碼裡不能出現任何下單函式」
的測試在守著,因為它是一頁純觀察的東西。回測會吃光 CPU、
模擬盤掃描會真的開倉 —— 兩者都是動作,不是觀察。

把動作混進觀察模組裡,那條測試就得放寬,而放寬之後它就不再擋得住
真正該擋的東西。

## 為什麼是 POST

GET 依定義不該變更狀態。/api/paper 會真的開模擬倉;/api/backtest 會
佔滿 CPU 幾分鐘並吃掉交易所配額。任何一個爬蟲、預抓連結的聊天軟體、
或瀏覽器的推測性載入,都可能在使用者不知情的情況下觸發它們。

## 為什麼是任務而不是同步回應

回測是長時間動作。做成同步端點會變成一個會逾時的請求,而且會佔住
一個 uvicorn worker —— 三個併發的回測就能讓整個 API 停止回應,
包括 /health,而那時候監控會以為系統掛了。
"""
import logging

from fastapi import APIRouter, Query

router = APIRouter()
logger = logging.getLogger("agmcis.api.jobs")


def _safe(name, fn, fallback):
    try:
        return fn()
    except Exception as exc:
        logger.exception("任務端點 %s 失敗", name)
        result = dict(fallback)
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


@router.post("/api/backtest")
def api_backtest(symbols: str = Query(None),
                 timeframe: str = Query("1h"),
                 candles: int = Query(1500, ge=200, le=10000)):
    """
    送出一次 Strategy Lab 回測。回傳 job_id,結果之後用
    /api/jobs/{job_id} 查。

    走的是 `scripts/run_strategy_lab.py` 用的同一支函式 ——
    兩條會產生不同結果的回測入口,遲早會有人引用其中一條的數字
    去解釋另一條的行為。
    """
    return _submit("backtest", {
        "symbols": symbols, "timeframe": timeframe, "candles": candles,
    })


@router.post("/api/paper")
def api_paper(limit: int = Query(5, ge=1, le=20)):
    """
    跑一輪模擬盤掃描。

    ⚠️ 它**會真的開模擬倉**,而且會經過完整的風控與執行鏈路。
    """
    return _submit("paper", {"limit": limit})


def _submit(kind, params):
    def run():
        from agmcis.lab.jobs import get_runner

        job = get_runner().submit(kind, params)
        return {
            "job_id": job.job_id,
            "kind": job.kind,
            "status": job.status,
            "poll": f"/api/jobs/{job.job_id}",
        }

    return _safe(f"submit_{kind}", run, {"job_id": None, "status": "FAILED"})


@router.get("/api/jobs")
def api_jobs(limit: int = Query(20, ge=1, le=100), kind: str = Query(None)):
    def run():
        from agmcis.lab.jobs import get_runner

        jobs = get_runner().store.recent(limit=limit, kind=kind)
        return {
            "jobs": [j.to_dict() for j in jobs],
            "running": [j.job_id for j in jobs if j.status == "RUNNING"],
        }

    return _safe("jobs", run, {"jobs": [], "running": []})


@router.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    """
    查一個任務。**找不到就明說找不到** —— 回一個空的成功結果
    會讓使用者以為任務跑完了而且什麼都沒發生。
    """
    def run():
        from agmcis.lab.jobs import get_runner

        job = get_runner().store.get(job_id)
        if job is None:
            return {"found": False, "job_id": job_id}

        payload = job.to_dict()
        payload["found"] = True
        return payload

    return _safe("job", run, {"found": False, "job_id": job_id})
