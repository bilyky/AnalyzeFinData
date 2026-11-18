# PROD review 2026-11-18: what happened on 11-17, and the next pull

Snapshot: logs up to 2026-11-18 07:15 PT, state files copied 08:25.

## E*TRADE outage on 11-17 (resolved)
- 08:27:13 dev logged in with PROD's key (pid 15580). At 08:45:05 PROD's token was rejected at its next use.
- PROD's automated logins at 08:45, 09:15 and 09:45 each logged `Security-code field not shown — submitting without
  a code` and failed. That hit the 3-strike hard block, and automation stopped until a person logged in. The same
  failure shape appeared once before, on Sun 11-15 07:01. Every successful login in these logs goes straight to
  `Verifier captured`. Why E*TRADE showed no code field isn't known yet; #287 logs the reason of the next failure.
- 18:56:43 the owner's manual login worked, but in a window without `AETHER_ETRADE_STORE=db`: the token went to the
  file only (no "shared store" line), and the shared failure count stayed at 3. PROD's 19:00 job copied the token to
  the shared store. The shared breaker was reset from dev at 19:11; the 21:00 automated login then worked and saved
  the 11-18 token to the shared store.
- Dev can't log in by itself any more: its login entries were removed from dev's `config.json` on 11-17, and dev
  uses the shared store.

## Deployed since the last review
**#274** (scheduled jobs without the Gemini CLI). Dashboard restart 18:41. The catch-up `rd_pattern_discovery.py
--date 2026-10-22` ran 18:45-18:55:
- the live `failure_dna_rules.json` is unchanged, and `failure_dna_rules_proposed.json` was written;
- one email went out;
- the AI step was answered by gemini_lite, after Groq refused the prompt (`413 Payload Too Large`) and gemini_oai
  timed out after 180 s.

Not yet confirmed: the task re-register. AETHER_StatusReport first runs today at 14:00, and AETHER_PatternDiscovery
on Sat 11-21 at 10:00.

## What this PR fixes
1. **Replay log flood:** the replay read each old snapshot with the schema check on. 10-22 is inside the
   09-01..10-30 factor gap, so it logged 1,012 `SCHEMA WARNING … no PGR factors` lines. It now reads them with
   `check_schema=False`, like the other historical reads.
2. **Smaller weekly AI prompt:** the `winners` list that repeats `missed` and `bought` is dropped. The 10-22
   report goes from 16,985 to 9,532 characters, and the prompt from 23,097 to 15,644. Whether Groq now accepts it
   isn't verified; the provider chain still falls back as before.
3. **`test_etrade.py` prints the token store** it uses, and warns when it isn't the shared one while a
   `database.url` is set.

## Next pull: steps
1. Merge **#277**, **#278**, **#287** and this PR, in that order.
2. Pull when no job is running. These are all code-only changes: no config, and no re-register beyond #274's.
3. #278's note (`2026-11-17-prod-review.md`) lists the owner steps: the Short_Long duplicate row, the stale
   hand-set stops, and the Chaikin renewal by **Sat 11-21 08:57 PT**.
4. If the task re-register for #274 wasn't run, run it now (dry run first), as in `2026-11-16-prod-no-gemini-cli.md`.
5. Run any manual E*TRADE command on PROD in a **new** PowerShell window. `test_etrade.py` now says `Token store: db`.

## After the pull, check
- The next failed automated E*TRADE login logs `re-auth attempt failed: …` with the reason (#287).
- Sat 11-21: `pattern_discovery_task.log` has no flood of SCHEMA WARNING lines, and the AI step is answered.
- The stop-monitor emails drop to new breaches and real new lows (#278).

## Seen, no action
- 11-18 07:00: MSCI hit its stop (636.79; filled 631.90, +$99). The game then bought 17 SA at 26.03, stop 20.43.
  - SA passed through two waivers (R&D #13 for its Bearish PGR, R&D #32 for R:R 0.34 and a 2.8% target).
  - The 1% risk cap cut the order from 57 to 17 shares, so its stop-out loss is about $100.
  - The waiver question is R&D #75 (INCONCLUSIVE, keep).
- Game: 12 positions, cash 20.3%, open risk to stops about 7.9% of equity, AGGRESSIVE profile.
- The weekly replay (today − 25 days) can't have 60-day returns, so its L60 analysis is always empty ("0 with 60d").
  A second replay about 90 days back would cover L60; that is a design choice, not done here.
- gemini_oai returned 503 to the exit evaluator at 07:00 on 11-17 and 11-18; Groq answered.
- Chaikin returned non-JSON for MCK twice (05:42 and 05:46). 14 of 553 OHLCV files are older than 10 days (oldest
  378); the log doesn't name them. Their stops fall back to 8% off the live price.
