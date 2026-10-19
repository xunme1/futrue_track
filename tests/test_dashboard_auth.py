# -*- coding: utf-8 -*-
"""全站密码登录验证（FUTURES_DASHBOARD_PASSWORD）的接口级测试。"""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.api import server


def _client():
    return TestClient(server.app, follow_redirects=False)


class AuthDisabledTests(unittest.TestCase):
    """未设置环境变量时全站开放（本地开发默认）。"""

    def test_open_access_without_password(self):
        with mock.patch.object(server, "DASHBOARD_PASSWORD", ""):
            client = _client()
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.get("/api/symbols").status_code, 200)
            response = client.get("/")
            self.assertNotEqual(response.status_code, 302)

    def test_missing_seat_preview_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(server, "PROJECT_ROOT", Path(tmp)), \
                mock.patch.object(server, "DASHBOARD_PASSWORD", ""):
            self.assertEqual(_client().get("/previews/seat-observatory.html").status_code, 404)


class AuthEnabledTests(unittest.TestCase):
    PASSWORD = "abc123"

    def setUp(self):
        patcher = mock.patch.object(server, "DASHBOARD_PASSWORD", self.PASSWORD)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _client()

    def test_pages_redirect_to_login_and_api_returns_401(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(self.client.get("/api/symbols").status_code, 401)
        self.assertEqual(self.client.get("/api/reports").status_code, 401)

    def test_open_paths_stay_open(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("请输入访问密码", response.text)

    def test_wrong_password_rejected(self):
        response = self.client.post("/api/login", json={"password": "nope"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(server.AUTH_COOKIE, response.cookies)

    def test_seat_preview_requires_login_and_serves_updated_members(self):
        path = "/previews/seat-observatory.html"
        response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login")
        self.client.post("/api/login", json={"password": self.PASSWORD})
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertIn("席位观察室", response.text)
        for member in ("东证期货", "永安期货", "平安期货"):
            self.assertIn(member, response.text)

    def test_login_sets_cookie_and_unlocks_site(self):
        response = self.client.post("/api/login", json={"password": self.PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn(server.AUTH_COOKIE, response.cookies)
        # TestClient 自动携带 cookie
        self.assertEqual(self.client.get("/api/symbols").status_code, 200)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_forged_cookie_rejected(self):
        self.client.cookies.set(server.AUTH_COOKIE, "forged-token")
        self.assertEqual(self.client.get("/api/symbols").status_code, 401)

    def test_password_change_invalidates_old_cookie(self):
        self.client.post("/api/login", json={"password": self.PASSWORD})
        self.assertEqual(self.client.get("/api/symbols").status_code, 200)
        with mock.patch.object(server, "DASHBOARD_PASSWORD", "newpass"):
            self.assertEqual(self.client.get("/api/symbols").status_code, 401)


if __name__ == "__main__":
    unittest.main()
