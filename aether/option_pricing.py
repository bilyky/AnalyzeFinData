"""Black-Scholes option pricing + realized-vol helpers (pure, no I/O, no scipy).

The options adviser (``aether/options_adviser.py``) recommends strategies from a *live*
option chain. To **backtest** those recommendations there is no historical chain to draw
on — E*TRADE serves only current chains and bulk historical option data is a paid product.
This module bridges that gap: it prices a synthetic chain from the underlying's own
**realized volatility** so the adviser can be replayed at any past date.

Every premium produced here is therefore **MODELED**, not a traded quote. Callers must label
it as such. Known biases, smallest-to-largest effect at ~35 DTE:

    * **Realized vol as an IV proxy** — the dominant approximation. Traded implied vol carries
      a volatility-risk-premium (empirically IV ≈ 1.1–1.3× trailing RV), so modeled premiums
      **understate** real option prices. The ``vrp_mult`` knob on :func:`synthesize_chain`
      stresses this; leave it 1.0 for the pure-RV baseline.
    * **European settlement** — no early exercise. Matters mainly for ITM puts / around
      dividends; immaterial for the OTM protection/income structures the adviser favours.
    * **Flat r / q=0** — a constant risk-free rate and no dividend yield. INTC paid a dividend
      (cut 2023); the effect on a ~35-DTE premium is small relative to the vol assumption.

Pure by design (only ``math`` + the :class:`OptionQuote` dataclass) so it is fully unit-testable
and safe to reuse anywhere — including, as a future enhancement, a fallback for the *live* adviser
when a real chain arrives without greeks/premiums (noted, not wired).
"""
from __future__ import annotations

import datetime
import math
from typing import Optional

from aether.options_adviser import OptionQuote


# Defaults for the modeled chain. Public so studies/tests can reference one source.
DEFAULT_RATE = 0.03          # flat risk-free rate (annual); see module note on the approximation
DEFAULT_DIV_YIELD = 0.0      # dividend yield q; 0 = ignore dividends (documented simplification)
DEFAULT_VOL_WINDOW = 21      # trailing trading days for realized vol (~1 option-holding month)
TRADING_DAYS = 252           # annualization factor for daily-return vol
DEFAULT_STRIKE_STEP = 2.5    # $ spacing of the synthetic strike ladder (typical for INTC)
DEFAULT_N_STRIKES = 12       # strikes each side of spot (mirrors adviser fetch_chain strikes=12)
DEFAULT_SPREAD_PCT = 0.015   # modeled half bid/ask spread as a fraction of mid


def norm_cdf(x: float) -> float:
    """Standard-normal CDF via ``math.erf`` (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float):
    """Black-Scholes-Merton d1/d2, or ``(None, None)`` when undefined (T≤0 or σ≤0)."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None, None
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vt
    return d1, d1 - vt


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str, q: float = DEFAULT_DIV_YIELD) -> float:
    """Black-Scholes-Merton price of a European call/put.

    Falls back to discounted intrinsic value when the diffusion is degenerate (``T<=0`` or
    ``sigma<=0``), so the function is total. ``option_type`` is case-insensitive CALL/PUT.
    """
    is_call = str(option_type).upper().startswith("C")
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    if d1 is None:
        # Degenerate: no time/vol left — value is (undiscounted) intrinsic.
        intrinsic = (S - K) if is_call else (K - S)
        return round(max(intrinsic, 0.0), 4)
    disc_s = S * math.exp(-q * T)
    disc_k = K * math.exp(-r * T)
    if is_call:
        px = disc_s * norm_cdf(d1) - disc_k * norm_cdf(d2)
    else:
        px = disc_k * norm_cdf(-d2) - disc_s * norm_cdf(-d1)
    return round(max(px, 0.0), 4)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str, q: float = DEFAULT_DIV_YIELD) -> float:
    """Analytic BSM delta: call in ``[0, 1]``, put in ``[-1, 0]``."""
    is_call = str(option_type).upper().startswith("C")
    d1, _ = _d1_d2(S, K, T, r, sigma, q)
    if d1 is None:
        # Degenerate: delta collapses to the ITM indicator (0/1 call, 0/-1 put).
        itm = (S > K) if is_call else (S < K)
        return (1.0 if is_call else -1.0) if itm else 0.0
    disc = math.exp(-q * T)
    return round(disc * norm_cdf(d1) if is_call else -disc * norm_cdf(-d1), 4)


