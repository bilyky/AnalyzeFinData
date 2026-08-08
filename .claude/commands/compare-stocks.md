# Compare Stocks — Side-by-Side + Ranked WHY

Compare several stocks and produce ONE decision-ready summary: an objective side-by-side of
their factors, a ranked recommendation, and an explicit WHY for each — grounding the numbers
against real-world news/events. Any agent can run this; it is surface-agnostic.

Symbols come from the invocation arguments (e.g. `/compare-stocks TG CC DAVE IBM`): `$ARGUMENTS`.
If none are given, ask the user which symbols to compare, then proceed.

## Step 1 — Get the structured data (single source of truth)

Run the deterministic engine and capture its JSON (logs go to stderr; `2>/dev/null` keeps stdout clean):
```bash
python scripts/analysis/compare_stocks.py $ARGUMENTS --json 2>/dev/null
```
This returns `{as_of, symbols, rows[], ranking[], meta{...}}`. Every number you report MUST come
from this payload — do not recompute, estimate, or invent factor values. (Equivalent HTTP form:
`POST /api/compare {"symbols":[...]}` — same engine.)

Note `meta.missing` (symbols not on the Research sheet — report them as "not covered", never guess
their numbers) and `meta.stale_warning` (see Step 4).

## Step 2 — Gather the qualitative layer (news / events)

For each found symbol, run a quick web search for the CURRENT year:
- Query: `"{SYMBOL}" stock news {YEAR}` and, if useful, `"{SYMBOL}" earnings analyst {YEAR}`.
- Look for: earnings dates/results, guidance changes, analyst upgrades/downgrades, sector or macro
  catalysts. Keep each to 1–2 sentences. If nothing material turns up, record "no material news found".
- Also fold in any project external-intel already surfaced (idea emails / intel block) if present.

Never fabricate a catalyst. Attribute each qualitative claim to its source/date.

## Step 3 — Write the comparison

Apply the framework in `prompts/compare_stocks.md` (the shared rubric) to the Step-1 data + Step-2
news. Produce, in order:

1. **SIDE-BY-SIDE** — a table: Symbol, Combined, S10, L60, PGR, Money Flow, LT Trend, Setup,
   Stop (source), Target (source), R:R, Patterns.
2. **RANKING** — restate the engine's `ranking` order and name the top pick. If your qualitative
   read would reorder it, say so and justify — never silently contradict the numbers.
3. **WHY** — one short paragraph per symbol citing its ACTUAL factors (e.g. "ranks first on a +11.9
   combined with Strong money flow and a confirmed Cup&Handle") reconciled with its news; end each
   with that symbol's single biggest risk.
4. **SUMMARY** — 3–5 bullets: pick, runner-up, who to avoid and why, key caveat.

## Step 4 — Rules

- If `meta.stale_warning` is set, state up front that stop/target levels and R:R are approximate
  (stale price cache) and lean on momentum/scores over exact levels.
- Honor the Rule of Loss Minimization: call out weak/absent stops and poor R:R; capital
  preservation outranks upside.
- No preamble, no boilerplate disclaimers. Finish with the `as_of` date from the payload.
