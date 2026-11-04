"""The daily pick lists the emails show: each defined once, with its criteria in plain words.

The lists answer different questions on purpose (a strict morning shortlist, a PGR-bullish
evening view, raw momentum), so their criteria differ. What must not differ is a list's own
definition between the code that builds it, the text the email shows, and the log that
aether/pick_eval.py scores later. A row is a plain dict:

    symbol, pgr ("Bu+".."Be-"), price, stop, target, setup (True/False/None or "1"/"OK"),
    s10, l60, br, industry, patterns

Fields a source doesn't have (the evening rows have no price/stop/target) are left out; a
list only reads the fields its criteria name.
"""
from dataclasses import dataclass
from typing import Callable

import risk_utils
from aether.config import CFG

PGR_RANK = {"Bu+": 5, "Bu": 4, "N": 3, "Be": 2, "Be-": 1}
MORNING_MIN_RR = 1.5      # the 05:30 shortlist's reward/risk floor (the game's own is CFG.system_default_min_rr)


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def is_setup(row) -> bool:
    return row.get("setup") in (True, 1, "1", "OK")


def total(row) -> float:
    return _f(row.get("s10")) + _f(row.get("l60"))


def rr_ratio(row) -> float:
    """Reward/risk to the row's target (the nearest swing-high resistance) over its stop."""
    price, stop, target = _f(row.get("price")), _f(row.get("stop")), _f(row.get("target"))
    risk = price - stop
    return (target - price) / risk if risk > 0 else 0.0


def is_elite(row) -> bool:
    """The shared elite-breakout waiver (risk_utils): a leader at new highs has its resistance
    at or just above the price, so its reward/risk reads near 0; the waiver lets it through."""
    return risk_utils.is_elite_breakout_candidate(total(row), _f(row.get("s10")))


@dataclass(frozen=True)
class PickList:
    key: str            # the log/scorecard id; keep it stable
    title: str
    criteria: str       # shown in the email, word for word
    admit: Callable[[dict], bool]
    score: Callable[[dict], float]
    entry: str          # "prior_close" (decided before the open) or "same_close" (after the close)

    def pick(self, rows, n=5, skip=0, exclude=()) -> list:
        ex = {s.upper() for s in exclude}
        cands = [r for r in rows if str(r.get("symbol", "")).upper() not in ex and self.admit(r)]
        cands.sort(key=lambda r: (-self.score(r), str(r.get("symbol"))))
        return cands[skip:skip + n]


def _elite_text() -> str:
    return (f"elite breakout = S10+L60 ≥ {CFG.system_bypass_score_floor:g} and "
            f"S10 ≥ {CFG.system_bypass_s10_floor:g}")


MORNING_TOP5 = PickList(
    "morning_top5", "Top High-Probability Setups",
    f"Setup OK; reward/risk to the next resistance ≥ {MORNING_MIN_RR:g}, waived for an elite breakout "
    f"(a leader at new highs reads ≈0 reward/risk); ranked by S10+L60. {{elite}}.",
    lambda r: is_setup(r) and (rr_ratio(r) >= MORNING_MIN_RR or is_elite(r)),
    total, "prior_close")

MORNING_RESERVES = PickList(
    "morning_reserves", "A-Reserves (next five)",
    "The next five on the Top High-Probability Setups ranking (ranks 6-10), same criteria.",
    MORNING_TOP5.admit, total, "prior_close")

def _bullish_setup(r) -> bool:
    return PGR_RANK.get(str(r.get("pgr")), 0) >= 4 and is_setup(r)


EVENING_COMBINED = PickList(
    "evening_s10_br", "TOP 5 BUY -- Combined (S10 + BR)",
    "PGR Bullish or better (Bu/Bu+) and Setup OK; ranked by Short10 + Buying Ratio.",
    _bullish_setup, lambda r: _f(r.get("s10")) + _f(r.get("br")), "same_close")

EVENING_S10 = PickList(
    "evening_s10", "TOP 5 BUY -- Short10 (Safe 10)",
    "PGR Bullish or better and Setup OK; ranked by Short10.",
    _bullish_setup, lambda r: _f(r.get("s10")), "same_close")

EVENING_L60 = PickList(
    "evening_l60", "TOP 5 BUY -- Long60 (Safe 60)",
    "PGR Bullish or better and Setup OK; ranked by Long60.",
    _bullish_setup, lambda r: _f(r.get("l60")), "same_close")

RAW_S10 = PickList(
    "raw_s10", "TOP 5 MOMENTUM -- Raw Short10",
    "Every Research symbol, no PGR or Setup filter; ranked by Short10. Watch for reversals.",
    lambda r: True, lambda r: _f(r.get("s10")), "same_close")

RAW_L60 = PickList(
    "raw_l60", "TOP 5 MOMENTUM -- Raw Long60",
    "Every Research symbol, no PGR or Setup filter; ranked by Long60.",
    lambda r: True, lambda r: _f(r.get("l60")), "same_close")

# The 07:00 Oracle filters in aether_oracle.get_oracle_buy_candidates (it needs the bubble z-score
# and the real account's holdings); this entry names it for the email, the log and the scorecard.
ORACLE_BUYS = PickList(
    "oracle_buys", "Oracle: Top High-Conviction Breakout Setups (real account)",
    "Setup OK; Short10 ≥ {oracle_s10}; bubble z-score below {oracle_bubble}; reward/risk ≥ {oracle_rr} "
    "or an elite breakout ({elite}); not already held in the real account; ranked by S10+L60; top 3 shown.",
    lambda r: True, total, "prior_close")

ALL = (MORNING_TOP5, MORNING_RESERVES, EVENING_COMBINED, EVENING_S10, EVENING_L60, RAW_S10, RAW_L60,
       ORACLE_BUYS)


def criteria(pl: PickList) -> str:
    return (pl.criteria.replace("{elite}", _elite_text())
            .replace("{oracle_s10}", f"{CFG.oracle_s10_floor:g}")
            .replace("{oracle_bubble}", f"{CFG.oracle_bubble_limit:g}")
            .replace("{oracle_rr}", f"{CFG.oracle_min_rr_ratio:g}"))
