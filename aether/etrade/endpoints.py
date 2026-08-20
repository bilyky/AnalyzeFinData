"""Centralised E*TRADE endpoint construction.

The auth core (``aether.etrade.__init__``) owns the canonical URL maps
(``_RENEW_URL`` / ``_REVOKE_URL`` / ``_ACCTLIST_URL``) because its tests and tooling
reference them there. This module is a thin, env-aware **view** over those maps
plus builders for the endpoints new interfaces need (quotes, market clock, option
chains, alerts, orders) — so extending the client with a new API is a one-line
addition here instead of a scattered literal.

It reads the package's maps lazily (at property/method call time) so it never
duplicates them and always reflects any patched value.
"""
from __future__ import annotations


def _pkg():
    from aether import etrade as _p
    return _p


class Endpoints:
    """Env-scoped endpoint builder for one E*TRADE environment."""

    def __init__(self, env: str = "production"):
        self.env = env

    @property
    def base(self) -> str:
        return "https://apisb.etrade.com" if self.env == "sandbox" else "https://api.etrade.com"

    # --- canonical maps owned by the auth core (single source of truth) -----
    @property
    def renew(self) -> str:
        return _pkg()._RENEW_URL[self.env]

    @property
    def revoke(self) -> str:
        return _pkg()._REVOKE_URL[self.env]

    @property
    def accounts_list(self) -> str:
        return _pkg()._ACCTLIST_URL[self.env]

    # --- builders for read / extension endpoints ----------------------------
    def market_clock(self) -> str:
        return f"{self.base}/v1/market/clock.json"

    def quote(self, symbol: str) -> str:
        return f"{self.base}/v1/market/quote/{symbol}.json"

    def option_chains(self) -> str:
        return f"{self.base}/v1/market/optionchains.json"

    def option_expire_dates(self) -> str:
        return f"{self.base}/v1/market/optionexpiredate.json"

    def alerts(self) -> str:
        return f"{self.base}/v1/user/alerts.json"

    def orders(self, account_id_key: str) -> str:
        return f"{self.base}/v1/accounts/{account_id_key}/orders.json"

    def preview_order(self, account_id_key: str) -> str:
        return f"{self.base}/v1/accounts/{account_id_key}/orders/preview.json"

    def place_order(self, account_id_key: str) -> str:
        return f"{self.base}/v1/accounts/{account_id_key}/orders/place.json"

    def cancel_order(self, account_id_key: str) -> str:
        return f"{self.base}/v1/accounts/{account_id_key}/orders/cancel.json"
