"""
出場管理。

position_monitor 處理的是**機械式**出場:停損、停利、強平 ——
價格碰到哪條線就出場,不需要判斷。

這個模組處理的是**判斷式**出場:Agent 群看完當下的市況,
認為這個部位該收了。Phase 9 的 ExitAgent 到目前為止只會「提示」,
這裡讓它真的執行。

兩條規則:

  1. **機械式出場優先。** 停損永遠比任何判斷更早、更硬。
     這一層不碰停損,也不會把停損移開。
  2. **判斷式出場只會平倉,不會開倉、不會加碼、不會移動停損。**
     一個能開倉的「出場管理器」不是出場管理器。
"""
import logging

from agmcis.agents.base import Vote
from agmcis.core.enums import Direction
from agmcis.execution import engine as execution_module
from agmcis.signal import agent_pipeline

logger = logging.getLogger("agmcis.execution.exit_manager")

# ExitAgent 的信心要到這個程度才真的平倉。
# 提示的門檻可以低,動手的門檻必須高。
EXIT_CONFIDENCE_THRESHOLD = 80.0

# 共識轉向到反方向時的信心門檻
REVERSAL_CONFIDENCE_THRESHOLD = 70.0

REASON_EXIT_AGENT = "Exit Agent:風險升高"
REASON_REVERSAL = "Agent 共識轉向"


def _position_direction(position):
    return Direction.parse(
        position.get("signal") or position.get("direction") or "",
    ) or Direction.WAIT


def evaluate_position(position, registry=None, supervisor=None):
    """
    回傳 (要不要平倉, 原因, 診斷資料)。

    這個函式**不平倉**,只做判斷 —— 讓它可以單獨測試,
    也讓「判斷」與「動作」在程式碼上是分開的兩件事。
    """
    symbol = position.get("symbol")

    deliberation, report = agent_pipeline.analyse_symbol(
        symbol, position=position, registry=registry, supervisor=supervisor,
    )

    opinions = {o.agent: o for o in deliberation.opinions}
    diagnostics = {
        "symbol": symbol,
        "votes": {a: o.vote.value for a, o in opinions.items()},
        "errors": [f"{o.agent}: {o.error}" for o in deliberation.opinions if o.error],
        "blocked_reason": deliberation.blocked_reason,
    }

    # 所有 Agent 都棄權通常代表資料不可用。
    # **資料不可用時不做判斷式出場** —— 看不見市況就不要憑空決定出場。
    # 停損還在,機械式保護不受影響。
    if all(o.vote is Vote.ABSTAIN for o in deliberation.opinions):
        diagnostics["skipped"] = "所有 Agent 棄權(通常是資料不可用)"
        return False, None, diagnostics

    exit_opinion = opinions.get("exit")
    if (exit_opinion is not None
            and exit_opinion.vote is Vote.WAIT
            and exit_opinion.confidence >= EXIT_CONFIDENCE_THRESHOLD):
        reason = f"{REASON_EXIT_AGENT}({'; '.join(exit_opinion.reasons)})"
        return True, reason, diagnostics

    held = _position_direction(position)
    consensus = deliberation.direction

    if (held.is_directional and consensus.is_directional
            and consensus is not held
            and deliberation.confidence >= REVERSAL_CONFIDENCE_THRESHOLD):
        reason = (
            f"{REASON_REVERSAL}:持有{held.value},共識{consensus.value} "
            f"(信心 {deliberation.confidence:.0f})"
        )
        return True, reason, diagnostics

    return False, None, diagnostics


def run_exit_manager(positions=None, engine=None, notify=True,
                     registry=None, supervisor=None):
    """
    掃描所有持倉,對符合條件的執行平倉。

    單一部位判斷失敗不會中斷整輪,但**會被記錄下來**,不是靜靜跳過 ——
    一個安靜失敗的出場管理器,表現起來跟「沒有出場條件成立」一模一樣。
    """
    if positions is None:
        from database_service import get_open_trades
        positions = get_open_trades()

    engine = engine or execution_module.get_engine()

    closed = []
    failed = []
    held = []
    errors = []

    for position in positions:
        symbol = position.get("symbol")

        try:
            should_exit, reason, diagnostics = evaluate_position(
                position, registry=registry, supervisor=supervisor,
            )
        except Exception as exc:
            logger.exception("Exit Manager | EVALUATE_FAILED | %s", symbol)
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            continue

        if not should_exit:
            held.append({"symbol": symbol, "diagnostics": diagnostics})
            continue

        result = engine.close(symbol, reason=reason)

        if result.ok:
            logger.info("Exit Manager | CLOSED | %s | %s", symbol, reason)
            closed.append({"symbol": symbol, "reason": reason,
                           "price": result.fill_price})

            if notify:
                _notify(position, symbol, result, reason)
        else:
            logger.warning(
                "Exit Manager | CLOSE_FAILED | %s | %s | %s",
                symbol, reason, result.reason,
            )
            failed.append({"symbol": symbol, "reason": result.reason})

    return {
        "checked": len(positions),
        "closed": closed,
        "closed_count": len(closed),
        "failed": failed,
        "held": held,
        "errors": errors,
    }


def _notify(position, symbol, result, reason):
    try:
        from notifier import notify_close_trade
        notify_close_trade(
            symbol, position.get("signal"), result.fill_price, reason=reason,
        )
    except Exception as exc:
        # 通知失敗不該讓平倉這件事看起來失敗了 —— 倉位已經平掉了。
        logger.warning("Exit Manager | NOTIFY_FAILED | %s | %s", symbol, exc)


def run_naked_position_sweep(positions=None, engine=None):
    """
    裸倉巡檢。沒有停損的部位沒有虧損上限,一律平掉。

    開倉時的檢查是主要防線(Execution Engine),這是安全網。
    """
    if positions is None:
        from database_service import get_open_trades
        positions = get_open_trades()

    engine = engine or execution_module.get_engine()
    return engine.sweep_naked_positions(positions)
