"""
錯誤分類與重試策略。

原本的邏輯是「NetworkError -> 重試,其他 -> 放棄」。問題是 ccxt 把語意
完全不同的錯誤都放在 NetworkError 底下:被限流、時鐘偏移都會拿到
跟網路抖動一樣的待遇。這裡鎖住每一類的正確處理方式。
"""
import unittest

import ccxt

from agmcis.core.errors import ExchangeUnavailableError, OrderRejectedError, OrderStateUnknownError
from agmcis.exchange import error_policy as ep


class TestClassification(unittest.TestCase):

    def test_rate_limit_backs_off_longer_than_a_timeout(self):
        rate = ep.classify(ccxt.RateLimitExceeded("429"))
        timeout = ep.classify(ccxt.RequestTimeout("slow"))

        self.assertEqual(rate.action, ep.ACTION_BACKOFF_LONG)
        self.assertEqual(timeout.action, ep.ACTION_RETRY)
        self.assertGreater(rate.backoff_multiplier, timeout.backoff_multiplier)

    def test_ddos_protection_backs_off_most(self):
        ddos = ep.classify(ccxt.DDoSProtection("blocked"))
        rate = ep.classify(ccxt.RateLimitExceeded("429"))

        self.assertEqual(ddos.action, ep.ACTION_BACKOFF_LONG)
        self.assertGreater(ddos.backoff_multiplier, rate.backoff_multiplier)

    def test_clock_skew_resyncs_instead_of_blind_retry(self):
        """InvalidNonce 是 NetworkError 的子類,但重試一百次也沒用。"""
        policy = ep.classify(ccxt.InvalidNonce("timestamp rejected"))

        self.assertEqual(policy.action, ep.ACTION_RESYNC_TIME)
        self.assertTrue(policy.is_retryable)

    def test_auth_errors_are_not_retried(self):
        for exc in [ccxt.AuthenticationError("bad key"), ccxt.PermissionDenied("nope")]:
            policy = ep.classify(exc)
            self.assertEqual(policy.action, ep.ACTION_FAIL, exc)
            self.assertFalse(policy.is_retryable)

    def test_order_problems_map_to_order_rejected(self):
        for exc in [ccxt.InsufficientFunds("no margin"),
                    ccxt.InvalidOrder("bad precision"),
                    ccxt.OrderNotFound("gone")]:
            policy = ep.classify(exc)
            self.assertEqual(policy.action, ep.ACTION_FAIL, exc)
            self.assertIs(policy.exception_class, OrderRejectedError, exc)

    def test_bad_symbol_is_not_retried(self):
        self.assertEqual(ep.classify(ccxt.BadSymbol("nope")).action, ep.ACTION_FAIL)

    def test_maintenance_is_retried_with_long_backoff(self):
        policy = ep.classify(ccxt.OnMaintenance("brb"))
        self.assertEqual(policy.action, ep.ACTION_RETRY)
        self.assertGreater(policy.backoff_multiplier, 1.0)

    def test_subclasses_match_before_parents(self):
        """OrderNotFound 是 InvalidOrder 的子類;分類順序必須讓子類先中。"""
        self.assertEqual(ep.classify(ccxt.OrderNotFound("x")).category, "order_not_found")
        self.assertEqual(ep.classify(ccxt.InvalidOrder("x")).category, "invalid_order")

    def test_unknown_exception_defaults_to_fail(self):
        """未分類的錯誤保守處理:不重試。"""
        policy = ep.classify(ValueError("something odd"))
        self.assertEqual(policy.action, ep.ACTION_FAIL)
        self.assertFalse(policy.is_retryable)


class TestWriteFailuresNeverRetry(unittest.TestCase):
    """
    送出訂單後失敗和讀取失敗完全不同:交易所可能已經收到。
    重送是重複開倉最常見的來源。
    """

    def test_timeout_on_write_requires_reconciliation(self):
        policy = ep.classify_write_failure(ccxt.RequestTimeout("no response"))

        self.assertEqual(policy.action, ep.ACTION_RECONCILE)
        self.assertIs(policy.exception_class, OrderStateUnknownError)
        self.assertFalse(policy.is_retryable)

    def test_network_error_on_write_requires_reconciliation(self):
        policy = ep.classify_write_failure(ccxt.NetworkError("connection reset"))
        self.assertEqual(policy.action, ep.ACTION_RECONCILE)

    def test_same_error_on_read_is_just_retried(self):
        """對照組:同一個例外在讀取時只是重試。"""
        self.assertEqual(ep.classify(ccxt.RequestTimeout("x")).action, ep.ACTION_RETRY)

    def test_explicit_rejection_on_write_is_still_a_plain_failure(self):
        """交易所明確拒絕代表它沒有收單,不需要對帳。"""
        policy = ep.classify_write_failure(ccxt.InsufficientFunds("no margin"))
        self.assertEqual(policy.action, ep.ACTION_FAIL)

    def test_rate_limit_on_write_is_not_reconcile(self):
        """429 代表請求被擋下,沒有進到撮合,可以安全退避重試。"""
        policy = ep.classify_write_failure(ccxt.RateLimitExceeded("429"))
        self.assertEqual(policy.action, ep.ACTION_BACKOFF_LONG)


class TestDescribe(unittest.TestCase):

    def test_describe_includes_category_and_action(self):
        text = ep.describe(ccxt.RateLimitExceeded("429"))
        self.assertIn("rate_limit", text)
        self.assertIn(ep.ACTION_BACKOFF_LONG, text)
