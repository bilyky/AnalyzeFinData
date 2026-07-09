# AnalyzeFinData — PowerGauge Stock Screener

Stock screener for ~485 symbols. Pulls live data from the Chaikin Analytics API, computes
entry-quality scores from local OHLCV history, and writes results into `Data/investment.xlsx`.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Setup](#setup)
3. [Credentials](#credentials)
4. [Running the Screener](#running-the-screener)
5. [Environment Variables](#environment-variables)
6. [Data / Folder Structure](#data--folder-structure)
7. [Reorganising the Symbol Cache](#reorganising-the-symbol-cache)
8. [Changing the Folder Structure](#changing-the-folder-structure)
9. [Testing](#testing)
10. [Excel Output — Column Reference](#excel-output--column-reference)
11. [Score Definitions](#score-definitions)
12. [Backtest Summary](#backtest-summary)

---

## How It Works

```
investment.xlsx (Research sheet)
       ↑ written by check_from_xls()
       │
powergauge.py  ←── Chaikin API (live PGR, signals, price)
       │        ←── Data/Symbol_full/<SYMBOL>_daily.json  (OHLCV history)
       │
scoring.py  ──── pure functions: streaks, seasonality, regime, short/long scores
       │
excel_output.py ── Research + Picks sheet formatting
```

**Three-phase run inside `check_from_xls`:**

| Phase | What happens | Threading |
|-------|-------------|-----------|
| 1. Collect | Read Research sheet, gather valid symbol rows | Serial |
| 2. Fetch | Call Chaikin API for every unique symbol | Parallel (`CHAIKIN_WORKERS`, default 10) |
| 3. Compute + Write | Compute scores, write cells | Serial |

---

## Setup

**Requirements:** Python 3.11+, packages in the virtualenv (`venv/`).

```bash
# Activate the virtualenv (Windows)
venv\Scripts\activate

# Key packages (already installed in venv)
# openpyxl  requests  urllib3  playwright
```

The `venv/` directory is gitignored — it ships with the repo only for local use.
If you recreate it: `pip install openpyxl requests urllib3 playwright`.

---

## Credentials

The screener needs a Chaikin Analytics session. Two credential sources are tried
**in this order**:

### 1. Environment variables (preferred)

```
CHAIKIN_EMAIL=your@email.com
CHAIKIN_PASSWORD=yourpassword
```

### 2. Config file

Copy the example and fill in your values:

```bash
cp chaikin_config.json.example chaikin_config.json
```

```json
{
  "email": "your@email.com",
  "password": "yourpassword"
}
```

`chaikin_config.json` is gitignored and will never be committed.

### Session token

On first run the screener logs in automatically and saves a session token to
`Data/session.txt`. Subsequent runs reuse the cached token (valid ~24 h).
If the token expires the screener re-authenticates automatically.

---

## Running the Screener

### Full run — all symbols in investment.xlsx

```python
import datetime
import powergauge

date = datetime.date.today()
powergauge.check_from_xls(prefer_cache=False, date=date)
```

### Targeted run — specific symbols only

```python
powergauge.check_from_xls(prefer_cache=False, date=date, symbols=["AAPL", "NVDA", "MSFT"])
```

### From cache (no API calls for symbols already fetched today)

```python
powergauge.check_from_xls(prefer_cache=True, date=date)
```

### Single-symbol test

```python
import datetime
import powergauge

pg = powergauge.get_symbol_data("AAPL", datetime.date.today(), prefer_cache=False, session_id="...")
print(pg.price, pg.pgr_corrected_value)
```

### File-based run (symbols_to_check.txt)

```python
powergauge.check_from_file(prefer_cache=False, date=date)
```

### If Excel is open when the run finishes

The screener detects the file-lock and saves to `Data/investment_pending_<timestamp>.xlsx`.
Close Excel, then rename/copy that file over `investment.xlsx`.

### Generating `state_of_the_day.xlsx`

The root-level `state_of_the_day.xlsx` is the live state workbook the pipeline,
dashboard, and AI game read from. It holds **real portfolio/screening data**, so it
is **git-ignored** and not shipped in the repo — a fresh clone must generate it
locally by running the screener (`powergauge.check_from_xls(...)`), which writes the
Research/Short_Long sheets. If a run finishes while the file is open, powergauge
saves a `state_of_the_day_pending_<timestamp>.xlsx`; close Excel and copy it over
`state_of_the_day.xlsx`.

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHAIKIN_EMAIL` | — | Login email (overrides config file) |
| `CHAIKIN_PASSWORD` | — | Login password (overrides config file) |
| `CHAIKIN_API_KEY` | Chaikin web-app default | Client API key; rotate if Chaikin updates it |
| `CHAIKIN_WORKERS` | `10` | Parallel fetch thread count |
| `REGIME_SYMBOL` | `RSP` | Ticker for market regime SMA(50). Set to `""` to disable |
| `HTTPS_PROXY` / `HTTP_PROXY` | — | Corporate proxy URL (passed to all requests) |

---

## Data / Folder Structure

```
AnalyzeFinData/
│
├── powergauge.py          # Main screener: API client, check_from_xls, PowerGauge class
├── scoring.py             # Pure scoring functions (no API dependency)
├── excel_output.py        # openpyxl helpers: Research headers, Picks sheet, shapeId fix
├── utils.py               # Shared helpers (_to_float)
├── verify_stops.py        # Backtester: stop/target/filter comparison across symbols
├── backtest.py            # Full factor backtest (336k obs, 2023-2025)
├── sync_data.py           # Sync OHLCV files from a secondary Data folder
├── organize_symbol_files.py  # Organise cached Symbol/ files
├── chaikin_config.json.example
│
├── tests/                 # 187 tests total (python -m unittest discover tests)
│   ├── conftest.py            # make_ohlcv() fixture helper
│   ├── test_scoring.py        # scoring pure functions
│   ├── test_compute.py        # PowerGauge compute helpers
│   ├── test_sell_rules.py     # unified exit policy (stop > soft > hold)
│   ├── test_ai_client.py      # configurable multi-provider AI dispatch
│   ├── test_sell_eval.py      # advisory exit-rubric layer
│   ├── test_decision_eval.py  # backtracking selector scorecard
│   ├── test_config.py         # unified config + env overrides
│   ├── test_accounts.py       # Short_Long accounts parsing
│   ├── test_server.py         # dashboard API + auth
│   ├── test_breadth_filter.py # SPY-RSP breadth divergence guard
│   └── test_short_long_sync.py
│
└── Data/                  # gitignored — local data only
    ├── investment.xlsx          # Main workbook (Research + Picks sheets)
    ├── session.txt              # Cached Chaikin session token
    ├── symbols_to_check.txt     # One symbol per line, used by check_from_file()
    ├── Symbol/                  # Per-symbol, per-date Chaikin API cache
    │   └── <SYMBOL>/            # One subdir per ticker (flat fallback still supported)
    │       └── <SYMBOL>_<DATE>.json
    ├── Symbol_full/             # Daily OHLCV history (Alpha Vantage format)
    │   └── <SYMBOL>_daily.json
    └── Backup/
        └── <YEAR>/
            └── investment_<TIMESTAMP>.xlsx
```

### Symbol cache layout (after reorganisation)

```
Data/Symbol/
├── AAPL/
│   ├── AAPL_2025-05-16.json
│   └── AAPL_2025-05-19.json
├── NVDA/
│   └── NVDA_2025-05-19.json
└── ...
```

The screener reads from `Symbol/<SYM>/<SYM>_<DATE>.json` and falls back to the
old flat `Symbol/<SYM>_<DATE>.json` if the file isn't found in the subdir.
New fetches are always written into the per-symbol subdir.

---

### Symbol_full JSON format

Each file is a standard Alpha Vantage "Time Series (Daily)" response:

```json
{
  "Time Series (Daily)": {
    "2025-05-16": {
      "1. open":   "189.30",
      "2. high":   "191.05",
      "3. low":    "188.50",
      "4. close":  "190.20",
      "5. volume": "52340100"
    }
  }
}
```

---

## Reorganising the Symbol Cache

Run this once to move all flat `Data/Symbol/<SYM>_<DATE>.json` files into
per-symbol subdirectories. Handles any number of files; safe to re-run (already-moved
files are skipped because they don't appear in the flat root any more).

```bash
python organize_symbol_files.py
```

**What it does:**

1. Scans `Data/Symbol/` for files matching `<SYM>_<DATE>.json`
2. Creates `Data/Symbol/<SYM>/` for each unique symbol
3. Moves every file into its symbol's subfolder
4. Prints progress every 10,000 files and a final summary

**Expected output (first run on a full dataset):**

```
  10000/414264 moved...
  ...
  410000/414264 moved...
Done: 414264 files moved into 528 symbol folders.
```

**`powergauge.py` is fully compatible with both layouts:**

| Operation | Subdir path tried first | Flat fallback |
|-----------|------------------------|---------------|
| Cache read (`prefer_cache=True`) | `Symbol/<SYM>/<SYM>_<DATE>.json` | `Symbol/<SYM>_<DATE>.json` |
| Cache write (after API fetch) | `Symbol/<SYM>/` (created if absent) | — |
| Index scan (`_build_cache_index`) | `os.walk` — picks up both flat and subdir | — |

No code changes are needed before or after running the script.

---

## Changing the Folder Structure

All folder paths are derived from `__file__` — no hardcoded absolute paths.
To move the project or its data, change only these constants near the top of each file:

### `powergauge.py`

```python
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "session.txt")
XLSX_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "investment.xlsx")
XLSX_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Backup")
OHLCV_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")
```

Change `"Data"` or add subdirectory segments as needed, e.g.:

```python
OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MarketData", "OHLCV")
```

### `scoring.py`

```python
# Inside market_regime():
path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "Data", "Symbol_full", f"{REGIME_SYMBOL}_daily.json")
```

Change `"Data", "Symbol_full"` to match your new OHLCV location.

### `sync_data.py`

```python
SRC = Path(r"D:\Develop\AnalyzeFinData_1\Data")
DST = Path(r"D:\Develop\AnalyzeFinData\Data")
```

Update both absolute paths to match your environment.

---

## Testing

Tests are plain `unittest.TestCase` — no pytest required.

### Run all tests

```bash
python -m unittest discover -s tests -v
```

Expected output: **187 tests, 0 failures.**

### Run a single file

```bash
python -m unittest tests.test_scoring -v
python -m unittest tests.test_compute -v
```

### Test coverage

| File | Classes | What is tested |
|------|---------|----------------|
| `tests/test_scoring.py` | 9 | `_to_float`, `week_of_month`, `predicted_win_pct`, `ohlcv_streak_perc`, `ohlcv_streak_count`, `rel_volume_bucket`, `short_score`, `long_score`, `market_regime` |
| `tests/test_compute.py` | 5 | `_pgr_str`, `_buying_ratio`, `_compute_pgr_fields`, recursion depth cap, symbol path validation |

`tests/conftest.py` provides `make_ohlcv(closes, volumes, highs, lows)` — builds a
synthetic Alpha Vantage time-series dict from plain price lists. Use it in new tests.

### Adding a test

```python
# tests/test_scoring.py
from tests.conftest import make_ohlcv

class TestMyNewThing(unittest.TestCase):
    def test_something(self):
        ohlcv = make_ohlcv([100, 102, 101, 105])
        result = my_function(ohlcv)
        self.assertEqual(result, expected)
```

### Regression guards

`TestShortScore.test_regression_exact_weights` and
`TestLongScore.test_regression_exact_weights` pin the exact numeric output of
`short_score` / `long_score` for a known input. If you change factor weights in
`scoring.py`, update these tests to match — they are intentional canaries.

---

## Excel Output — Column Reference

### Research sheet (one row per symbol)

Columns A–D and I, Q are pre-existing and preserved by the screener.

| Col | Excel | Field | Description |
|-----|-------|-------|-------------|
| A | A | — | Preserved (user data) |
| B | B | — | Preserved |
| C | C | — | Preserved |
| D | D | Symbol | Ticker; read by screener to identify the row |
| E | E | Industry | Sector / industry name from Chaikin `metaInfo` |
| F | F | Prev PGR | Corrected PGR from the most recent prior cache snapshot |
| G | G | PGR | Current Corrected Power Gauge Rating (Be- / Be / N / Bu / Bu+). Shows `X/Y` when corrected ≠ raw |
| H | H | Ind Strength | Industry group signal: Strong / Weak / (blank) |
| I | I | — | Preserved |
| J | J | Stop | Stop price = min(3-day lows) × 0.99. Zero when entry filter fails |
| K | K | Price | Last price from Chaikin API |
| L | L | Target | Resistance target = highest 10-day high above current price. Zero when entry filter fails |
| M | M | R/R | Risk/Reward = (Target − Price) / (Price − Stop). Zero when entry filter fails |
| N | N | Prev Move% | Cumulative % move since the previous Chaikin cache snapshot |
| O | O | Prev % | Day-change% recorded in the previous snapshot |
| P | P | Change% | Today's price change% from Chaikin |
| Q | Q | — | Preserved |
| R | R | LT Trend | Long-term price trend: Strong / Neutral / Weak (Weak = recovery candidate) |
| S | S | Money Flow | Institutional money flow: Strong / Neutral / Weak |
| T | T | OB/OS | Overbought / Oversold zone: Optimal / Early / Neutral / Wait |
| U | U | Setup | Entry filter: `1` passed, `0` failed. Pass = Price > SMA(20) AND Price > Close[3d ago] |
| V | V | BR Score | Buying Ratio: composite score −10 to +10 (see [Score Definitions](#score-definitions)) |
| W | W | Seasonal | Week-of-month seasonality: +1.0 strong tailwind … −1.0 headwind |
| X | X | Win% 10d | Predicted 10-day win% from backtest based on BR bucket |
| Y | Y | Short10 | 10-day entry-quality score −10 to +10 |
| Z | Z | Long60 | 60-day position-quality score −10 to +10 |

### Picks sheet (auto-refreshed every run)

Four ranked tables of Top 5, one section each:

| Table | Description |
|-------|-------------|
| TOP 5 BUY — Short10 | Best 5 symbols by Short10 score (descending) |
| TOP 5 SELL — Short10 | Worst 5 symbols by Short10 score (ascending) |
| TOP 5 BUY — Long60 | Best 5 symbols by Long60 score (descending) |
| TOP 5 SELL — Long60 | Worst 5 symbols by Long60 score (ascending) |

Each table has these columns:

| Col | Field | Description |
|-----|-------|-------------|
| Rank | Rank | 1–5 within this table |
| Symbol | Symbol | Ticker |
| Industry | Industry | Sector / industry name |
| Score | Score | The Short10 or Long60 score for this table |
| BR | BR Score | Buying Ratio composite score |
| PGR | PGR | Power Gauge Rating string |
| OB/OS | OB/OS | Overbought / Oversold zone |
| Money Flow | Money Flow | Institutional money flow signal |
| LT Trend | LT Trend | Long-term price trend |
| Setup | Setup | OK (green) = entry filter passed; -- (grey) = failed |
| Price | Price | Last price |

---

## Score Definitions

### BR Score (Buying Ratio) — column V

Composite entry-quality signal combining multiple Chaikin signals and OHLCV-derived factors.
Range: −10 to +10.

| Component | Contribution |
|-----------|-------------|
| PGR (1→5) | −2 to +2 (linear) |
| R/R ratio | 0→−1, ≥0.5→+0.5, ≥1→+1, ≥2→+1.5, ≥3→+2 |
| LT Trend | Weak +1, Strong −1 |
| Money Flow | Strong +0.75, Weak −0.75 |
| OB/OS | Optimal +1, Early +0.25, Wait −0.25 |
| Industry | Weak +0.5, Strong −0.5 |
| PGR Delta | Any change +0.25 |
| Seasonality | −1.0 to +1.0 |

**Thresholds:** ≥4 strong buy · 2–4 moderate · 0–2 weak · −2–0 avoid · ≤−2 strong avoid

### Win% 10d — column X

Predicted 10-day win rate from backtest (238k obs, 466 symbols, 2023–2025):

| BR range | Win% |
|----------|------|
| ≥ 4.0 | 64.3% |
| 2.0 – 3.9 | 57.6% |
| 0.0 – 1.9 | 53.1% |
| −2.0 – −0.1 | 50.3% |
| ≤ −2.1 | 46.3% |

### Short10 — column Y

10-day entry-quality score. Factor weights derived from 336k observations (2023–2025),
NA-filtered. Range: −10 to +10.

| Factor | Weight (% spread) | Values |
|--------|-------------------|--------|
| Rel Volume | 4.4% | High +2.5, Very High +0.5, Normal 0, Low −2.0 |
| OB/OS | 4.3% | Optimal +3.0, Early +1.0, Neutral 0, Wait −2.0 |
| Money Flow | 3.5% | Strong +3.0, Neutral 0, Weak −2.0 |
| Industry Str | 3.1% (contrarian) | Weak +2.0, Strong −2.0 |
| LT Trend | 2.1% (contrarian) | Weak +1.5, Neutral 0, Strong −1.5 |
| Seasonality | — | −1.0 to +1.0 |
| Market Regime | — | Bull +1.0, Neutral 0, Bear −1.0 |

> PGR, PGR Delta, and R/R all showed <2% spread at 10d — excluded.

**Very High volume is dampened (+0.5, not +2.5)** — news-driven spikes degrade entry quality.

**Industry and LT Trend are contrarian** — oversold sectors and extended-down trends
show stronger forward returns (recovery effect).

### Long60 — column Z

60-day position-quality score. Same methodology, different weights. Range: −10 to +10.

| Factor | Weight (% spread) | Values |
|--------|-------------------|--------|
| LT Trend | 4.5% (contrarian) | Weak +4.0, Neutral 0, Strong −3.0 |
| Rel Volume | 2.8% | High +2.0, Very High 0, Normal 0, Low −1.0 |
| Money Flow | 2.5% | Strong +2.5, Neutral 0, Weak −2.0 |
| Industry Str | 2.4% (contrarian) | Weak +2.0, Strong −1.5 |
| OB/OS | 2.3% | Optimal +1.5, Early +0.5, Neutral 0, Wait −0.5 |
| Seasonality | — | −0.5 to +0.5 (scaled by 0.5×) |
| Market Regime | — | Bull +1.5, Neutral 0, Bear −1.5 |

> The LT Trend contrarian effect is twice as strong at 60d vs 10d (4.5% vs 2.1%).

### Market Regime

Read from `Data/Symbol_full/<REGIME_SYMBOL>_daily.json`. SMA(50) computed on
all trading days up to and including the run date.

| Regime | Condition |
|--------|-----------|
| Bull | Price > SMA(50) by more than 2% |
| Bear | Price < SMA(50) by more than 2% |
| Neutral | Within ±2% of SMA, or data unavailable |

Default symbol: `RSP` (equal-weight S&P 500). Override with `REGIME_SYMBOL` env var.
Set to `""` to disable (all symbols treated as Neutral).

---

## Backtest Summary

- **Dataset:** 336k observations, ~485 symbols, Jan 2023 – Dec 2025
- **Method:** Walk-forward, no look-ahead. OHLCV from Alpha Vantage daily files.
- **Entry filter (t+d3):** Price > SMA(20) AND Price > Close[3d ago]
  - Lifts 10-day win rate by ~16 percentage points vs no filter
  - Validated on 51/51 test symbols
- **Key findings:**
  - Money Flow is the single strongest predictor at both 10d and 60d
  - PGR adds no measurable signal at horizons < 60d
  - Very High volume (news spikes) degrades short-term returns vs High volume
  - LT Trend and Industry Strength are both contrarian — oversold > overbought
  - NA values in scoring columns skew 60d results; always filter before analysing
