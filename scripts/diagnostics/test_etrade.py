"""Quick E*TRADE OAuth test — run interactively: python test_etrade.py"""
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import etrade


# Default to production if specified, else sandbox
env = "production" if len(sys.argv) > 1 and sys.argv[1] == "production" else "sandbox"

# Explicitly pass allow_browser=True to authorize browser re-authentication
tokens = etrade.get_tokens(env=env, allow_browser=True)

market = etrade.get_market(tokens)

accts = etrade.get_accounts(tokens)
