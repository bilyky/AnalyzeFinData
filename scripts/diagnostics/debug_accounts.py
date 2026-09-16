import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import console_safe
console_safe.install()

from aether import etrade
import json

from aether_logger import get_logger as _get_logger

_log = _get_logger("debug_accounts")

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
            
            sys.stdout.write(f"\n--- Account {account_id} ---\n")
            bal_resp = accts_api.get_account_balance(account_key, resp_format="json")

            # Print only the keys and non-numeric configuration fields to avoid showing balances
            def print_structure(d, indent=0):
                for k, v in d.items():
                    if isinstance(v, dict):
                        sys.stdout.write("  " * indent + f"{k}:\n")
                        print_structure(v, indent + 1)
                    elif isinstance(v, list):
                        sys.stdout.write("  " * indent + f"{k}: [LIST]\n")
                    else:
                        # Show keys and "config" type values, mask numbers
                        if any(word in k.lower() for word in ["margin", "type", "level", "status", "mode", "pdt"]):
                            sys.stdout.write("  " * indent + f"{k}: {v}\n")
                        else:
                            sys.stdout.write("  " * indent + f"{k}: [VALUE]\n")

            print_structure(bal_resp)

    except Exception as e:
        _log.error(f"[debug_accounts] Error: {e}", exc_info=True)

if __name__ == "__main__":
    debug_account_details()
