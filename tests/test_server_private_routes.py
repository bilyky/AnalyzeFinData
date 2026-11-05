"""Private dashboard routes need the login token. The dashboard listens on the network
(web.host 0.0.0.0 on PROD), and /api/accounts (real broker accounts), the personal INTC plan
and the logs used to answer anyone. The route functions are called directly from
create_app()'s route table (starlette's TestClient needs a package this venv doesn't have)."""
import asyncio
import types
import unittest
from unittest import mock

from fastapi import HTTPException

import server

PRIVATE = ["/api/accounts", "/plan/intc", "/api/pipeline/logs", "/api/logs/aether", "/api/logs/aether/json"]
PUBLIC_SAMPLE = ["/api/health", "/api/roadmap"]


def _app():
    cfg = types.SimpleNamespace(web_secret="test-secret", admins=[])
    with mock.patch.object(server, "_cfg", return_value=cfg):
        return server.create_app()


def _endpoint(app, path):
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) == path and "GET" in r.methods)


class TestPrivateRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def _call(self, path, **kw):
        fn = _endpoint(self.app, path)
        defaults = {"lines": 10} if "logs" in path else {}
        if path.endswith("/json"):
            defaults["level"] = ""
        return asyncio.run(fn(**{**defaults, **kw}))

    def test_private_routes_refuse_without_a_token(self):
        for path in PRIVATE:
            for auth in ("", "Bearer nonsense", "Bearer " + server.make_token("u", "other-secret")):
                with self.subTest(path=path, auth=auth[:12]), self.assertRaises(HTTPException) as cm:
                    self._call(path, authorization=auth)
                self.assertEqual(cm.exception.status_code, 401)

    def test_accounts_answer_with_a_valid_token(self):
        token = server.make_token("admin", "test-secret")
        with mock.patch.object(server.data_api, "read_accounts", return_value={"accounts": []}) as read:
            self.assertEqual(self._call("/api/accounts", authorization="Bearer " + token), {"accounts": []})
        read.assert_called_once()

    def test_logs_answer_with_a_valid_token(self):
        token = server.make_token("admin", "test-secret")
        with mock.patch.object(server.data_api, "read_log_tail", return_value=["a"]):
            self.assertEqual(self._call("/api/pipeline/logs", authorization="Bearer " + token), {"lines": ["a"]})

    def test_chat_needs_a_token(self):
        fn = next(r.endpoint for r in self.app.routes if getattr(r, "path", None) == "/api/chat")
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(fn(messages=[{"role": "user", "content": "hi"}], authorization=""))
        self.assertEqual(cm.exception.status_code, 401)

    def test_public_routes_still_exist_without_auth_params(self):
        for path in PUBLIC_SAMPLE:
            fn = _endpoint(self.app, path)
            self.assertNotIn("authorization", fn.__code__.co_varnames, path)


if __name__ == "__main__":
    unittest.main()
