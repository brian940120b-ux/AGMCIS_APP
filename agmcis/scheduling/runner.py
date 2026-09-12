"""
單一排程器。

Phase 1 修正:原本有三套互不知道對方存在的排程迴圈 ——
  scheduler.py          60 秒  -> position_monitor + auto_trader
  auto_runner.py        300 秒 -> auto_trader + daily_report
  opportunity_runner.py 1800 秒-> opportunity_scanner(也會開倉)

三者同時跑的時候,開倉路徑有三條、平倉路徑有兩條,而且彼此不知道對方剛做了什麼。
現在收斂成一個 tick 迴圈,每個任務各自有間隔設定,共用同一份狀態檔。

舊的三個入口腳本都保留,改成呼叫這裡 —— systemd 設定完全不用改。
"""
import json
import time
from datetime import datetime

from agmcis.config import settings
from agmcis.scheduling.jobs import Job


class SchedulerRunner:
    def __init__(self, jobs, tick_seconds=None, status_file=None, logger=None):
        self.jobs = list(jobs)
        self.tick_seconds = tick_seconds or settings.SCHEDULER_TICK_SECONDS
        self.status_file = status_file or settings.SCHEDULER_STATUS_FILE
        self.started_at = datetime.now()

        if logger is None:
            from logger_service import logger as default_logger
            logger = default_logger
        self.logger = logger

    # ---------------- 單次 tick ----------------

    def tick(self, now=None):
        """跑完所有到期的任務,回傳這一輪實際執行的任務名稱與結果。"""
        now = now if now is not None else time.monotonic()
        ran = {}

        for job in self.jobs:
            if not job.is_due(now):
                continue

            was_failing = job.last_error is not None

            try:
                result = job.run()
                job.mark_success(result, now)
                ran[job.name] = result
                self._log_job(job, result)

                if was_failing:
                    self._event(
                        "SCHEDULER_JOB_RECOVERED", "INFO", job.name,
                        f"{job.name} 在 {job.error_count} 次失敗後恢復",
                    )
            except Exception as exc:
                job.mark_failure(exc, now)
                ran[job.name] = {"error": str(exc)}
                self.logger.exception("Scheduler | %s | FAILED | %s", job.name, exc)

                # 只在「從正常變成失敗」時記事件。一個每分鐘失敗一次的
                # 任務會在一天內寫進一千四百列,而那一千四百列講的是
                # 同一件事 —— 值得記的是它什麼時候開始壞、什麼時候好。
                if not was_failing:
                    self._event(
                        "SCHEDULER_JOB_FAILED", "ERROR", job.name,
                        f"{type(exc).__name__}: {exc}",
                    )

        if ran:
            self.write_status()

        return ran

    def _event(self, event_type, severity, source, detail, payload=None):
        """
        寫一列系統事件(第六十四節的 system_events 表)。

        這張表不在下單路徑上,而且寫入的時機正好是「有東西壞了」——
        那時候壞掉的很可能就是資料庫本身。所以寫不進去只寫 log,
        不往上拋:一個因為記不了「排程壞了」而讓排程器掛掉的機制,
        比沒有這個機制更糟。
        """
        try:
            from database_service import insert_system_event
            insert_system_event(
                event_type, severity=severity, source=source,
                detail=detail, payload=payload,
            )
        except Exception as exc:
            self.logger.warning(
                "Scheduler | EVENT_WRITE_FAILED | %s | %s: %s",
                event_type, type(exc).__name__, exc,
            )

    def _log_job(self, job, result):
        if isinstance(result, dict):
            # position_monitor 的結果值得攤開來看 —— 平倉數曾經被寫死為 0
            if "closed_count" in result:
                self.logger.info(
                    "Scheduler | %s | checked=%s closed=%s skipped=%s unprotected=%s",
                    job.name,
                    result.get("checked"),
                    result.get("closed_count"),
                    len(result.get("skipped", [])),
                    len(result.get("unprotected", [])),
                )
                return
            if "status" in result:
                reason = result.get("reason")
                self.logger.info(
                    "Scheduler | %s | %s%s",
                    job.name, result["status"], f" ({reason})" if reason else "",
                )
                return

        self.logger.info("Scheduler | %s | done", job.name)

    # ---------------- 狀態檔 ----------------

    def status(self):
        return {
            "status": "running",
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tick_seconds": self.tick_seconds,
            "jobs": [job.status() for job in self.jobs],
            "next_due": {
                job.name: round(job.seconds_until_due(), 1)
                for job in self.jobs
                if job.enabled
            },
        }

    def write_status(self, extra=None):
        payload = self.status()
        if extra:
            payload.update(extra)

        try:
            with open(self.status_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.logger.exception("Scheduler | 無法寫入狀態檔 | %s", exc)

        return payload

    # ---------------- 主迴圈 ----------------

    def run_forever(self):
        enabled = [j.name for j in self.jobs if j.enabled]
        self.logger.info(
            "Scheduler | START | tick=%ds | jobs=%s", self.tick_seconds, ", ".join(enabled),
        )
        self._event(
            "SCHEDULER_START", "INFO", "scheduler",
            f"tick={self.tick_seconds}s jobs={len(enabled)}",
            payload={"jobs": enabled, "tick_seconds": self.tick_seconds},
        )
        self.write_status()

        while True:
            try:
                self.tick()
            except Exception as exc:
                # tick() 內部已經逐一處理過任務例外,走到這裡代表排程器本身有問題
                self.logger.exception("Scheduler | TICK_FAILED | %s", exc)

            time.sleep(self.tick_seconds)


# ---------------- 任務組合 ----------------
#
# 每個生產 service 沿用它原本負責的任務子集,所以切換到統一排程器
# 不會突然讓每個 process 都去做全部的事。
#
# 建議的最終狀態:只跑 JOB_SET_ALL 一個 process,停用其餘服務。
# 過渡期間各服務維持原本範圍,行為與 Phase 1 之前一致。

JOB_SET_ALL = "all"                   # 完整排程器(建議最終只跑這一個)
JOB_SET_POSITION = "position"         # 原 agmcis-position:只管出場
JOB_SET_OPPORTUNITY = "opportunity"   # 原 agmcis-opportunity:只掃機會
JOB_SET_TRADER = "trader"             # 原 auto_runner:自動開倉 + 日報


def _job_position_monitor():
    from position_monitor import run_position_monitor
    return Job(
        name="position_monitor",
        run=run_position_monitor,
        interval_seconds=settings.SCHEDULER_POSITION_INTERVAL,
        tags=["exit"],
    )


def _job_trailing_stop():
    from trailing_stop import apply_trailing_stop
    return Job(
        name="trailing_stop",
        run=apply_trailing_stop,
        interval_seconds=settings.SCHEDULER_TRAILING_INTERVAL,
        tags=["exit"],
    )


def _job_exit_manager():
    from agmcis.execution.exit_manager import run_exit_manager
    return Job(
        name="exit_manager",
        run=run_exit_manager,
        interval_seconds=settings.SCHEDULER_EXIT_MANAGER_INTERVAL,
        tags=["exit"],
    )


def _job_naked_position_sweep():
    from agmcis.execution.exit_manager import run_naked_position_sweep
    return Job(
        name="naked_position_sweep",
        run=run_naked_position_sweep,
        interval_seconds=settings.SCHEDULER_NAKED_SWEEP_INTERVAL,
        tags=["risk"],
    )


def _job_config_audit():
    from agmcis.config.audit import run_config_audit

    return Job(
        name="config_audit",
        run=run_config_audit,
        interval_seconds=settings.SCHEDULER_CONFIG_AUDIT_INTERVAL,
        tags=["risk"],
    )


def _job_research_loop():
    """
    研究循環(第七十七節)。把自我檢討的結論變成有編號、有狀態、
    追蹤得到的提案草稿。

    它**不改任何東西**,也不推進任何提案 —— 第七十八節禁止的是
    「自己修改 → 自己測試 → 自己批准 → 自己 Live」這一整條鏈,
    這個工作刻意只做第一個箭頭之前的那一步。
    """
    from agmcis.review.proposals import run_research_loop

    return Job(
        name="research_loop",
        run=run_research_loop,
        interval_seconds=settings.SCHEDULER_RESEARCH_INTERVAL,
        tags=["research"],
    )


def _job_news_archive():
    """
    新聞歸檔(第六十四節)。

    它**不影響任何交易決策** —— 消息面風險走的是
    agmcis/risk/news_risk.py,那條路徑自己抓標題、自己判斷,
    不讀這張表。這個工作只負責把讀過的東西留下來,
    因為 RSS 來源不保留歷史。
    """
    from agmcis.data.news_archive import archive_news

    return Job(
        name="news_archive",
        run=archive_news,
        interval_seconds=settings.SCHEDULER_NEWS_ARCHIVE_INTERVAL,
        tags=["research"],
    )


def _job_calendar_watch():
    """
    事件日曆保鮮(第五十一節)。

    日曆是人工維護的,而它過期之後系統會安靜地少一層保護:
    Macro Agent 從「沒有事件」變成「不知道有沒有事件」而棄權,
    但那在投票畫面上看起來一樣。這個工作讓那件事會主動說話。
    """
    from agmcis.risk.calendar_watch import run_calendar_watch

    return Job(
        name="calendar_watch",
        run=run_calendar_watch,
        interval_seconds=settings.SCHEDULER_CALENDAR_WATCH_INTERVAL,
        tags=["risk"],
    )


def _job_strategy_mirror():
    """
    策略清單鏡像(第六十四節)。

    鏡像會落後一個週期,而那是刻意的:狀態變更的路徑上多一個
    資料庫寫入,等於多一個「策略停不下來」的失敗點。
    判斷能不能下單走的永遠是檔案。
    """
    from agmcis.strategy.mirror import mirror_strategies

    return Job(
        name="strategy_mirror",
        run=mirror_strategies,
        interval_seconds=settings.SCHEDULER_STRATEGY_MIRROR_INTERVAL,
        tags=["research"],
    )


def _job_pending_orders():
    """
    掛單巡檢(第十三節):到期的取消、觸價的成交。

    間隔與部位監控一致 —— 掛單與停損看的是同一種東西(價格有沒有
    走到某一條線),用不同的頻率檢查會讓其中一個系統性地慢半拍。
    """
    from agmcis.execution.pending import run_pending_orders

    return Job(
        name="pending_orders",
        run=run_pending_orders,
        interval_seconds=settings.SCHEDULER_POSITION_INTERVAL,
        tags=["execution"],
    )


def _job_drift_monitor():
    """
    策略退化偵測(第七十四 / 七十五 / 七十六節)。

    它唯一會自己做的動作是 PAUSE —— 第七十八節明講 AI 不得自己改策略。
    停掉是可逆的、不需要人工核可就能做的最強動作;改參數不是。
    """
    from agmcis.strategy.health import run_drift_monitor

    return Job(
        name="drift_monitor",
        run=run_drift_monitor,
        interval_seconds=settings.SCHEDULER_DRIFT_MONITOR_INTERVAL,
        tags=["risk"],
    )


def _job_rate_limit_cleanup():
    """
    清掉舊的限流紀錄。每次 API 呼叫寫一列,不清理這張表會一直長。

    功能沒開的時候這個工作直接跳過 —— 表可能根本不存在,
    每小時對一張不存在的表發一次 DELETE 只會在 log 裡堆錯誤。
    """
    from agmcis.exchange.shared_rate_limit import SharedRateLimitStore

    def cleanup():
        if not settings.EXCHANGE_SHARED_RATE_LIMIT:
            return {"status": "DISABLED"}

        SharedRateLimitStore().cleanup()
        return {"status": "CLEANED"}

    return Job(
        name="rate_limit_cleanup",
        run=cleanup,
        interval_seconds=settings.SCHEDULER_RATE_LIMIT_CLEANUP_INTERVAL,
        tags=["maintenance"],
    )


def _job_reconciliation():
    from agmcis.execution.reconciliation import run_reconciliation
    return Job(
        name="reconciliation",
        run=run_reconciliation,
        interval_seconds=settings.SCHEDULER_RECONCILE_INTERVAL,
        tags=["risk"],
    )


def _job_risk_alert():
    from risk_alert import check_risk_alerts
    return Job(
        name="risk_alert",
        run=check_risk_alerts,
        interval_seconds=settings.SCHEDULER_RISK_ALERT_INTERVAL,
        tags=["risk"],
    )


def _job_auto_trader():
    from auto_trader import run_auto_trader
    return Job(
        name="auto_trader",
        run=run_auto_trader,
        interval_seconds=settings.SCHEDULER_TRADER_INTERVAL,
        enabled=settings.AUTO_TRADING_ENABLED,
        tags=["entry"],
    )


def _job_opportunity_scanner():
    from opportunity_scanner import scan_opportunities
    return Job(
        name="opportunity_scanner",
        run=scan_opportunities,
        interval_seconds=settings.SCHEDULER_OPPORTUNITY_INTERVAL,
        enabled=settings.AUTO_TRADING_ENABLED,
        tags=["entry"],
    )


def _job_daily_report():
    return Job(
        name="daily_report",
        run=_daily_report_once,
        interval_seconds=3600,
        tags=["report"],
    )


# 任務工廠都是延後 import 的 —— 這些模組會連資料庫與交易所,
# 在 import agmcis.scheduling 時就載入會讓單元測試無法在離線環境跑。
JOB_SETS = {
    JOB_SET_ALL: [
        _job_position_monitor, _job_trailing_stop, _job_exit_manager,
        _job_naked_position_sweep, _job_reconciliation, _job_risk_alert,
        _job_rate_limit_cleanup, _job_config_audit, _job_drift_monitor,
        _job_pending_orders, _job_research_loop, _job_news_archive,
        _job_strategy_mirror, _job_calendar_watch, _job_auto_trader,
        _job_opportunity_scanner, _job_daily_report,
    ],
    JOB_SET_POSITION: [
        _job_position_monitor, _job_trailing_stop, _job_exit_manager,
        _job_naked_position_sweep, _job_reconciliation, _job_risk_alert,
        _job_rate_limit_cleanup, _job_config_audit, _job_drift_monitor,
        _job_pending_orders, _job_calendar_watch,
    ],
    JOB_SET_OPPORTUNITY: [_job_opportunity_scanner],
    JOB_SET_TRADER: [_job_auto_trader, _job_daily_report],
}


def build_jobs(job_set=JOB_SET_ALL):
    if job_set not in JOB_SETS:
        raise ValueError(f"未知的任務組合 {job_set!r},可用:{sorted(JOB_SETS)}")
    return [factory() for factory in JOB_SETS[job_set]]


def build_default_jobs():
    """向下相容。"""
    return build_jobs(JOB_SET_ALL)


_last_report_date = None


def _daily_report_once():
    """每小時檢查一次,只在設定的時間且當天還沒發過時才真的發送。"""
    global _last_report_date

    now = datetime.now()
    if now.hour != settings.DAILY_REPORT_HOUR:
        return {"status": "NOT_DUE", "hour": now.hour}

    if _last_report_date == now.date():
        return {"status": "ALREADY_SENT"}

    from daily_report import send_daily_report

    send_daily_report()
    _last_report_date = now.date()
    return {"status": "SENT"}


def run_scheduler(job_set=JOB_SET_ALL, status_file=None):
    """
    啟動排程器。

    每個 job_set 用自己的狀態檔,這樣過渡期間多個 service 並存時
    不會互相覆蓋彼此的狀態。
    """
    if status_file is None and job_set != JOB_SET_ALL:
        status_file = f"scheduler_status_{job_set}.json"

    runner = SchedulerRunner(build_jobs(job_set), status_file=status_file)
    return runner.run_forever()
