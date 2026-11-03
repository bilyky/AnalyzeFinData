# PROD — theme watchlists (#156) + secrets to rotate — 2026-10-07

**What it adds:** two watch-only theme lists, **AI buildout** (power / cooling / electrical /
construction) and **robot vision** (eyes / chips / software). They are ranked by SEC filings
(revenue and backlog growth, 8-K material agreements) plus 60-day strength vs SPY and money flow.
There's a new web tab, Research > Theme Watch, served by `/api/ai_buildout`.

**What it does NOT change:** scoring, the game, buys or sells. Nothing imports it except the web
API and its own scripts. The signal study (R&D #51) came out **marginal**, so the score has no buy
weight.

Merged to main 2026-10-07 as `d26c377` (PR #156, which contains #157 and #159).

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`.
`python` = `venv_new\Scripts\python.exe`. This rides the same `python server.py upgrade` as
`2026-10-06-prod-finalize.md` (step 4 there). Use its lock check for timing and its step 2
backup.

---

## 0. Rotate three exposed secrets (do this first)

On 2026-10-05 a config review printed these to a dev console and session transcript. Treat them as
exposed:

| Secret | Where to regenerate | Where it lives in `config.json` |
|---|---|---|
| OpenRouter API key | openrouter.ai, Keys | `ai.providers.openrouter.api_key_source` (literal key) |
| E*TRADE production consumer key | E*TRADE developer portal | `etrade.production.consumer_key` |
| E*TRADE production consumer secret | E*TRADE developer portal | `etrade.production.consumer_secret` |

- `Data/config.prod_fixed.json` (finalize note, step 3) **still carries the old OpenRouter key**.
  Put the new key into it before copying, or edit `config.json` right after copying.
- After changing the E*TRADE consumer key/secret, I'd expect tokens minted with the old key to stop
  working, so plan for a fresh mint. I haven't tested this. Check
  `python server.py etrade-status` after the restart.
- Never paste the new values into a note, chat or log.

## 1. Pre-check (read-only, cmd)

SEC must be reachable from PROD and must see a contact email in the User-Agent (otherwise 403):

```
curl -sS -o NUL -w "%{http_code}\n" -A "AnalyzeFinData research YOUR_EMAIL" https://www.sec.gov/files/company_tickers.json
python -c "from aether.config import CFG; print('sec contact set:', bool(CFG.email_sender_address))"
```

- `200` and `sec contact set: True`: go.
- `403`: SEC is rate-blocking this egress (usually lifts in ~10 min) or the User-Agent has no
  contact. Retry later. `AETHER_SEC_CONTACT=<email>` overrides the config address.
- No connection: PROD's network or proxy can't reach sec.gov. The tab stays empty; nothing else is
  affected.

## 2. Deploy

Nothing extra. It ships with `python server.py upgrade` (finalize note, step 4). No config keys and
no data migration. The restart loads the new route and web page.

## 3. First scans (run by hand, once the pipeline is idle)

```
python scripts\monitoring\ai_buildout_watch.py
python scripts\monitoring\ai_buildout_watch.py --theme robot_vision
```

- Dry runs: they write `Data\ai_buildout_watch.json/.html` and `Data\robot_vision_watch.json/.html`
  and send no email. Add `--send` to email the report.
- The first AI-buildout run fetches SEC submissions for the whole Research-sheet universe (~520) to
  read industry codes, plus facts for the themed names. On dev that took ~7 minutes with some proxy
  504s. Responses are cached in `Data\edgar_cache\` for 20 h, so later runs are quick.
- Expected: about 44 AI-buildout names and 14 robot-vision names (dev, 2026-10-05).
- If the log says `EDGAR returned 403 3 times in a row ... No more SEC requests this run`, it
  stopped on purpose and used the cache. Re-run after ~10 min.
- **Do not run** `scripts\backtesting\theme_signal_study.py` on PROD. It fetches facts for every
  company, and its cache reached 1.7 GB on dev. The daily scan doesn't need it.

## 4. Add the 25 new symbols

The universe has one source: the **Research sheet**. The root `state_of_the_day.xlsx` is the
source list, and a full run rebuilds `Data\state_of_the_day.xlsx` from it, which the game reads.

1. Back up the root workbook first (the finalize backup only copies the `Data\` one):
   `copy state_of_the_day.xlsx Data\Backup\%TS%\state_of_the_day_root.xlsx`
2. In the root `state_of_the_day.xlsx`, sheet **Research**, add one row per symbol. Copy columns
   B and C from an existing row (currently `14` and `14`; I don't know what they encode), put the
   symbol in **D** and the industry in **E**. Leave the rest empty; the pipeline fills it.
3. Close Excel before the next pipeline run.

```
SEI BE SMR TT APH HUBB NVT POWL PWR EME FIX STRL PRIM MLM VMC OUST AEVA MVIS ARBE CGNX AMBA LSCC INDI CEVA AUR
```

| Theme | Bucket | Symbols |
|---|---|---|
| AI buildout | power | SEI, BE, SMR |
| | cooling | TT |
| | electrical | APH, HUBB, NVT, POWL |
| | construction | PWR, EME, FIX, STRL, PRIM, MLM, VMC |
| Robot vision | eyes | OUST, AEVA, MVIS, ARBE, CGNX |
| | chips | AMBA, LSCC, INDI, CEVA |
| | software | AUR |

Notes:
- **ARBE is optional.** It's a foreign filer; its latest SEC quarter is 2025-12, so its figures show
  "old" and don't score.
- These names **will also become game candidates** once they're on the Research sheet, like any
  other symbol. That's a trading-universe change, not just a watchlist change.
- `Data\symbols_to_check.txt` is no longer read by the watchlist. `main.py`'s `check_from_file`
  still writes the daily `symbols_to_check_<date>.csv` from it (used by backtests). Add the 25 there
  too if you want those CSVs to include them.
- Price history: I don't know whether the pipeline downloads OHLCV for a new symbol automatically.
  After the next run, check `dir Data\Symbol_full\SEI_daily.json`. Until it exists, the RS/CMF
  columns for new names stay empty.

## 5. Verify

- Web: Research > **Theme Watch**. The dropdown switches between the two themes, and the line
  under the title shows the scan date and count.
- API (from the PROD box):
  `curl -s "http://127.0.0.1:8888/api/ai_buildout?theme=robot_vision"` returns JSON with `rows`.
- A scan saved before this deploy (none on PROD) would lack the `rpo_suspect` flag. Any fresh scan
  has it.

## 6. Optional: schedule it

Nothing schedules the scan. Run it by hand when you want it, or ask for a scheduled task. It should
run outside the 05:00–07:59 and 13:00–17:59 busy windows, and check the pipeline locks first.

## 7. Rollback

```
git revert -m 1 d26c377
python server.py restart
```

Optionally delete `Data\ai_buildout_watch.*`, `Data\robot_vision_watch.*` and `Data\edgar_cache\`.
No scores or game state depend on them. If you added the 25 symbols, restore the root workbook from
the step 4 backup.

## 8. Suggestions (follow-ups, not deploy steps)

- **One symbol list.** The watchlist reads the Research sheet. `Data\symbols_to_check.txt` is still read
  by `main.py` (`check_from_file`, which writes the daily CSVs for backtests), `database.py` and
  `scripts\diagnostics\compare_target.py`. Moving those to the Research sheet too would retire the
  `.txt` for good.
- **Schedule the scan** (section 6) once the first manual runs look right.
- **Price history for new names.** After the next full run, check `dir Data\Symbol_full\SEI_daily.json`.
  If it's missing, the new names need an OHLCV fetch before their RS/CMF columns fill in.
- **Re-run the R&D #51 study** (on dev, not PROD) once split-adjusted bars exist and the new names have
  OHLCV. Robot vision can't be tested until at least 5 of its names have price data.
- **Weigh the universe change.** The 25 names become game candidates, not just watchlist rows. If you
  only want to watch them, keep them off the Research sheet: all 25 are seed names, so both themes
  still scan them. The trade-off is that they likely get no OHLCV, so their RS/CMF columns stay empty.
