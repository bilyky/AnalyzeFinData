# Run a Study — backtest-first evidence before changing a rule

Use this before wiring, loosening or removing any trading rule, gate or factor, and when asked "is X
helping?". Agent-agnostic: any agent can follow it. Output: a reproducible script, a verdict under a
rule fixed in advance, and the result recorded where the project tracks R&D (here `plans/roadmap.md`)
— nulls included.

## 1. Frame it before touching data

- **The question in one line**, tied to the symptom that raised it (the log line, the ledger rows).
- **Groups and outcome:** what is compared (e.g. gate passes vs the cohort it excludes), over which
  horizon, on which universe. Mirror the production rule *exactly* — call the code's own function for
  the rule; never re-implement it in the study.
- **Decision rule, written down first:** the statistic, the threshold, and what each verdict does
  ("TOO_STRICT → consider relaxing; INCONCLUSIVE → keep"). Put it in the script's docstring.

## 2. Build it so it can't fool you

- **No look-ahead:** inputs use only data available at decision time; outcomes start after it.
- **Count independent evidence, not rows.** Observations on the same date share one market move —
  compare groups *within* each date and test across dates. Overlapping horizons (10-day returns on
  consecutive days) are serially correlated: use a HAC / Newey-West t with lag = (horizon in sampling
  periods) − 1 — daily samples of a 10-day return → lag 9; month-end samples of a 20-day return → lag 0
  — and report the plain t only as context.
- **A small, real sample is still a sample.** One portfolio's ledger shows direction and magnitude,
  not significance — say so.
- **Reuse the project's data loaders and adjustments** (e.g. split-adjusted prices); ledger prices
  are nominal at trade time, so rescale before comparing.
- **Parallelize across independent entities** (symbols) when the replay is slow; confirm the parallel
  run reproduces the serial numbers on a small slice.

## 3. Test the study itself

- Unit-test the grouping, the statistic and the **verdict function** with fixtures where the
  candidate statistics disagree. Mutate the verdict (decide on the wrong statistic, swap a branch) and
  confirm a test fails.
- Reconcile totals against the source (sum of per-trip P&L = ledger P&L; open trips = open positions).

## 4. Run, then report honestly

- Print n, the effect size, and every statistic you computed; write the full report to a JSON file
  with the data range and run date.
- **If you change the decision statistic after seeing a result, disclose it** — state the verdict
  under the original rule and why the new one is better — then fix the rule for every future run.
- State what the study does **not** measure (e.g. stop-outs, drawdowns, costs) and what would
  re-open it.
- **Never wire on INCONCLUSIVE**, and treat a pass as a reason to propose a change, not to make it:
  the live change is its own PR, gated on the risk rules.

## 5. Record it

Add or update the item in the project's single R&D list (status, n, effect, statistic, verdict,
script path, caveats), and ship script + tests + note as one PR (see `ship-pr`).
