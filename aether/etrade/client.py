"""``ETradeClient`` — the single, extensible object that encapsulates E*TRADE.

This is the front door the rest of AETHER (and the standalone microservice) should
use going forward. It composes five resource interfaces —

    client.auth       tokens / renew / keep-alive / revoke / circuit-breaker /
                      scheduled (unattended) re-auth
    client.market     quotes / market clock / option chains
    client.accounts   positions / account list
    client.orders     place / cancel / list            (extension stub, Phase 3)
    client.alerts     broker alert feed                 (extension stub, Phase 3)

— and adds two things the free-function API can't express cleanly:

* **Roles.** ``role="auth"`` is the single-writer control plane (may renew tokens
  and, with ``allow_browser=True``, open a browser). ``role="data"`` is the
  stateless, horizontally-scalable read plane: it may only *read* the current
  token and call read endpoints — ``get_tokens`` / ``renew`` / ``revoke`` /
  ``keep_alive`` raise. This is the ban-safety boundary that lets the data plane
  scale without multiplying re-auth attempts on the shared credential.
* **A pluggable data layer** (``store``) so token/browser-state/breaker persistence
  can move from files to a DB with no call-site change, once wired.

IMPLEMENTATION: every method **delegates to the proven free functions** in
``aether.etrade`` (looked up on the package at call time). The class adds structure,
role enforcement and extension points; it does NOT re-implement — so all existing
behaviour, tests and ban-safety guarantees carry through unchanged.

``store`` is wired **one port at a time**, each in its own follow-up PR rather than as
one refactor across these already-tested paths. Wired so far: the **token read path**
(``auth.current_token`` and the data-role ``_tokens`` resolver route through
``store.tokens.load``). Still on ``_pkg`` directly: the token **write/renew** paths
(``get_tokens``/``keep_alive``/``renew``/``revoke``, which internally save/soft-delete)
and the browser-state / reauth ports.
"""
from __future__ import annotations

from typing import Optional

from aether import etrade as _pkg
from aether.etrade.store import make_etrade_store


class ETradeError(RuntimeError):
    """Base error for the E*TRADE client."""


class RoleNotPermitted(ETradeError):
    """Raised when a data-plane client attempts a control-plane (auth) action."""


# ---------------------------------------------------------------------------
# Resource interfaces
# ---------------------------------------------------------------------------

class _AuthInterface:
    """Token lifecycle + the anti-ban circuit breaker."""

    def __init__(self, client: "ETradeClient"):
        self._c = client

    def _require_auth_role(self, action: str):
        if self._c.role != "auth":
            raise RoleNotPermitted(
                f"'{action}' is an auth-role action; this client has role='{self._c.role}'. "
                f"Use current_token() on the data plane, or construct ETradeClient(role='auth')."
            )

    # -- control plane (auth role only) ------------------------------------
    def get_tokens(self, allow_browser: Optional[bool] = None) -> Optional[dict]:
        """Full token acquisition (renew / headless re-auth / optional browser)."""
        self._require_auth_role("get_tokens")
        ab = self._c.allow_browser if allow_browser is None else allow_browser
        return _pkg.get_tokens(self._c.env, allow_browser=ab)

    def keep_alive(self) -> Optional[dict]:
        """Ban-safe renew-only refresh of a still-valid same-day token."""
        self._require_auth_role("keep_alive")
        return _pkg.keep_alive(self._c.env)

    def renew(self, tokens: dict) -> Optional[dict]:
        self._require_auth_role("renew")
        return _pkg.renew_tokens(tokens, self._c.env)

    def revoke(self, tokens: dict) -> bool:
        self._require_auth_role("revoke")
        return _pkg.revoke_tokens(tokens, self._c.env)

    def reset_circuit_breaker(self) -> None:
        self._require_auth_role("reset_circuit_breaker")
        _pkg.reset_reauth_circuit_breaker(self._c.env)

    def scheduled_reauth(self) -> dict:
        """The ONE unattended automated re-auth door: renew-first (pure HTTP, no
        browser), and only if that fails does it open a browser AT MOST once — and
        only when the profile is trusted and the breaker is clear. Auth-role only —
        the scaled data plane must never trigger a re-auth on the shared credential.

        Unlike ``get_tokens``, this door is deliberately NOT governed by the client's
        ``allow_browser`` flag — its own trust-marker + circuit-breaker gate decides
        whether a browser opens, so one can open here even on an ``allow_browser=False``
        client. That is intentional: this is the sanctioned scheduled door, and gating
        it on the flag would defeat its purpose.

        Returns ``scheduled_reauth``'s JSON-serializable result dict
        ({ok, env, reason, browser_opened, ...})."""
        self._require_auth_role("scheduled_reauth")
        return _pkg.scheduled_reauth(self._c.env)

    # -- read-only (both roles) --------------------------------------------
    def current_token(self) -> Optional[dict]:
        """Today's cached token (same-day ET guard), or None. Never renews, never
        opens a browser, never writes — safe on the scaled data plane."""
        return self._c.store.tokens.load(self._c.env)

    def probe(self, tokens: dict):
        """Tri-state live probe: True (authorized) / False (401-403) / None (blip)."""
        return _pkg._probe_token_auth(tokens, self._c.env)

    def cooldown_remaining(self) -> float:
        """Seconds left on the automated-reauth cooldown (0 = gate open)."""
        return _pkg._reauth_cooldown_remaining(self._c.env)


