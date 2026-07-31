# Dynamic Scarcity-Bucket Allocation Cap

> **STATUS: SHIPPED (Option C).** Implemented in `ai_portfolio_game.py`
> (`_conviction_cap_pct()` + the allocation block of `_execute_buys()`), with
> per-profile ramp keys in `get_strategy_rules()`. Options A and B below are the
> alternatives that were considered and rejected; they are documented here so the
> decision is auditable and reversible.

## 1. Problem

The Core-Satellite model splits capital into a **20% Scarcity bucket** (gold/silver/
commodity "core" holds) and an **80% Standard bucket**. `_execute_buys()` downsizes any
buy that would push its bucket over the cap.

The old code relaxed the cap with a **binary cliff — "Adaptive Cap Relaxation"**: if a
candidate's conviction score `total >= 8.0`, the scarcity cap was *suspended entirely*
and the full desired position was bought. This had two concrete defects:

1. **Discontinuity at the boundary.** A candidate at `total = 7.99` was capped at 20%
   of equity; one at `total = 8.00` could take an unbounded share of the bucket. A
   0.01-point scoring difference — well inside the noise of a factor re-weight — produced
   a ~10x jump in position size. Score is an ordinal ranking signal, not a calibrated
   probability; hanging a step-function off one threshold of it is fragile.

2. **DEFENSIVE was silently always-uncapped.** The relaxation threshold was a **global
   8.0**, but `get_strategy_rules("DEFENSIVE")` sets `min_score_threshold = 10.0`. Every
   buy that DEFENSIVE was allowed to make had already cleared 10.0 ≥ 8.0 — so the
   suspension *always* fired. The capital-preservation profile, the one that most needs
   concentration control, had **no effective scarcity cap at all.** This directly
   contradicts CLAUDE.md's Rule of Loss Minimization (capital preservation is the
   absolute priority; maintain a high defensive cash cushion).

We want conviction to still earn a larger position — but *smoothly and bounded*, and with
the relaxation tied to each profile's own risk appetite rather than a global constant.

## 2. Options considered

### Option A — Global conviction ramp (single set of constants)

Replace the cliff with a linear ramp `base → ceiling` as `total` rises from `relax_start`
to `relax_full`, using **one global** `(base, ceiling, relax_start, relax_full)` for all
profiles.

```
cap(total) = base                                   if total <= relax_start
           = base + (total-start)/(full-start) * (ceiling-base)   if between
           = ceiling                                if total >= relax_full
```

- ✅ Fixes defect #1 (the discontinuity) — the size curve is continuous.
- ❌ Does **not** fix defect #2. A single global `relax_start` (say 8.0) still sits below
  DEFENSIVE's 10.0 floor, so DEFENSIVE buys still start already up the ramp. And a single
  global `ceiling` forces the same maximum concentration on the aggressive and
  capital-preservation profiles, which is exactly the conflation we're trying to remove.
- **Rejected:** solves the cosmetic problem (smoothness) but not the dangerous one
  (DEFENSIVE concentration).

### Option B — Per-profile ramp, but no per-position ceiling

Same ramp as C, with the constants moved **into each profile** so `relax_start` can be
pinned to that profile's `min_score_threshold` and each profile gets its own `ceiling`.
But keep the pre-existing behavior that `_execute_buys()` applies **only** the bucket cap
— no independent per-name ceiling on the buy path.

- ✅ Fixes both defect #1 and defect #2.
- ❌ Leaves a separate latent bug: `_execute_buys()` never enforced
  `max_allocation_pct` (the per-*position* ceiling) — only the pyramiding/add path did.
  So a single high-conviction buy into an *empty* bucket could legitimately consume the
  entire bucket (e.g. the full 40% ceiling in one name). The ramp bounds the *bucket*,
  but not concentration in any *one* symbol.
- **Rejected:** correct on the cap, but ships a known single-name concentration hole.

### Option C — Per-profile ramp **+** per-position ceiling on every buy  ← SHIPPED

Option B plus: after bucket downsizing, **every** buy (scarcity or standard) is clamped to
`equity * max_allocation_pct` for that profile. This closes the single-name hole and makes
the per-position ceiling a genuine invariant of the buy path, not just the add path.

