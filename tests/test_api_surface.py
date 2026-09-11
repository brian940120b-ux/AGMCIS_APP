"""
實際啟動 main.py 的 FastAPI app,驗證存取控制與路由表。

這是整合層的檢查:單元測試證明 require_api_key 會拒絕,
這裡證明它真的掛在每一個 /api/* 上,而且沒有重複註冊的路由。
"""
import os
import unittest


class TestApiSurface(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["DASHBOARD_KEY"] = "test-key"

        import importlib
        import web_auth
        importlib.reload(web_auth)

        import main
        cls.main = importlib.reload(main)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("DASHBOARD_KEY", None)

    def client(self):
        """每個測試用全新的 client —— TestClient 會保留 cookie,
        共用會讓前一個測試取得的 session 污染後面的驗證測試。"""
        from fastapi.testclient import TestClient
        return TestClient(self.main.app)

    def _paths(self):
        self.main.app.openapi_schema = None
        return self.main.app.openapi()["paths"]

    def test_no_duplicate_routes(self):
        """FastAPI 原本會對 6 條重複路由發出 Duplicate Operation ID 警告。"""
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.main.app.openapi_schema = None
            self.main.app.openapi()

        duplicates = [w for w in caught if "Duplicate Operation ID" in str(w.message)]
        self.assertEqual(duplicates, [], f"仍有重複路由:{[str(w.message) for w in duplicates]}")

    def test_every_api_endpoint_requires_a_key(self):
        unprotected = []
        paths = self._paths()

        for path in [p for p in paths if p.startswith("/api/")]:
            for method in paths[path]:
                # 每個請求用乾淨的 client,確保沒有帶到任何 session
                response = getattr(self.client(), method)(path)
                if response.status_code not in (401, 503):
                    unprotected.append(f"{method.upper()} {path} -> {response.status_code}")

        self.assertEqual(unprotected, [], f"以下端點沒有要求金鑰:{unprotected}")

    def test_auto_trader_is_no_longer_a_get(self):
        """GET 依定義不該變更狀態。原本 GET /api/auto_trader 會實際開倉。"""
        paths = self._paths()
        self.assertIn("/api/auto_trader", paths)
        self.assertIn("post", paths["/api/auto_trader"])
        self.assertNotIn("get", paths["/api/auto_trader"])

    def test_dashboard_requires_key(self):
        response = self.client().get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 401)

    def test_dashboard_key_in_query_sets_cookie_and_redirects(self):
        """金鑰不該留在網址列 —— 驗證後換成 HttpOnly cookie 並導回乾淨路徑。"""
        from web_auth import COOKIE_NAME

        response = self.client().get("/?key=test-key", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertIn(COOKIE_NAME, response.cookies)
        self.assertIn("httponly", response.headers["set-cookie"].lower())

    def test_wrong_key_does_not_set_cookie(self):
        from web_auth import COOKIE_NAME

        response = self.client().get("/?key=wrong", follow_redirects=False)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn(COOKIE_NAME, response.cookies)

    def test_v1_dashboard_requires_key(self):
        self.assertEqual(self.client().get("/v1", follow_redirects=False).status_code, 401)
