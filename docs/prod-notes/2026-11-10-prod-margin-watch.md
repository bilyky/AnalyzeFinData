# Margin watch in the Oracle (R&D #39): what you need to do

The Oracle's section of the daily email gets a **Margin watch** for every real account with a
margin loan (`accounts.real` in config), not only the Oracle's own account. For each one:

- debt, net value, equity as a share of holdings;
- the maintenance requirement and the excess, and how far the holdings can fall before a call
  (red: at or under the requirement; amber: within 10%);
- interest per month, if you set your rate;
- **pay down first**: the five sales that free the most requirement per dollar (100% names first),
  losses before gains. A loss on a symbol bought in the last 30 days (a vest or ESPP purchase, from
  position-watch's private config) is marked "wash window" and listed last: that sale would harvest
  nothing;
- an account with debt but no priced holdings says "holdings unpriced" instead of a requirement.

It uses the broker's live figures only. Debt, margin equity and any open call (house, Fed, minimum-equity, cash) come straight from E*TRADE's balance data (`marginBalance`, `regtEquity`, `OpenCalls`), and an account the broker reports without a margin loan is left out. On a day the broker can't be read it shows nothing. It
places no orders. The Oracle reads the accounts once per run and shares that read with its own
audit; when the read fails, it doesn't retry. The log gets a WARNING for an open call, for an
account at or under its requirement, and for debt with unpriced holdings.

## 1. Private files on PROD (never in git)

- **`Data\margin_requirements.json`**: each holding's maintenance requirement from E*TRADE's
  margin page, per account: `{"<last 4 digits>": {"SYMBOL": 0.30, ...}}`. Copy the one from dev.
  A holding that isn't in the file is assumed at 30%, and the email says how much of the
  requirement is assumed. Update the file when E*TRADE changes a rate or you buy a new name.
- **Optional, `oracle.margin_rate_pct`** in `config.json` (repo root; your margin rate, % a year, for
  example `10.8`). Without it the interest line is left out.
- **Already there if position-watch is set up:** `Data\position_watch\config.json` (its `symbol`
  and `acquisition_dates`) feeds the wash-window mark. Without it, nothing is marked.

## 2. Deploy

Merge, then pull. No restart: the email job imports it on its next run.

## 3. Check

The next daily email's Oracle section ends with "Margin watch". Compare the debt with E*TRADE's
"Margin Balance" for the account: they should match (they did when checked on dev).

## Roll back

Revert the PR. The private file can stay; nothing else reads it.
