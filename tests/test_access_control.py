"""
存取控制。

鎖住兩個安全性修正:
  1. Telegram listener 必須檢查 chat_id。原本任何 Telegram 使用者都能發
     /emergency、/close BTC/USDT 控制整套系統。
  2. /api/* 必須要金鑰。原本完全公開,而且 GET /api/auto_trader 會實際開倉。
     另外金鑰未設定時必須「拒絕所有人」,不是「放行所有人」。
"""
import importlib
import os
import unittest
from unittest.mock import MagicMock, patch


class TestTelegramAuthorization(unittest.TestCase):

    def _config(self, **env):
        for key in ["TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_CHAT_ID", "CHAT_ID",
                    "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"]:
            os.environ.pop(key, None)
        os.environ.update(env)
        import telegram_config
        return importlib.reload(telegram_config)

    def tearDown(self):
        for key in ["TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_CHAT_ID", "CHAT_ID",
                    "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"]:
            os.environ.pop(key, None)

    def test_authorizes_configured_chat_id(self):
        cfg = self._config(TELEGRAM_CHAT_ID="12345")
        self.assertTrue(cfg.is_authorized(12345))
        self.assertTrue(cfg.is_authorized("12345"))

    def test_rejects_other_chat_ids(self):
        cfg = self._config(TELEGRAM_CHAT_ID="12345")
        self.assertFalse(cfg.is_authorized(99999))
        self.assertFalse(cfg.is_authorized("99999"))

    def test_rejects_missing_chat_id(self):
        cfg = self._config(TELEGRAM_CHAT_ID="12345")
        self.assertFalse(cfg.is_authorized(None))

    def test_no_whitelist_means_nobody_is_authorized(self):
        """沒設定授權名單時必須全部拒絕,而不是全部放行。"""
        cfg = self._config()
        self.assertEqual(cfg.allowed_chat_ids(), set())
        self.assertFalse(cfg.is_authorized(12345))

    def test_supports_multiple_allowed_ids(self):
        cfg = self._config(TELEGRAM_ALLOWED_CHAT_IDS="111, 222,333")
        self.assertTrue(cfg.is_authorized(111))
        self.assertTrue(cfg.is_authorized(333))
        self.assertFalse(cfg.is_authorized(444))

    def test_falls_back_to_legacy_env_names(self):
        cfg = self._config(BOT_TOKEN="legacy-token", CHAT_ID="777")
        self.assertEqual(cfg.BOT_TOKEN, "legacy-token")
        self.assertTrue(cfg.is_authorized(777))


class TestTelegramListenerDispatch(unittest.TestCase):

    def test_unauthorized_update_never_reaches_a_handler(self):
        import telegram_listener

        update = {"message": {"chat": {"id": 99999}, "text": "/emergency"}}

        with patch.object(telegram_listener, "is_authorized", return_value=False), \
             patch.object(telegram_listener, "dispatch") as dispatch, \
             patch.object(telegram_listener, "logger") as log:
            telegram_listener.handle_update(update)

        dispatch.assert_not_called()
        self.assertTrue(log.warning.called)

    def test_authorized_update_is_dispatched(self):
        import telegram_listener

        update = {"message": {"chat": {"id": 12345}, "text": "/status"}}

        with patch.object(telegram_listener, "is_authorized", return_value=True), \
             patch.object(telegram_listener, "dispatch", return_value=True) as dispatch, \
             patch.object(telegram_listener, "logger"):
            telegram_listener.handle_update(update)

        dispatch.assert_called_once_with("/status")

    def test_symbol_command_is_normalized(self):
        import telegram_listener

        handler = MagicMock()
        with patch.dict(telegram_listener.SYMBOL_COMMANDS, {"/close": handler}, clear=True):
            self.assertTrue(telegram_listener.dispatch("/close btcusdt"))
        handler.assert_called_once_with("BTC/USDT")

    def test_unknown_command_returns_false(self):
        import telegram_listener
        self.assertFalse(telegram_listener.dispatch("/not_a_command"))


class TestApiKeyAuth(unittest.TestCase):

    def _auth(self, key=None):
        if key is None:
            os.environ.pop("DASHBOARD_KEY", None)
        else:
            os.environ["DASHBOARD_KEY"] = key
        import web_auth
        return importlib.reload(web_auth)

    def tearDown(self):
        os.environ.pop("DASHBOARD_KEY", None)

    def _request(self, cookies=None, headers=None, query=None):
        request = MagicMock()
        request.cookies = cookies or {}
        request.headers = headers or {}
        request.query_params = query or {}
        return request

    def test_accepts_correct_key_from_header(self):
        auth = self._auth("s3cret")
        request = self._request(headers={auth.HEADER_NAME: "s3cret"})
        self.assertTrue(auth.is_authenticated(request))

    def test_accepts_correct_key_from_cookie(self):
        auth = self._auth("s3cret")
        request = self._request(cookies={auth.COOKIE_NAME: "s3cret"})
        self.assertTrue(auth.is_authenticated(request))

    def test_accepts_correct_key_from_query(self):
        auth = self._auth("s3cret")
        self.assertTrue(auth.is_authenticated(self._request(query={"key": "s3cret"})))

    def test_rejects_wrong_key(self):
        auth = self._auth("s3cret")
        self.assertFalse(auth.is_authenticated(self._request(query={"key": "wrong"})))

    def test_rejects_no_key(self):
        auth = self._auth("s3cret")
        self.assertFalse(auth.is_authenticated(self._request()))

    def test_unset_key_rejects_everyone(self):
        """DASHBOARD_KEY 未設定時不得放行任何人,也不能 fallback 到寫死的預設值。"""
        auth = self._auth(None)
        self.assertEqual(auth.dashboard_key(), "")
        self.assertFalse(auth.key_is_valid("agmcis2026"))
        self.assertFalse(auth.is_authenticated(self._request(query={"key": "agmcis2026"})))

    def test_dependency_raises_401_without_key(self):
        from fastapi import HTTPException
        auth = self._auth("s3cret")
        with self.assertRaises(HTTPException) as ctx:
            auth.require_api_key(self._request())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_dependency_raises_503_when_key_unset(self):
        from fastapi import HTTPException
        auth = self._auth(None)
        with self.assertRaises(HTTPException) as ctx:
            auth.require_api_key(self._request(query={"key": "anything"}))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_dependency_passes_with_valid_key(self):
        auth = self._auth("s3cret")
        request = self._request(headers={auth.HEADER_NAME: "s3cret"})
        self.assertTrue(auth.require_api_key(request))
