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
from agmcis.risk import portfolio
from agmcis.safety import safe_live
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
    realized_pnl_7d: float = 0.0
    trades_24h: int = 0
    consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    open_symbols: List[str] = field(default_factory=list)
    # 現有部位的結構。曝險上限只看總和,相關性上限要看每一腿 ——
    # 每一筆至少要有 symbol、direction 與名目價值(或 size_usdt + leverage)。
    open_legs: List[dict] = field(default_factory=list)

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
    def __init__(self, limits=None, mode=None):
        # limits 預設用全域設定;測試可以注入自己的
        self._limits = limits
        # mode 預設用全域 TRADING_MODE。指定它可以在測試裡驗證
        # 「切到實單之後額度會變小」而不用改全域設定。
        self._mode = mode

    def _limit(self, name, default=None):
        """
        取一個風控參數,然後套用 SAFE LIVE 上限。

        SAFE LIVE 只會讓數字更嚴格,不會放寬 —— 注入的 limits
        (測試用)也一樣要經過那一層,否則測試驗到的是一條
        生產環境不會走的路徑。
        """
        if self._limits is not None and name in self._limits:
            base = self._limits[name]
        else:
            base = getattr(settings, name, default)

        return safe_live.tighten(name, base, mode=self._mode)

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

        weekly_limit = abs(self._limit("MAX_WEEKLY_LOSS_USDT", 900))
        if state.realized_pnl_7d <= -weekly_limit:
            blockers.append("MAX_WEEKLY_LOSS")
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
                 min_notional=None, correlation=None, news=None) -> RiskDecision:
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

        # ---- 消息面:重大事件時間窗內不開新倉 ----
        # 放在算倉位之前 —— 不開就不用算。平倉不走這條路徑,
        # 所以消息面永遠不會擋住出場。
        risk_multiplier = 1.0

        if news is not None:
            if news.blocks_entry:
                logger.warning(
                    "Risk Engine | REJECT | %s | 消息面:%s",
                    intent.symbol, " / ".join(news.reasons),
                )
                return RiskDecision(
                    intent=intent, approved=False,
                    reason=" / ".join(news.reasons) or "重大事件時間窗",
                    blockers=["NEWS_BLACKOUT"],
                )
            risk_multiplier = max(0.0, min(1.0, float(news.risk_multiplier)))

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
            # 消息面只會讓風險變小:multiplier 在上面被夾在 [0, 1]。
            risk_per_trade_pct=(
                self._limit("MAX_RISK_PER_TRADE_PCT", 1.0) * risk_multiplier
            ),
            available_balance=state.available_balance,
            current_exposure_usdt=state.current_exposure_usdt,
            max_exposure_pct=self._limit("MAX_EXPOSURE_PCT", 80),
            min_notional=min_notional,
            # 實單的單筆名目硬上限。非實單模式是 None(不限制)——
            # 名目本來就由「風險 ÷ 停損距離」自然決定。
            max_notional=safe_live.max_notional(mode=self._mode),
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

        # ---- 組合層:這一筆跟已經有的部位是不是同一個賭注 ----
        # 必須放在算完倉位之後 —— 曝險要用實際的名目價值,不是意圖。
        exposure = portfolio.assess(
            candidate={
                "symbol": intent.symbol,
                "direction": intent.direction,
                "notional_usdt": sizing.notional,
                "entry": intent.entry,
                "stop_loss": intent.stop_loss,
            },
            open_positions=state.open_legs,
            equity=state.equity,
            matrix=correlation,
            max_symbol_pct=self._limit("MAX_SYMBOL_EXPOSURE_PCT", 50),
            max_cluster_risk_pct=self._limit("MAX_CORRELATED_RISK_PCT", 3.0),
            threshold=self._limit("CORRELATION_THRESHOLD", 0.7),
            assumed_risk_pct=self._limit("MAX_RISK_PER_TRADE_PCT", 1.0),
        )

        if not exposure.allowed:
            logger.warning(
                "Risk Engine | REJECT | %s | 組合風險:%s | %s",
                intent.symbol, ",".join(exposure.blockers),
                " / ".join(exposure.warnings),
            )
            return RiskDecision(
                intent=intent, approved=False,
                reason=" / ".join(exposure.warnings) or ",".join(exposure.blockers),
                blockers=list(exposure.blockers),
            )

        logger.info(
            "Risk Engine | APPROVE | %s | size=%.2f lev=%gx 名目=%.2f 風險=%.2f (%.2f%%)"
            " | 相關群 %s 風險 %.2f%%",
            intent.symbol, sizing.size_usdt, leverage_decision.leverage,
            sizing.notional, sizing.risk_usdt, sizing.risk_pct_of_equity,
            "+".join(exposure.cluster_symbols), exposure.cluster_risk_pct,
        )

        return RiskDecision(
            intent=intent,
            approved=True,
            size_usdt=sizing.size_usdt,
            leverage=leverage_decision.leverage,
            reason=None,
            blockers=[],
            warnings=list(exposure.warnings) + (
                list(news.reasons) + list(news.warnings) if news is not None else []
            ),
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
