"""Quick E*TRADE OAuth test — run interactively: python test_etrade.py"""
import etrade

print("=== E*TRADE sandbox OAuth test ===")
tokens = etrade.get_tokens(env="sandbox")
print("\nTokens received:", list(tokens.keys()))

print("\n--- Quote: AAPL ---")
market = etrade.get_market(tokens)
print(market.get_quote(["AAPL"], resp_format="json"))

print("\n--- Accounts ---")
accts = etrade.get_accounts(tokens)
print(accts.list_accounts(resp_format="json"))
