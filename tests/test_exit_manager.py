"""
判斷式出場(Phase 12)。

position_monitor 處理機械式出場:停損、停利、強平 —— 碰到線就出場。
這裡處理的是判斷式出場:Agent 群認為這個部位該收了。

兩層的關係很重要:**機械式出場永遠優先,而且這一層不碰停損。**
一個會移動停損的「出場管理器」等於把風險上限拿掉了。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.agents.base import AgentOpinion, Vote
from agmcis.agents.consensus import Deliberation
from agmcis.core.enums import Direction
from agmcis.execution import exit_manager
from agmcis.execution.engine import ExecutionResult


LONG_POSITION = {"symbol": "BTC/USDT", "signal": "做多", "entry_price": 100.0,
                 "stoploss": 97.0, "takeprofit": 110.0, "leverage": 3}


def deliberation(direction=Direction.LONG, confidence=80.0, opinions=None,
                 blocked_reason=None):
    return Deliberation(
        symbol="BTC/USDT", direction=direction, confidence=confidence,
        opinions=opinions if opinions is not None else [
            AgentOpinion(agent="trend", vote=Vote.LONG, confidence=80),
        ],
        blocked_reason=blocked_reason,
    )


def evaluate(position, result):
    with patch.object(exit_manager.agent_pipeline, "analyse_symbol",
                      return_value=(result, None)):
        return exit_manager.evaluate_position(position)


class TestWhenToExit(unittest.TestCase):

    def test_a_confident_exit_agent_closes_the_position(self):
        opinions = [
            AgentOpinion(agent="trend", vote=Vote.LONG, confidence=90),
            AgentOpinion(agent="exit", vote=Vote.WAIT, confidence=90,
                         reasons=["距離停損只剩 0.4%"]),
        ]
        should_exit, reason, _ = evaluate(
            LONG_POSITION, deliberation(opinions=opinions),
        )

        self.assertTrue(should_exit)
        self.assertIn("Exit Agent", reason)

    def test_a_hesitant_exit_agent_does_not(self):
        """提示的門檻可以低,動手的門檻必須高。"""
        opinions = [
            AgentOpinion(agent="trend", vote=Vote.LONG, confidence=90),
            AgentOpinion(agent="exit", vote=Vote.WAIT, confidence=60,
                         reasons=["RSI 偏高"]),
        ]
        should_exit, _, _ = evaluate(LONG_POSITION, deliberation(opinions=opinions))

        self.assertFalse(should_exit)

    def test_consensus_flipping_against_the_position_closes_it(self):
        should_exit, reason, _ = evaluate(
            LONG_POSITION, deliberation(Direction.SHORT, confidence=85),
        )

        self.assertTrue(should_exit)
        self.assertIn("轉向", reason)

    def test_a_weak_reversal_does_not_close_the_position(self):
        should_exit, _, _ = evaluate(
            LONG_POSITION, deliberation(Direction.SHORT, confidence=55),
        )

        self.assertFalse(should_exit)

    def test_consensus_agreeing_with_the_position_holds(self):
        should_exit, _, _ = evaluate(
            LONG_POSITION, deliberation(Direction.LONG, confidence=95),
        )

        self.assertFalse(should_exit)

    def test_a_wait_consensus_is_not_a_reason_to_close(self):
        """「現在不適合開新倉」與「手上這張該平掉」是兩件事。"""
        should_exit, _, _ = evaluate(
            LONG_POSITION,
            deliberation(Direction.WAIT, blocked_reason="觀望票過半"),
        )

        self.assertFalse(should_exit)

    def test_all_agents_abstaining_never_triggers_a_discretionary_exit(self):
        """
        看不見市況就不要憑空決定出場。停損還在,機械式保護不受影響。
        """
        opinions = [AgentOpinion(agent=f"a{i}") for i in range(4)]
        should_exit, _, diagnostics = evaluate(
            LONG_POSITION, deliberation(opinions=opinions),
        )

        self.assertFalse(should_exit)
        self.assertIn("棄權", diagnostics["skipped"])

    def test_a_short_position_exits_when_consensus_turns_long(self):
        short = dict(LONG_POSITION, signal="做空")
        should_exit, reason, _ = evaluate(
            short, deliberation(Direction.LONG, confidence=85),
        )

        self.assertTrue(should_exit)


class TestItOnlyCloses(unittest.TestCase):
    """
    一個能開倉的「出場管理器」不是出場管理器;
    一個會移動停損的出場管理器等於把風險上限拿掉。
    """

    def test_the_module_never_opens_a_position(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agmcis", "execution", "exit_manager.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        for banned in ("create_paper_trade", "insert_trade", "submit_entry",
                       "update_trade_stoploss", ".execute("):
            with self.subTest(name=banned):
                self.assertNotIn(banned, source)

    def test_it_calls_close_and_nothing_else_on_the_engine(self):
        engine = MagicMock()
        engine.close.return_value = ExecutionResult(ok=True, status="CLOSED",
                                                    fill_price=99.0)

        with patch.object(exit_manager.agent_pipeline, "analyse_symbol",
                          return_value=(deliberation(Direction.SHORT, 85), None)):
            exit_manager.run_exit_manager(
                positions=[LONG_POSITION], engine=engine, notify=False,
            )

        engine.close.assert_called_once()
        engine.execute.assert_not_called()


class TestRunLoop(unittest.TestCase):

    def _engine(self, ok=True, reason=None):
        engine = MagicMock()
        engine.close.return_value = ExecutionResult(
            ok=ok, status="CLOSED" if ok else "CLOSE_FAILED",
            fill_price=99.0 if ok else None, reason=reason,
        )
        return engine

    def _run(self, positions, result, engine=None):
        engine = engine or self._engine()
        with patch.object(exit_manager.agent_pipeline, "analyse_symbol",
                          return_value=(result, None)):
            return exit_manager.run_exit_manager(
                positions=positions, engine=engine, notify=False,
            ), engine

    def test_a_closed_position_is_reported(self):
        summary, engine = self._run(
            [LONG_POSITION], deliberation(Direction.SHORT, 85),
        )

        self.assertEqual(summary["closed_count"], 1)
        self.assertEqual(summary["closed"][0]["symbol"], "BTC/USDT")

    def test_a_held_position_is_reported_with_its_diagnostics(self):
        summary, engine = self._run([LONG_POSITION], deliberation(Direction.LONG))

        self.assertEqual(summary["closed_count"], 0)
        self.assertEqual(len(summary["held"]), 1)
        self.assertIn("votes", summary["held"][0]["diagnostics"])
        engine.close.assert_not_called()

    def test_a_failed_close_is_reported_not_swallowed(self):
        engine = self._engine(ok=False, reason="沒有持倉")

        with patch.object(exit_manager, "logger"):
            summary, _ = self._run(
                [LONG_POSITION], deliberation(Direction.SHORT, 85), engine=engine,
            )

        self.assertEqual(summary["closed_count"], 0)
        self.assertEqual(len(summary["failed"]), 1)

    def test_one_broken_position_does_not_stop_the_round(self):
        """
        安靜失敗的出場管理器,表現起來跟「沒有出場條件成立」一模一樣。
        """
        positions = [LONG_POSITION, dict(LONG_POSITION, symbol="ETH/USDT")]
        engine = self._engine()

        def analyse(symbol, **kwargs):
            if symbol == "BTC/USDT":
                raise RuntimeError("boom")
            return deliberation(Direction.SHORT, 85), None

        with patch.object(exit_manager.agent_pipeline, "analyse_symbol",
                          side_effect=analyse), \
             patch.object(exit_manager, "logger"):
            summary = exit_manager.run_exit_manager(
                positions=positions, engine=engine, notify=False,
            )

        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["closed_count"], 1)

    def test_no_positions_is_a_quiet_no_op(self):
        summary, engine = self._run([], deliberation())

        self.assertEqual(summary["checked"], 0)
        engine.close.assert_not_called()


class TestNakedSweepIsWiredIn(unittest.TestCase):

    def test_it_delegates_to_the_execution_engine(self):
        engine = MagicMock()
        engine.sweep_naked_positions.return_value = {"closed": [], "stuck": []}

        exit_manager.run_naked_position_sweep(positions=[], engine=engine)

        engine.sweep_naked_positions.assert_called_once()


class TestSchedulerRegistration(unittest.TestCase):
    """排好的工作沒有被排程器叫到,等於沒有寫。"""

    def test_both_jobs_are_in_the_position_job_set(self):
        from agmcis.scheduling import runner

        names = [factory.__name__ for factory
                 in runner.JOB_SETS[runner.JOB_SET_POSITION]]

        self.assertIn("_job_exit_manager", names)
        self.assertIn("_job_naked_position_sweep", names)

    def test_the_position_job_set_still_contains_no_entry_jobs(self):
        """
        Phase 1 的教訓:合併排程器時把所有工作塞進每個 service,
        結果四個 service 都在開倉。
        """
        from agmcis.scheduling import runner

        names = [factory.__name__ for factory
                 in runner.JOB_SETS[runner.JOB_SET_POSITION]]

        self.assertNotIn("_job_auto_trader", names)
        self.assertNotIn("_job_opportunity_scanner", names)


if __name__ == "__main__":
    unittest.main()
