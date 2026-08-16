import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


import etrade


def check_accounts():
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
            acct.get("accountId", "N/A")
            account_key = acct.get("accountIdKey", "")
            account_type = acct.get("accountType", "N/A")
            
            # Fetch balance to check margin status
            bal_resp = accts_api.get_account_balance(account_key, resp_format="json")
            bal_data = bal_resp.get("BalanceResponse", {})
            
            # Margin accounts are affected by PDT and 4210
            # We look for marginLevel or accountType containing 'MARGIN'
            "MARGIN" in account_type.upper() or bal_data.get("marginLevel", "") != ""
            

    except Exception:
        pass

if __name__ == "__main__":
    check_accounts()
