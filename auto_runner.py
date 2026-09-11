"""
自動開倉 + 日報入口(原本是一個獨立的 300 秒迴圈)。

實作已統一到 agmcis/scheduling/(Phase 1),職責範圍維持原本的:
自動開倉與每日報表。間隔由 SCHEDULER_TRADER_INTERVAL 控制(預設 300)。

⚠️ 這個入口與 scheduler.py 的完整組合重疊。
   建議只跑 scheduler.py,不要同時啟用這個。
"""
import sys

from agmcis.scheduling.runner import JOB_SET_TRADER, run_scheduler

if __name__ == "__main__":
    sys.exit(run_scheduler(JOB_SET_TRADER))
