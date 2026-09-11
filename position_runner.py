"""
agmcis-position service 入口。

實作已統一到 agmcis/scheduling/(Phase 1),但這個 service 維持原本的職責範圍:
只管出場 —— 持倉 TP/SL 檢查、trailing stop、風控告警。**不會開倉。**

間隔由 .env 控制:
    SCHEDULER_POSITION_INTERVAL(預設 60)
    SCHEDULER_TRAILING_INTERVAL(預設 120)
    SCHEDULER_RISK_ALERT_INTERVAL(預設 300)
"""
import sys

from agmcis.scheduling.runner import JOB_SET_POSITION, run_scheduler

if __name__ == "__main__":
    sys.exit(run_scheduler(JOB_SET_POSITION))
