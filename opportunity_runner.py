"""
agmcis-opportunity service 入口。

實作已統一到 agmcis/scheduling/(Phase 1),但這個 service 維持原本的職責範圍:
只跑機會掃描。間隔由 SCHEDULER_OPPORTUNITY_INTERVAL 控制(預設 1800)。
"""
import sys

from agmcis.scheduling.runner import JOB_SET_OPPORTUNITY, run_scheduler

if __name__ == "__main__":
    sys.exit(run_scheduler(JOB_SET_OPPORTUNITY))
