"""The win rate, seen from the training process, and a stop rule on it.

Each worker's league keeps its own tally and none of it reaches the process
doing the gradient steps, so until now a training run could not say how it
was doing in the units the day is decided in — only in reward. Every episode
now carries its verdict in `info`, and this callback reads it: a rolling win
rate overall and per opponent, written to the training logger (TensorBoard's
`league/` section), and optionally a stop when the rate clears a target.

The stop is what makes an *exploiter* (SRC-012, SRC-007): a fresh policy
trained against one frozen checkpoint until it beats it seven times in ten,
then added to the pool as a permanent opponent. Nothing here decides who is
in the pool; that is `--opponent-pool`. This only knows when to stop.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

#: How many recent rounds a rate is taken over. Fifty rounds is roughly what
#: one worker sees in a million steps at 10 Hz, so it is also about the whole
#: history of a short exploiter run.
WINDOW = 50


def _base_callback() -> Any:
    from stable_baselines3.common.callbacks import BaseCallback

    return BaseCallback


class WinRate(_base_callback()):  # type: ignore[misc]
    """Rolling win rate from the verdicts in `info`, with an optional stop.

    `stop_at` is a win rate in [0, 1]; None never stops. `min_rounds` is how
    many rounds must be in the window before the rate is trusted, so three
    lucky wins at the start do not end a run.
    """

    def __init__(
        self, *, stop_at: float | None = None, min_rounds: int = WINDOW, window: int = WINDOW
    ) -> None:
        super().__init__(verbose=0)
        if stop_at is not None and not 0.0 < stop_at <= 1.0:
            raise ValueError(f"stop_at is a win rate in (0, 1], not {stop_at}")
        if min_rounds < 1 or window < 1:
            raise ValueError("min_rounds and window are counts of rounds, at least one")
        self.stop_at = stop_at
        self.min_rounds = min_rounds
        self.recent: deque[bool] = deque(maxlen=window)
        self.by_opponent: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=window))
        self.rounds = 0
        #: Set when the stop rule fired, so the caller can tell this stop
        #: from a time budget or a Ctrl+C.
        self.reached = False

    # ------------------------------------------------------------- reading

    def observe(self, infos: list[dict[str, Any]]) -> None:
        """Take the verdicts out of one step's infos. Public for tests."""
        for info in infos:
            verdict = info.get("verdict")
            if verdict is None:
                continue
            won = verdict == "BLUE"
            self.recent.append(won)
            self.rounds += 1
            opponent = info.get("opponent_name")
            if opponent is not None:
                self.by_opponent[str(opponent)].append(won)

    @property
    def win_rate(self) -> float | None:
        """None until a round has ended; the window's rate after."""
        if not self.recent:
            return None
        return sum(self.recent) / len(self.recent)

    def rates_by_opponent(self) -> dict[str, float]:
        return {name: sum(results) / len(results) for name, results in self.by_opponent.items() if results}

    # ---------------------------------------------------------------- sb3

    def _on_step(self) -> bool:
        before = self.rounds
        self.observe(self.locals.get("infos", []))
        if self.rounds == before:
            return True
        rate = self.win_rate
        if rate is not None:
            self.logger.record("league/win_rate", rate)
            self.logger.record("league/rounds", self.rounds)
            for name, value in self.rates_by_opponent().items():
                self.logger.record(f"league/win_rate_vs_{name}", value)
        if (
            self.stop_at is not None
            and rate is not None
            and len(self.recent) >= self.min_rounds
            and rate >= self.stop_at
        ):
            self.reached = True
            print(
                f"\nwin rate {rate:.0%} over the last {len(self.recent)} rounds clears "
                f"{self.stop_at:.0%} — stopping, target met",
                flush=True,
            )
            return False
        return True


__all__ = ["WINDOW", "WinRate"]
