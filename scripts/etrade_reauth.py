"""Trigger the sanctioned automated E*TRADE re-auth door by hand.

The "run it myself" companion to the web "+" button and the lazy first-request auto-mint: all
three call the SAME door, ``etrade.scheduled_reauth()`` — renew-first (pure HTTP, no browser),
and only if that fails does it open a browser AT MOST once, gated by the trust marker + anti-ban
circuit breaker + a non-blocking single-flight lock. This is a thin, unattended-safe wrapper:

    python scripts/etrade_reauth.py                 # env=production (default)
    python scripts/etrade_reauth.py --env sandbox

It prints the door's JSON result and exits 0 on success, 1 otherwise. It does NOT clear the
circuit breaker — a human does that deliberately (scripts/diagnostics/test_etrade.py or a reset)
so a ban-risk streak can't be papered over by re-running this.
"""
import argparse
import json
import sys
from pathlib import Path

# Repo root on sys.path so the root-level console_safe module and the aether package resolve;
# then make stdout/stderr emoji-safe on Windows (no-op elsewhere) before any output.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import console_safe
console_safe.install()

from aether import etrade
from aether.logger import get_logger

_log = get_logger("etrade_reauth")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Trigger the automated E*TRADE re-auth door.")
    parser.add_argument("--env", default="production", choices=["production", "sandbox"],
                        help="E*TRADE environment (default: production).")
    args = parser.parse_args(argv)

    _log.info("E*TRADE re-auth: invoking scheduled_reauth(env=%s)...", args.env)
    result = etrade.scheduled_reauth(args.env)
    # The full structured result (ok/reason/browser_opened/breaker_state) — no secrets in it.
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    _log.info("E*TRADE re-auth: reason=%s ok=%s browser_opened=%s",
              result.get("reason"), result.get("ok"), result.get("browser_opened"))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
