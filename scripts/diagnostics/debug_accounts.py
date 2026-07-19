import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import etrade
import json

def debug_account_details():
    try:
        env = "production"
        tokens = etrade.get_tokens(env)
        if not tokens:
            return

        accts_api = etrade.get_accounts(tokens, env)
        resp = accts_api.list_accounts(resp_format="json")
        
        accounts = resp.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])
        if isinstance(accounts, dict):
            accounts = [accounts]

        for acct in accounts:
            account_id = acct.get("accountId", "N/A")
            account_key = acct.get("accountIdKey", "")
            
            print(f"\n--- Account {account_id} ---")
            bal_resp = accts_api.get_account_balance(account_key, resp_format="json")
            
            # Print only the keys and non-numeric configuration fields to avoid showing balances
            def print_structure(d, indent=0):
                for k, v in d.items():
                    if isinstance(v, dict):
                        print("  " * indent + f"{k}:")
                        print_structure(v, indent + 1)
                    elif isinstance(v, list):
                        print("  " * indent + f"{k}: [LIST]")
                    else:
                        # Show keys and "config" type values, mask numbers
                        if any(word in k.lower() for word in ["margin", "type", "level", "status", "mode", "pdt"]):
                            print("  " * indent + f"{k}: {v}")
                        else:
                            print("  " * indent + f"{k}: [VALUE]")

            print_structure(bal_resp)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_account_details()