def realized_vol(closes, window: int = DEFAULT_VOL_WINDOW,
                 periods: int = TRADING_DAYS) -> Optional[float]:
    """Annualized realized volatility = stdev of daily **log** returns over the trailing
    ``window`` closes, scaled by ``sqrt(periods)``.

    Uses the last ``window`` log returns from ``closes`` (pass ``closes[:i+1]`` for an
    entry-anchored, no-look-ahead estimate). Requires clean, contiguous **positive** closes in the
    window: it returns ``None`` on any non-numeric or non-positive bar (refusing to bridge a gap and
    distort the estimate), when there are fewer than two usable returns, or when the series is flat.
    """
    if not closes or len(closes) < 3:
        return None
    try:
        prices = [float(c) for c in closes[-(window + 1):]]
    except (TypeError, ValueError):
        return None                                    # non-numeric bar in the window -> refuse
    # A zero/negative close dropped from the MIDDLE would silently bridge two non-adjacent days into
    # one log return and corrupt the estimate. Refuse the whole window rather than guess.
    if len(prices) < 3 or any(p <= 0 for p in prices):
        return None
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1)     # sample variance (ddof=1)
    daily = math.sqrt(var)
    if daily <= 0:
        return None
    return daily * math.sqrt(periods)


def synthesize_chain(spot: float, expiry: datetime.date, today: datetime.date,
                     sigma: float, *, r: float = DEFAULT_RATE, q: float = DEFAULT_DIV_YIELD,
                     n_strikes: int = DEFAULT_N_STRIKES, step: float = DEFAULT_STRIKE_STEP,
                     spread_pct: float = DEFAULT_SPREAD_PCT,
                     vrp_mult: float = 1.0) -> list:
    """Build a **modeled** option chain (list of :class:`OptionQuote`) around ``spot``.

    Strikes lie on a ``step``-dollar ladder bracketing ``spot`` (``n_strikes`` each side). Each
    strike gets a Call and a Put priced by :func:`bs_price`; ``mid`` is the BS price and a
    modeled half-spread (``spread_pct`` of mid, floored at $0.02) sets ``bid``/``ask`` so the
    adviser's conservative fill conventions (buy at ``ask``, sell at ``bid``) apply realistically.
    ``delta`` comes from :func:`bs_delta` so ``--select delta`` works on the synthetic chain too.

    ``vrp_mult`` scales ``sigma`` to stress the vol-risk-premium bias (see module note); 1.0 is
    the pure realized-vol baseline. Returns ``[]`` if inputs are degenerate.
    """
    if spot is None or spot <= 0 or sigma is None or sigma <= 0:
        return []
    T = (expiry - today).days / 365.0
    if T <= 0:
        return []
    eff_sigma = sigma * vrp_mult
    base = round(spot / step) * step
    strikes = [round(base + j * step, 2) for j in range(-n_strikes, n_strikes + 1)]
    quotes: list = []
    for K in strikes:
        if K <= 0:
            continue
        for otype in ("CALL", "PUT"):
            mid = bs_price(spot, K, T, r, eff_sigma, otype, q=q)
            half = max(0.02, round(spread_pct * mid, 2))
            quotes.append(OptionQuote(
                option_type=otype,
                strike=K,
                bid=round(max(0.0, mid - half), 2),
                ask=round(mid + half, 2),
                last=round(mid, 2),
                delta=bs_delta(spot, K, T, r, eff_sigma, otype, q=q),
                expiry=expiry,
                in_the_money=(K < spot) if otype == "CALL" else (K > spot),
            ))
    return quotes
