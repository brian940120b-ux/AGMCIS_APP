"""
整條鏈路的接線測試。

每一層都有自己的單元測試,但**沒有人驗證它們真的接得起來**。
層與層之間的介面錯誤不會讓任何單元測試變紅 —— 它們只會讓系統安靜地
少做一件事。

Phase 11 抓到過一次:`build_context()` 讀 `funding.get("rate")`,
但 adapter 給的欄位名是 `funding_rate`。症狀是 FundingAgent 永遠棄權、
永遠不報錯,整整一個 Phase 沒有人發現。那種錯只有走完整條鏈路才抓得到。

這裡只 mock 三個邊界:

    市場資料(打交易所)
    合約規則(打交易所)
    資料庫(寫入)

中間全部是真的:指標、市況、十二個 Agent、Consensus、Supervisor、
Risk Engine、Trading Rules、Execution Engine、PaperBroker、成本模型。
"""
import math
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from agmcis.core.models import RiskDecision
from agmcis.risk.engine import AccountState


def bullish_frame(count=200, base=100.0):
    """
    穩定上升但有回檔的行情。

    刻意不用單調上漲:那會讓 RSI 卡在 100、ADX 卡在 100,
    真實的 Agent 一個都不會進場,測試就變成「什麼都沒驗到」。
    """
    rows = []
    for i in range(count):
        trend = i * 0.35
        wave = 2.2 * math.sin(i / 7.0)
        close = base + trend + wave
        open_ = base + (i - 1) * 0.35 + 2.2 * math.sin((i - 1) / 7.0)
        rows.append({
            "timestamp": 1_700_000_000_000 + i * 3_600_000,
            "open": open_,
            "high": max(open_, close) + 0.4,
            "low": min(open_, close) - 0.4,
            "close": close,
            "volume": 1500.0 + 300.0 * abs(math.sin(i / 4.0)),
        })
    return pd.DataFrame(rows)


class QualityReport:
    summary = "ok"
    ok = True
    warnings = []


def trading_rules(symbol="BTC/USDT"):
    """
    一組寬鬆但真實的合約規則。

    刻意不用「完全沒有限制」—— 那會讓 Trading Rules Engine 變成一個
    什麼都不做的通道,而這個測試的重點就是每一層都真的有跑到。
    """
    from agmcis.core.models import TradingRules

    return TradingRules(
        exchange="bingx",
        market_type="perpetual",
        symbol=symbol,
        tick_size=0.1,
        step_size=0.001,
        min_qty=0.001,
        max_qty=1000.0,
        min_notional=5.0,
        contract_size=1.0,
        price_precision=1,
        qty_precision=3,
        max_leverage=125.0,
    )


