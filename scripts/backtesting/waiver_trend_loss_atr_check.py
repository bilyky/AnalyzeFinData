"""R&D #113 follow-up, written and run AFTER the result (disclosed): is FAR / BELOW_FALLING just high volatility?
Same events as the study; adds ATR14% at the snapshot day; compares flagged vs other within ATR
terciles (terciles of each test's own events). Read-only."""
import json
import os
import pickle
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import numpy as np

import rule_walkforward as wf
import waiver_trend_loss_study as st
from aether import risk_utils

_bars = {}


def atr_pct(sym, i):
    if sym not in _bars:
        _bars[sym] = risk_utils._load_ohlcv_bars(sym)
    d, h, lo, c = _bars[sym]
    if i < 15:
        return None
    tr = [max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1])) for k in range(i - 13, i + 1)]
    return sum(tr) / 14 / c[i] if c[i] else None


def by_tercile(events, flag):
    xs = [e for e in events if e["atr"] is not None]
    cuts = np.quantile([e["atr"] for e in xs], [1 / 3, 2 / 3])
    out = []
    for t, (lo, hi) in enumerate([(-1, cuts[0]), (cuts[0], cuts[1]), (cuts[1], 9)]):
        g = [e for e in xs if lo < e["atr"] <= hi]
        f = [e for e in g if e[flag]]
        o = [e for e in g if not e[flag]]
        rate = lambda z: sum(e["big_loss"] for e in z) / len(z) if z else None
        diffs = st.monthly_diffs(g, flag)
        dl = [d for _, d, _ in diffs]
        out.append({"tercile": t + 1, "atr_range": [round(float(lo), 4), round(float(hi), 4)],
                    "n_flagged": len(f), "n_other": len(o), "loss_flagged": rate(f), "loss_other": rate(o),
                    "monthly_d_loss": st._mean(dl), "t": st.t_hac(dl) if len(dl) > 2 else None, "months": len(dl)})
    return out


def main(panel):
    rows = pickle.load(open(panel, "rb"))
    a = wf.to_arrays(rows)
    admit, waiver_only = st.oracle_mask(a)
    ev_a = st.build_events(a, waiver_only, lambda ev, idx: {"far": ev["below_high"] > st.FAR_PCT,
                                                          "atr": atr_pct(ev["sym"], ev["i"])})
    ev_b = st.build_events(a, admit, lambda ev, idx: {"bf200": ev["below_falling_200"],
                                                    "atr": atr_pct(ev["sym"], ev["i"])})
    res = {"A_far_by_atr_tercile": by_tercile(ev_a, "far"), "B_bf200_by_atr_tercile": by_tercile(ev_b, "bf200"),
           "n": [len(ev_a), len(ev_b)]}
    sys.stdout.write(json.dumps(res, indent=1, default=str) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
