"""
排程器入口。

實作在 agmcis/scheduling/(Phase 1)。保留舊函式名稱 run_once / run_loop /
write_status,讓既有呼叫端不用改。

這個入口跑**完整**任務組合(出場 + trailing + 風控告警 + 自動開倉 +
機會掃描 + 日報),是建議的最終狀態:
    只啟用 agmcis(這個),停用 agmcis-position 與 agmcis-opportunity。

過渡期間如果三個 service 都還開著,它們各自只做原本負責的事
(見 agmcis/scheduling/runner.py 的 JOB_SETS),行為與 Phase 1 之前一致。
"""
import sys

from agmcis.scheduling.runner import (
    JOB_SET_ALL,
    SchedulerRunner,
    build_jobs,
    run_scheduler,
)

_runner = None


def get_runner(job_set=JOB_SET_ALL):
    global _runner
    if _runner is None:
        _runner = SchedulerRunner(build_jobs(job_set))
    return _runner


def run_once():
    """跑一輪到期的任務。第一次呼叫時所有任務都會被視為到期。"""
    return get_runner().tick()


def run_loop(interval=None, job_set=JOB_SET_ALL):
    runner = get_runner(job_set)
    if interval:
        runner.tick_seconds = interval
    return runner.run_forever()


def write_status(*args, **kwargs):
    """向下相容。狀態內容現在由排程器自己組裝。"""
    return get_runner().write_status()


if __name__ == "__main__":
    sys.exit(run_scheduler(JOB_SET_ALL))
