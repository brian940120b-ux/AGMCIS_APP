"""
非同步任務(Master Prompt 第八十五節的 /api/backtest 與 /api/paper)。

那兩條路徑一直沒做,理由是回測是長時間動作 —— 做成同步 HTTP 端點
會變成一個會逾時的請求。這個模組把它變成「送出 → 拿到 id → 之後來查」。

## 三個設計決定

**一、任務在背景執行緒跑,不在請求裡。**
一個跑五分鐘的 HTTP handler 會佔住一個 worker,而 uvicorn 的 worker
數量是有限的。三個併發的回測就能讓整個 API 停止回應,
包括 /health —— 而那時候監控會以為系統掛了。

**二、同時只跑一個。**
回測吃 CPU,而這台機器同時還要跑排程、監控持倉、算指標。
併發跑五個回測會讓停損檢查延遲,而那是拿真錢在換一份報告。
排隊比較慢,但慢的是報告不是停損。

**三、失敗的任務保留錯誤訊息,不會消失。**
一個「送出去之後就再也查不到」的任務,使用者只會重送一次,
然後兩個都失敗、兩份錯誤都沒人看到。
"""
import json
import logging
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger("agmcis.lab.jobs")

JOB_FILE = "data/jobs.json"

QUEUED = "QUEUED"
RUNNING = "RUNNING"
DONE = "DONE"
FAILED = "FAILED"

# 保留最近幾筆。任務結果會累積,而舊的回測報告沒有人會回頭看。
MAX_KEPT = 50


def _utcnow():
    return datetime.now(timezone.utc)


@dataclass
class Job:
    job_id: str = ""
    kind: str = ""
    status: str = QUEUED
    params: Dict = field(default_factory=dict)
    result: Optional[Dict] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: _utcnow().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def __post_init__(self):
        if not self.job_id:
            stamp = _utcnow().strftime("%Y%m%dT%H%M%S")
            self.job_id = f"{stamp}-{str(uuid.uuid4())[:8]}"

    @property
    def is_finished(self):
        return self.status in (DONE, FAILED)

    def to_dict(self):
        return dict(self.__dict__)


class JobStore:
    """
    純檔案。與提案、策略狀態同樣的理由 —— 而且多一個:
    任務結果本來就是「跑完之後給人看」的東西,不需要交易路徑上的
    那種一致性保證。
    """

    def __init__(self, path=None, max_kept=MAX_KEPT):
        self.path = Path(path or JOB_FILE)
        self.max_kept = int(max_kept)
        self._lock = threading.Lock()

    def load(self):
        if not self.path.exists():
            return []

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("任務檔讀取失敗 | %s", self.path)
            return []

        if not isinstance(payload, list):
            return []

        jobs = []
        for item in payload:
            try:
                jobs.append(Job(**item))
            except Exception:
                logger.exception("任務解析失敗,略過這一筆")
        return jobs

    def upsert(self, job):
        with self._lock:
            jobs = [j for j in self.load() if j.job_id != job.job_id]
            jobs.append(job)
            # 只留最近的。舊的回測報告沒有人會回頭看,
            # 但檔案會一直長。
            jobs = sorted(jobs, key=lambda j: j.created_at)[-self.max_kept:]

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    [j.to_dict() for j in jobs], ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        return job

    def get(self, job_id):
        for job in self.load():
            if job.job_id == job_id:
                return job
        return None

    def recent(self, limit=20, kind=None):
        jobs = self.load()
        if kind:
            jobs = [j for j in jobs if j.kind == kind]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)[:limit]

    def running(self):
        return [j for j in self.load() if j.status == RUNNING]


class JobRunner:
    """
    一次跑一個。第二個進來的會排隊。

    ⚠️ 這是**單一程序**的佇列。多個 uvicorn worker 會各有一個 ——
    那不是問題(每個 worker 一次一個,總量仍然受控),但也不是
    一個分散式佇列。要真正的佇列請用 Redis 或 Celery,
    而那是這個系統目前不需要的複雜度。
    """

    def __init__(self, store=None, handlers=None):
        self._store = store
        self.handlers: Dict[str, Callable] = dict(handlers or {})
        self._lock = threading.Lock()
        self._thread = None

    @property
    def store(self):
        if self._store is None:
            self._store = JobStore()
        return self._store

    def register(self, kind, handler):
        self.handlers[kind] = handler
        return self

    def submit(self, kind, params=None, run_in_background=True):
        """
        送出一個任務。回傳 Job(狀態通常是 QUEUED)。

        未註冊的 kind 直接拒絕 —— 收下一個永遠不會被執行的任務,
        比拒絕它更糟:使用者會一直等一個不會來的結果。
        """
        if kind not in self.handlers:
            raise ValueError(
                f"沒有註冊 {kind!r} 的處理器,可用:{sorted(self.handlers)}"
            )

        job = Job(kind=kind, params=dict(params or {}))
        self.store.upsert(job)

        if not run_in_background:
            self._execute(job)
            return self.store.get(job.job_id)

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                # 已經有一個在跑。這一個留在 QUEUED,由跑完的那個接手。
                return job

            self._thread = threading.Thread(
                target=self._drain, name="agmcis-jobs", daemon=True,
            )
            self._thread.start()

        return job

    def _drain(self):
        """把佇列跑完。一次一個。"""
        while True:
            queued = [j for j in self.store.load() if j.status == QUEUED]
            if not queued:
                return

            self._execute(sorted(queued, key=lambda j: j.created_at)[0])

    def _execute(self, job):
        job.status = RUNNING
        job.started_at = _utcnow().isoformat()
        self.store.upsert(job)

        logger.info("Job | START | %s | %s", job.kind, job.job_id)

        try:
            result = self.handlers[job.kind](**job.params)
            job.result = result if isinstance(result, dict) else {"value": result}
            job.status = DONE
        except Exception as exc:
            # 保留錯誤訊息。一個「送出去之後就再也查不到」的任務,
            # 使用者只會重送一次,然後兩個都失敗、兩份錯誤都沒人看到。
            job.status = FAILED
            job.error = (
                f"{type(exc).__name__}: {exc}\n"
                + "".join(traceback.format_tb(exc.__traceback__))[-2000:]
            )
            logger.exception("Job | FAILED | %s | %s", job.kind, job.job_id)

        job.finished_at = _utcnow().isoformat()
        self.store.upsert(job)

        logger.info(
            "Job | %s | %s | %s", job.status, job.kind, job.job_id,
        )
        return job


