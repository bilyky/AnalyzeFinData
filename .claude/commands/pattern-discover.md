# Pattern Discovery — Missed Winners Analysis

You are AETHER's alpha research agent. When invoked, you run a historical replay to
find which stocks the system missed, WHY it missed them, and extract candidate new
patterns to improve the scoring model.

Run weekly on Saturday with a date ~3-4 weeks back so both 10-day (S10) and 60-day
(L60) forward windows have fully settled OHLCV data.

---

## Step 1 — Determine replay date

If the user provided a date (e.g. `/pattern-discover 2026-03-01`), use it.
Otherwise default to today minus 25 days (settled 10-day window).

OHLCV constraint: replay date must be ≤ 2026-04-04 for 15-day window,
≤ 2026-02-24 for 60-day window.

---

## Step 2 — Run the computation script

```bash
python scripts/backtesting/pattern_discovery.py --date {DATE} --top 10
```

Wait for completion. It will print a summary and write:
    Data/pattern_discovery_{DATE}.json

If it errors, read the traceback, diagnose, and fix before continuing.

---

## Step 3 — Load and parse results

Read `Data/pattern_discovery_{DATE}.json`. Focus on:
- `s10_analysis.missed` — stocks that moved strongly in 10 days but system rejected
- `l60_analysis.missed` — stocks that moved strongly in 60 days but system rejected
- `s10_analysis.false_positives` — stocks system would have BOUGHT but lost money (top losers)
- `l60_analysis.false_positives` — same for 60-day horizon
- `candidates_s10` / `candidates_l60` — missed-winner pattern candidates
- `fp_guards_s10` / `fp_guards_l60` — false-positive guard signal candidates
- `s10_analysis.universe_avg` / `l60_analysis.universe_avg` — baseline

---

## Step 4 — Reason about WHY each winner was missed

For each missed winner in both S10 and L60 lists, answer:

**1. Rejection cause**: Which filter triggered? (score < threshold, pgr not Bu/Bu+, setup=False?)

**2. Contrarian factor check**: Was any currently-penalized factor actually bullish here?
   - `lt_trend=Strong` is penalized -1.5 in S10 — but was this stock in an accelerating uptrend?
   - `industry_strength=Strong` is penalized -2.0 — but was the sector genuinely leading?
   - High `chart_score` or `momentum_score` penalized at -0.15 — but was momentum confirming?

**3. Context**: What was the market regime? What sector? Was there a macro catalyst
   (earnings, FDA, sector rotation) that the system has no signal for?

**4. Combo signals**: Were 2-3 weaker signals all pointing the same direction?
   E.g. buying_ratio=5.8 (below threshold) + money_flow=Strong + rsi_divergence>0

**5. Timing signal**: Was digit_sum firing? Was there a gap-up open?

---

## Step 5 — Name and formalize each candidate pattern

For each insight from Step 4, produce a structured entry:

```
PATTERN: [Short descriptive name]
TRIGGER: [Exact condition — e.g. lt_trend=Strong AND money_flow=Strong AND industry=Energy]
MISS REASON: [Which weight/threshold causes the system to skip it]
HYPOTHESIS: [Economic reasoning — why should this combination work?]
N ON THIS DATE: [How many stocks triggered this condition on the replay date]
AVG RETURN (triggered): [From candidates_* in the JSON]
LIFT vs BASELINE: [How much better than universe average]
CONFIDENCE: HIGH (N>=20, lift>3%, z>=2) / MEDIUM (N>=10) / LOW (N<10)
VALIDATION COMMAND: [python scripts/backtesting/pattern_discovery.py --date {DATE} --validate]
```

---

## Step 5b — Analyze false positives (bought but lost)

For the top losers in `false_positives`, answer:

**What warning sign was present but ignored?**
- Was money_flow=Weak but other signals overrode it?
- Was ob_os=Wait (overbought) — was the system buying into exhaustion?
- Was there a high chart_score/momentum_score (breakout-style entry into a reversal)?
- Was PGR=Bu but declining (pgr_delta negative)?

**Guard signal candidates** (from `fp_guards_s10` / `fp_guards_l60`):
- These are factor values that appeared in >=40% of false positives
- Present them as candidate rejection filters: "Add ob_os=Wait as a soft -1.0 penalty"
- Compare these to existing weights — if the factor is already penalized, was the
  penalty not strong enough?

---

## Step 6 — Separate S10 insights from L60 insights

S10 misses (10-day) → timing and entry quality patterns:
- Volume surge patterns
- OB/OS timing issues
- Contrarian candlestick/momentum weighting

L60 misses (60-day) → trend and position quality patterns:
- lt_trend contrarian weight is too aggressive
- Industry strength in sector momentum cycles
- Score threshold too high, excluding genuine recovery plays

Present them separately since they suggest different model changes.

---

## Step 7 — Prioritize and recommend

Rank candidate patterns by:
1. **Lift** (avg return of triggered signals vs universe average)
2. **N** (more observations = more reliable)
3. **Economic plausibility** (does the hypothesis make sense in market terms?)
4. **Recurrence** (flag if this pattern appeared in previous discovery runs)

Recommend the top 2 patterns for validation:
```
Recommended next: python scripts/backtesting/pattern_discovery.py \
    --date {DATE} --validate --date-range 2025-06-01:2026-02-28
```

---

## Step 8 — Output structured findings

Print in this format:

```
====================================================
PATTERN DISCOVERY: {DATE}
====================================================

SUMMARY
  Replay date: {DATE}  |  Universe: {N} symbols
  S10 top-10: {X} bought, {Y} missed, {Z} not in universe
  L60 top-10: {X} bought, {Y} missed, {Z} not in universe
  False positives: {A} S10 losers, {B} L60 losers among would-have-bought
  Universe avg 10d: {X}% | Universe avg 60d: {X}%

MISSED WINNERS — S10 (10-day)  [signals to ADD / raise weights]
  {symbol}: +{ret}% | Rejected: {reason} | Key anomaly: {factor}

MISSED WINNERS — L60 (60-day)
  {symbol}: +{ret}% | Rejected: {reason} | Key anomaly: {factor}

FALSE POSITIVES — S10 (bought but lost)  [signals to ADD as guards]
  {symbol}: {ret}% | Warning sign present: {factor}={value}

CANDIDATE PATTERNS — missed winners (ranked by lift)
  + {PatternName}
    Condition: {condition}
    N={n}, avg={avg}%, lift={lift}%, confidence={HIGH/MEDIUM/LOW}
    Hypothesis: {text}

CANDIDATE GUARD SIGNALS — false positives (ranked by fp_rate)
  - {factor}={value} appeared in {pct}% of false positives
    Universe avg when present: {avg}%
    Recommendation: {text}

RECOMMENDED NEXT STEPS
  [ ] Validate top missed-winner pattern: {command}
  [ ] Validate top guard signal: {command}
  [ ] If validated (|z|>=2.0, N>=30): add to Aug 22 R&D backlog
  [ ] Note recurrence: did any pattern appear in previous runs?
```

---

## Notes

- **Do not recommend weight changes to scoring.py** until a pattern is validated
  across multiple dates with |z|>=2.0 and N>=30
- **Flag overfitting risk**: a pattern found on a single date with N<10 is LOW
  confidence regardless of lift
- **Prioritize patterns that explain multiple missed winners** over one-off anomalies
- **The weekly cadence matters**: run on the same day each week and note whether
  the same patterns recur — recurrence across 3+ weeks = strong validation signal
