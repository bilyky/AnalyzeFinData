"""Guards the E*TRADE token-auth tri-state and its one job: never destroy a good
token on a transient blip.

Red-green anchor: `_probe_token_auth` replaced the old boolean `_test_tokens_valid`.
On the pre-fix code this import fails and the transient case deleted the cache, so
`test_transient_probe_does_not_delete_token` fails; on the fixed code both pass.
"""
import unittest
from unittest import mock

from aether import etrade


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def _session_returning(resp=None, exc=None):
    """A patched OAuth1Session factory whose .get() yields `resp` or raises `exc`."""
    factory = mock.MagicMock()
    if exc is not None:
        factory.return_value.get.side_effect = exc
    else:
        factory.return_value.get.return_value = resp
    return factory


_DUMMY_TOKENS = {"oauth_token": "t", "oauth_token_secret": "s"}


class TestProbeTokenAuthTriState(unittest.TestCase):
    """_probe_token_auth must distinguish a real rejection from a transient failure."""

    def setUp(self):
        # Keep the probe off the network and out of CFG.require — creds are irrelevant here.
        p = mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "pw"))
        p.start()
        self.addCleanup(p.stop)

    def _probe_with(self, resp=None, exc=None):
        with mock.patch.object(etrade, "OAuth1Session", _session_returning(resp, exc)):
            return etrade._probe_token_auth(_DUMMY_TOKENS, env="production")

    def test_http_200_is_authorized(self):
        self.assertIs(self._probe_with(resp=_FakeResp(200)), True)

    def test_http_401_is_explicit_rejection(self):
        self.assertIs(self._probe_with(resp=_FakeResp(401)), False)

    def test_http_403_is_explicit_rejection(self):
        self.assertIs(self._probe_with(resp=_FakeResp(403)), False)

    def test_http_500_is_indeterminate(self):
        # A broker-side 5xx is NOT the token's fault — must not be treated as a rejection.
        self.assertIsNone(self._probe_with(resp=_FakeResp(500)))

    def test_network_exception_is_indeterminate(self):
        self.assertIsNone(self._probe_with(exc=ConnectionError("proxy down")))


class TestGetTokensDeletionScoping(unittest.TestCase):
    """get_tokens invalidates the cache ONLY on an explicit 401/403 — never on a transient
    blip — and does so via SOFT delete (recoverable), never a hard os.remove."""

    def setUp(self):
        for name, kw in (
            ("_load_config", {"return_value": ("ck", "cs", "u", "pw")}),
            ("_load_tokens", {"return_value": dict(_DUMMY_TOKENS)}),
        ):
            p = mock.patch.object(etrade, name, **kw)
            p.start()
            self.addCleanup(p.stop)

    def test_explicit_rejection_soft_deletes_cache(self):
        # An explicit 401 invalidates the cache via the project-wide SOFT delete — the token
        # is moved to the garbage can (recoverable within retention), NEVER a hard os.remove.
        # With every automated path exhausted get_tokens then fails SOFT to None (documented
        # -> dict | None contract; ETRADE_AUTH.md rule 6), never raising.
        with mock.patch.object(etrade, "_probe_token_auth", return_value=False), \
             mock.patch.object(etrade, "_load_tokens_any_date", return_value=None), \
             mock.patch.object(etrade.os.path, "exists", return_value=False), \
             mock.patch.object(etrade.trash, "soft_delete") as soft, \
             mock.patch.object(etrade.os, "remove") as rm:
            result = etrade.get_tokens(env="production", allow_browser=False)
        self.assertIsNone(result)
        soft.assert_called_once_with(etrade._TOKEN_PATH, reason="rejected-401")
        rm.assert_not_called()   # soft-delete only — the token is recoverable, not destroyed

    def test_transient_probe_does_not_delete_token(self):
        # None (transient) must skip deletion (soft OR hard) AND still attempt renewal.
        sentinel = {"renewed": True}
        with mock.patch.object(etrade, "_probe_token_auth", return_value=None), \
             mock.patch.object(etrade, "renew_tokens", return_value=sentinel) as renew, \
             mock.patch.object(etrade.trash, "soft_delete") as soft, \
             mock.patch.object(etrade.os, "remove") as rm:
            result = etrade.get_tokens(env="production", allow_browser=False)
        soft.assert_not_called()
        rm.assert_not_called()
        renew.assert_called_once()
        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
