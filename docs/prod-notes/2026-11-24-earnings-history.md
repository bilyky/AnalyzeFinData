# Earnings history from SEC filings (R&D #98 data step)

New module `aether/earnings_history.py`. It changes no live behaviour: nothing reads the file yet. The studies that will use it come in their own PRs.

## Build on PROD (optional now, needed before the earnings studies run there)

`python -m aether.earnings_history`

- It takes about 12 minutes through the EDGAR cache (`Data/edgar_cache`) and writes `Data/earnings_history.json`.
- SEC requires a contact email in the User-Agent. The client uses `AETHER_SEC_CONTACT` or else config's `email_sender.address`. Without one, SEC answers 403.
- The command exits 1 and prints `"complete": false` when SEC blocked the run, served an expired cache, or an older filings page was missing. Re-run later when that happens.

## Evidence

`python -m aether.earnings_history --status` shows `"complete": true` with about 450 symbols and 34,000+ events, the earliest in 2004.
