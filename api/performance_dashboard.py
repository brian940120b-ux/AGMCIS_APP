"""
Performance Dashboard(Master Prompt 第六十一節)。

第六十一節列了四塊,每一塊有固定的欄位:

    Account   Equity / Available / Margin / Unrealized PnL / Realized PnL
    Trading   Win Rate / Profit Factor / Expectancy / Trades / Wins / Losses
    Risk      Drawdown / Daily Risk / Weekly Risk / Exposure / Leverage
    Strategy  Best Strategy / Worst Strategy / Strategy Win Rate / Strategy PF

這些數字散在 `/api/stats`、`/api/risk`、`/api/analytics_pro`、
`/api/strategy_health` 四個端點,而且分群統計只在自我檢討的報告裡。
這個端點按第六十一節的版面把它們組成一份。

## 三件不會做的事

**一、不會用少數幾筆就選出「最佳策略」。**
`agmcis/review/attribution.py` 的 Bucket 有 `reliable`(至少 20 筆)。
樣本不足的策略不會被選為 Best 或 Worst —— 一個兩戰兩勝的策略
排在第一名,是在鼓勵人去跟一個不存在的優勢。
不足時 best/worst 回 None,並在 `insufficient_sample` 裡列出來。

**二、Profit Factor 沒有虧損單時回 None,不回無限大。**
無限大排序起來永遠第一。

**三、拿不到的數字回 None,不回 0。**
0 在儀表板上讀起來像一個真的結論。「Drawdown 0%」與
「算不出 Drawdown」在畫面上必須不一樣。
"""
import logging

from fastapi import APIRouter

from agmcis.config import settings

router = APIRouter()
logger = logging.getLogger("agmcis.api.performance_dashboard")


@router.get("/api/performance_dashboard")
def api_performance_dashboard():
    return build_dashboard()


def _safe(name, fn, fallback):
    try:
        return fn()
    except Exception as exc:
        logger.exception("績效面板區塊 %s 失敗", name)
        result = dict(fallback)
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def build_dashboard():
    trades = _closed_trades()

    return {
        "account": _account(),
        "trading": _trading(trades),
        "risk": _risk(),
        "strategy": _strategy(trades),
    }


def _closed_trades():
    try:
        from database_service import get_closed_trades
        return get_closed_trades()
    except Exception:
        logger.exception("績效面板 | 已平倉交易讀取失敗")
        return []


# ---------------- Account ----------------

def _account():
    def run():
        from agmcis.risk.account_state import build_account_state
        from database_service import get_open_trades

        state = build_account_state()
        open_trades = get_open_trades()

        return {
            "equity": state.equity,
            "available_balance": state.available_balance,
            # 佔用保證金 = 每一筆的 size_usdt 加總。用倉位價值加總會
            # 得到一個名字叫保證金但實際是名目金額的數字。
            "margin_used": round(
                sum(float(t.get("size_usdt") or 0) for t in open_trades), 4,
            ),
            "unrealized_pnl": state.unrealized_pnl_usdt,
            "realized_pnl_24h": state.realized_pnl_24h,
            "realized_pnl_7d": state.realized_pnl_7d,
            "open_positions": state.open_positions,
        }

    return _safe("account", run, {
        "equity": None, "available_balance": None, "margin_used": None,
        "unrealized_pnl": None, "realized_pnl_24h": None,
        "realized_pnl_7d": None, "open_positions": None,
    })


# ---------------- Trading ----------------

def _trading(trades):
    def run():
        from agmcis.review.attribution import Bucket, closed_with_pnl

        rows = closed_with_pnl(trades)

        total = Bucket(key="all")
        for trade in rows:
            total.add(float(trade["pnl_usdt"]))

        payload = total.to_dict()
        payload["mode"] = str(getattr(settings, "TRADING_MODE", "paper")).upper()
        # 這一份的樣本夠不夠下結論。第二節:誠實的 55-65% 勝過造假的 75%,
        # 而一份沒說樣本數的勝率,連誠不誠實都判斷不了。
        payload["reliable"] = total.reliable
        return payload

    return _safe("trading", run, {
        "trades": 0, "wins": 0, "losses": 0, "win_rate": None,
        "profit_factor": None, "expectancy": None, "net_pnl": None,
        "reliable": False,
    })


# ---------------- Risk ----------------

def _risk():
    def run():
        from agmcis.risk.account_state import build_account_state
        from agmcis.risk.engine import get_engine

        state = build_account_state()
        gate = get_engine().check_gate(state)
        limits = settings.risk_limits_dict()

        return {
            "max_drawdown_pct": state.max_drawdown_pct,
            # Daily / Weekly Risk 是**已經用掉的**,不是上限。
            # 顯示上限會讓一個已經打滿日虧損的帳戶看起來還有空間。
            "daily_loss_usdt": state.realized_pnl_24h,
            "weekly_loss_usdt": state.realized_pnl_7d,
            "exposure_pct": round(state.exposure_pct, 2),
            "max_leverage": limits.get("MAX_LEVERAGE"),
            "consecutive_losses": state.consecutive_losses,
            "allowed": gate.allowed,
            "blockers": list(gate.blockers),
            "warnings": list(gate.warnings),
            "limits": limits,
        }

    return _safe("risk", run, {
        "max_drawdown_pct": None, "daily_loss_usdt": None,
        "weekly_loss_usdt": None, "exposure_pct": None,
        "max_leverage": None, "allowed": False,
        "blockers": ["RISK_STATE_UNAVAILABLE"], "limits": {},
    })


# ---------------- Strategy ----------------

def _strategy(trades):
    def run():
        from agmcis.review import attribution

        grouped, skipped = attribution.by_strategy(trades)
        buckets = [grouped[key].to_dict() for key in sorted(grouped)]

        reliable = [b for b in buckets if b["reliable"]]
        thin = [b["key"] for b in buckets if not b["reliable"]]

        return {
            "strategies": buckets,
            # 排序只用樣本足夠的那些 —— 見模組說明。
            "best": _pick(reliable, best=True),
            "worst": _pick(reliable, best=False),
            "insufficient_sample": thin,
            "min_sample": attribution.MIN_SAMPLE,
            # key_fn 回 None 的交易被排除了(沒有記策略名的舊資料)。
            # 這個數字要顯示,否則加總對不起來會讓人以為算錯了。
            "unclassified": skipped,
        }

    return _safe("strategy", run, {
        "strategies": [], "best": None, "worst": None,
        "insufficient_sample": [], "unclassified": 0,
    })


def _pick(buckets, best=True):
    """
    依期望值挑最好 / 最差。

    **用期望值不用總損益**:總損益偏袒交易次數多的策略,而那不是
    「比較好」,是「跑得比較多」。也不用 PF —— PF 可能是 None
    (沒有虧損單),而 None 沒辦法排序。
    """
    if not buckets:
        return None

    return (max if best else min)(buckets, key=lambda b: b["expectancy"])
