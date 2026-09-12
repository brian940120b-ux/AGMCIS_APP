"""
系統暫停 / 恢復。

狀態放檔案:它必須在資料庫掛掉的時候仍然讀得到,而且
「讀不到就當成暫停」比「讀不到就當成執行中」安全。

第六十五節要求任何重要操作都要稽核。暫停與恢復都是重要操作 ——
尤其是**恢復**:一個被暫停的系統被誰、在什麼時候恢復,是事後檢討
最需要知道的一件事。
"""
import logging
from pathlib import Path

logger = logging.getLogger("AGMCIS")

MODE_FILE = "system_mode.txt"

RUNNING = "RUNNING"
PAUSED = "PAUSED"


def get_mode():
    path = Path(MODE_FILE)

    if not path.exists():
        path.write_text(RUNNING)
        return RUNNING

    try:
        return path.read_text().strip() or RUNNING
    except Exception:
        # 讀不到就當成暫停。反過來(讀不到就當成執行中)會讓一次
        # 檔案系統問題變成「系統在沒有人知道的情況下繼續交易」。
        logger.exception("系統模式讀取失敗 | %s | 視為 PAUSED", MODE_FILE)
        return PAUSED


def _audit(action, before, after, actor, detail):
    """稽核寫不進去不該讓暫停失敗 —— 但一定要喊。"""
    try:
        from agmcis.review.decision_log import audit
        audit(action, actor=actor, target="SYSTEM_MODE",
              before=before, after=after, detail=detail)
    except Exception:
        logger.exception("系統模式稽核寫入失敗 | %s", action)


def pause_system(actor="system", reason=""):
    before = get_mode()
    Path(MODE_FILE).write_text(PAUSED)

    logger.warning("System | PAUSED | %s | %s", actor, reason)
    _audit("SYSTEM_PAUSE", before, PAUSED, actor, reason)
    return True


def resume_system(actor="system", reason=""):
    before = get_mode()
    Path(MODE_FILE).write_text(RUNNING)

    logger.warning("System | RESUMED | %s | %s", actor, reason)
    _audit("SYSTEM_RESUME", before, RUNNING, actor, reason)
    return True
