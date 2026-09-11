"""
Kill Switch(Master Prompt 第 47 條)。

原本的「緊急停止」只做到一件事:停止新開倉。
但真正需要 kill switch 的時候(系統行為異常、市場劇變、發現重大 bug),
光是不開新倉不夠 —— 已經掛在市場上的單與已經持有的部位還在承擔風險。

完整的 kill switch 有四個動作:
    1. STOP_NEW        停止新開倉
    2. CANCEL_ORDERS   撤掉所有掛單
    3. CLOSE_POSITIONS 平掉所有部位
    4. DISABLE_AUTO    關閉自動交易

**每一個動作都必須寫稽核紀錄**,包含誰觸發、什麼時候、結果如何。
緊急操作沒有紀錄,事後就無法還原當時發生什麼事。

設計上刻意分成兩段:
    arm()    只做第 1、4 項(停止新單)—— 這是安全且可逆的
    panic()  做全部四項 —— 這會實際動到市場部位,需要明確意圖
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from agmcis.config import settings

logger = logging.getLogger("agmcis.kill_switch")

ACTION_STOP_NEW = "STOP_NEW"
ACTION_CANCEL_ORDERS = "CANCEL_ORDERS"
ACTION_CLOSE_POSITIONS = "CLOSE_POSITIONS"
ACTION_DISABLE_AUTO = "DISABLE_AUTO"

AUDIT_FILE = "kill_switch_audit.log"


@dataclass
class KillSwitchResult:
    triggered_by: str
    reason: str
    actions: List[str] = field(default_factory=list)
    cancelled_orders: int = 0
    closed_positions: int = 0
    errors: List[str] = field(default_factory=list)
    at: str = ""

    @property
    def ok(self):
        return not self.errors

    def to_dict(self):
        return {
            "triggered_by": self.triggered_by,
            "reason": self.reason,
            "actions": list(self.actions),
            "cancelled_orders": self.cancelled_orders,
            "closed_positions": self.closed_positions,
            "errors": list(self.errors),
            "at": self.at,
        }


class KillSwitch:
    def __init__(self, stop_file=None, audit_file=None,
                 order_canceller=None, position_closer=None,
                 open_orders_provider=None, open_positions_provider=None):
        self.stop_file = Path(stop_file or settings.EMERGENCY_STOP_FILE)
        self.audit_file = Path(audit_file or AUDIT_FILE)
        self._cancel_order = order_canceller
        self._close_position = position_closer
        self._open_orders = open_orders_provider
        self._open_positions = open_positions_provider

    # ---------------- 狀態 ----------------

    @property
    def is_armed(self):
        return self.stop_file.exists()

    def status(self):
        return {
            "armed": self.is_armed,
            "stop_file": str(self.stop_file),
            "audit_file": str(self.audit_file),
            "can_cancel_orders": self._cancel_order is not None,
            "can_close_positions": self._close_position is not None,
        }

    # ---------------- 稽核 ----------------

    def _audit(self, event, payload):
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }

        try:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            # 稽核寫不進去也不能讓緊急操作停下來,但一定要讓人知道
            logger.exception("Kill Switch | 稽核紀錄寫入失敗 | %s", exc)

        logger.warning("Kill Switch | %s | %s", event, json.dumps(record, ensure_ascii=False))
        return record

    def read_audit(self, limit=50):
        if not self.audit_file.exists():
            return []

        lines = self.audit_file.read_text(encoding="utf-8").strip().split("\n")
        records = []
        for line in lines[-limit:]:
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"raw": line})
        return records

    # ---------------- 動作 ----------------

    def arm(self, triggered_by="system", reason=""):
        """
        停止新開倉。安全且可逆 —— 不會動到既有部位。

        這是自動風控觸發時該用的,例如單日虧損達上限。
        """
        at = datetime.now(timezone.utc).isoformat()

        try:
            self.stop_file.write_text(
                f"{at}\ntriggered_by={triggered_by}\nreason={reason}\n",
                encoding="utf-8",
            )
        except Exception as exc:
            result = KillSwitchResult(
                triggered_by=triggered_by, reason=reason,
                errors=[f"無法建立停止旗標: {exc}"], at=at,
            )
            self._audit("ARM_FAILED", result.to_dict())
            return result

        result = KillSwitchResult(
            triggered_by=triggered_by, reason=reason,
            actions=[ACTION_STOP_NEW, ACTION_DISABLE_AUTO], at=at,
        )
        self._audit("ARM", result.to_dict())
        return result

    def disarm(self, triggered_by="system", reason=""):
        """解除。這也要記錄 —— 誰在什麼時候把保險打開同樣重要。"""
        at = datetime.now(timezone.utc).isoformat()
        existed = self.stop_file.exists()

        if existed:
            try:
                self.stop_file.unlink()
            except Exception as exc:
                result = KillSwitchResult(
                    triggered_by=triggered_by, reason=reason,
                    errors=[f"無法移除停止旗標: {exc}"], at=at,
                )
                self._audit("DISARM_FAILED", result.to_dict())
                return result

        result = KillSwitchResult(
            triggered_by=triggered_by,
            reason=reason or ("解除" if existed else "原本就未啟動"),
            at=at,
        )
        self._audit("DISARM", result.to_dict())
        return result

    def panic(self, triggered_by="manual", reason="", close_positions=True):
        """
        完整的緊急停止:停新單 + 撤掉所有掛單 + 平掉所有部位。

        ⚠️ 這會實際動到市場部位。與 arm() 分開是刻意的 ——
        自動風控只會 arm(),panic() 需要明確意圖。

        任何一個步驟失敗都不會中止其他步驟:能撤的先撤、能平的先平,
        失敗的記在 errors 裡。緊急狀況下「做到多少算多少」比「一個失敗全放棄」好。
        """
        at = datetime.now(timezone.utc).isoformat()
        result = KillSwitchResult(triggered_by=triggered_by, reason=reason, at=at)

        # 1. 先停新單 —— 撤單與平倉期間絕對不能又開新倉
        arm_result = self.arm(triggered_by=triggered_by, reason=f"PANIC: {reason}")
        result.actions.extend(arm_result.actions)
        result.errors.extend(arm_result.errors)

        # 2. 撤掉所有掛單
        if self._cancel_order and self._open_orders:
            try:
                orders = self._open_orders() or []
            except Exception as exc:
                orders = []
                result.errors.append(f"取得掛單清單失敗: {exc}")

            for order in orders:
                try:
                    self._cancel_order(order)
                    result.cancelled_orders += 1
                except Exception as exc:
                    result.errors.append(f"撤單失敗 {order}: {exc}")

            result.actions.append(ACTION_CANCEL_ORDERS)
        else:
            result.errors.append("未接上撤單能力,掛單沒有被撤掉")

        # 3. 平掉所有部位
        if close_positions:
            if self._close_position and self._open_positions:
                try:
                    positions = self._open_positions() or []
                except Exception as exc:
                    positions = []
                    result.errors.append(f"取得持倉清單失敗: {exc}")

                for position in positions:
                    try:
                        self._close_position(position)
                        result.closed_positions += 1
                    except Exception as exc:
                        result.errors.append(f"平倉失敗 {position}: {exc}")

                result.actions.append(ACTION_CLOSE_POSITIONS)
            else:
                result.errors.append("未接上平倉能力,部位沒有被平掉")

        self._audit("PANIC", result.to_dict())
        return result


def build_wired_kill_switch(stop_file=None, audit_file=None):
    """
    接上真正的執行能力(Phase 16)。

    Phase 5 建了 kill switch 的骨架,但 `order_canceller` 與 `position_closer`
    一直是 None —— `panic()` 會記錄「未接上平倉能力,部位沒有被平掉」然後結束。
    一個按下去不會平倉的緊急按鈕,比沒有按鈕更危險:你會以為自己按過了。

    平倉走 Execution Engine,理由跟 Phase 12 一樣:
    不要在系統裡長出第二條下單/平倉路徑。

    撤單在模擬盤沒有意義(模擬盤沒有掛在市場上的單),
    所以 `order_canceller` 仍然是 None,而 `status()` 會據實回報
    `can_cancel_orders = False`。假裝有這個能力比沒有更糟。
    """
    from agmcis.execution.engine import get_engine

    def close_position(position):
        symbol = position.get("symbol") if isinstance(position, dict) else position
        result = get_engine().close(symbol, reason="Kill Switch 緊急平倉")

        if not result.ok:
            raise RuntimeError(result.reason or f"{symbol} 平倉失敗")

        return result

    def open_positions():
        from database_service import get_open_trades
        return get_open_trades()

    return KillSwitch(
        stop_file=stop_file,
        audit_file=audit_file,
        position_closer=close_position,
        open_positions_provider=open_positions,
    )


_switch = None


def get_kill_switch():
    global _switch
    if _switch is None:
        _switch = build_wired_kill_switch()
    return _switch


def set_kill_switch(switch):
    global _switch
    _switch = switch
