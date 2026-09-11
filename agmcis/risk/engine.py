"""
Risk Engine —— 唯一的風險裁決入口。

鐵律(Master Prompt 第 19 條):
  任何開倉路徑都必須先通過這一層,而且它可以否決任何 Agent 的建議。
  即使所有策略都說 STRONG BUY,Risk Engine 說 REJECT 就是不開。

它回答三個問題,順序不能顛倒:
  1. 現在可不可以開新倉?      (帳戶層級的閘門)
  2. 這一筆該用幾倍槓桿?      (由停損距離與波動度決定,不是信心)
  3. 這一筆該押多少保證金?    (由風險與停損距離反推)

輸入是 TradeIntent(Agent 唯一能產出的東西),輸出是 RiskDecision。
Execution Engine 只接受 approved=True 的裁決。
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from agmcis.config import settings
from agmcis.core.models import RiskDecision, TradeIntent
from agmcis.risk import leverage as leverage_module
from agmcis.risk import position_sizing

logger = logging.getLogger("agmcis.risk_engine")


@dataclass
class AccountState:
    """
    風控判斷需要的帳戶狀態。由呼叫端提供,讓引擎保持純粹可測。

    刻意不讓引擎自己去查 —— 自己查會讓每次判斷都打一次資料庫與 API,
    而且沒辦法寫測試。
    """
    equity: float
    available_balance: Optional[float] = None
    open_positions: int = 0
    current_exposure_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    realized_pnl_24h: float = 0.0
    trades_24h: int = 0
    consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    open_symbols: List[str] = field(default_factory=list)

    @property
    def exposure_pct(self):
        if not self.equity:
            return 0.0
        return self.current_exposure_usdt / self.equity * 100.0


@dataclass
class GateResult:
    allowed: bool
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    emergency: bool = False

    @property
    def reason(self):
        return ",".join(self.blockers) if self.blockers else None


class RiskEngine:
    def __init__(self, limits=None):
        # limits 預設用全域設定;測試可以注入自己的
        self._limits = limits

    def _limit(self, name, default=None):
        if self._limits is not None and name in self._limits:
            return self._limits[name]
        return getattr(settings, name, default)

    # ---------------- 1. 帳戶層級閘門 ----------------

    def check_gate(self, state: AccountState) -> GateResult:
        """
        帳戶層級的開倉許可。與「要開哪一檔」無關。

        任何一項不通過就不能開新倉。這些是硬性的,不是建議。
        """
        blockers = []
        warnings = []
        emergency = False

        if Path(self._limit("EMERGENCY_STOP_FILE", "emergency.stop")).exists():
            blockers.append("EMERGENCY_STOP_FILE")
            emergency = True

        if Path(self._limit("TRADING_PAUSE_FILE", "trading_pause.flag")).exists():
            blockers.append("TRADING_PAUSE_FLAG")

        if not self._limit("AUTO_TRADING_ENABLED", True):
            blockers.append("AUTO_TRADING_DISABLED")

        if state.max_drawdown_pct >= self._limit("MAX_DRAWDOWN_PCT", 15):
            blockers.append("MAX_DRAWDOWN")
            emergency = True

        if state.exposure_pct >= self._limit("MAX_EXPOSURE_PCT", 80):
            blockers.append("MAX_EXPOSURE")

        if state.open_positions >= self._limit("MAX_OPEN_POSITIONS", 5):
            blockers.append("MAX_OPEN_POSITIONS")

        if state.unrealized_pnl_usdt <= self._limit("MAX_TOTAL_OPEN_LOSS_USDT", -300):
            blockers.append("MAX_TOTAL_OPEN_LOSS")

        daily_limit = abs(self._limit("MAX_DAILY_LOSS_USDT", 300))
        if state.realized_pnl_24h <= -daily_limit:
            blockers.append("MAX_DAILY_LOSS")
            emergency = True

        if state.consecutive_losses >= self._limit("MAX_CONSECUTIVE_LOSSES", 4):
            blockers.append("MAX_CONSECUTIVE_LOSSES")

        if state.trades_24h >= self._limit("MAX_TRADES_PER_DAY", 10):
            blockers.append("MAX_TRADES_PER_DAY")

        min_pf = self._limit("MIN_PROFIT_FACTOR", 0.8)
        if state.profit_factor and state.profit_factor < min_pf:
            warnings.append(f"Profit Factor {state.profit_factor} 低於建議值 {min_pf}")

        return GateResult(
            allowed=not blockers,
            blockers=blockers,
            warnings=warnings,
            emergency=emergency,
        )

    # ---------------- 2. 完整裁決 ----------------

    def evaluate(self, intent: TradeIntent, state: AccountState,
                 atr=None, mtf_score=None, contract_max_leverage=None,
                 min_notional=None) -> RiskDecision:
        """
        對一個 TradeIntent 做完整裁決。

        回傳 RiskDecision。approved=False 時 size_usdt 與 leverage 沒有意義。
        """
        gate = self.check_gate(state)

        if not gate.allowed:
            logger.warning(
                "Risk Engine | REJECT | %s | %s", intent.symbol, gate.reason,
            )
            return RiskDecision(
                intent=intent, approved=False,
                reason=gate.reason, blockers=list(gate.blockers),
            )

        # 同一檔不重複開倉。這裡擋是為了在算倉位之前就結束,
        # Trading Rules 層還會再擋一次 —— 重複的防線是刻意的。
        if intent.symbol in (state.open_symbols or []):
            return RiskDecision(
                intent=intent, approved=False,
                reason="DUPLICATE_POSITION",
                blockers=["DUPLICATE_POSITION"],
            )

        stop_distance_pct = intent.stop_distance_pct

        # ---- 槓桿:由停損距離與波動度決定,不是信心分數 ----
        leverage_decision = leverage_module.decide(
            stop_distance_pct=stop_distance_pct,
            max_leverage=self._limit("MAX_LEVERAGE", 5),
            atr=atr,
            price=intent.entry,
            mtf_score=mtf_score,
            contract_max_leverage=contract_max_leverage,
        )

        # ---- 倉位:由風險與停損距離反推 ----
        sizing = position_sizing.calculate_size(
            equity=state.equity,
            stop_distance_pct=stop_distance_pct,
            leverage=leverage_decision.leverage,
            risk_per_trade_pct=self._limit("MAX_RISK_PER_TRADE_PCT", 1.0),
            available_balance=state.available_balance,
            current_exposure_usdt=state.current_exposure_usdt,
            max_exposure_pct=self._limit("MAX_EXPOSURE_PCT", 80),
            min_notional=min_notional,
        )

        if not sizing.approved:
            logger.info(
                "Risk Engine | REJECT | %s | sizing: %s", intent.symbol, sizing.reason,
            )
            return RiskDecision(
                intent=intent, approved=False,
                reason=sizing.reason, blockers=["SIZING_REJECTED"],
            )

        # ---- 最後一道:停損必須先於強平觸發 ----
        if not leverage_module.stop_is_safer_than_liquidation(
            stop_distance_pct, leverage_decision.leverage
        ):
            reason = (
                f"槓桿 {leverage_decision.leverage}x 會讓強平價比停損還近"
                f"(停損距離 {stop_distance_pct:.2f}%)"
            )
            logger.error("Risk Engine | REJECT | %s | %s", intent.symbol, reason)
            return RiskDecision(
                intent=intent, approved=False,
                reason=reason, blockers=["LIQUIDATION_BEFORE_STOP"],
            )

        logger.info(
            "Risk Engine | APPROVE | %s | size=%.2f lev=%gx 名目=%.2f 風險=%.2f (%.2f%%)",
            intent.symbol, sizing.size_usdt, leverage_decision.leverage,
            sizing.notional, sizing.risk_usdt, sizing.risk_pct_of_equity,
        )

        return RiskDecision(
            intent=intent,
            approved=True,
            size_usdt=sizing.size_usdt,
            leverage=leverage_decision.leverage,
            reason=None,
            blockers=[],
        )


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = RiskEngine()
    return _engine


def set_engine(engine):
    global _engine
    _engine = engine
