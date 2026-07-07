"""
Unified configuration loader for the AETHER trading system.

Priority (highest first):
  1. Environment variables (override everything — useful for CI/CD or secrets managers)
  2. config.json in the project root (copy from config.json.example and fill in values)

Usage:
    from config import CFG

    email    = CFG.chaikin_email
    password = CFG.chaikin_password
    key      = CFG.etrade_production_key
    secret   = CFG.etrade_production_secret
    api_key  = CFG.rapidapi_key
"""

import json
import os

_DIR      = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_DIR, "config.json")


def _load_file() -> dict:
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"config.json is malformed: {e}") from e


class _Config:
    def __init__(self):
        raw = _load_file()

        # ── Chaikin ──────────────────────────────────────────────────────────
        chaikin = raw.get("chaikin", {})
        self.chaikin_email    = os.environ.get("CHAIKIN_EMAIL")    or chaikin.get("email",    "")
        self.chaikin_password = os.environ.get("CHAIKIN_PASSWORD") or chaikin.get("password", "")

        # ── E*TRADE ──────────────────────────────────────────────────────────
        etrade = raw.get("etrade", {})
        self.etrade_username = os.environ.get("ETRADE_USERNAME") or etrade.get("username", "")
        self.etrade_password = os.environ.get("ETRADE_PASSWORD") or etrade.get("password", "")
        self.etrade_proxy    = os.environ.get("ETRADE_PROXY")    or etrade.get("proxy",    "")

        sandbox    = etrade.get("sandbox",    {})
        production = etrade.get("production", {})
        self.etrade_sandbox_key        = os.environ.get("ETRADE_SANDBOX_KEY")        or sandbox.get("consumer_key",    "")
        self.etrade_sandbox_secret     = os.environ.get("ETRADE_SANDBOX_SECRET")     or sandbox.get("consumer_secret", "")
        self.etrade_production_key     = os.environ.get("ETRADE_PRODUCTION_KEY")     or production.get("consumer_key",    "")
        self.etrade_production_secret  = os.environ.get("ETRADE_PRODUCTION_SECRET")  or production.get("consumer_secret", "")

        # ── RapidAPI / Alpha Vantage ──────────────────────────────────────────
        rapidapi = raw.get("rapidapi", {})
        self.rapidapi_key = os.environ.get("RAPIDAPI_KEY") or rapidapi.get("api_key", "")

    def require(self, *attrs: str) -> None:
        """Raise RuntimeError if any of the given attributes are empty."""
        missing = [a for a in attrs if not getattr(self, a, "")]
        if missing:
            raise RuntimeError(
                f"Missing config: {', '.join(missing)}.\n"
                f"  Set the corresponding env var(s) or add them to {_CFG_PATH}\n"
                f"  (copy {_CFG_PATH.replace('config.json', 'config.json.example')} as a template)"
            )


CFG = _Config()