class _MarketInterface:
    """Market data + the options extension point."""

    def __init__(self, client: "ETradeClient"):
        self._c = client

    def quotes(self, symbols: list, tokens: Optional[dict] = None) -> dict:
        return _pkg.fetch_quotes(self._c._tokens(tokens), symbols, self._c.env)

    def raw_market(self, tokens: Optional[dict] = None):
        """The underlying pyetrade ETradeMarket object (escape hatch)."""
        return _pkg.get_market(self._c._tokens(tokens), self._c.env)

    def is_open_now(self, tokens: Optional[dict] = None):
        return _pkg.is_market_open_now(self._c._tokens(tokens), self._c.env)

    def options_chain(self, symbol: str, expiry=None, *, chain_type: Optional[str] = None,
                      strike_near: Optional[int] = None, no_of_strikes: Optional[int] = None,
                      resp_format: str = "json") -> dict:
        """Fetch the option chain for ``symbol`` (the interface the collar/options
        adviser needs). ``expiry`` is a ``datetime.date`` (None = nearest term).

        Wraps ``pyetrade.ETradeMarket.get_option_chains``. NOTE: not yet exercised
        against the live broker — verify end-to-end before relying on the shape.
        """
        market = _pkg.get_market(self._c._tokens(tokens=None), self._c.env)
        return market.get_option_chains(
            symbol, expiry_date=expiry, chain_type=chain_type,
            strike_price_near=strike_near, no_of_strikes=no_of_strikes,
            resp_format=resp_format,
        )

    def option_expiry_dates(self, symbol: str, resp_format: str = "json") -> dict:
        market = _pkg.get_market(self._c._tokens(tokens=None), self._c.env)
        return market.get_option_expire_date(symbol, resp_format=resp_format)


class _AccountsInterface:
    def __init__(self, client: "ETradeClient"):
        self._c = client

    def positions(self, tokens: Optional[dict] = None) -> list:
        return _pkg.fetch_positions(self._c._tokens(tokens), self._c.env)

    def raw_accounts(self, tokens: Optional[dict] = None):
        return _pkg.get_accounts(self._c._tokens(tokens), self._c.env)


class _OrdersInterface:
    """Order placement — EXTENSION STUB (Phase 3).

    The contract intentionally takes a client ``idempotency_token`` so a retried
    request can never double-fill. No live order code exists in AETHER today; these
    raise until deliberately implemented (order placement is real money — it stays
    an explicit, reviewed step, never an accidental capability).
    """

    def __init__(self, client: "ETradeClient"):
        self._c = client

    def place(self, account_id_key: str, order: dict, idempotency_token: str):
        raise NotImplementedError(
            "Order placement is not implemented (Phase 3). It requires a preview->place "
            "flow with an idempotency token; implement in aether/etrade/orders.py."
        )

    def cancel(self, account_id_key: str, order_id: str):
        raise NotImplementedError("Order cancel not implemented (Phase 3).")

    def list(self, account_id_key: str):
        raise NotImplementedError("Order list not implemented (Phase 3).")


class _AlertsInterface:
    """Broker alert feed — EXTENSION STUB (Phase 3). GET /v1/user/alerts."""

    def __init__(self, client: "ETradeClient"):
        self._c = client

    def list(self, since=None):
        raise NotImplementedError(
            "Alerts feed not implemented (Phase 3). Wrap GET /v1/user/alerts in "
            "aether/etrade/alerts.py."
        )


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

class ETradeClient:
    """Cohesive, extensible E*TRADE client.

    Parameters
    ----------
    env : "sandbox" | "production"
    role : "auth" | "data"
        "auth" (default) is the single-writer control plane; "data" is the
        read-only, scalable plane (no renew / browser / writes).
    store : EtradeStore, optional
        Persistence backend; defaults to ``make_etrade_store()`` (file, or DB when
        DATABASE_URL is set).
    allow_browser : bool
        Default for ``auth.get_tokens`` — only meaningful in the auth role and only
        honoured in an interactive TTY.

    Example
    -------
    >>> et = ETradeClient("production", role="data")
    >>> et.market.quotes(["AAPL", "SPY"])          # read plane, no re-auth
    >>> ctrl = ETradeClient("production", role="auth")
    >>> ctrl.auth.keep_alive()                     # control plane keeps token warm
    """

    def __init__(self, env: str = "sandbox", *, role: str = "auth",
                 store=None, allow_browser: bool = False):
        if role not in ("auth", "data"):
            raise ValueError(f"role must be 'auth' or 'data', got {role!r}")
        self.env = env
        self.role = role
        self.allow_browser = allow_browser
        self.store = store if store is not None else make_etrade_store()

        self.auth = _AuthInterface(self)
        self.market = _MarketInterface(self)
        self.accounts = _AccountsInterface(self)
        self.orders = _OrdersInterface(self)
        self.alerts = _AlertsInterface(self)

    def _tokens(self, tokens: Optional[dict] = None) -> Optional[dict]:
        """Resolve tokens for a read call.

        Explicit tokens win. Otherwise the auth role acquires/renews via get_tokens;
        the data role only *reads* the current cached token (never renews/opens a
        browser) — keeping the scaled plane ban-safe.
        """
        if tokens is not None:
            return tokens
        if self.role == "auth":
            return _pkg.get_tokens(self.env, allow_browser=self.allow_browser)
        return self.store.tokens.load(self.env)

    def __repr__(self) -> str:
        return f"<ETradeClient env={self.env!r} role={self.role!r} backend={self.store.backend!r}>"
