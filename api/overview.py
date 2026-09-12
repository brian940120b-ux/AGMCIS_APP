"""
系統總覽(Master Prompt 第一百零五節)。

第一百零五節描述的是**打開系統的第一眼**應該看到什麼:

    AGMCIS / AI PRIVATE TRADING BUTLER
    SYSTEM STATUS  🟢 ONLINE
    EXCHANGE       BingX
    MARKETS        Standard Futures / Perpetual Futures
    MODE           PAPER
    AI AGENTS      12/12 ACTIVE

    然後 Agent 開始工作,最後列出 TOP OPPORTUNITIES,
    點進去看得到 WHY。

這個端點把那一頁需要的東西一次組好。

## 為什麼是一個端點而不是五個

第一眼看到的東西必須**同時**正確。分成五個端點的話,狀態列可能顯示
PAPER 而下面的機會清單是用 LIVE 的參數算出來的 —— 那兩個數字在畫面上
並排,但它們來自不同的時刻。一次組好就沒有這個問題。

## 「12/12 ACTIVE」不是寫死的

Agent 數量從註冊表讀,而且會回報**這一輪實際有意見的**有幾個。
一個永遠顯示 12/12 的儀表板,在三個 Agent 因為缺資料而棄權時
看起來一模一樣 —— 那正是最需要被看見的時候。
"""
import logging

from fastapi import APIRouter, Query

from agmcis.config import settings

router = APIRouter()
logger = logging.getLogger("agmcis.api.overview")

# 第一眼要掃幾檔。太多會讓首頁變慢,而首頁慢等於沒有人會看。
DEFAULT_SCAN = 5


def _safe(name, fn, fallback):
    try:
        return fn()
    except Exception as exc:
        logger.exception("總覽區塊 %s 失敗", name)
        result = dict(fallback)
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


@router.get("/api/overview")
def api_overview(limit: int = Query(DEFAULT_SCAN, ge=1, le=20)):
    return build_overview(limit=limit)


def build_overview(limit=DEFAULT_SCAN):
    status = _status()
    agents = _agents()
    opportunities = _opportunities(limit=limit)

    return {
        "title": "AGMCIS",
        "subtitle": "AI PRIVATE TRADING BUTLER",
        "status": status,
        "agents": agents,
        "opportunities": opportunities,
    }


def _status():
    """狀態列。模式是這裡最重要的一格 —— 它決定其他數字是不是真錢。"""
    def run():
        from api.health import build_health
        from agmcis.safety import safe_live

        health = build_health()
        mode = str(getattr(settings, "TRADING_MODE", "paper")).lower()

        return {
            "online": health["status"] != "unhealthy",
            "health": health["status"],
            "components": health["components"],
            "exchange": "BingX",
            "markets": ["Standard Futures", "Perpetual Futures"],
            "mode": mode.upper(),
            "is_live": mode == "live",
            "safe_live": safe_live.snapshot(),
            "auto_trading": bool(getattr(settings, "AUTO_TRADING_ENABLED", False)),
        }

    return _safe("status", run, {
        "online": False, "health": "unknown", "components": {},
        "exchange": "BingX", "mode": "UNKNOWN", "is_live": False,
    })


def _agents():
    """
    Agent 名冊與這一輪的活躍度。

    `active` 是**這一輪真的有意見的** Agent 數,不是註冊的數量。
    一個永遠顯示 12/12 的儀表板,在三個 Agent 棄權時看起來一模一樣。
    """
    def run():
        from agmcis.agents.registry import get_registry

        registry = get_registry()
        names = list(registry.names)

        return {
            "registered": len(names),
            "names": names,
            # 活躍數要跑一輪才知道,由 _opportunities 那邊填 ——
            # 這裡先給註冊數,前端會用機會清單裡的實際票數覆蓋。
            "active": None,
            "roles": _roles(names),
        }

    return _safe("agents", run, {"registered": 0, "names": [], "active": None})


# Agent 的角色說明。給 UI 顯示「這個 Agent 在做什麼」用(第六十二節)。
ROLE_TEXT = {
    "trend": "分析趨勢結構",
    "momentum": "計算動能轉折",
    "mean_reversion": "尋找過度偏離",
    "breakout": "監看突破與假突破",
    "volume": "檢查量能是否confirm",
    "btc_correlation": "比對大盤方向",
    "volatility": "評估波動環境",
    "regime": "判定市場狀態",
    "funding": "檢查資金費率",
    "position_risk": "計算部位風險",
    "exit": "評估出場條件",
    "news": "掃描消息面",
}


def _roles(names):
    return [
        {"agent": name, "doing": ROLE_TEXT.get(name, "分析中")}
        for name in names
    ]


def _opportunities(limit=DEFAULT_SCAN, min_score=None):
    """
    TOP 機會(第五十三節)。

    **品質不足就不硬選。** 第五十三節寫得很明白:「如果沒有足夠高品質:
    NO HIGH QUALITY SETUP,不要硬選 TOP 3」。一個永遠有三個推薦的系統,
    在沒有機會的時候會推薦三個最不差的 —— 而「最不差」不是「好」。
    """
    def run():
        from agmcis.signal import agent_pipeline

        threshold = (
            float(min_score) if min_score is not None
            else float(getattr(settings, "MIN_SIGNAL_SCORE", 70))
        )

        symbols = list(settings.WATCHLIST_SYMBOLS)[:limit]
        rows = []
        active_agents = 0

        for symbol in symbols:
            deliberation, report = agent_pipeline.analyse_symbol(symbol)
            payload = report.to_dict() if report else {}

            votes = dict(deliberation.votes)
            active_agents = max(
                active_agents,
                sum(1 for v in votes.values() if v != "ABSTAIN"),
            )

            score = payload.get("score")
            rows.append({
                "symbol": symbol,
                "direction": deliberation.direction.value,
                "score": score,
                "confidence": round(deliberation.confidence, 2),
                "tradable": deliberation.intent is not None,
                "blocked_reason": deliberation.blocked_reason,
                "votes": votes,
                # WHY:點進去要看到的東西。每個 Agent 的理由都在這裡,
                # 不是只有一句「趨勢向上」。
                "why": [
                    {
                        "agent": o.agent,
                        "vote": o.vote.value,
                        "confidence": round(o.confidence, 1),
                        "reasons": list(o.reasons)[:3],
                        "error": o.error,
                    }
                    for o in deliberation.opinions
                ],
                "regime": payload.get("regime"),
                "score_breakdown": payload.get("score_breakdown"),
            })

        ranked = sorted(
            rows,
            key=lambda r: (r["score"] if r["score"] is not None else -1),
            reverse=True,
        )
        qualified = [
            r for r in ranked
            if r["tradable"] and (r["score"] or 0) >= threshold
        ]

        return {
            "scanned": len(rows),
            "min_score": threshold,
            "top": qualified[:3],
            # 沒有合格的機會時,把掃過的全部列出來並標明**它們不合格**。
            # 藏起來會讓人以為系統沒在跑。
            "considered": ranked,
            "no_high_quality_setup": not qualified,
            "active_agents": active_agents,
        }

    return _safe("opportunities", run, {
        "scanned": 0, "top": [], "considered": [],
        "no_high_quality_setup": True, "active_agents": 0,
    })
