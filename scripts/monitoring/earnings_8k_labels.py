"""Scheduled entry point for the earnings press-release labels (aether/earnings_8k): re-reads new SEC filings,
then labels up to --max reports with the configured AI tier. Task AETHER_Earnings8K."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from aether import earnings_8k  # noqa: E402

if __name__ == "__main__":
    sys.exit(earnings_8k.main(sys.argv[1:]))
