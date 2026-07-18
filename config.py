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
        chaikin = raw.get("chaikin") or {}
        self.chaikin_email    = os.environ.get("CHAIKIN_EMAIL")    or chaikin.get("email",    "")
        self.chaikin_password = os.environ.get("CHAIKIN_PASSWORD") or chaikin.get("password", "")

        # ── E*TRADE ──────────────────────────────────────────────────────────
        etrade     = raw.get("etrade") or {}
        sandbox    = etrade.get("sandbox")    or {}
        production = etrade.get("production") or {}
        self.etrade_username = os.environ.get("ETRADE_USERNAME") or etrade.get("username", "")
        self.etrade_password = os.environ.get("ETRADE_PASSWORD") or etrade.get("password", "")
        self.etrade_proxy    = os.environ.get("ETRADE_PROXY")    or etrade.get("proxy",    "")
        self.etrade_sandbox_key        = os.environ.get("ETRADE_SANDBOX_KEY")        or sandbox.get("consumer_key",    "")
        self.etrade_sandbox_secret     = os.environ.get("ETRADE_SANDBOX_SECRET")     or sandbox.get("consumer_secret", "")
        self.etrade_production_key     = os.environ.get("ETRADE_PRODUCTION_KEY")     or production.get("consumer_key",    "")
        self.etrade_production_secret  = os.environ.get("ETRADE_PRODUCTION_SECRET")  or production.get("consumer_secret", "")

        # ── RapidAPI / Alpha Vantage ──────────────────────────────────────────
        rapidapi = raw.get("rapidapi") or {}
        self.rapidapi_key = os.environ.get("RAPIDAPI_KEY") or rapidapi.get("api_key", "")

        # ── Email Intelligence (multiple mailboxes configuration) ────────────
        email_intel = raw.get("email_intel") or {}
        mailboxes_list = email_intel.get("mailboxes") or []
        self.mailboxes = []
        if not mailboxes_list:
            primary_email = os.environ.get("SENDER_EMAIL") or os.environ.get("RECIPIENT_EMAIL") or "bilyky@gmail.com"
            self.mailboxes.append({
                "email": primary_email,
                "password_env": "SMTP_PASSWORD",
                "imap_server": "imap.gmail.com"
            })
        else:
            for mb in mailboxes_list:
                self.mailboxes.append({
                    "email": mb.get("email", ""),
                    "password_env": mb.get("password_env", "SMTP_PASSWORD"),
                    "imap_server": mb.get("imap_server", "imap.gmail.com")
                })

        # ── Email Sender (credentials for dispatching reports) ───────────────
        email_sender = raw.get("email_sender") or {}
        self.smtp_password = os.environ.get("SMTP_PASSWORD") or email_sender.get("password", "")

        # ── AI evaluation backends (multiple named providers, each toggleable) ──
        ai = raw.get("ai") or {}
        self.ai_primary       = os.environ.get("AI_PRIMARY") or ai.get("primary", "")
        self.ai_providers     = ai.get("providers") or {}
        self.ai_max_intel_emails = int(
            os.environ.get("AI_MAX_INTEL_EMAILS", "") or ai.get("max_intel_emails", 20)
        )

        # ── Real brokerage accounts (last-4 IDs; PII — never hardcode in source) ─
        # Ordered: [0] = top Short_Long table (T1), [1] = bottom table (T2).
        # ACCOUNTS_REAL env var (JSON array) overrides config when set.
        accounts = raw.get("accounts") or {}
        _acc_env = os.environ.get("ACCOUNTS_REAL")
        if _acc_env:
            try:
                self.accounts_real = json.loads(_acc_env) or []
            except (json.JSONDecodeError, ValueError):
                self.accounts_real = []
        else:
            self.accounts_real = accounts.get("real") or []

        # ── Web Dashboard ─────────────────────────────────────────────────────
        web = raw.get("web") or {}
        self.web_port   = int(os.environ.get("WEB_PORT", "") or web.get("port", 8888))
        self.web_host   = os.environ.get("WEB_HOST") or web.get("host", "0.0.0.0")
        # Admin accounts: list of {"user": ..., "pass": ...}. Empty = admin actions disabled.
        # WEB_ADMINS env var (JSON array) overrides the config file when set.
        _admins_env = os.environ.get("WEB_ADMINS")
        if _admins_env:
            try:
                self.web_admins = json.loads(_admins_env) or []
            except (json.JSONDecodeError, ValueError):
                self.web_admins = []
        else:
            self.web_admins = web.get("admins") or []
        # HMAC signing secret for session tokens. Empty = server generates an
        # ephemeral one at startup (tokens don't survive a restart).
        self.web_secret = os.environ.get("WEB_SECRET") or web.get("secret", "")

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
