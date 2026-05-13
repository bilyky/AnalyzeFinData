import json
import os
import time
import pyetrade

_DIR = os.path.dirname(__file__)
_CONFIG_PATH = os.path.join(_DIR, "etrade_config.json")
_TOKEN_PATH  = os.path.join(_DIR, "Data", "etrade_tokens.json")


# ---------------------------------------------------------------------------
# Config / token helpers
# ---------------------------------------------------------------------------

def _load_config(env="sandbox"):
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    ck = cfg[env]["consumer_key"]
    cs = cfg[env]["consumer_secret"]
    username = cfg.get("username", "")
    password = cfg.get("password", "")
    proxy = cfg.get("proxy")
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"]  = proxy
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY",  None)
    return ck, cs, username, password


def _save_tokens(tokens, env):
    tokens["env"] = env
    tokens["saved_at"] = time.time()
    os.makedirs(os.path.dirname(_TOKEN_PATH), exist_ok=True)
    with open(_TOKEN_PATH, "w") as f:
        json.dump(tokens, f, indent=2)


def _load_tokens(env):
    """Return cached tokens if they exist and are less than 90 minutes old."""
    if not os.path.exists(_TOKEN_PATH):
        return None
    with open(_TOKEN_PATH) as f:
        tokens = json.load(f)
    if tokens.get("env") != env:
        return None
    age_minutes = (time.time() - tokens.get("saved_at", 0)) / 60
    if age_minutes > 90:
        print(f"Cached tokens expired ({age_minutes:.0f} min old), re-authenticating...")
        return None
    print(f"Using cached tokens ({age_minutes:.0f} min old).")
    return tokens


# ---------------------------------------------------------------------------
# OAuth — automated via Playwright
# ---------------------------------------------------------------------------

def _get_tokens_via_playwright(auth_url, username, password):
    """Open the E*TRADE auth URL, log in, accept, and return the verifier code."""
    from playwright.sync_api import sync_playwright

    verifier = [None]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        page = browser.new_page()

        print(f"Opening E*TRADE authorization page...")
        page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)

        # --- Login form ---
        page.wait_for_selector("input#USER", timeout=15000)
        page.fill("input#USER", username)
        page.fill("input#PASSWORD", password)
        page.click("input#logon_button")

        # --- Accept/Authorize page ---
        page.wait_for_selector("input[value='Accept']", timeout=15000)
        page.click("input[value='Accept']")

        # --- Verification code page ---
        # E*TRADE shows the code in a <div> or <input> after accepting
        page.wait_for_load_state("domcontentloaded", timeout=15000)

        # Try to find the verifier code — E*TRADE renders it as plain text
        # in a div with id="oauth_pin" or similar, or in the page body
        for selector in ["div#oauth_pin", "input#oauth_pin", "div.oauth-pin", "span.verifier"]:
            el = page.query_selector(selector)
            if el:
                verifier[0] = el.inner_text().strip()
                break

        if not verifier[0]:
            # Fallback: grab it from the URL query param if redirected
            url = page.url
            if "oauth_verifier=" in url:
                verifier[0] = url.split("oauth_verifier=")[1].split("&")[0]

        if not verifier[0]:
            # Last resort: print the page text so we can see what E*TRADE returned
            print("Could not auto-extract verifier. Page content:")
            print(page.inner_text("body")[:500])
            verifier[0] = input("Enter the verification code manually: ").strip()

        browser.close()

    return verifier[0]


def get_tokens(env="sandbox"):
    """Get OAuth tokens — uses cache if fresh, otherwise runs Playwright login."""
    cached = _load_tokens(env)
    if cached:
        return cached

    ck, cs, username, password = _load_config(env)
    oauth = pyetrade.ETradeOAuth(ck, cs)
    auth_url = oauth.get_request_token()
    print(f"Auth URL: {auth_url}")

    verifier_code = _get_tokens_via_playwright(auth_url, username, password)
    print(f"Verifier code: {verifier_code}")

    tokens = oauth.get_access_token(verifier_code)
    _save_tokens(tokens, env)
    print("Tokens saved to cache.")
    return tokens


# ---------------------------------------------------------------------------
# API object factories
# ---------------------------------------------------------------------------

def get_market(tokens, env="sandbox"):
    ck, cs, _, _ = _load_config(env)
    return pyetrade.ETradeMarket(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"], dev=(env == "sandbox"))


def get_accounts(tokens, env="sandbox"):
    ck, cs, _, _ = _load_config(env)
    return pyetrade.ETradeAccounts(ck, cs, tokens["oauth_token"], tokens["oauth_token_secret"], dev=(env == "sandbox"))


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tokens = get_tokens()

    print("\n--- Quote: AAPL ---")
    market = get_market(tokens)
    print(market.get_quote(["AAPL"], resp_format="json"))

    print("\n--- Accounts ---")
    accts = get_accounts(tokens)
    print(accts.list_accounts(resp_format="json"))
