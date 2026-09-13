"""
私有端點:唯讀,而且金鑰絕不外洩。

═══ 這一組守的兩條線 ═══
一、**這個模組不能有下單能力。** 不是「被擋住」,是根本沒寫。
    有人日後在這裡加一個 post(),這一組要變紅。
二、**金鑰不得出現在任何輸出**(第十條)。repr、str、例外訊息、
    回傳的報告 —— 全部都要乾淨。

第二條特別重要,因為失敗的路徑最容易漏:一個帶著簽章的 url 被塞進
例外訊息,那個訊息會被寫進 log、寄進 Telegram、貼進對話。
**這座城邦已經因為這件事踩過一次雷。**
"""
import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from exchange.bingx import private

SECRET = "s3cr3t-do-not-leak-this-value-anywhere"
KEY = "publickey1234567890abcdef"


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.headers = {}
        self._body = body if body is not None else {"code": 0, "data": {}}

    def json(self):
        return self._body


class FakeSession:
    """記下每一次呼叫,好讓測試檢查送出去的東西。"""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse()


def client(session=None, mode="demo"):
    return private.ReadOnlyClient(
        private.Credentials(key=KEY, secret=SECRET),
        mode=mode, session=session or FakeSession())


class TestTheSignatureFollowsTheSpec(unittest.TestCase):
    """
    2026-09-13 查證:HMAC-SHA256,簽的是 `k=v&k=v`,**值不做 URL 編碼**,
    十六進位小寫。
    """

    def test_it_signs_the_raw_query_string(self):
        import hashlib
        import hmac

        params = {"symbol": "BTC-USDT", "timestamp": 1700000000000}
        expected = hmac.new(
            SECRET.encode(), b"symbol=BTC-USDT&timestamp=1700000000000",
            hashlib.sha256).hexdigest()

        self.assertEqual(private.signature(params, SECRET), expected)

    def test_values_are_not_url_encoded_before_signing(self):
        """
        先編碼再簽,交易所算出來的會不一樣 —— 而它只會回一句
        「簽名錯誤」,不告訴你為什麼。這是最容易踩的一個坑。
        """
        signed = private.signature({"symbol": "BTC-USDT"}, SECRET)
        wrong = private.signature({"symbol": "BTC%2DUSDT"}, SECRET)

        self.assertNotEqual(signed, wrong)

    def test_the_signature_is_lowercase_hex(self):
        sig = private.signature({"a": 1}, SECRET)
        self.assertEqual(sig, sig.lower())
        self.assertEqual(len(sig), 64)
        int(sig, 16)          # 拋例外就代表不是十六進位


