# Stop-check line and the 11-06 decision log (PROD review 11-20, items 2 and 3)

## What we found
**The decision log in the drops (item 3).** Every drop from 11-06 to 11-20 has the same `decision_log.jsonl`, byte for
byte. Twenty other files are also unchanged since the 11-06 drop, among them `pick_lists_log.jsonl`. PROD's logs show
the exit loop ran every trading day after 11-06, and there is no `[decision_eval] log write failed` warning. So PROD
most likely still writes the log, and the drops carry an old copy. This PR doesn't change how the log is written.

**SA sold a day late (item 2).** Yahoo 5-minute bars for 11-19:
- 06:45 PT: SA traded 21.5 to 22.4;
- 07:00 bar: open and high 20.75, low 20.12. The first trades under the 20.43 stop were in this bar.

The game's run priced at 07:00:04. At that time SA was most likely just above its stop, so the run held. SA fell
below the stop minutes later. The game checks stops once a day, so it sold at the next run (11-20, at 18.21).
The logs can't show which price the 07:00 run used, and that's what this PR adds.

Two things that looked like causes but weren't:
- the hourly "Current: $25.55" lines come from report jobs that read cached closes (25.55 is the 11-18 close);
- the monitor's 17.82 has no timestamp. SA didn't trade that low until after 07:30.

## What this PR changes
- The run log gets one line per held position at the exit check:
  `[Stop check] SA: price $20.75 (live quote), stop $20.43 -> HOLD`. It says `no quote, cost used` when there was no
  quote, and `gap guard: stop held` when the gap guard held the stop.
- Each decision record in `Data/decision_log.jsonl` also keeps `stop` and `time`.
- The E*TRADE price fetch logs `Prices: E*TRADE quotes for N/M symbols.`

No behaviour change: no buy, sell or stop rule moved.

## PROD steps
1. Pull `main`. No config change.
2. After the next trading-day 07:00 run, check `logs\aether.log`:
   - one `[Stop check]` line per held position;
   - one `Prices: E*TRADE quotes for N/M symbols.` line, with N close to M.
3. Copy `Data\decision_log.jsonl` and `Data\pick_lists_log.jsonl` fresh into the next drop and note their modified
   times. The newest rows should be from that day and carry `stop` and `time`.
4. Do steps 2 and 3 on two trading days in a row. If the drop still shows 11-06 rows while step 2 passes, the drop
   copy is at fault, not the code.

Rollback: revert the PR.
