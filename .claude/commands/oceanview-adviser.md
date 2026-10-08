# OceanView Options + CPA/Tax Adviser

The **OceanView Agent** — a symbol-agnostic consumer of AETHER's options engine. Build a
menu of protection / income option strategies for **any** stock position (`--symbol <sym>`;
**default INTC**) — collar, protective put, covered call, cash-secured put — each with its
economics *and* its U.S.-tax considerations (LTCG/STCG holding period, IRC §1259
constructive sale, §1092 qualified covered call, §1091 wash sale).

Engine: `aether/options_adviser.py` (pure, unit-tested, interface-agnostic). CLI:
`options_adviser.py`. INTC is only the default argument, not the agent's identity — point it
at any holding. This skill runs the **offline** path and reports the menu; it recommends
only — it never places an order.

## Step 0 — Boot from the OceanView Context Pack

Before advising, load the **Context Pack** (`aether/oceanview_context.py`): one manifest of
account state, the paper-game summary, study gates, data health and guardrails, with a health
verdict. The evening pipeline (`daily_task`) rebuilds it every day; read it **offline**
(`live=False` never touches the broker and writes nothing: the pipeline's verdict file, which the
watchdog gate reads, is only written by the scheduled build):

```bash
python -c "from aether.oceanview_context import build_oceanview_context as b; import json; p=b(live=False); print(json.dumps({'meta': p['meta'], 'data_health': p['state']['data_health'], 'portfolio': p['state']['portfolio']}, indent=1, default=str))"
```

Let the verdict (`meta.health`) decide what you may say:

| health | meaning | what you may do |
|---|---|---|
| `ok` | live broker snapshot, data healthy | use the numbers; still cite `meta.broker_as_of` |
| `degraded` | snapshot from cache (≤ 24 h, 72 h on weekends) and/or data-health warnings | every account/position number carries its as-of stamp (`broker_as_of`, `staleness_hours`) |
| `failed` | no snapshot, or a stale one | **don't quote current account or position numbers.** Strategy math with explicit hypothetical inputs (`--spot/--qty/--cost …`) is still fine; say the pack is failed and why (`meta.warnings`) |

- **Data health of the symbol you're advising on:** if it's in `data_health.placeholder_heavy`
  or `data_health.no_ohlcv`, its AETHER ATR stop is unreliable or missing (8% fallback). Don't
  anchor the protective-put strike to that stop without saying so; pass `--stop` explicitly or
  flag it.
- **Guardrails** (`pack["guardrails"]`) travel with the pack: recommend-only (a human
  executes; there is no order path), ban-safe (no browser), backup-before-write.
- **Private data:** the full pack (`state.accounts`, `state.sleeves`) holds real account
  digits and balances. Keep them in the private conversation. Never paste them into a PR,
  issue, commit or anything public. The command above prints only meta, data health and the
  paper-game summary.

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

Start the summary with the pack's verdict, e.g. "Context pack: **degraded**, broker snapshot
as of 2026-10-08 17:31 PT (cache, 15 h old)". If it's **failed**, say so and keep to the
hypothetical inputs.

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