class Boundaries:
    """把三個邊界包在一起,測試只需要說「市場長這樣」。"""

    def __init__(self, frame=None, price=None, funding_rate=0.00005):
        self.frame = frame if frame is not None else bullish_frame()
        self.price = price if price is not None else float(
            self.frame["close"].iloc[-1]
        )
        self.funding_rate = funding_rate
        self.inserted = []

    def __enter__(self):
        rules_registry = MagicMock()
        rules_registry.get.side_effect = lambda symbol, *a, **k: trading_rules(symbol)

        self._patches = [
            patch("agmcis.data.market_data.get_ohlcv_checked",
                  return_value=(self.frame, QualityReport())),
            patch("agmcis.data.market_data.get_price", return_value=self.price),
            patch("agmcis.data.market_data.get_funding_rate",
                  return_value={"symbol": "X", "funding_rate": self.funding_rate}),
            patch("agmcis.exchange.trading_rules.get_registry",
                  return_value=rules_registry),
            # 加分項的資料源全部關掉:這個測試驗的是整條鏈路接得起來,
            # 不是「訂單簿抓不抓得到」。不關的話它會真的去打 BingX,
            # 把一個離線測試變成一個依賴網路的測試。
            patch("agmcis.signal.agent_pipeline.enrich", return_value={
                "order_book": None, "long_short_ratio": None,
                "sentiment_score": None, "news_risk": None,
            }),
            patch("database_service.get_open_trade", return_value=None),
            patch("paper_trading.insert_trade", side_effect=self._insert),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self._patches:
            item.stop()
        return False

    def _insert(self, **kwargs):
        self.inserted.append(kwargs)
        return len(self.inserted)


def fresh_rules_engine():
    """
    Trading Rules Engine 會快取 registry,所以每次都給它一個新的 ——
    否則第一個測試抓到的(真實)registry 會留給後面的測試用。
    """
    from agmcis.exchange import trading_rules as tr
    from agmcis.execution.rules_engine import TradingRulesEngine

    return TradingRulesEngine(registry=tr.get_registry())


def account(equity=10000.0):
    return AccountState(
        equity=equity,
        available_balance=equity,
        open_positions=0,
        profit_factor=2.0,
    )


class TestTheWholeChainFitsTogether(unittest.TestCase):

    def _deliberate(self, boundaries_kwargs=None):
        from agmcis.agents.registry import AgentRegistry, deliberate
        from agmcis.agents.supervisor import Supervisor
        from agmcis.signal import agent_pipeline

        with Boundaries(**(boundaries_kwargs or {})) as boundaries:
            context = agent_pipeline.build_context("BTC/USDT")
            result = deliberate(
                context, registry=AgentRegistry(), supervisor=Supervisor(),
            )

        return context, result, boundaries

    def test_market_data_reaches_the_agents_as_usable_indicators(self):
        context, _, _ = self._deliberate()

        self.assertTrue(context.data_ok)
        self.assertIsNotNone(context.indicators.ema20)
        self.assertIsNotNone(context.indicators.rsi)
        self.assertIsNotNone(context.regime)

    def test_the_funding_rate_actually_reaches_the_funding_agent(self):
        """
        這正是 Phase 11 抓到的那個錯:欄位名接錯時 context.funding_rate
        永遠是 None,FundingAgent 永遠棄權,而且完全不報錯。
        """
        context, _, _ = self._deliberate({"funding_rate": 0.004})

        self.assertEqual(context.funding_rate, 0.004)

        from agmcis.agents.base import Vote
        from agmcis.agents.builtin import FundingAgent

        self.assertIs(FundingAgent().analyse(context).vote, Vote.SHORT)

    def test_every_agent_produces_an_opinion_without_crashing(self):
        _, (deliberation, _), _ = self._deliberate()

        from agmcis.agents.registry import get_registry

        # 不寫死數量:Agent 會增加,而這個測試要驗的是
        # 「每一個都產出意見而且沒有炸掉」,不是「剛好有幾個」。
        self.assertEqual(
            len(deliberation.opinions), len(get_registry().names),
        )
        self.assertEqual(deliberation.errors, [])

    def test_a_bullish_market_produces_a_long_intent(self):
        """
        一個永遠不交易的系統,跟一個會崩潰的系統一樣是壞的。
        """
        _, (deliberation, report), _ = self._deliberate()

        self.assertIsNotNone(
            deliberation.intent,
            f"沒有產生 intent:{deliberation.blocked_reason} / {deliberation.votes}",
        )
        self.assertEqual(deliberation.direction.value, "做多")
        self.assertFalse(report.vetoed)

    def test_the_intent_carries_a_stop_on_the_correct_side(self):
        _, (deliberation, _), _ = self._deliberate()
        intent = deliberation.intent

        self.assertIsNotNone(intent.stop_loss)
        self.assertLess(intent.stop_loss, intent.entry)

    def test_the_intent_carries_the_agent_votes_for_attribution(self):
        """歸因資料必須在這一刻就帶上 —— 事後推不回來。"""
        _, (deliberation, _), _ = self._deliberate()

        from agmcis.agents.registry import get_registry

        self.assertEqual(
            len(deliberation.intent.agent_votes), len(get_registry().names),
        )
        self.assertIsNotNone(deliberation.intent.market_regime)


class TestIntentThroughRiskAndExecution(unittest.TestCase):

    def _intent(self):
        from agmcis.agents.registry import AgentRegistry, deliberate
        from agmcis.agents.supervisor import Supervisor
        from agmcis.signal import agent_pipeline

        with Boundaries():
            context = agent_pipeline.build_context("BTC/USDT")
            deliberation, _ = deliberate(
                context, registry=AgentRegistry(), supervisor=Supervisor(),
            )
        return deliberation.intent

    def _decision(self, intent):
        from agmcis.risk.engine import RiskEngine

        return RiskEngine().evaluate(intent, account())

    def test_risk_engine_accepts_a_well_formed_intent(self):
        decision = self._decision(self._intent())

        self.assertTrue(decision.approved, decision.reason)
        self.assertGreater(decision.size_usdt, 0)
        self.assertGreaterEqual(decision.leverage, 1)

    def test_risk_decides_the_size_not_the_agents(self):
        """
        TradeIntent 刻意不帶 size 與 leverage。
        如果哪天它帶了,這個測試會紅。
        """
        intent = self._intent()

        self.assertEqual(
            {"size_usdt", "leverage", "quantity"} & set(vars(intent)), set(),
        )

    def test_the_risk_taken_matches_the_configured_percentage(self):
        from agmcis.config import settings

        decision = self._decision(self._intent())
        expected = 10000.0 * settings.MAX_RISK_PER_TRADE_PCT / 100

        # 四捨五入與各層的保守調整只會讓它更小,不會更大
        self.assertLessEqual(decision.risk_usdt, expected * 1.001)

    def test_execution_opens_a_protected_position(self):
        from agmcis.execution.broker import PaperBroker
        from agmcis.execution.engine import ExecutionEngine, OPENED

        intent = self._intent()
        decision = self._decision(intent)

        with Boundaries() as boundaries:
            # 開倉之後 has_protection 要看得到停損
            def get_open_trade(symbol):
                if not boundaries.inserted:
                    return None
                return dict(boundaries.inserted[-1], symbol=symbol)

            broker = PaperBroker(positions=get_open_trade)
            engine = ExecutionEngine(
                broker=broker, persist=False,
                rules_engine=fresh_rules_engine(),
            )
            result = engine.execute(decision)

        self.assertEqual(result.status, OPENED, result.reason)
        self.assertTrue(result.ok)

    def test_the_stored_trade_carries_the_costs_and_attribution(self):
        from agmcis.execution.broker import PaperBroker
        from agmcis.execution.engine import ExecutionEngine

        intent = self._intent()
        decision = self._decision(intent)

        with Boundaries() as boundaries:
            def get_open_trade(symbol):
                if not boundaries.inserted:
                    return None
                return dict(boundaries.inserted[-1], symbol=symbol)

            ExecutionEngine(
                broker=PaperBroker(positions=get_open_trade), persist=False,
                rules_engine=fresh_rules_engine(),
            ).execute(decision)

            stored = boundaries.inserted[-1]

        # Phase 10:成本
        self.assertGreater(stored["entry_fee"], 0)
        self.assertIsNotNone(stored["liquidation_price"])
        self.assertEqual(stored["cost_basis"], "WITH_COSTS")
        # 成交價含滑點,比下單價差
        self.assertGreater(stored["entry_price"], stored["requested_entry_price"])
        # Phase 15:歸因
        from agmcis.agents.registry import get_registry

        self.assertEqual(
            len(stored["agent_votes"]), len(get_registry().names),
        )
        self.assertIsNotNone(stored["market_regime"])

    def test_the_liquidation_price_is_further_than_the_stop(self):
        """
        這條規則從 Phase 5 一路守到 Phase 12。
        走完整條鏈路之後它仍然必須成立。
        """
        from agmcis.execution.broker import PaperBroker
        from agmcis.execution.engine import ExecutionEngine

        intent = self._intent()
        decision = self._decision(intent)

        with Boundaries() as boundaries:
            def get_open_trade(symbol):
                if not boundaries.inserted:
                    return None
                return dict(boundaries.inserted[-1], symbol=symbol)

            ExecutionEngine(
                broker=PaperBroker(positions=get_open_trade), persist=False,
                rules_engine=fresh_rules_engine(),
            ).execute(decision)

            stored = boundaries.inserted[-1]

        self.assertLess(stored["liquidation_price"], stored["stoploss"])


class TestBrokenDataStopsTheWholeChain(unittest.TestCase):
    """資料壞掉不等於市場中性。這條規則必須在每一層都成立。"""

    def test_bad_data_produces_no_intent(self):
        from agmcis.agents.registry import AgentRegistry, deliberate
        from agmcis.agents.supervisor import Supervisor
        from agmcis.signal import agent_pipeline

        class BadReport:
            summary = "資料過期"

        with patch("agmcis.data.market_data.get_ohlcv_checked",
                   return_value=(None, BadReport())):
            context = agent_pipeline.build_context("BTC/USDT")
            deliberation, _ = deliberate(
                context, registry=AgentRegistry(), supervisor=Supervisor(),
            )

        self.assertFalse(context.data_ok)
        self.assertIsNone(deliberation.intent)

    def test_an_unapproved_decision_never_reaches_the_broker(self):
        from agmcis.execution.broker import Broker
        from agmcis.execution.engine import (
            ExecutionEngine, REJECTED_NOT_APPROVED,
        )

        broker = MagicMock(spec=Broker)
        rejected = RiskDecision(
            intent=self._any_intent(), approved=False, reason="MAX_DAILY_LOSS",
        )

        with patch("agmcis.execution.engine.logger"):
            result = ExecutionEngine(broker=broker, persist=False).execute(rejected)

        self.assertEqual(result.status, REJECTED_NOT_APPROVED)
        broker.submit_entry.assert_not_called()

    def _any_intent(self):
        from agmcis.core.models import TradeIntent

        return TradeIntent(
            symbol="BTC/USDT", market_type="perpetual", direction="做多",
            entry=100.0, stop_loss=97.0,
        )


if __name__ == "__main__":
    unittest.main()
