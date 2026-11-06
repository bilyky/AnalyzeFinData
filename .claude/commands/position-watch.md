# Position Watch: Daily Monitor + RETRO for a Concentrated Holding

A daily, **recommend-only** review of one concentrated holding (`--symbol`, default **INTC**):
how it's behaving, whether the move is its own or the sector's, whether a small trim into a
replacement makes sense today, and a RETRO of yesterday's call. A slow, adjustable process,
never a one-shot sale.

**Part of OceanView:** a sub-skill of `.claude/commands/oceanview-adviser.md`. It reads the
same OceanView context pack (Step 0 there) and follows the same guardrails. For INTC it
replaces OceanView's options menu, which Intel's policy rules out.

Engine: `scripts/monitoring/position_watch.py` (pure functions, unit-tested). It never places
an order and never opens a browser. Private inputs and outputs live in `Data/position_watch/`
(git-ignored): `config.json`, `<SYM>_lots.json`, `<SYM>_journal.jsonl`.

## Guardrails (read first, every time)

- **Recommend-only.** The user decides and places any trade.
- **Intel holders:** Intel's Insider Trading Policy bans employees from options, hedges
  (collars, swaps, exchange funds) and short sales on INTC. Never suggest them. **Use public
  information only.** If the user is aware of material non-public information, the answer
  is "don't trade today", whatever the numbers say.
- **The floor:** never suggest selling below `floor_pct` of the total (default 30%).
- **Private data:** account values, lots and the journal stay in this conversation and in
  `Data/`. Never paste them into a PR, issue, commit or anything public.
- **Data freshness:** say which bar the metrics use (`as_of_bar`) and where the price came
  from. If the OHLCV cache is more than 2 trading days old, say the trend numbers are stale.

## Step 0: Clock and context

1. Check the clock (ET) with a shell command. Don't assume the date.
2. The script reads the OceanView context pack offline for account values (see
   `.claude/commands/oceanview-adviser.md` Step 0 for what `health` allows). If `health` is `failed`, don't
   quote current account numbers.

## Step 1: Run the monitor

```bash
python scripts/monitoring/position_watch.py --symbol INTC --live
```

`--live` takes today's price from one read-only E*TRADE quote (needs today's token; without it
drop `--live` and the last cached close is used). Add `--json` for the full report.
`--no-journal` runs without recording the call (use it for a second look on the same day).

## Step 2: Read it back to the user

Lead with the **CALL** (TRIM / HOLD / WAIT) and its reasons, then:

| Section | What to explain |
|---|---|
| Trend | price vs SMA 20/50/200, drawdown from the 52-week high, 1/5/20/60-day returns |
| vs peers | beta and the **own move** (residual) over 1/5/20 days with its z-score. Say plainly whether today was "the sector" or "INTC itself" |
| Recovery | RBR tags. A `RBR↑` tag pauses trims (a recovery is forming) |
| Replacements | each one's stretch vs its 50-day average and RSI. "Stretched" = wait for a better entry |
| Concentration | % of the total and the dollars above the floor |
| Lots (on TRIM) | which lots, est. tax, and whether today is inside a wash-sale window |

## Step 3: RETRO, then record the lesson

1. Read the RETRO line: was the last call good (TRIM: did the symbol lag the replacement
   since? HOLD/WAIT: did it keep up?), and the running hit rate.
2. Write **one sentence** on what we learned: about the pattern, never blame. For example,
   "the drop was sector-wide (z −0.4), so holding was right", or "a TRIM on a −6% day would
   have sold the low". Save it:

```bash
python scripts/monitoring/position_watch.py --symbol INTC --lesson "<one sentence>"
```

3. Every ~20 calls, review the hit rate and the lessons with the user. Propose rule changes
   only with evidence, as a new `RULES_VERSION`, and say what changed.

## Step 4: Dated investment plan (when one exists)

If `Data/position_watch/invest_plan.json` exists (a short plan with dated buy steps, stops and a
sell window), run its daily check and read the TODAY lines back:

```bash
python scripts/monitoring/invest_plan.py
```

It says which step is due, "wait a day" after a market drop of 2%+, whether RSP is stretched,
each holding against its stop, and the days to the sell window. Before a winners step, check
each candidate's last 3 similar setups (CLAUDE.md). Record actual purchases in the plan's
`fills` (date, symbol, shares, price; a stop price for single stocks). Recommend-only.

## The rules (v2, heuristics, tuned by the RETRO)

- **Never act when not sure.** A TRIM becomes WAIT when any input is in doubt: the price
  history ends before the last completed session, a peer or replacement is missing or behind,
  the account snapshot is `failed`, or a requested live quote failed. The report lists them.
- **After a big drop, wait a day.** The crash-day check looks at the last *completed* session,
  so the day after a big drop is a WAIT too (the user's choice, 2026-11-03).
- **TRIM** a tranche (`tranche_pct` of the position, default 5%, never past the floor) only
  when **all** hold:
  - the symbol is below its 50-day average;
  - it's weak **on its own** (20-day residual z < −1);
  - no recovery pattern (`RBR`) is forming;
  - it isn't a crash day (worse than −5%: don't sell into it);
  - and a replacement's entry is OK.
- **WAIT** when the trim case holds but it's a crash day or every replacement is stretched.
- **HOLD** otherwise, including at the floor and while recovering.
- **Lots:** losses first, but only outside ±30 days of a vest or ESPP purchase (wash sale).
  Then long-term lots with the smallest gain per share. Short-term lots last.

## Private config

`Data/position_watch/config.json` and the lots file are set up once; the fields and how to keep
them current are in `docs/prod-notes/2026-11-03-prod-position-watch-229.md` (steps 2 and 4).
