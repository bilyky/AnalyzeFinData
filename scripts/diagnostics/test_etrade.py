"""Quick E*TRADE OAuth test — run interactively: python test_etrade.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import etrade

print("=== E*TRADE OAuth test ===")
# Default to production if specified, else sandbox
env = "production" if len(sys.argv) > 1 and sys.argv[1] == "production" else "sandbox"
print(f"Target Environment: {env}")

# Explicitly pass allow_browser=True to authorize browser re-authentication
tokens = etrade.get_tokens(env=env, allow_browser=True)
print("\nTokens received:", list(tokens.keys()))

print("\n--- Quote: AAPL ---")
market = etrade.get_market(tokens)
print(market.get_quote(["AAPL"], resp_format="json"))

print("\n--- Accounts ---")
accts = etrade.get_accounts(tokens)
print(accts.list_accounts(resp_format="json"))
