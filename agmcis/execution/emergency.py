"""
EMERGENCY PROTECTION:成交了但停損沒掛上去的時候要做什麼。

Master Prompt 第十八節把這件事拆成六個步驟:

    1. Retry SL
    2. Verify SL
    3. If still failed → Reduce Position
    4. If necessary → Close Position
    5. Disable New Orders
    6. Notify User

在這個模組出現以前,Execution Engine 只做了第 4 步:檢查一次保護,
沒有就立刻平倉。那是安全的,但過度反應 —— 掛停損單失敗最常見的原因是
**一次網路抖動或一次 429**,而不是「這個部位不可能有停損」。
重試一次就能救回來的部位,被白白平掉一次就付一次來回手續費與滑點。

所以順序很重要:**先想辦法救,救不動才縮,縮不動才砍。**
但每一步之間都要重新 Verify —— 「送出了重試請求」不等於「停損存在」。

## 兩條不讓步的規則

**一、任何一步的結果不明,一律當成沒有保護。**
`ensure_stop_loss()` 拋例外、`has_protection()` 查不到、broker 根本
沒實作這個方法 —— 全部走同一條路:視為仍然裸倉,進到下一步。
「查不到」被當成「沒問題」是這整個系統最容易死人的一種寫法。

**二、進到第 3 步就停止新單,即使後來救回來了。**
會掛不上停損的環境,下一單一樣會掛不上。這時候繼續開新倉,是在
一個已知會失敗的通道上重複同一個賭注。所以停新單的條件不是
「最後有沒有平掉」,而是「有沒有走到需要縮倉這一步」。

停新單寫的是 `TRADING_PAUSE_FILE` —— Risk Engine 的帳戶層閘門本來就
會讀它。這裡不另外發明一套開關,是為了不要有第二個「交易是否暫停」
的真相來源。解除必須是人工的。
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from agmcis.config import settings

logger = logging.getLogger("agmcis.execution.emergency")


# 重試幾次掛停損。次數不宜多:每一次重試,裸倉就多活一段時間。
DEFAULT_SL_RETRIES = 2

# 第 3 步縮掉多少。縮一半是為了把最大虧損砍半,不是為了留下部位。
DEFAULT_REDUCE_FRACTION = 0.5


# 結局
PROTECTED = "PROTECTED"                        # 本來就有保護,沒有進緊急流程
PROTECTED_ON_RETRY = "PROTECTED_ON_RETRY"      # 重試後掛上了
PROTECTED_AFTER_REDUCE = "PROTECTED_AFTER_REDUCE"   # 縮倉後掛上了
CLOSED = "CLOSED"                              # 救不回來,已平倉
STUCK = "STUCK"                                # 救不回來又平不掉 —— 需要人工


@dataclass
class ProtectionOutcome:
    status: str
    steps: List[str] = field(default_factory=list)
    new_orders_disabled: bool = False
    reduced_fraction: Optional[float] = None
    reason: Optional[str] = None

    @property
    def protected(self):
        return self.status in (
            PROTECTED, PROTECTED_ON_RETRY, PROTECTED_AFTER_REDUCE,
        )

    @property
    def position_open(self):
        """這個結局結束之後,倉位還在不在。"""
        return self.protected

    @property
    def needs_human(self):
        return self.status == STUCK

    def to_dict(self):
        return {
            "status": self.status,
            "steps": list(self.steps),
            "new_orders_disabled": self.new_orders_disabled,
            "reduced_fraction": self.reduced_fraction,
            "reason": self.reason,
            "protected": self.protected,
            "needs_human": self.needs_human,
        }


def _verify(broker, symbol, steps):
    """
    停損現在存不存在。

    查詢本身失敗一律回 False —— 見模組開頭的規則一。
    """
    try:
        protected = bool(broker.has_protection(symbol))
    except NotImplementedError:
        logger.error(
            "Emergency | VERIFY_UNSUPPORTED | %s | "
            "broker 沒有實作 has_protection,無法確認停損存在", symbol,
        )
        steps.append("verify:unsupported")
        return False
    except Exception as exc:
        logger.exception("Emergency | VERIFY_FAILED | %s", symbol)
        steps.append(f"verify:error:{type(exc).__name__}")
        return False

    steps.append("verify:ok" if protected else "verify:missing")
    return protected


def _retry_stop_loss(broker, symbol, stop_price, attempt, steps):
    """送一次掛停損請求。回傳有沒有送成功(不等於停損存在)。"""
    try:
        ok = bool(broker.ensure_stop_loss(symbol, stop_price))
    except NotImplementedError:
        logger.error(
            "Emergency | RETRY_UNSUPPORTED | %s | "
            "broker 沒有實作 ensure_stop_loss,無法重試掛停損", symbol,
        )
        steps.append("retry:unsupported")
        return False
    except Exception as exc:
        logger.exception("Emergency | RETRY_FAILED | %s | 第 %s 次", symbol, attempt)
        steps.append(f"retry{attempt}:error:{type(exc).__name__}")
        return False

    steps.append(f"retry{attempt}:{'sent' if ok else 'refused'}")
    return ok


def _reduce(broker, symbol, fraction, steps):
    """縮倉。回傳有沒有真的縮掉。"""
    try:
        ok = bool(broker.reduce_position(
            symbol, fraction, reason="裸倉緊急縮倉(停損掛不上去)",
        ))
    except NotImplementedError:
        logger.error(
            "Emergency | REDUCE_UNSUPPORTED | %s | "
            "broker 沒有實作 reduce_position,直接進平倉", symbol,
        )
        steps.append("reduce:unsupported")
        return False
    except Exception as exc:
        logger.exception("Emergency | REDUCE_FAILED | %s", symbol)
        steps.append(f"reduce:error:{type(exc).__name__}")
        return False

    steps.append("reduce:ok" if ok else "reduce:refused")
    return ok


def disable_new_orders(reason, pause_file=None):
    """
    第 5 步。寫 TRADING_PAUSE_FILE,Risk Engine 的閘門下一次就會擋住新單。

    寫檔失敗是 CRITICAL:這代表「已知會掛不上停損」這件事沒有被記住,
    下一個訊號會照常開倉。
    """
    path = Path(pause_file or getattr(settings, "TRADING_PAUSE_FILE",
                                      "trading_pause.flag"))
    try:
        path.write_text(f"EMERGENCY_PROTECTION: {reason}\n", encoding="utf-8")
    except Exception:
        logger.critical(
            "Emergency | PAUSE_WRITE_FAILED | %s | "
            "無法停止新單,系統會在同樣的環境下繼續開倉", path,
        )
        return False

    logger.critical("Emergency | NEW_ORDERS_DISABLED | %s | %s", path, reason)
    return True


def notify(symbol, outcome, notifier=None):
    """
    第 6 步。通知失敗不改變結局 —— 倉位該平的已經平了。
    """
    message = (
        f"🚨 AGMCIS EMERGENCY PROTECTION\n"
        f"{symbol}\n"
        f"結果:{outcome.status}\n"
        f"步驟:{' → '.join(outcome.steps) if outcome.steps else '-'}\n"
        f"新單:{'已停止' if outcome.new_orders_disabled else '未停止'}"
    )
    if outcome.reason:
        message += f"\n原因:{outcome.reason}"
    if outcome.needs_human:
        message += "\n⚠️ 需要人工介入:倉位還在且沒有停損"

    try:
        if notifier is None:
            from notifier import send_telegram as notifier
        notifier(message)
        return True
    except Exception as exc:
        logger.warning("Emergency | NOTIFY_FAILED | %s | %s", symbol, exc)
        return False


def protect(broker, symbol, stop_price, retries=DEFAULT_SL_RETRIES,
            reduce_fraction=DEFAULT_REDUCE_FRACTION,
            close_position=None, pause_file=None, notifier=None,
            disable_orders=True):
    """
    六步驟緊急保護。

    `close_position` 是一個 callable(symbol, reason) -> bool,由呼叫端傳入 ——
    Execution Engine 的平倉不只是送單,還要推狀態機、寫 order store,
    這個模組不該知道那些事。沒有傳就退回 broker.close_position()。

    回傳 ProtectionOutcome。呼叫端必須看 `protected`,不要看有沒有例外。
    """
    steps = []

    # ---- 第 2 步先跑:已經有保護就什麼都不用做 ----
    if _verify(broker, symbol, steps):
        return ProtectionOutcome(status=PROTECTED, steps=steps)

    logger.error(
        "Emergency | NAKED_POSITION | %s | 已成交但沒有停損,進入緊急保護", symbol,
    )

    # ---- 第 1 + 2 步:重試並逐次確認 ----
    for attempt in range(1, max(0, retries) + 1):
        _retry_stop_loss(broker, symbol, stop_price, attempt, steps)

        if _verify(broker, symbol, steps):
            logger.warning(
                "Emergency | PROTECTED_ON_RETRY | %s | 第 %s 次重試掛上停損",
                symbol, attempt,
            )
            outcome = ProtectionOutcome(status=PROTECTED_ON_RETRY, steps=steps)
            notify(symbol, outcome, notifier=notifier)
            return outcome

    # 走到這裡代表重試救不回來。不管後面結果如何,新單一律停 ——
    # 見模組開頭的規則二。
    reason = f"{symbol} 掛停損失敗 {retries} 次"
    disabled = disable_new_orders(reason, pause_file=pause_file) if disable_orders else False

    # ---- 第 3 步:縮倉 ----
    reduced = _reduce(broker, symbol, reduce_fraction, steps)

    if reduced:
        _retry_stop_loss(broker, symbol, stop_price, "_after_reduce", steps)

        if _verify(broker, symbol, steps):
            logger.warning(
                "Emergency | PROTECTED_AFTER_REDUCE | %s | 縮倉 %.0f%% 後掛上停損",
                symbol, reduce_fraction * 100,
            )
            outcome = ProtectionOutcome(
                status=PROTECTED_AFTER_REDUCE, steps=steps,
                new_orders_disabled=disabled,
                reduced_fraction=reduce_fraction,
            )
            notify(symbol, outcome, notifier=notifier)
            return outcome

    # ---- 第 4 步:平倉 ----
    closed, close_reason = _close(broker, symbol, close_position, steps)

    outcome = ProtectionOutcome(
        status=CLOSED if closed else STUCK,
        steps=steps,
        new_orders_disabled=disabled,
        reduced_fraction=reduce_fraction if reduced else None,
        reason=None if closed else close_reason,
    )

    if closed:
        logger.error(
            "Emergency | CLOSED | %s | 停損救不回來,已平倉", symbol,
        )
    else:
        logger.critical(
            "Emergency | STUCK | %s | 裸倉且平不掉,需要人工介入:%s",
            symbol, close_reason,
        )

    notify(symbol, outcome, notifier=notifier)
    return outcome


def _close(broker, symbol, close_position, steps):
    """回傳 (有沒有平掉, 失敗原因)。"""
    try:
        if close_position is not None:
            ok = bool(close_position(symbol, "裸倉緊急平倉(停損掛不上去)"))
            steps.append("close:ok" if ok else "close:refused")
            return ok, None if ok else "平倉被拒絕"

        result = broker.close_position(
            symbol, reason="裸倉緊急平倉(停損掛不上去)",
        )
    except Exception as exc:
        logger.exception("Emergency | CLOSE_EXCEPTION | %s", symbol)
        steps.append(f"close:error:{type(exc).__name__}")
        return False, f"{type(exc).__name__}: {exc}"

    ok = bool(getattr(result, "ok", False))
    steps.append("close:ok" if ok else "close:refused")
    return ok, None if ok else getattr(result, "reason", "平倉失敗")
