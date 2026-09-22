"""Quick E*TRADE OAuth test — run interactively: python test_etrade.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import console_safe
console_safe.install()


from aether import etrade

sys.stdout.write("=== E*TRADE OAuth test ===\n")
# Default to production if specified, else sandbox
env = "production" if len(sys.argv) > 1 and sys.argv[1] == "production" else "sandbox"
sys.stdout.write(f"Target Environment: {env}\n")

# Explicitly pass allow_browser=True to authorize browser re-authentication
tokens = etrade.get_tokens(env=env, allow_browser=True)
sys.stdout.write(f"\nTokens received: {list(tokens.keys())}\n")

sys.stdout.write("\n--- Quote: AAPL ---\n")
market = etrade.get_market(tokens)
sys.stdout.write(str(market.get_quote(["AAPL"], resp_format="json")) + "\n")

sys.stdout.write("\n--- Accounts ---\n")
accts = etrade.get_accounts(tokens)
sys.stdout.write(str(accts.list_accounts(resp_format="json")) + "\n")
