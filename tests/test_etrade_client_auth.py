"""Coverage for ``ETradeClient.auth`` as a functional **superset** of the free-function surface.

PR 2 of the free-function retirement program completes the client's write/renew/maintenance API so
every consumer can migrate to the object *before* the free functions are relocated/deleted (PRs 3-5).
The only method missing from ``_AuthInterface`` was ``scheduled_reauth`` — the ONE unattended
automated re-auth door — so these tests anchor it red-green (delegation + role guard) and lock the
ban-safety invariant that **no** control-plane method is reachable from the scaled data plane.

Everything still delegates outward to ``aether.etrade`` (the state machine relocates in PR 5); these
tests patch the free functions on the package and assert the client routes to them with its own env.
"""
import unittest
from unittest import mock

from aether import etrade
from aether.etrade.client import ETradeClient, RoleNotPermitted


class TestScheduledReauthOnClient(unittest.TestCase):
    """auth.scheduled_reauth() delegates to _pkg.scheduled_reauth and is auth-role only."""

    def test_delegates_to_pkg_with_client_env(self):
        sentinel = {"ok": True, "env": "production", "reason": "renewed", "browser_opened": False}
        client = ETradeClient("production", role="auth")
        with mock.patch.object(etrade, "scheduled_reauth", return_value=sentinel) as m:
            got = client.auth.scheduled_reauth()
        self.assertIs(got, sentinel)
        m.assert_called_once_with("production")

    def test_blocked_on_data_role_and_never_calls_pkg(self):
        # Ban-safety: the scaled data plane must never trigger a re-auth on the shared credential.
        client = ETradeClient("production", role="data")
        with mock.patch.object(etrade, "scheduled_reauth") as m:
            with self.assertRaises(RoleNotPermitted):
                client.auth.scheduled_reauth()
        m.assert_not_called()


class TestControlPlaneClosedOnDataRole(unittest.TestCase):
    """Every auth (control-plane) method raises RoleNotPermitted in the data role and never
    reaches the underlying free function — the single ban-safety boundary for the scaled plane.

    This is the superset's load-bearing guarantee: adding a method to _AuthInterface must also add
    it to the role-gated set, or the data plane could renew/open a browser on the shared credential.
    """

    # (client method name, *call args) — args are dummies; the guard must fire before any use.
    _CONTROL = [
        ("get_tokens", ()),
        ("keep_alive", ()),
        ("renew", ({"oauth_token": "x", "oauth_token_secret": "y"},)),
        ("revoke", ({"oauth_token": "x", "oauth_token_secret": "y"},)),
        ("reset_circuit_breaker", ()),
        ("scheduled_reauth", ()),
    ]
    # The free functions each control method delegates to — patched so a guard REGRESSION (a method
    # slipping the role check) would call a mock we can assert was never hit, not the real auth path.
    _PKG_FNS = ["get_tokens", "keep_alive", "renew_tokens", "revoke_tokens",
                "reset_reauth_circuit_breaker", "scheduled_reauth"]

    def test_all_control_methods_closed_on_data_role(self):
        client = ETradeClient("production", role="data")
        with mock.patch.multiple(
            etrade,
            **{fn: mock.DEFAULT for fn in self._PKG_FNS},
        ) as mocks:
            for name, args in self._CONTROL:
                with self.subTest(method=name):
                    with self.assertRaises(RoleNotPermitted):
                        getattr(client.auth, name)(*args)
            for fn, m in mocks.items():
                self.assertFalse(m.called, f"data role reached _pkg.{fn} — ban-safety breach")

    def test_all_control_methods_present_in_auth_role(self):
        # Characterization guard (not red-green for this PR beyond scheduled_reauth): the superset
        # exposes each control method as a callable in the auth role. Delegation targets are patched
        # so nothing touches the real broker/token files.
        client = ETradeClient("production", role="auth")
        with mock.patch.multiple(
            etrade,
            **{fn: mock.DEFAULT for fn in self._PKG_FNS},
        ):
            for name, args in self._CONTROL:
                with self.subTest(method=name):
                    self.assertTrue(callable(getattr(client.auth, name)))
                    getattr(client.auth, name)(*args)  # must not raise in the auth role


if __name__ == "__main__":
    unittest.main()
