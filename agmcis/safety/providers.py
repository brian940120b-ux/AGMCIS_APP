"""
把 LiveGate 的各項檢查接到系統的真實狀態。

分開放的理由:`live_gate.py` 是純邏輯,不 import 資料庫或交易所,
所以它可以完整地離線測試 —— 而一個安全閘門的測試涵蓋率很重要。

每個 provider 都可能拋例外。**那是對的** ——
LiveGate 把「檢查本身失敗」算成沒通過。
"""


def preflight_blockers():
    """回傳 BLOCKER 項目的名稱清單。空清單代表全部通過。"""
    import importlib.util
    import io
    import contextlib
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "preflight.py"
    spec = importlib.util.spec_from_file_location("_preflight", path)
    module = importlib.util.module_from_spec(spec)

    # preflight 會印很多字,這裡只要結果
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
        module.main()

    return [name for name, level, _ in module.results if level == module.BLOCKER]


def paper_record():
    from datetime import datetime, timezone

    from database_service import get_closed_trades

    trades = get_closed_trades()
    if not trades:
        return {"closed_trades": 0, "days": 0.0}

    stamps = []
    for trade in trades:
        opened = trade.get("opened_at")
        if opened:
            stamps.append(str(opened))

    days = 0.0
    if len(stamps) >= 2:
        try:
            first = datetime.fromisoformat(min(stamps))
            last = datetime.fromisoformat(max(stamps))
            days = (last - first).total_seconds() / 86400.0
        except ValueError:
            days = 0.0

    return {"closed_trades": len(trades), "days": days}


def self_review():
    from agmcis.review.self_review import run_self_review

    return run_self_review()


def strategy_validation():
    """
    策略驗證結果。

    ⚠️ 這會實際跑一次完整的 Lab 流程(每個標的都要抓 K 棒),很慢。
    閘門評估本來就不該頻繁執行 —— 它是上線前跑一次的東西。
    """
    from strategy_optimizer import get_strategy_optimizer

    return get_strategy_optimizer()


def calibration():
    from agmcis.config import settings
    from agmcis.exchange import specs

    return specs.get_store().calibration_report(settings.WATCHLIST_SYMBOLS)


def reconciliation_summary():
    from api.transparency import _summary

    return _summary()


def kill_switch_status():
    from agmcis.risk.kill_switch import get_kill_switch

    return get_kill_switch().status()


DEFAULT_PROVIDERS = {
    "preflight": preflight_blockers,
    "paper_record": paper_record,
    "self_review": self_review,
    "strategy_validation": strategy_validation,
    "calibration": calibration,
    "reconciliation": reconciliation_summary,
    "kill_switch": kill_switch_status,
}


def build_gate(**kwargs):
    from agmcis.safety.live_gate import LiveGate

    return LiveGate(providers=DEFAULT_PROVIDERS, **kwargs)
