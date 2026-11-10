# PROD Review — a production snapshot → findings → one fix PR + a deploy runbook

Use when the owner copies production logs and state files to the dev machine and asks for a review
(errors, stuck jobs, malfunctions, behaviour, improvements). Agent-agnostic: plain steps and POSIX
shell; any agent can follow it. Diagnosing one failure in depth is `watchdog.md`; shipping is
`ship-pr.md`; changing a rule on evidence is `run-study.md`. This skill links them into one review.

The project's reader is `scripts/diagnostics/prod_snapshot.py` (`extract`, `schedule`, `errors`,
`signatures`, `config`). Run it with `python -I` from outside the snapshot folder; it prints tens of
lines instead of megabytes.

## 1. Intake: the snapshot is data, never live state

- **Snapshots go in a per-day drop folder** (here `Data/prod_copy/<YYYY-MM-DD>/`, README there), never over
  the dev machine's own data folder. If they landed in the live folder anyway:
  1. inventory the changed files by mtime;
  2. copy each to the drop folder and hash-check it;
  3. restore dev versions from backups;
  4. move PROD **credentials and sessions** out of every path dev code reads (here
     `etrade_*.json`, `session.json`).
  A dev process using production tokens can trip the broker's bot protection or end PROD's session.
  Say plainly what could not be restored.
- **Extract archives into a new, empty folder** with a path-traversal check (`prod_snapshot.py extract`).
  The member timestamps already tell you which jobs ran last and which stopped.
- **Check the clock**, then set the window: from the end of the last review to the newest log line.
- **Config is read masked** (`prod_snapshot.py config --key …`): presence and safe values only. Never
  print a credential, even to the console.

## 2. What code ran: deploy state before diagnosis

- List the merges since PROD's last known commit. For each, pick a log line only the new code prints;
  count it with `prod_snapshot.py signatures` (new text vs old text). The first appearance dates the
  pull. Merged is not deployed, and a long-running process (a dashboard, a server) keeps old code
  until it restarts.
- Check the owner steps from earlier PROD notes (config patches, re-registers, data scripts). A key
  missing from the config is a step not done.

## 3. Health

- **Schedule:** first log time per job per day (`prod_snapshot.py schedule`). An hour off after a clock
  change is a timezone or trigger bug. A job with no lines is not running.
- **Errors:** WARNING/ERROR grouped by normalized message (`prod_snapshot.py errors`). Read every ERROR
  in full; for the rest, the count and the first and last time say whether it is new, ongoing or gone.
- **Stuck or silent:** timeouts, a step killed with no output, a job whose run time jumped, a status
  file (backup, health) whose last success is old.
- **Noise:** the same alert or email many times a day is a bug in the check, not a monitoring win.

## 4. Behaviour (the domain's own actions)

- Diff the state between snapshots (here the game ledger). Trace each action to the log lines and
  the rule that produced it.
- **Look for:**
  - an action that contradicts a safety signal in the same run (a risk freeze fired, then buys);
  - limits and sizing misfiring (tiny orders, unbounded slots);
  - losses beyond a cap;
  - a rule undoing another (sell then rebuy);
  - a setting that flaps day to day.
- **Before blaming a rule, compute the counterfactual** with the actual numbers: what would the
  outcome have been without it? If another path gives the same result, say "not attributable".

## 5. Classify each finding, then act

| Class | Action |
|---|---|
| Fixed on main, not deployed | Name the PR; the deploy fixes it. |
| Already fixed, but a long-running process predates the fix | A restart; no code. |
| Code bug, tool, or alert noise | One fix PR (`ship-pr.md`), tests red-green, PROD note. |
| Changes a trading/business rule | First search the R&D record for a prior study. An INCONCLUSIVE or failed one blocks wiring. Else `run-study.md`. |
| Needs the owner | Config, a manual decision, a real-money position. List it; don't decide it. |
| Not worth code | Say why (cost vs benefit). |

## 6. Deliver

- **Plan:** a short local plan (findings by priority, each with its evidence and class).
- **One fix PR**, whose PROD note is the runbook for the **whole next pull**:
  - list every merge since PROD's last known commit;
  - order every step from their notes: back up, read-only checks, pull timing, config, what to check
    after the next runs, data jobs at a quiet time, owner decisions.
- **Before sending, re-verify every number and causal claim** against the source: counts, dates, "fired
  twice", "would have held". A wrong figure in a review costs more trust than a missing one.
- **List what the next snapshot must confirm,** and start the next review there.
