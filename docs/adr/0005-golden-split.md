# 0005 — Split the daily-run golden test

**Status:** Accepted, 2026-11-05.

## Context
- **What the golden test records.** `tests/test_scenario_runner.py` with
  `tests/fixtures/scenario_runner_golden.json` pins 11 full daily-run cases. Each case records the
  saved state, transactions, decisions, trade DNA, emails, covered-call calls, the exception,
  **and every logger call in order**.
- **Why that is a problem.** Every code move or log fix breaks the golden test. It then has to be
  regenerated, which weakens it as a safety net.

## Decision
- **(a) A behaviour golden.** It keeps the same 11 cases and pins everything except the logger
  stream. Every structural change must leave it byte-identical.
- **(b) An audit-log test.** `tests/test_daily_run_audit_logs.py` holds about 15 explicit
  assertions (`assertLogs`) on the lines the project rules require:
  - each `🛑 AI BUY REJECTED` gate, with its values;
  - the stop-breach exit;
  - `QUEUED BUY SKIPPED`;
  - `Aborting AI Move`;
  - the no-prices `RuntimeError`;
  - the circuit-breaker tighten and restore.

  The message text may change; the meaning may not.
- **Retire the old mutation check.** `scripts/utils/mutate_scenario_steps.py` goes once
  `steps.py` is deleted, after its mutants are rewritten as unit tests on the new pure policies.
- **Measured gates, so no safety net is lost in transit.**
  - **When the golden is split (S1):** the behaviour golden plus the audit test must still catch
    the 56 mutants the full golden catches today. The #241 review measured that 8 of them are
    caught only through log lines.
  - **Before the stage tests and the mutation script are deleted (S9):** the new unit tests must
    catch all 155 mutants, or a mapped equivalent of each.
- **CI runs the tests.** It gains a job for the unit, contract and architecture suites. Today it
  runs none.

## Why
- **Behaviour stays pinned exactly, and the required audit lines stay checked.** Moving code or
  removing a duplicated log line no longer churns the golden.

## Rejected
- **Pinning every log line.** It is the strictest option, but it makes every structural PR
  regenerate the golden.
