"""
持倉管理進入點(agmcis-position service 使用)。

平倉判斷邏輯已統一到 position_monitor.run_position_monitor(),
這裡只保留原本的函式名稱與 log 行為,避免兩套實作各自漂移。
"""
from logger_service import logger
from position_monitor import run_position_monitor


def manage_open_positions():
    result = run_position_monitor()

    if result["checked"] == 0:
        logger.info("目前沒有持倉需要管理")
        return result

    logger.info(
        "自動平倉檢查完成 | 檢查 %d 筆 | 平倉 %d 筆 | 略過 %d 筆 | 無停損 %d 筆",
        result["checked"], result["closed_count"],
        len(result["skipped"]), len(result["unprotected"]),
    )

    return result
