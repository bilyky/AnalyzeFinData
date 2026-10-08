import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

import openpyxl

import notify
from aether import oceanview_context, paths
import powergauge
import rapidapi
import watchdog
from config import CFG


# --- LOGGING CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
_log = logging.getLogger("aether.daily_task")
_log.setLevel(logging.INFO)

# Avoid duplicate handlers if imported
if not _log.handlers:
    fh = logging.FileHandler(Path(__file__).resolve().parent / "daily_task.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _log.addHandler(fh)
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(ch)

STEP_TIMEOUT_S = 600          # run_command default (run_history.py, main.py)
UNBOUNDED_STEPS_SLACK_S = 600  # lint, data load, report build + email: no timeout of their own
CONTEXT_PACK_SLACK_S = 300     # OceanView pack build: a few broker reads + local files
PACK_MAX_STALE_H = 24          # weekday: a broker snapshot older than this -> health "failed"
PACK_MAX_STALE_WEEKEND_H = 72  # Sat/Sun: no fresh token, Friday's snapshot is expected


def worst_case_runtime_seconds():
    """Longest daily_task.main() can legitimately run: every bounded step at its timeout
    (run_history + main.py, the backup sync, the recovery pass) plus slack for the steps
    that have none. The Evening task's scheduler limit must be at least this."""
    return (2 * STEP_TIMEOUT_S + watchdog.SYNC_TIMEOUT_S + rapidapi.pass_timeout_seconds()
            + UNBOUNDED_STEPS_SLACK_S + CONTEXT_PACK_SLACK_S)


def pack_max_stale_hours(today=None):
    """How old the cached broker snapshot may be before the pack calls itself "failed".

    No E*TRADE token is minted on weekends, so Saturday/Sunday builds fall back to Friday's
    snapshot (~24-48 h old): allowing 72 h there keeps a normal weekend from alerting."""
    today = today or datetime.date.today()
    return PACK_MAX_STALE_WEEKEND_H if today.weekday() >= 5 else PACK_MAX_STALE_H


def build_context_pack():
    """OceanView Context Pack — rebuild it and record its health for the watchdog gate.

    Ban-safe: the broker read inside goes through etrade.keep_alive (renew-only, never a
    browser). The pack writes its cache (Data/oceanview_context.json) on a live success and
    its verdict (Data/oceanview_context_status.json) on every build; watchdog alerts when the
    verdict is "failed" or goes stale. Non-fatal by design: the report has already gone out.

    Runs BEFORE the RapidAPI recovery pass: the broker read needs a same-day ET token, and
    the token's day ends at midnight ET (21:00 PT), while the pass can run for hours (PROD
    2026-10-07: 17:15 -> 18:56 PT; worst case ~5 h). Building first keeps the live read well
    inside the token's day; the trade-off is that data health reflects the bars before
    tonight's repair."""
    _log.info("Building the OceanView context pack...")
    try:
        meta = oceanview_context.build_oceanview_context(
            live=True, max_stale_hours=pack_max_stale_hours())["meta"]
        _log.info(f"OceanView context pack: health={meta['health']} source={meta['source']} "
                  f"warnings={len(meta['warnings'])}")
    except Exception as e:
        _log.warning(f"Warning: OceanView context pack build failed (non-fatal): {e}")


def run_ohlcv_recovery():
    """OHLCV recovery pass — repair missing/stale/placeholder Symbol_full bars via RapidAPI.

    Runs LAST in main() (after the report and the backup sync, even if the report failed)
    because a full pass is long: its timeout covers the whole per-run fetch budget
    (rapidapi.pass_timeout_seconds), since the old 600 s default killed every pass after
    ~42 of ~500 symbols (PROD logs 2026-09-16..30). Running it first would push the
    evening report back by the length of the pass. Non-fatal by design."""
    _log.info("Running OHLCV recovery pass (rapidapi.py)...")
    try:
        run_command([sys.executable, "rapidapi.py"], timeout=rapidapi.pass_timeout_seconds())
    except Exception as e:
        _log.warning(f"Warning: OHLCV recovery failed (non-fatal): {e}")


def run_command(command_list, timeout=STEP_TIMEOUT_S):
    _log.info(f"Running: {' '.join(command_list)}")
    # Use Path(__file__) for the script if it's a local script
    if len(command_list) > 1 and command_list[1].endswith(".py"):
        command_list[1] = str(Path(__file__).resolve().parent / command_list[1])
    
    try:
        result = subprocess.run(command_list, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            _log.info(f"Error (exit code {result.returncode}): {result.stderr}")
        else:
            _log.info("Command completed successfully.")
        return result.stdout
    except subprocess.TimeoutExpired:
        _log.warning(f"🚨 Command timed out after {timeout}s: {' '.join(command_list)}")
        return ""

def get_symbols_from_xls():
    wb = None
    try:
        # We read from the ROOT folder file
        src_path = BASE_DIR / "state_of_the_day.xlsx"
        wb = openpyxl.load_workbook(src_path, data_only=True, read_only=True)
        ws = wb['Research']
        symbols = []
        for row in ws.iter_rows(min_row=2):
            val = row[3].value
            if val:
                symbols.append(str(val))
        return symbols
    except Exception as e:
        _log.error(f"Error reading symbols from XLS: {e}")
        return []
    finally:
        if wb:
            try:
                wb.close()
            except Exception:
                pass

def get_all_data(date):
    # Dynamic import to use existing logic
    sys.path.insert(0, str(BASE_DIR))

    powergauge._build_cache_index()

    # Get symbols from XLS to ensure we only analyze "Research" symbols
    research_symbols = get_symbols_from_xls()
    if not research_symbols:
        _log.info("Warning: No symbols found in Research sheet. Falling back to all cached symbols.")
        symbol_dir = Path(paths.symbol_dir())
        cached_symbols = []
        if symbol_dir.exists():
            for _root, _dirs, files in os.walk(symbol_dir):
                for f in files:
                    if f.endswith(f"_{date}.json"):
                        cached_symbols.append(f.rsplit('_', 1)[0])
        research_symbols = list(set(cached_symbols))

    results = []
    for symbol in research_symbols:
        try:
            pg = powergauge.get_symbol_data(symbol, date, True, "dummy")
            if pg.price == -1:
                continue

            ohlcv_path = Path(paths.ohlcv_dir()) / f"{symbol}_daily.json"
            ohlcv_ts = None
            if ohlcv_path.exists():
                with open(ohlcv_path) as _f:
                    ohlcv_ts = json.load(_f).get('Time Series (Daily)')

            f = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
            pgr_val = pg.pgr_corrected_value if pg.pgr_corrected_value != 0 else pg.pgr_value

            results.append({
                'symbol': symbol,
                's10': f['short_score'],
                'l60': f['long_score'],
                'br': f['buying_ratio'],
                'pgr': f['pgr'],
                'pgr_val': pgr_val,
                'setup': f['setup_ok']
            })
        except Exception:
            continue

    return results


def get_description(r):
    desc = []
    if r['s10'] >= 5:
        desc.append(f"Strong entry (S10: {r['s10']})")
    elif r['s10'] > 0:
        desc.append(f"Positive entry (S10: {r['s10']})")
    if r['br'] >= 2.5:
        desc.append(f"Massive pressure (BR: {r['br']})")
    elif r['br'] >= 1.5:
        desc.append(f"Accumulation (BR: {r['br']})")
    if r['l60'] >= 3:
        desc.append(f"Strong trend (L60: {r['l60']})")
    elif r['l60'] > 0:
        desc.append(f"Healthy trend (L60: {r['l60']})")
    if r['pgr'] == 'Bu+':
        desc.append("Very Bullish")
    return "; ".join(desc) if desc else "Solid technicals"


def _sync_warning(sync_ok, today, error=None):
    """Build the post-run data-sync (warning_html, subject_line) for the daily email.

    watchdog.sync_data_folder() returns a bool: it is False only on a genuine sync
    failure (robocopy rc>=8, missing source, or an exception it caught internally).
    True covers both success and the intentional "offline but not a failure" bypass,
    so only False (or a raised `error`) raises the warning banner. `error` is set when
    the sync call itself raised out to the caller.
    """
    ok_subject = f"AETHER Daily Rotation & Momentum Report: {today}"
    fail_subject = f"⚠️ AETHER Daily Rotation & Momentum Report: {today} (Sync Failed)"

    if error is not None:
        html = (
            "<hr style='border: 0; border-top: 1px solid #e1e4e8; margin: 20px 0;'>"
            "<h4 style='color: #e74c3c; margin: 0 0 10px 0;'>⚠️ WARNING: Post-Run Data Synchronization Error!</h4>"
            f"<p style='color: #7f8c8d; font-size: 13px; margin: 0;'>The data synchronization engine threw an exception: {error}</p>"
        )
        return html, fail_subject

    if sync_ok is False:
        html = (
            "<hr style='border: 0; border-top: 1px solid #e1e4e8; margin: 20px 0;'>"
            "<h4 style='color: #e74c3c; margin: 0 0 10px 0;'>⚠️ WARNING: Post-Run Data Synchronization Failed!</h4>"
            "<p style='color: #7f8c8d; font-size: 13px; margin: 0;'>The backup drive or UNC storage path \\\\10.0.0.156\\Storage\\ was unreachable or disconnected during this run. "
            "All local trade entries completed nominal, but Data caches were not mirrored to the network storage drive. "
            "Please check your backup drive connectivity.</p>"
        )
        return html, fail_subject

    return "", ok_subject


def main():
    # Ensure we are in the script's directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description="AETHER Daily Task Automation & Reporter.")
    parser.add_argument("--report-only", action="store_true", help="Generate and send HTML report from cached data immediately, skipping data fetching.")
    parser.add_argument("--cached", action="store_true", help="Alias for --report-only.")
    args = parser.parse_args()
    
    report_only = args.report_only or args.cached

    today = datetime.date.today()
    _log.info(f"Starting daily automation for {today}")

    # ── Configuration Placeholder Audit ──
    if getattr(CFG, "has_placeholders", False):
        _log.warning("🚨 [Config Alert] Active placeholders detected! Dispatching configuration health warning email...")
        details_str = "\n".join(f"- {ph}" for ph in CFG.placeholder_details)
        msg = f"AETHER Configuration Health Alert!\n\nActive placeholders were detected in your configuration:\n\n{details_str}\n\nPlease update your config.json or environment variables immediately to resolve this."
        try:
            notify.send_email("ALERT: AETHER Configuration Placeholders Detected", msg)
        except Exception as e:
            _log.error(f"Failed to send configuration health alert email: {e}")

    try:
        if not report_only:
            # 0. Self-Validation (Linting)
            # We run ruff to catch errors like NameError before we even start
            _log.info("Running self-validation (linting)...")
            # We use --select E to focus on basic syntax and logic errors
            ruff_cmd = [sys.executable, "-m", "ruff"]
            venv_new_python = os.path.join("venv_new", "Scripts", "python.exe")
            venv_python = os.path.join("venv", "Scripts", "python.exe")
            
            if not os.path.exists(venv_new_python) and os.path.exists(venv_python):
                venv_new_python = venv_python

            try:
                # Check if the primary interpreter has ruff available
                try:
                    test_res = subprocess.run(ruff_cmd + ["--version"], capture_output=True)
                    if test_res.returncode != 0:
                        raise FileNotFoundError("Ruff not found in current python executable")
                except Exception:
                    # If primary interpreter fails, fall back to virtual environment Python
                    if os.path.exists(venv_new_python):
                        ruff_cmd = [venv_new_python, "-m", "ruff"]

                lint_result = subprocess.run(ruff_cmd + ["check", "--select", "F,E9,F63,F7,F82", "daily_task.py"], capture_output=True, text=True)
                if lint_result.returncode != 0:
                    raise RuntimeError(f"Self-validation failed:\n{lint_result.stdout}\n{lint_result.stderr}")
                _log.info("Self-validation passed.")
            except RuntimeError:
                raise
            except Exception as e:
                _log.info(f"Warning: Self-validation skipped (ruff package not available): {e}")

            # 1. Sync last 5 days history
            run_command([sys.executable, "run_history.py", "5"])

            # 2. Run main script to populate Data and Excel
            run_command([sys.executable, "main.py"])
            # (The OHLCV recovery pass runs LAST — see run_ohlcv_recovery in the finally below.)

        # 3. Get all processed data
        all_symbols_data = get_all_data(today)
        if not all_symbols_data:
            _log.info("Error: No symbol data retrieved for report.")
            return

        # 4. Generate Tables (Same logic as Excel Picks sheet)
        p_map = {'Bu+': 5, 'Bu': 4, 'N': 3, 'Be': 2, 'Be-': 1}
        bullish = [r for r in all_symbols_data if p_map.get(r['pgr'], 0) >= 4 and r['setup'] == 1]
        if not bullish:
            bullish = [r for r in all_symbols_data if p_map.get(r['pgr'], 0) >= 4]

        bearish = [r for r in all_symbols_data if p_map.get(r['pgr'], 0) < 4]
        if not bearish:
            bearish = all_symbols_data

        top_combined = sorted(bullish, key=lambda x: x['s10'] + x['br'], reverse=True)[:5]
        top_s10 = sorted(bullish, key=lambda x: x['s10'], reverse=True)[:5]
        top_l60 = sorted(bullish, key=lambda x: x['l60'], reverse=True)[:5]

        # Aggressive (Unfiltered)
        agg_s10 = sorted(all_symbols_data, key=lambda x: x['s10'], reverse=True)[:5]
        agg_l60 = sorted(all_symbols_data, key=lambda x: x['l60'], reverse=True)[:5]

        top_sell = sorted(all_symbols_data, key=lambda x: x['s10'], reverse=False)[:5]

        # 5. Format and send HTML email
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: sans-serif; color: #333; }}
                table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
                th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #ddd; font-size: 12px; }}
                th {{ background-color: #2E4057; color: white; }}
                .buy {{ background-color: #E2EFDA; }}
                .agg {{ background-color: #EBF5FB; }}
                .sell {{ background-color: #FCE4D6; }}
                .symbol {{ font-weight: bold; }}
                .score {{ font-weight: bold; color: #27ae60; }}
                .pgr-high {{ color: #27ae60; font-weight: bold; }}
                .pgr-low {{ color: #c0392b; font-weight: bold; }}
                h2 {{ color: #2c3e50; border-bottom: 3px solid #2E4057; padding-bottom: 5px; }}
                h3 {{ color: #2c3e50; background: #eee; padding: 5px 10px; border-left: 5px solid #2E4057; }}
            </style>
        </head>
        <body>
            <h2>Daily Trade Report: {today}</h2>
            
            <h3>SECTION 1: CONSERVATIVE (Bullish + Setup OK)</h3>
            <p><i>Confirmed quality setups with fundamental and technical alignment.</i></p>

            <b>TOP 5 BUY -- Combined (S10 + BR)</b>
            <table>
                <thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>Total</th><th>Rationale</th></tr></thead>
                <tbody>
        """

        def add_rows(rows, row_class, use_combined=False):
            res = ""
            for i, r in enumerate(rows, 1):
                pgr_val = p_map.get(r['pgr'], 0)
                pgr_style = "pgr-high" if pgr_val >= 4 else ("pgr-low" if pgr_val <= 2 else "")
                score = round(r['s10'] + r['br'], 1)
                res += f"""
                    <tr class="{row_class}">
                        <td>{i}</td>
                        <td class="symbol">{r['symbol']}</td>
                        <td class="{pgr_style}">{r['pgr']}</td>
                        <td>{r['s10']:.1f}</td>
                        <td>{r['br']:.1f}</td>
                        {f'<td class="score">{score}</td>' if use_combined else f'<td>{r["l60"]:.1f}</td>'}
                        <td><small>{get_description(r)}</small></td>
                    </tr>
                """
            return res

        html += add_rows(top_combined, "buy", use_combined=True)
        html += "</tbody></table>"

        html += "<b>TOP 5 BUY -- Short10 (Safe 10)</b>"
        html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
        html += add_rows(top_s10, "buy")
        html += "</tbody></table>"

        html += "<b>TOP 5 BUY -- Long60 (Safe 60)</b>"
        html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
        html += add_rows(top_l60, "buy")
        html += "</tbody></table>"

        html += "<h3>SECTION 2: AGGRESSIVE (Raw Momentum - Unfiltered)</h3>"
        html += "<p><i>Highest technical scores regardless of PGR rating. Watch for reversals.</i></p>"

        html += "<b>TOP 5 MOMENTUM -- Raw Short10</b>"
        html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
        html += add_rows(agg_s10, "agg")
        html += "</tbody></table>"

        html += "<b>TOP 5 MOMENTUM -- Raw Long60</b>"
        html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
        html += add_rows(agg_l60, "agg")
        html += "</tbody></table>"

        html += "<h3>SECTION 3: WEAKNESS</h3>"
        html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
        html += add_rows(top_sell, "sell")
        html += "</tbody></table>"

        html += """
            <p><small>S10: 10-day entry score | BR: Buying Ratio | L60: 60-day position score</small></p>
        </body>
        </html>
        """

        _log.info("Running post-run Data synchronization...")
        sync_warning_html = ""
        subject_line = f"AETHER Daily Rotation & Momentum Report: {today}"
        
        try:
            # We execute the data sync BEFORE the email is sent so we can append any failures transparently!
            sync_ok = watchdog.sync_data_folder()
            sync_warning_html, subject_line = _sync_warning(sync_ok, today)
        except Exception as sync_err:
            _log.warning(f"Post-run sync failed: {sync_err}")
            sync_warning_html, subject_line = _sync_warning(None, today, error=sync_err)

        if sync_warning_html:
            html = html.replace("</body>", f"{sync_warning_html}</body>")

        _log.info("Generating rich HTML report...")
        notify.send_email(subject_line, html, is_html=True)
        _log.info("Automation completed successfully.")

    except Exception as e:
        error_msg = f"Automation Failed for {today}\n\nError: {str(e)}\n\nFull Traceback:\n{traceback.format_exc()}"
        _log.info(f"FATAL ERROR: {error_msg}")
        try:
            notify.send_email(f"ALERT: Daily Trade Report Failed ({today})", error_msg)
            _log.info("Error alert email sent.")
        except Exception as notify_err:
            _log.info(f"Could not send error alert email: {notify_err}")
    finally:
        if not report_only:
            build_context_pack()       # before the long repair: needs the same-day ET token
            run_ohlcv_recovery()


if __name__ == "__main__":
    main()