_runner = None


def get_runner():
    global _runner
    if _runner is None:
        _runner = JobRunner(handlers=default_handlers())
    return _runner


def set_runner(runner):
    global _runner
    _runner = runner


# ---------------- 內建的任務 ----------------

def _persist_backtest(run_id, rows, timeframe, candles, seed, synthetic):
    """
    把每一列結果寫進 backtests 表(第六十四節)。

    **一列一個「策略 × 標的」,不是一列一次執行。** 理由是查詢的問題
    幾乎都長成「這個策略在這檔上表現如何」,而那個問題沒辦法從一列
    含五個標的的彙總裡回答。

    寫入失敗不影響回測結果 —— 呼叫端已經拿到 dict 了,
    這裡只是留一份給以後查。但一定寫 log。
    """
    try:
        from database_service import insert_backtest
    except Exception as exc:
        logger.warning("Backtest | PERSIST_UNAVAILABLE | %s", exc)
        return {"stored": 0, "error": str(exc)}

    stored = 0
    for index, row in enumerate(rows):
        strategy = row.get("strategy")
        symbol = row.get("symbol")

        # run_id 要在整張表裡唯一,而且看得出是哪一次執行的哪一列。
        # 用索引而不是只用名字:strategy 可能是 None(ERROR 的那些列)。
        key = f"{run_id}:{index}:{strategy or 'ERROR'}"

        try:
            insert_backtest(
                key,
                metrics={
                    k: row.get(k) for k in (
                        "oos_trades", "oos_expectancy_r", "oos_profit_factor",
                        "oos_return_pct", "oos_max_drawdown_pct",
                        "health_score", "walk_forward_consistency",
                        "risk_of_ruin", "blockers", "error",
                    )
                },
                strategy=strategy, symbol=symbol, timeframe=timeframe,
                candles=int(candles),
                config={
                    "params": row.get("params"),
                    "seed": seed,
                    # 這一列是不是 live 會用的那條管線。沒有這個欄位的話,
                    # 一個舊策略的 PASS 讀起來會跟 live 管線的 PASS 一樣。
                    "is_live_pipeline": bool(row.get("is_live_pipeline")),
                },
                verdict=row.get("verdict"),
                synthetic=bool(synthetic),
            )
            stored += 1
        except Exception as exc:
            logger.warning(
                "Backtest | PERSIST_ROW_FAILED | %s | %s: %s",
                key, type(exc).__name__, exc,
            )

    return {"stored": stored, "run_id": run_id}


def run_backtest_job(symbols=None, timeframe="1h", candles=1500, seed=None,
                     synthetic=False):
    """
    跑一次 Strategy Lab(第八十五節的 /api/backtest)。

    走的是 `scripts/run_strategy_lab.py` 用的同一支函式 ——
    **不是另一條路徑**。兩條會產生不同結果的回測入口,遲早會有人
    引用其中一條的數字去解釋另一條的行為。

    回傳的內容裡有 `is_live_pipeline` 標記:沒有那個標記的 PASS
    是舊的 strategies/*.py 通過的,而它們不是 live 在用的東西
    (那個陷阱在 ROADMAP 裡)。
    """
    from agmcis.config import settings
    from strategy_optimizer import get_strategy_optimizer

    wanted = symbols or list(settings.WATCHLIST_SYMBOLS)[:3]
    if isinstance(wanted, str):
        wanted = [s.strip() for s in wanted.split(",") if s.strip()]

    result = get_strategy_optimizer(
        symbols=wanted, timeframe=timeframe,
        candles=int(candles), seed=seed,
    )

    rows = result.get("all_results", []) or []

    run_id = f"{_utcnow().strftime('%Y%m%dT%H%M%S')}-{str(uuid.uuid4())[:8]}"
    persisted = _persist_backtest(
        run_id, rows, timeframe, candles, seed, synthetic,
    )

    return {
        "run_id": run_id,
        "persisted": persisted,
        "synthetic": bool(synthetic),
        "symbols": wanted,
        "timeframe": timeframe,
        "candles": int(candles),
        "evaluated": len(rows),
        "live_pipeline_passed": [
            r.get("strategy") for r in rows
            if r.get("verdict") == "PASS" and r.get("is_live_pipeline")
        ],
        "legacy_passed": [
            r.get("strategy") for r in rows
            if r.get("verdict") == "PASS" and not r.get("is_live_pipeline")
        ],
        "strategy_summary": result.get("strategy_summary", []),
        "all_results": rows,
    }


def run_paper_job(symbols=None, limit=5):
    """
    跑一輪模擬盤掃描(第八十五節的 /api/paper)。

    ⚠️ 它**會真的開模擬倉**。這是一個動作,不是一個查詢 ——
    所以對應的 HTTP 端點是 POST,而不是 GET。
    """
    from auto_trader import run_auto_trader

    return run_auto_trader(max_candidates=int(limit))


def default_handlers():
    return {
        "backtest": run_backtest_job,
        "paper": run_paper_job,
    }
