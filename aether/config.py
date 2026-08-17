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
import sys
from pathlib import Path

_DIR      = str(Path(__file__).resolve().parent.parent)
_CFG_PATH = os.path.join(_DIR, "config.json")


def _load_file() -> dict:
    try:
        with open(_CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.stderr.write(
            f"\n⚠️  [CONFIG WARNING] config.json was not found at:\n"
            f"  {_CFG_PATH}\n"
            f"  Falling back to empty default settings. Please ensure this is intended!\n\n"
        )
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
        self.chaikin_uid      = os.environ.get("CHAIKIN_UID")      or chaikin.get("uid",      "")
        self.chaikin_api_key  = os.environ.get("CHAIKIN_API_KEY")  or chaikin.get("api_key",  "")

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
            primary_email = os.environ.get("SENDER_EMAIL") or os.environ.get("RECIPIENT_EMAIL") or ""
            if primary_email:
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
        self.smtp_password         = email_sender.get("password", "") or os.environ.get("SMTP_PASSWORD", "")
        self.email_sender_address  = os.environ.get("SENDER_EMAIL")     or email_sender.get("address",   "")
        self.email_recipient_address = os.environ.get("RECIPIENT_EMAIL") or email_sender.get("recipient", self.email_sender_address)

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

        # ── AETHER Oracle advisory (real-account doubling goal) ─────────────────
        # account: last-4 of the real account to audit (defaults to the first
        #          real account). start_equity: baseline for the doubling target
        #          (PII — never hardcode in source). target_date: goal deadline.
        oracle = raw.get("oracle") or {}
        target = (
            os.environ.get("ORACLE_ACCOUNT")
            or oracle.get("account")
            or oracle.get("account_id")
        )

        if "oracle" in raw:
            # If the user explicitly configured 'oracle', they MUST define a valid account
            if not target:
                raise ValueError(
                    "🚨 [AETHER CONFIG ERROR] The 'oracle' section is configured in config.json, "
                    "but no valid 'account' or 'account_id' was specified! "
                    "Please define 'account_id' inside the 'oracle' block."
                )
            # The configured target MUST exist in the accounts_real list to prevent trading on incorrect accounts
            if target not in self.accounts_real:
                raise ValueError(
                    f"🚨 [AETHER CONFIG ERROR] Configured Oracle account '{target}' was not found "
                    f"in the real accounts list: {self.accounts_real}! "
                    "Please verify your config.json accounts.real and oracle.account_id."
                )
            self.oracle_account = target
        else:
            # Legacy default fallback if the entire 'oracle' block is absent
            self.oracle_account = target or (self.accounts_real[0] if self.accounts_real else "")

        self.oracle_start_equity = float(
            os.environ.get("ORACLE_START_EQUITY") or oracle.get("start_equity") or 0.0
        )
        self.oracle_target_date = os.environ.get("ORACLE_TARGET_DATE") or oracle.get("target_date", "")
        self.oracle_s10_floor = float(os.environ.get("ORACLE_S10_FLOOR") or oracle.get("s10_floor", 2.5))
        self.oracle_bubble_limit = float(os.environ.get("ORACLE_BUBBLE_LIMIT") or oracle.get("bubble_limit", 2.5))
        self.oracle_min_rr_ratio = float(os.environ.get("ORACLE_MIN_RR_RATIO") or oracle.get("min_rr_ratio", 1.5))
        self.oracle_decay_limit = float(os.environ.get("ORACLE_DECAY_LIMIT") or oracle.get("decay_limit", -2.0))

        # ── Legacy PostgreSQL (database.py — unused in normal operation) ────────
        db = raw.get("database") or {}
        self.database_url = os.environ.get("DATABASE_URL") or db.get("url", "")

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

        # ── Quantitative Gating Parameters (single-sourced buy-gate thresholds) ──
        # Every knob below is read at exactly one production site; see the referenced
        # helper/gate in ai_portfolio_game.py and aether/risk_utils.py.
        system = raw.get("system") or {}
        self.system_default_s10_floor   = float(os.environ.get("AETHER_DEFAULT_S10_FLOOR")   or system.get("default_s10_floor", 2.5))
        self.system_adaptive_s10_floor  = float(os.environ.get("AETHER_ADAPTIVE_S10_FLOOR")  or system.get("adaptive_s10_floor", 2.0))
        self.system_cash_drag_threshold = float(os.environ.get("AETHER_CASH_DRAG_THRESHOLD") or system.get("cash_drag_threshold", 25.0))
        
        # Breakout Waiver / PGR Bypass parameters
        self.system_bypass_score_floor  = float(os.environ.get("AETHER_BYPASS_SCORE_FLOOR")  or system.get("bypass_score_floor", 8.0))
        self.system_bypass_s10_floor    = float(os.environ.get("AETHER_BYPASS_S10_FLOOR")    or system.get("bypass_s10_floor", 2.0))
        self.system_default_min_rr      = float(os.environ.get("AETHER_DEFAULT_MIN_RR")      or system.get("default_min_rr", 2.0))
        
        # Pyramiding parameters
        self.system_pyramiding_cash_ratio = float(os.environ.get("AETHER_PYRAMIDING_CASH_RATIO") or system.get("pyramiding_cash_ratio", 0.10))
        self.system_pyramiding_s10_floor  = float(os.environ.get("AETHER_PYRAMIDING_S10_FLOOR")  or system.get("pyramiding_s10_floor", 0.0))
        self.system_pyramiding_l60_floor  = float(os.environ.get("AETHER_PYRAMIDING_L60_FLOOR")  or system.get("pyramiding_l60_floor", 2.0))

        # ── Configuration Health Checks ───────────────────────────────────────
        self.verify_config_health()

    def verify_config_health(self):
        """Check for configuration placeholders (example.com, YOUR_...) and print high-visibility warning banners."""
        placeholders = []
        
        # Check emails
        email_fields = [
            ("Chaikin Email", self.chaikin_email),
            ("Sender Email", self.email_sender_address),
            ("Recipient Email", self.email_recipient_address)
        ]
        for field, email_val in email_fields:
            if email_val and "example.com" in email_val.lower():
                placeholders.append(f"{field} is set to placeholder: '{email_val}'")
                
        for i, mb in enumerate(self.mailboxes):
            mb_email = mb.get("email")
            if mb_email and "example.com" in mb_email.lower():
                placeholders.append(f"Mailbox {i+1} Email is set to placeholder: '{mb_email}'")
                
        # Check standard placeholder words
        auth_fields = [
            ("Chaikin Email", self.chaikin_email),
            ("Chaikin Password", self.chaikin_password),
            ("E*TRADE Username", self.etrade_username),
            ("E*TRADE Password", self.etrade_password),
            ("E*TRADE Sandbox Key", self.etrade_sandbox_key),
            ("E*TRADE Sandbox Secret", self.etrade_sandbox_secret),
            ("E*TRADE Production Key", self.etrade_production_key),
            ("E*TRADE Production Secret", self.etrade_production_secret),
            ("RapidAPI Key", self.rapidapi_key)
        ]
        for field, val in auth_fields:
            if val and any(ph in str(val).lower() for ph in ["your_", "placeholder", "your-"]):
                placeholders.append(f"{field} is set to placeholder value: '{val}'")
                
        if placeholders:
            def safe_write(s):
                try:
                    sys.stdout.write(s)
                except UnicodeEncodeError:
                    enc = getattr(sys.stdout, 'encoding', 'cp1252') or 'cp1252'
                    sys.stdout.write(s.encode(enc, errors='replace').decode(enc))

            safe_write("\n" + "!" * 80 + "\n")
            safe_write("🚨  [AETHER CONFIG HEALTH ALERT] Malconfigured Placeholders Detected!\n")
            safe_write("!" * 80 + "\n")
            for ph in placeholders:
                safe_write(f"  🛑 {ph}\n")
            safe_write("-" * 80 + "\n")
            safe_write("  Please update your 'config.json' file or environment variables to use real keys/accounts.\n")
            safe_write("!" * 80 + "\n\n")
            self.has_placeholders = True
            self.placeholder_details = placeholders
        else:
            self.has_placeholders = False
            self.placeholder_details = []

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
