# Plan tool: long-term lots and win/loss odds (owner 2026-11-21)

## What changed in `scripts/monitoring/invest_plan.py`
- **Long-term lots.** A fill with `"hold": "long"` is money held into the next year. It counts in its symbol's average
  stop (the owner's rule: a new buy moves the average), but it never covers a dated step, and the sell window leaves
  it out. The sell-window line now reads "sell the plan's lots ... keep the N long-term lot(s)".
- **Win/loss odds.** For each index fund held, one note line with the base rate of today's setup from the fund's own
  history: past days in the same band of distance from the 50-day average (8%+ under, 4-8% under, 0-4% under, 0-4%
  over, 4%+ over), 20+ sessions apart, with the share up after 60 sessions, the median return, and how often -8% came
  before +8% within 60 sessions. It's descriptive, with n shown; it isn't a tested rule. `--json` has it under `odds`.

## How to record a long-term buy
Add the fill to the plan file as usual, with `"hold": "long"`:
`{"date": "2026-11-24", "symbol": "SPY", "shares": 2, "price": 690.0, "stop": null, "account": "....", "hold": "long"}`

## PROD check
After the pull, run `python scripts/monitoring/invest_plan.py`. It should show an odds line for SPY and for RSP.
With no long-term fills yet, every other line reads as before. The one difference is the wording of the sell window
(from 12-15).

Rollback: revert the PR.
