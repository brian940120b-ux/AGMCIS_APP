"""
請求節流。

ccxt 的 enableRateLimit 只是固定間隔,不會在收到 429 之後退讓。
被限流時繼續打只會讓封鎖時間變長。
"""
import unittest

from agmcis.exchange.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingSleep:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.clock.advance(seconds)

    @property
    def total(self):
        return sum(self.calls)


class TestTokenBucket(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.sleep = RecordingSleep(self.clock)
        self.limiter = RateLimiter(max_calls=3, period_seconds=10.0, clock=self.clock)

    def test_allows_a_burst_up_to_the_limit(self):
        """一次掃描 50 檔需要突發能力,不能是固定間隔。"""
        for _ in range(3):
            self.assertEqual(self.limiter.acquire(sleep=self.sleep), 0.0)
        self.assertEqual(self.sleep.calls, [])

    def test_blocks_once_the_budget_is_spent(self):
        for _ in range(3):
            self.limiter.acquire(sleep=self.sleep)

        waited = self.limiter.acquire(sleep=self.sleep)
        self.assertGreater(waited, 0)
        self.assertAlmostEqual(waited, 10.0)

    def test_budget_recovers_as_the_window_slides(self):
        for _ in range(3):
            self.limiter.acquire(sleep=self.sleep)
        self.assertEqual(self.limiter.available(), 0)

        self.clock.advance(11)
        self.assertEqual(self.limiter.available(), 3)
        self.assertEqual(self.limiter.acquire(sleep=self.sleep), 0.0)

    def test_wait_time_reports_zero_when_free(self):
        self.assertEqual(self.limiter.wait_time(), 0.0)


class TestCooldown(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.sleep = RecordingSleep(self.clock)
        self.limiter = RateLimiter(max_calls=100, period_seconds=10.0, clock=self.clock)

    def test_penalise_blocks_subsequent_calls(self):
        self.limiter.penalise(30.0)
        self.assertTrue(self.limiter.in_cooldown)

        waited = self.limiter.acquire(sleep=self.sleep)
        self.assertAlmostEqual(waited, 30.0)

    def test_cooldown_expires(self):
        self.limiter.penalise(5.0)
        self.clock.advance(6)

        self.assertFalse(self.limiter.in_cooldown)
        self.assertEqual(self.limiter.acquire(sleep=self.sleep), 0.0)

    def test_repeated_penalties_extend_not_shorten(self):
        """第二次 429 不該把冷卻時間縮短。"""
        self.limiter.penalise(30.0)
        self.limiter.penalise(5.0)
        self.assertAlmostEqual(self.limiter.wait_time(), 30.0, places=3)

    def test_counts_are_tracked_for_observability(self):
        self.limiter.penalise(1.0)
        self.limiter.penalise(1.0)
        self.assertEqual(self.limiter.status()["cooldown_count"], 2)

    def test_status_shape(self):
        status = self.limiter.status()
        for key in ["max_calls", "used", "available", "in_cooldown", "throttled_count"]:
            self.assertIn(key, status)

    def test_reset_clears_everything(self):
        self.limiter.acquire(sleep=self.sleep)
        self.limiter.penalise(30.0)
        self.limiter.reset()

        self.assertFalse(self.limiter.in_cooldown)
        self.assertEqual(self.limiter.status()["used"], 0)
