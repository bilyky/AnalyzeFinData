# PROD review 2026-11-22: runbook for the next pull

Snapshot: logs to 11-22 10:07 PT. The weekend had no trade runs; the last one was Fri 11-20 07:00.

## What the pull brings
- **#281:** reference ETFs kept current, with an email alarm when one is behind. It never freezes buys. Merged 11-22, after these logs.
- **This PR:**
  - The intraday monitor skips weekends and holidays, and re-alerts an already-reported breach only after a further 3% drop.
  - The earnings calendar names a missing E\*TRADE session instead of a `TypeError`.
  - Pattern discovery's Phase 1 logs progress and any symbol slower than 10 s.
  - The E\*TRADE TOTP login logs what page it saw (no field values) when the security-code field or the submit button is missing.
- **Already live (most likely pulled at the 11-21 15:01 dashboard restart):** #299 (the calendar was built 11-21 17:12), and #311 / #302 / #291 / #312 by merge time. #311's `[Stop check]` line can only show on a trade day.

## Steps
1. **Before Monday 05:30 PT, and after 21:00 PT Sunday** (the 11-22 token expires at midnight ET):
   - start an E\*TRADE re-auth from the dashboard's System page and check that it reports ok;
   - the 07:00 automatic attempt failed on 11-21 and 11-22, while a dashboard re-auth at 08:57 on 11-22 worked.
2. **Pull at a quiet time** (no job running). No config change and no task re-registration.
   - Restart the dashboard so its re-auth button uses the new login logging (optional).
3. **After tonight's 22:10 backup:** check that `Data/backup_status.json` shows `ok`. It failed on 11-20 and 11-21 (NAS offline); the NAS answered again from about 03:00 on 11-22.
4. **Monday after 07:00:**
   - `[Stop check]` lines appear in `aether.log`;
   - on the next evening run after the pull, the reference ETFs are fetched (`[RapidAPI]` lines for QQQ, SMH, SOXX, VXX, UVXY).

## Owner items (no code)
- **Research sheet:** 18 symbols return no Chaikin market data (EA, CTRA, WBD, IRBT, MAKX, NDX, QRVO, PCH, HOLX, ROKU, AREN, RYI, ASRT, MRC, IPG, IAC, MGPI, K). Remove the ones that are delisted or renamed.
- **Drop copies:** `decision_log.jsonl` (last row 11-06), `state_of_the_day.xlsx`, `trade_history_dna.json`, `autonomous_run.log` and `aether.zip` were byte-identical to the 11-20 drop. Check their modification times on PROD.
- **Short_Long:** TG is still listed twice.
- **Chaikin:** the token expires Fri 11-27 14:26 PT; renew by Thu 11-26.

Rollback: revert this PR's commit.