- ✅ Continuous size curve (defect #1).
- ✅ `relax_start` = each profile's `min_score_threshold`, so no profile starts pre-relaxed;
   DEFENSIVE is capped again (defect #2).
- ✅ No single name can exceed the profile's per-position ceiling, in *any* bucket.
- ✅ The cap never disappears — worst case it clamps at `ceiling`, which is still a bound.
- **Chosen.**

## 3. Shipped model (Option C)

`_conviction_cap_pct(total, base_pct, ceiling_pct, relax_start, relax_full)` returns the
bucket cap fraction:

| `total` vs ramp            | cap returned                                             |
|----------------------------|----------------------------------------------------------|
| `total <= relax_start`     | `base_pct`                                                |
| `relax_start < total < relax_full` | `base_pct + frac*(ceiling_pct - base_pct)`, `frac = (total-relax_start)/(relax_full-relax_start)` |
| `total >= relax_full`      | `ceiling_pct`                                             |

Degenerate guards: if `ceiling_pct <= base_pct` or `relax_full <= relax_start`, it returns
`base_pct` (ramp disabled → behaves as a flat cap). Standard-bucket buys use the flat
`base` (1 − scarcity_pct) cap unchanged. Then both buckets pass through the per-position
ceiling clamp.

### Per-profile constants (`get_strategy_rules`)

| Profile     | base | ceiling | relax_start (=min_score) | relax_full\* | max_allocation_pct |
|-------------|------|---------|--------------------------|--------------|--------------------|
| AGGRESSIVE  | 0.20 | 0.40    | 2.0                      | 10.0         | 0.15               |
| BALANCED    | 0.20 | 0.35    | 5.0                      | 11.0         | 0.15               |
| DEFENSIVE   | 0.20 | 0.25    | 10.0                     | 16.0         | 0.10               |

\* `relax_full` is calibrated against the empirical `total` score distribution (see the
Jul-18 optimizer discipline in CLAUDE.md and `scripts/backtesting/validate_scarcity_cap.py`).
DEFENSIVE deliberately has the **lowest ceiling** (0.25) and the **highest** `relax_start` —
it takes the most conviction to earn the least extra concentration, matching the Rule of
Loss Minimization.

## 4. Empirical validation (2026-07-30)

`scripts/backtesting/validate_scarcity_cap.py` imports the shipped `_conviction_cap_pct`
and `get_strategy_rules` (no mocks) and runs them over the **real** `total = Short10 +
Long60` distribution from the live Research sheet — a one-day cross-section of the full
universe (506 scored rows, 271 active setups). It is **not** a portfolio-P&L backtest (the
repo has none); it validates the cap mechanism and concentration behavior. Findings:

- **DEFENSIVE pathology confirmed empirically:** under the old global-8.0 suspension,
  **3/3 (100%)** of DEFENSIVE-eligible active setups (min_score 10.0) ran with the scarcity
  cap fully **suspended**. Option C caps them at 20.5–25.0% instead. This is the single
  strongest justification for the change — the capital-preservation profile had zero
  concentration control.
- **The cliff was in-distribution, not hypothetical:** AGGRESSIVE eligible scores reach
  +16.9, so the old 20%→unbounded jump at 8.0 actually fired (4/26 eligible). The new ramp
  is continuous across the whole range (20.7%→40.0%).
- **Calibration:** eligible-score p90 was AGGRESSIVE +10.1, BALANCED +16.1, DEFENSIVE +16.7.
  AGGRESSIVE's `relax_full` was 8.0 ≈ p70 (median +4.1), i.e. mid-conviction names reached
  the full 40% ceiling too easily — **raised to 10.0** to reserve the ceiling for genuine
  top-decile conviction (n=26, well supported). BALANCED (n=9) and DEFENSIVE (n=3) samples
  are too thin to retune off a single day and are **held at their starting values pending
  multi-day data** (re-run the script over more snapshots before adjusting).

## 5. Tests

`tests/test_custom_sprints.py :: TestCoreSatelliteAllocation`:

- `test_scarcity_asset_downsizing_and_caps` — base cap below `relax_start` (50 sh).
- `test_scarcity_cap_ramps_with_conviction` — midpoint of ramp (150 sh at 30% cap).
- `test_scarcity_cap_clamps_at_ceiling` — clamps at ceiling, not suspended (250 sh, not 500).
- `test_per_position_ceiling_binds_on_scarcity_buy` — per-name ceiling tighter than bucket.
- `test_per_position_ceiling_binds_on_standard_buy` — regression guard for the Option-B hole.
- `test_defensive_min_score_buy_not_prematurely_relaxed` — a buy at DEFENSIVE's 10.0 floor
  gets the base cap, not the old always-on suspension (50 sh, not 500).

Plus `TestExecuteBuys::test_fallback_on_verify_failure` updated (APA 76 → 30 sh) to encode
the now-enforced DEFENSIVE 10% per-position ceiling.

## 6. Follow-ups

- Calibrate `relax_full` per profile against the real score distribution.
- Consider surfacing the active cap % in the daily email allocation block for transparency.
