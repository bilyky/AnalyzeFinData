# INTC Options + CPA/Tax Adviser

Build a menu of protection / income option strategies for a stock position (default
**INTC**) — collar, protective put, covered call, cash-secured put — each with its
economics *and* its U.S.-tax considerations (LTCG/STCG holding period, IRC §1259
constructive sale, §1092 qualified covered call, §1091 wash sale).

Engine: `aether/options_adviser.py` (pure, unit-tested). CLI: `options_adviser.py`.
This skill runs the **offline** path and reports the menu; it recommends only — it
never places an order.

## Step 1 — Run the adviser (offline)

Pull the position + AETHER stop/target automatically from the workbook when present.
Add `--print --no-email` to inspect in the terminal without sending mail:

```bash
python options_adviser.py --symbol INTC --print --no-email
```

If there is no live workbook/position (e.g. a fresh worktree), or you want to model a
hypothetical, pass explicit inputs — the flags override sourced values:

```bash
python options_adviser.py --symbol INTC --print --no-email \
  --spot 109 --stop 97 --target 115 --cost 80 --qty 200 --acquired 2025-09-15
```

- `--stop` → protective-put strike anchor · `--target` → covered-call strike anchor
- `--qty` / `--cost` / `--acquired` → position size, basis, lot date (drives the tax flags)
- Omit `--no-email` to deliver the HTML report to the configured recipient (the default).

## Step 2 — Read back the menu

The table lists, per strategy: legs, net debit/credit, max loss, max gain, protection
floor, and upside cap. Below it, each strategy's notes, breakeven(s), and **tax
considerations**.

## Step 3 — Report

Summarize for the user:

| Strategy | Net | Downside floor | Upside cap | Key tax flag |
|---|---|---|---|---|
| Collar | debit/credit | $ | $ | §1259 / holding-period |
| Protective put | debit | $ | — | holding-period |
| Covered call | credit | — | $ | §1092 QCC |
| Cash-secured put | credit | — | — | §1091 wash sale |

Then state a recommendation grounded in the numbers — e.g. "for an appreciated lot
**near the 1-year mark**, a wide collar caps risk cheaply but any forced assignment
still realizes STCG; a protective put keeps the clock running to long-term." Always
pass through the tax **disclaimer** — these are considerations, not advice; confirm
with a CPA.

## Live validation (only when explicitly asked)

`--live` performs a single ban-safe `options_chain` fetch through the shared E*TRADE
token/breaker (no browser). It also validates the chain shape: if the live payload
differs from `normalize_chain`'s assumptions the tool prints a diagnostic instead of
guessing. Do **not** run `--live` as part of a routine offline report.

By default the live fetch auto-selects an expiry near **~35 DTE** (nearest listed
expiry ≥ 30 days out) so the menu is actionable — the broker's default front month is
often 1-DTE, which collapses the collar's put and call onto one strike. Override with:

- `--expiry YYYY-MM-DD` — pin an exact expiry (skips auto-selection)
- `--dte N` — target a different days-to-expiry window (e.g. `--dte 60`)

Both are **live-only**; the offline path always uses the fixture's expiry.

```bash
python options_adviser.py --symbol INTC --live --print --dte 45
```
