import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import etrade
import json

def check_accounts():
    try:
        env = "production"
        tokens = etrade.get_tokens(env)
        if not tokens:
            print("Failed to get tokens.")
            return

        accts_api = etrade.get_accounts(tokens, env)
        resp = accts_api.list_accounts(resp_format="json")
        
        accounts = resp.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])
        if isinstance(accounts, dict):
            accounts = [accounts]

        print(f"\n{'Account ID':<15} {'Type':<15} {'Margin Status':<15} {'Affected?'}")
        print("-" * 60)

        for acct in accounts:
            account_id = acct.get("accountId", "N/A")
            account_key = acct.get("accountIdKey", "")
            account_type = acct.get("accountType", "N/A")
            
            # Fetch balance to check margin status
            bal_resp = accts_api.get_account_balance(account_key, resp_format="json")
            bal_data = bal_resp.get("BalanceResponse", {})
            
            # Margin accounts are affected by PDT and 4210
            # We look for marginLevel or accountType containing 'MARGIN'
            is_margin = "MARGIN" in account_type.upper() or bal_data.get("marginLevel", "") != ""
            affected = "YES (PDT Removed)" if is_margin else "NO (Cash Account)"
            
            print(f"{account_id:<15} {account_type:<15} {'Margin' if is_margin else 'Cash':<15} {affected}")

    except Exception as e:
        print(f"Error checking accounts: {e}")

if __name__ == "__main__":
    check_accounts()
