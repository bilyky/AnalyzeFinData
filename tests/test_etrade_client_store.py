"""First direct coverage of ``ETradeClient`` <-> ``store`` wiring — the token READ path.

Red-green anchor: the token read must resolve through ``store.tokens.load(env)``, not through
``_pkg._load_tokens`` directly. We inject a *fake* store returning a sentinel and, in the same
test, patch ``etrade._load_tokens`` to return a *different* value. On the pre-wiring code
``current_token()`` / ``_tokens()`` call ``_pkg._load_tokens`` and ignore the injected store, so
they return the ``_load_tokens`` value and these tests FAIL; once the two call sites route through
the store, they return the store's value and pass.

Scope: reads only. The auth-role write/renew paths (``get_tokens``/``keep_alive``/``renew``/
``revoke``) still go through ``_pkg`` and are wired in a later PR.
"""
import os
import unittest
from unittest import mock

from aether import etrade
from aether.etrade.client import ETradeClient
from aether.etrade.store import make_etrade_store, EtradeStore


_STORE_TOKEN = {"oauth_token": "from-store", "oauth_token_secret": "s"}
_PKG_TOKEN = {"oauth_token": "from-pkg-load-tokens", "oauth_token_secret": "s"}


class _FakeTokenStore:
    """Records the envs it was asked for and returns a fixed sentinel."""

    def __init__(self, value):
        self._value = value
        self.load_calls = []

    def load(self, env):
        self.load_calls.append(env)
        return self._value


class _FakeStore:
    """Minimal stand-in for ``EtradeStore`` — only ``.tokens.load`` and ``.backend`` are touched."""

    def __init__(self, value, backend="fake"):
        self.tokens = _FakeTokenStore(value)
        self.browser_state = None
        self.reauth = None
        self.backend = backend


class TestTokenReadRoutesThroughStore(unittest.TestCase):
    """current_token() and the data-role _tokens() read via store.tokens.load, not _pkg._load_tokens."""

    def _client(self, store):
        return ETradeClient("production", role="data", store=store)

    def test_current_token_uses_injected_store(self):
        store = _FakeStore(_STORE_TOKEN)
        client = self._client(store)
        with mock.patch.object(etrade, "_load_tokens", return_value=_PKG_TOKEN) as m:
            got = client.auth.current_token()
        self.assertEqual(got, _STORE_TOKEN)              # from the store...
        self.assertEqual(store.tokens.load_calls, ["production"])
        m.assert_not_called()                            # ...not the pkg free function

    def test_data_role_tokens_resolution_uses_injected_store(self):
        store = _FakeStore(_STORE_TOKEN)
        client = self._client(store)
        with mock.patch.object(etrade, "_load_tokens", return_value=_PKG_TOKEN) as m:
            got = client._tokens()                        # data role, no explicit tokens
        self.assertEqual(got, _STORE_TOKEN)
        self.assertIn("production", store.tokens.load_calls)
        m.assert_not_called()

    def test_explicit_tokens_bypass_the_store(self):
        # An explicit token arg must win and never hit the store (unchanged contract).
        store = _FakeStore(_STORE_TOKEN)
        client = self._client(store)
        explicit = {"oauth_token": "explicit", "oauth_token_secret": "s"}
        self.assertEqual(client._tokens(explicit), explicit)
        self.assertEqual(store.tokens.load_calls, [])


class TestCurrentTokenIsBanSafe(unittest.TestCase):
    """The read path must never renew or open a browser (data-plane safety)."""

    def test_current_token_never_calls_get_tokens(self):
        store = _FakeStore(_STORE_TOKEN)
        client = ETradeClient("production", role="data", store=store)
        with mock.patch.object(etrade, "get_tokens") as gt, \
             mock.patch.object(etrade, "_load_tokens", return_value=_PKG_TOKEN):
            client.auth.current_token()
        gt.assert_not_called()


class TestDefaultStoreSelection(unittest.TestCase):
    """Without DATABASE_URL, the default backend is the file adapter (today's behaviour)."""

    class _Cfg:
        database_url = None

    def test_make_store_is_file_backend_without_db_url(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            store = make_etrade_store(self._Cfg())
        self.assertIsInstance(store, EtradeStore)
        self.assertEqual(store.backend, "file")


if __name__ == "__main__":
    unittest.main()
