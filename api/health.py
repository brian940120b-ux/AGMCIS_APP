"""
/health(Master Prompt 第六十六節)。

這個端點與 /api/system_health 有一個關鍵差異:**它不需要 API 金鑰。**

理由很實際:健康檢查是給外部監控用的 —— uptime 監測、負載平衡器、
systemd 的看門狗。一個需要金鑰才打得到的健康端點,等於沒有健康端點,
因為真正需要它的東西不會帶金鑰。

## 不洩漏任何東西

不需要金鑰就代表任何人都看得到,所以這裡**只回狀態,不回內容**:

  * 回「資料庫 error」,不回連線字串、不回錯誤訊息裡的主機名稱。
  * 回「BingX error」,不回 API 回應內容。
  * 回交易模式(paper / live),因為那不是秘密,而且是最常需要
    從外面確認的一件事。

錯誤細節留在 /api/system_health(需要金鑰)與 log 裡。

## 失敗的預設是 degraded,不是 healthy

每一項檢查自己包 try/except,而例外一律算「這一項壞了」。
一個在自己壞掉時回報健康的健康檢查,比沒有健康檢查更糟 ——
它會讓監控系統安靜下來。
"""
import logging

from fastapi import APIRouter, Response

router = APIRouter()
logger = logging.getLogger("agmcis.api.health")

OK = "ok"
ERROR = "error"
DISABLED = "disabled"
UNKNOWN = "unknown"
# 「還能用但快不能用了」。degraded 與 error 分開,是因為前者
# 需要的是安排時間去處理,後者需要的是現在就去看。
DEGRADED = "degraded"

# 這幾項壞掉代表系統不能交易。其他項目壞掉只是降級。
CRITICAL_COMPONENTS = ("database", "risk_engine", "trading_engine")


def _check(name, probe):
    """
    跑一項檢查。例外一律算壞掉 —— 見模組開頭。

    細節只進 log,不進回應:這個端點不需要金鑰。
    """
    try:
        return OK if probe() else ERROR
    except Exception as exc:
        logger.warning("Health | %s | %s: %s", name, type(exc).__name__, exc)
        return ERROR


def _database():
    from database_service import get_account
    get_account()
    return True


def _bingx():
    from exchange_engine import test_connection
    return bool(test_connection().get("bingx", {}).get("success"))


def _market_data():
    """
    取得一筆行情。這一項與 BingX 連線分開,是因為連線正常但資料
    過期或不合格,對交易來說一樣是不能用 —— 第五十節的資料品質閘門
    會擋下來,而外面看到的會是「系統連著但不下單」。
    """
    from agmcis.data.market_data import get_price
    return get_price("BTC/USDT") is not None


def _risk_engine():
    from agmcis.risk.engine import get_engine
    return get_engine() is not None


def _trading_engine():
    from agmcis.execution.engine import get_engine
    return get_engine() is not None


def _agent_engine():
    from agmcis.agents.registry import get_registry
    return len(get_registry().names) > 0


def _scheduler():
    """
    排程器有沒有在跑。用狀態檔的新鮮度判斷 ——
    程序還在但工作卡住的情況,用「程序在不在」看不出來。
    """
    import json
    import time
    from pathlib import Path

    from agmcis.config import settings

    path = Path(getattr(settings, "SCHEDULER_STATUS_FILE", "scheduler_status.json"))
    if not path.exists():
        return False

    payload = json.loads(path.read_text(encoding="utf-8"))
    updated = payload.get("updated_at_epoch") or payload.get("updated_at")

    if isinstance(updated, (int, float)):
        # 超過五分鐘沒更新就當成卡住。排程的最短間隔是 60 秒。
        return (time.time() - float(updated)) < 300

    # 有檔案但沒有可判讀的時間戳 —— 說不出是新是舊,一律算不健康。
    return False


def _websocket():
    """
    BingX 行情 WebSocket(第四十九節)。

    三種狀態,而且它們的意思不一樣:
      disabled  沒有啟用。系統走 REST,慢一點但正常。
      ok        連著而且有在收資料。
      error     啟用了但**放棄重連**了 —— 這一個必須看得見,
                因為一個安靜地永遠重連的背景執行緒看起來跟正常運作
                一模一樣,而使用者會以為自己有即時行情。
    """
    try:
        from agmcis.exchange.bingx.stream import get_stream

        stream = get_stream()
    except Exception:
        return DISABLED

    if stream is None or not stream.status.running:
        return DISABLED

    if stream.status.gave_up:
        return ERROR

    return OK if stream.status.connected else ERROR


def _news_calendar():
    """
    事件日曆(第五十一節)。三種狀態:

      ok        日曆新鮮,消息面那一層有在保護。
      degraded  快過期,或有幾筆事件解析不了。
      error     過期 / 不存在 / 壞掉 —— **消息面風險現在沒有在保護
                任何東西**,而系統其他部分看起來完全正常。

    這一項不在 CRITICAL_COMPONENTS 裡:日曆過期不代表不能交易,
    代表少一層保護。實單那一側由 LIVE SAFETY GATE 擋。
    """
    try:
        from agmcis.risk import calendar_watch

        state = calendar_watch.inspect_calendar()
    except Exception as exc:
        logger.warning("Health | news_calendar | %s: %s", type(exc).__name__, exc)
        return ERROR

    if state["status"] == calendar_watch.OK:
        return OK
    if state["status"] == calendar_watch.EXPIRING:
        return DEGRADED
    return ERROR


def build_health():
    components = {
        "api": OK,                       # 能跑到這裡,API 就是活的
        "database": _check("database", _database),
        "bingx": _check("bingx", _bingx),
        "market_data": _check("market_data", _market_data),
        "risk_engine": _check("risk_engine", _risk_engine),
        "trading_engine": _check("trading_engine", _trading_engine),
        "agent_engine": _check("agent_engine", _agent_engine),
        "scheduler": _check("scheduler", _scheduler),
        "websocket": _websocket(),
        "news_calendar": _news_calendar(),
    }

    failed = [name for name, state in components.items() if state == ERROR]
    critical = [name for name in failed if name in CRITICAL_COMPONENTS]
    # 「還能用但快不能用了」。它不算 failed(監控不該為它半夜叫人),
    # 但整體狀態要降級 —— 一個回 healthy 的系統沒有人會去看細節。
    degraded = [name for name, state in components.items() if state == DEGRADED]

    if critical:
        status = "unhealthy"
    elif failed or degraded:
        status = "degraded"
    else:
        status = "healthy"

    try:
        from agmcis.config import settings
        mode = getattr(settings, "TRADING_MODE", UNKNOWN)
    except Exception:
        mode = UNKNOWN

    return {
        "status": status,
        "trading_mode": mode,
        "components": components,
        "failed": failed,
        "critical_failed": critical,
        "degraded": degraded,
    }


@router.get("/health")
def health(response: Response):
    """
    HTTP 狀態碼也要對:監控系統看的是狀態碼,不是 JSON 內容。
    一個永遠回 200 的健康端點,對負載平衡器而言等於永遠健康。

    unhealthy -> 503(不能交易)
    degraded  -> 200(還能交易,但有東西壞了,細節在 JSON 裡)
    """
    payload = build_health()

    if payload["status"] == "unhealthy":
        response.status_code = 503

    return payload
