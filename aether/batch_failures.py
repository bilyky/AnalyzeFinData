"""When do a batch's per-item failures need a person?

A batch that fetches hundreds of symbols (or reads a handful of emails) always loses a few to a
timeout or a rate-limit guard, and the next run retries them. Logging each one at ERROR sent the
watchdog's recovery email for every such blip (PROD 2026-11-02: two of ~400 RapidAPI fetches).
So each item's failure is a WARNING, and one summary line per batch is an ERROR only when most
of the batch failed: that is an outage (10-22: DNS down, every Chaikin fetch failed), not a blip.
"""

# Below this many attempts a failure share says little.
MIN_ATTEMPTS = 4


def most_failed(failed: int, attempted: int) -> bool:
    """True when more than half of at least MIN_ATTEMPTS attempts failed."""
    return attempted >= MIN_ATTEMPTS and failed * 2 > attempted


def log_batch_failures(log, label: str, failed: list, attempted: int) -> bool:
    """One line naming the failed items: ERROR when most_failed, else WARNING. Returns whether
    it was an ERROR. Logs nothing when nothing failed."""
    if not failed:
        return False
    alert = most_failed(len(failed), attempted)
    names = ", ".join(str(f) for f in failed[:30]) + (" ..." if len(failed) > 30 else "")
    (log.error if alert else log.warning)(
        f"{label}: {len(failed)} of {attempted} failed{' (most of the batch)' if alert else ''}: {names}")
    return alert
