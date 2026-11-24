# Circuit breaker: the one-day move against the right session; R&D #125 freeze study (2026-11-24)

## The fix
- `aether/circuit_breaker.check_systemic_risk` measured the one-day SPY move against the close **two sessions back**
  on a morning run, when the cached history ends yesterday. On 11-24 that would read +3.5% for an open 1.1% under
  Monday's close. A flat morning after a 2% drop could freeze buys by mistake; a real 2% drop after an up day could
  be missed.
- The measures are now one pure function, `breaker_measures(closes, live, last_is_today)`; the thresholds live in
  `breaker_reason`. A bar dated before today is an earlier session, so the live price is compared with it and that
  close stays in the 10-session window. An undated bar, or one dated today, is replaced by the live price as before.
- Nothing else changes: same thresholds (-2% day, -5% from the 10-session peak, VXX +15%), same opening window.

## R&D #125 (plan G1): INCONCLUSIVE, the freeze stays
66 episodes since 2000. SPY's 20-session excess after the breaker turned on: mean +0.01%, t 0.02. Details are in the
roadmap.

## PROD check
After the pull, on a morning when SPY opens near yesterday's close, the 07:00 log should show no
"Single-Day Capitulation" line. When the breaker fires, its reason should quote a one-day move that matches SPY
against the previous close.

Rollback: revert the PR.