class TestDemoIsTheDefault(unittest.TestCase):
    """
    連實盤要是一個明確的決定,不是一個預設值。
    """

    def test_no_setting_means_demo(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(private.host(), private.DEMO_HOST)
            self.assertFalse(private.is_live())

    def test_live_requires_the_exact_word(self):
        self.assertEqual(private.host("live"), private.LIVE_HOST)
        self.assertTrue(private.is_live("live"))

    def test_a_typo_is_refused_rather_than_defaulted(self):
        """
        拼錯就當成 demo 聽起來安全,但它會讓「我以為我設了 live」
        變成無聲的事。反過來才危險,所以兩邊都直接拒絕。
        """
        for typo in ("LIVE_", "prod", "真的", "1"):
            with self.subTest(value=typo):
                with self.assertRaises(ValueError):
                    private.host(typo)

    def test_the_demo_host_is_the_vst_one(self):
        self.assertIn("vst", private.DEMO_HOST)


class TestNothingCanPlaceAnOrder(unittest.TestCase):
    """
    ⚠️ 這一組是這個檔案存在的第一個理由。
    """

    SOURCE = ROOT / "exchange" / "bingx" / "private.py"

    def tree(self):
        return ast.parse(self.SOURCE.read_text(encoding="utf-8"),
                         filename=str(self.SOURCE))

    def test_the_client_has_no_write_methods(self):
        for name in ("post", "delete", "put", "submit", "create_order",
                     "cancel", "cancel_all", "close_position"):
            with self.subTest(method=name):
                self.assertFalse(
                    hasattr(private.ReadOnlyClient, name),
                    f"ReadOnlyClient 多了 {name}() —— 這個模組不該有"
                    "任何改變帳戶狀態的能力")

    def test_no_write_verb_is_sent_anywhere(self):
        """
        AST 層面:整個檔案裡不該出現 POST / DELETE / PUT 這些動詞。
        """
        body = self.SOURCE.read_text(encoding="utf-8")
        for verb in ('"POST"', "'POST'", '"DELETE"', "'DELETE'",
                     '"PUT"', "'PUT'"):
            self.assertNotIn(verb, body, f"檔案裡出現了 {verb}")

    def test_every_endpoint_in_the_list_is_a_query(self):
        """
        READ_ONLY 這份清單裡不該有任何會下單、撤單、改槓桿的路徑。
        """
        forbidden = ("/trade/order", "/trade/batchOrders", "/trade/closeAll",
                     "/trade/cancel", "/trade/leverage", "/trade/marginType",
                     "/trade/positionMargin")
        for name, path in private.READ_ONLY.items():
            with self.subTest(endpoint=name):
                for bad in forbidden:
                    self.assertFalse(
                        path.startswith(bad) or path == bad,
                        f"{name} 指向會改變狀態的端點 {path}")

    def test_open_orders_is_a_query_not_an_action(self):
        """
        openOrders 長得像 trade 路徑,但它是查詢。留一條測試說明
        這是刻意的,免得下一個人看到 /trade/ 就以為漏了。
        """
        self.assertTrue(
            private.READ_ONLY["perp_open_orders"].endswith("openOrders"))


class TestTheSecretNeverLeaks(unittest.TestCase):
    """
    ⚠️ 第二個理由。這座城邦已經因為金鑰外洩踩過一次雷。
    """

    def test_repr_and_str_are_masked(self):
        creds = private.Credentials(key=KEY, secret=SECRET)

        self.assertNotIn(SECRET, repr(creds))
        self.assertNotIn(SECRET, str(creds))
        self.assertNotIn(SECRET, f"{creds}")
        self.assertIn("遮蔽", repr(creds))

    def test_the_masked_key_shows_almost_nothing(self):
        creds = private.Credentials(key=KEY, secret=SECRET)
        masked = creds.masked()

        self.assertNotIn(SECRET, masked)
        self.assertLess(len(masked), 12)

    def test_a_short_key_is_masked_entirely(self):
        self.assertEqual(
            private.Credentials(key="short", secret=SECRET).masked(),
            "<遮蔽>")

    def test_a_transport_failure_does_not_carry_the_signed_url(self):
        """
        **最重要的一條。**

        簽章與 timestamp 都在 query string 裡。把 url 塞進例外訊息,
        那個訊息會被寫進 log、寄進 Telegram、貼進對話。
        """
        class Boom:
            def request(self, *a, **kw):
                raise ConnectionError("網路壞了")

        with self.assertRaises(private.PrivateCallFailed) as caught:
            client(session=Boom()).positions()

        text = str(caught.exception)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("signature=", text)
        self.assertNotIn(KEY, text)

    def test_an_http_error_does_not_carry_the_signed_url(self):
        session = FakeSession([FakeResponse(status=401)])

        with self.assertRaises(private.PrivateCallFailed) as caught:
            client(session=session).positions()

        text = str(caught.exception)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("signature=", text)
        self.assertNotIn(KEY, text)

    def test_a_business_error_does_not_carry_the_signed_url(self):
        session = FakeSession([
            FakeResponse(body={"code": 100001, "msg": "signature error"})])

        with self.assertRaises(private.PrivateCallFailed) as caught:
            client(session=session).positions()

        text = str(caught.exception)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("signature=", text)
        # 但它要給有用的線索
        self.assertIn("簽章對不上", text)

    def test_the_verify_report_contains_no_credentials(self):
        session = FakeSession([
            FakeResponse(body={"code": 0, "data": {"balance": {
                "asset": "VST", "equity": "100000", "balance": "100000"}}}),
            FakeResponse(body={"code": 0, "data": []}),
            FakeResponse(body={"code": 0, "data": []}),
            FakeResponse(body={"code": 0, "data": {}}),
        ])

        report = client(session=session).verify()
        text = repr(report)

        self.assertNotIn(SECRET, text)
        self.assertNotIn(KEY, text)
        self.assertIn("…", report["金鑰"])

    def test_the_api_key_goes_in_the_header_not_the_query(self):
        session = FakeSession()
        client(session=session).positions()

        call = session.calls[0]
        self.assertEqual(call["headers"]["X-BX-APIKEY"], KEY)
        self.assertNotIn(KEY, call["url"])


class TestItAsksTheExchangeRatherThanGuessing(unittest.TestCase):
    """
    餘額端點在 v2 / v3 之間改過版。第五條:不要靠模型記憶猜 API。
    """

    def test_it_tries_both_balance_endpoints(self):
        session = FakeSession([
            FakeResponse(status=404),
            FakeResponse(body={"code": 0, "data": {"balance": {}}}),
        ])

        path, _data = client(session=session).discover_balance()

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(path, private.READ_ONLY["perp_balance_v2"])

    def test_it_remembers_which_one_worked(self):
        session = FakeSession([
            FakeResponse(status=404),
            FakeResponse(body={"code": 0, "data": {"balance": {}}}),
            FakeResponse(body={"code": 0, "data": {"balance": {}}}),
        ])
        c = client(session=session)

        c.balance()
        c.balance()

        # 第一次兩個都問,第二次直接用記住的那一個
        self.assertEqual(len(session.calls), 3)

    def test_both_failing_raises_with_both_reasons(self):
        session = FakeSession([FakeResponse(status=404),
                               FakeResponse(status=404)])

        with self.assertRaises(private.PrivateCallFailed) as caught:
            client(session=session).discover_balance()

        self.assertIn("兩個餘額端點都問不到", str(caught.exception))


class TestMissingDataIsNotZero(unittest.TestCase):
    """第九十四條:「讀不到餘額」與「餘額是零」是兩件事。"""

    def test_an_absent_field_summarises_as_none(self):
        got = private._summarise_balance({"balance": {"asset": "VST"}})

        self.assertIsNone(got["權益"])
        self.assertIsNone(got["可用保證金"])
        self.assertEqual(got["資產"], "VST")

    def test_a_real_zero_stays_zero(self):
        got = private._summarise_balance(
            {"balance": {"asset": "VST", "equity": "0"}})

        self.assertEqual(got["權益"], 0.0)
        self.assertIsNotNone(got["權益"])

    def test_an_unparseable_number_is_none_not_a_guess(self):
        got = private._summarise_balance(
            {"balance": {"equity": "not a number"}})

        self.assertIsNone(got["權益"])


class TestCredentialsComeFromTheEnvironment(unittest.TestCase):

    def test_missing_credentials_explain_the_permissions_needed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(private.CredentialsMissing) as caught:
                private.Credentials.from_env()

        text = str(caught.exception)
        self.assertIn("讀取", text)
        self.assertIn("提款", text)

    def test_blank_values_count_as_missing(self):
        with patch.dict(os.environ,
                        {private.ENV_KEY: "  ", private.ENV_SECRET: ""},
                        clear=True):
            with self.assertRaises(private.CredentialsMissing):
                private.Credentials.from_env()


if __name__ == "__main__":
    unittest.main()
