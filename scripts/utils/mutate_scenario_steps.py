"""Mutation check for the scenario stages in aether/scenario/steps.py.

Applies each mutant (one small, deliberate bug) to steps.py in turn, runs the stage
test modules in a child process, and reports which tests caught it. Two outputs:

* every mutant that SURVIVED (no test failed) — a real coverage gap; and
* every stage test that never failed under any mutant — a candidate for removal
  (it may still pin behaviour no mutant here targets, so read it before deleting).

steps.py is restored byte-for-byte after every mutant (and on any error), and the
script refuses to start if steps.py has uncommitted changes. Exit code 1 if any
mutant survived or an anchor no longer matches, so it can gate a cleanup PR.

Usage (from the repo root):  python scripts/utils/mutate_scenario_steps.py
Used for PR #150 (93 -> 86 stage tests, 60/61 mutants caught at the time).
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEPS = ROOT / "aether" / "scenario" / "steps.py"
MODULES = ["tests.test_scenario_steps", "tests.test_scenario_assemble", "tests.test_scenario_settle",
           "tests.test_scenario_queued", "tests.test_scenario_exits",
           "tests.test_scenario_sell_exec", "tests.test_scenario_buy_screen"]

# (label, exact text in steps.py, replacement). Each anchor must occur exactly once.
MUTANTS = [
    # B5 determine_profile / gate
    ("gate ratio 0.40->0.50", 'cash_ratio > 0.40', 'cash_ratio > 0.50'),
    ("gate min_score 9.5->9.0", 'min_score=9.5', 'min_score=9.0'),
    ("gate equity>1.0 -> >0", 'if equity > 1.0 else 0', 'if equity > 0 else 0'),
    ("manual mode MANUAL->ADAPTIVE", 'state["profile_mode"] = "MANUAL"', 'state["profile_mode"] = "ADAPTIVE"'),
    # assemble
    ("assemble: drop SPY", '+ ["SPY"]', '+ []'),
    ("assemble: skip heal", 'game._heal_symbol_cache(_s)', 'pass'),
    ("assemble: scarcity overwrite", 'and "is_scarcity" not in state["positions"][sym]', ''),
    # settle
    ("settle: no RuntimeError", 'raise RuntimeError("Critical Data Failure', 'return prices or ("Critical Data Failure'),
    ("settle: skip circuit breaker", 'game.circuit_breaker.enforce_circuit_breaker(state, prices)', 'pass'),
    ("settle: skip options settle", 'game.options.resolve_expiring_options(state, today, prices)', 'pass'),
    ("settle: equity not rounded", 'round(game._live_equity(state["balance"], state["positions"], prices), 0)',
     'game._live_equity(state["balance"], state["positions"], prices)'),
    # queued
    ("queued: no reset", '    state["queued_orders"] = []\n\n\ndef decide_exits', '    pass\n\n\ndef decide_exits'),
    ("queued: sell no unwind", 'game.options.unwind_option_liability_if_held(sym, pos, state, price, today)\n            state["positions"].pop(sym)\n            proceeds',
     'state["positions"].pop(sym)\n            proceeds'),
    ("queued: no freshness skip", 'refusing to derive an ATR stop from untrustworthy data.")\n                    continue',
     'refusing to derive an ATR stop from untrustworthy data.")'),
    ("queued: 8% -> 10% fallback", 'stop_loss = round(price * 0.92, 2)', 'stop_loss = round(price * 0.90, 2)'),
    ("queued: balance>500 -> >0", 'if state["balance"] > 500 and available_slots > 0:', 'if state["balance"] > 0 and available_slots > 0:'),
    ("queued: dna s10<->l60", 'q_s10 = float(r_row[24] or 0.0)', 'q_s10 = float(r_row[25] or 0.0)'),
    # exits
    ("exits: row[8]->row[10]", 'prev_close = float(row[8]', 'prev_close = float(row[10]'),
    ("exits: ratchet 1.0x->0.5x", '> (1.0 * atr):', '> (0.5 * atr):'),
    ("exits: breakeven 1.5x->2.0x", '> (1.5 * atr):', '> (2.0 * atr):'),
    ("exits: keep-1-share off", 'sell_qty = max(0, min(sell_qty, pos["qty"] - 1))', 'sell_qty = max(0, min(sell_qty, pos["qty"]))'),
    ("exits: gap frozen keeps stop", 'stop_loss=None if is_gap_frozen else pos.get("stop_loss")', 'stop_loss=pos.get("stop_loss")'),
    ("exits: HOLD verdict ignored", 'if v in ("FLAG-FOR-REVIEW", "HOLD"):\n                    ai_override = True\n                    override_verdict = v\n                    override_reason = f"Real-time',
     'if v in ("FLAG-FOR-REVIEW",):\n                    ai_override = True\n                    override_verdict = v\n                    override_reason = f"Real-time'),
    ("exits: no dedup of queued sell", 'if not any(q["symbol"] == sym and q["type"] == "SELL" for q in state.get("queued_orders", [])):', 'if True:'),
]

MUTANTS += [  # round 2: targets behaviours round 1 never touched
    ("gate: always upgrade", 'if profile == "DEFENSIVE" and cash_ratio > 0.40', 'if True or profile == "DEFENSIVE" and cash_ratio > 0.40'),
    ("manual-adaptive applies gate", 'state["profile_mode"] = "ADAPTIVE"\n        game._log.info(f"🤖 AI ACTIVE STRATEGY: {profile} (Adaptive pilot restored)")',
     'state["profile_mode"] = "ADAPTIVE"\n        profile = _apply_cash_deployment_gate(state, profile)\n        game._log.info(f"🤖 AI ACTIVE STRATEGY: {profile} (Adaptive pilot restored)")'),
    ("expired manual stays MANUAL", 'game._log.console("  [AETHER] Manual override expired. Automatically resetting back to Adaptive autopilot...")\n        state["profile_mode"] = "ADAPTIVE"',
     'game._log.console("  [AETHER] Manual override expired. Automatically resetting back to Adaptive autopilot...")'),
    ("heal: drop max_stale_days", 'stale_syms = [s for s in symbols_to_check if game._cache_stale(s, max_stale_days=game._MAX_STALE_DAYS)]', 'stale_syms = [s for s in symbols_to_check if game._cache_stale(s)]'),
    ("heal: heal everything", 'if stale_syms:\n        game._log.info(f"[Pre-flight]', 'stale_syms = symbols_to_check\n    if stale_syms:\n        game._log.info(f"[Pre-flight]'),
    ("scarcity: no industry coerce", 'industry_str = row[4] or ""', 'industry_str = row[4]'),
    ("scarcity: classify non-held", 'if sym in state["positions"] and "is_scarcity"', 'if sym and "is_scarcity"'),
    ("settle: no missing warn", 'if missing_prices:\n        game._log.console', 'if False:\n        game._log.console'),
    ("settle: <=0 not missing", 'or not prices[sym] or prices[sym] <= 0]', 'or not prices[sym]]'),
    ("settle: fetch only positions", 'prices = game.get_live_prices(all_syms)', 'prices = game.get_live_prices(symbols_to_check)'),
    ("queued: sell skips stop_loss", '"stop_loss": pos.get("stop_loss"),\n            }', '}'),
    ("queued: slots ignored", 'if state["balance"] > 500 and available_slots > 0:', 'if state["balance"] > 500:'),
    ("queued: qty0 still fills", '                if qty > 0:\n                    cost = qty * price', '                if True:\n                    cost = qty * price'),
    ("queued: buy even if held", 'elif order["type"] == "BUY" and sym not in state["positions"]:', 'elif order["type"] == "BUY":'),
    ("queued: sell even if not held", 'if order["type"] == "SELL" and sym in state["positions"]:', 'if order["type"] == "SELL":'),
    ("queued: no heal before skip", 'game._heal_symbol_cache(sym)\n                if game._cache_stale', 'if game._cache_stale'),
    ("queued: dna default Neutral->None", 'q_pgr = "Neutral"', 'q_pgr = None'),
    ("queued: price<=0 not skipped", 'if price <= 0:\n            continue', 'if price < 0:\n            continue'),
    ("exits: FLAG->HOLD", 'new_action = "WATCH"', 'new_action = "HOLD"'),
    ("exits: stored key ignored", 'for key in ["shadow_verdict", "ai_verdict", "verdict"]:', 'for key in []:'),
    ("exits: stored verdicts ignored", 'pos_verdicts = pos.get("verdicts", {})', 'pos_verdicts = {}'),
    ("exits: stored before realtime", '# 1. Check real-time shadow verdicts generated in entry\n            realtime_verdicts = entry.get("verdicts", {})', '# 1. Check real-time shadow verdicts generated in entry\n            realtime_verdicts = {}'),
    ("exits: scarcity 1.5->1.6", 'multiplier = 1.5 if is_scarcity', 'multiplier = 1.6 if is_scarcity'),
    ("exits: default mult 2.5->3", 'rules.get("atr_multiplier", 2.5)', 'rules.get("atr_multiplier", 3.0)'),
    ("exits: ratchet can lower", 'if recalculated_stop > old_stop:', 'if True:'),
    ("exits: no highest tracking", 'pos["highest_close_since_acq"] = highest_close', 'pass'),
    ("exits: prev_close fallback 0", 'prev_close = pos.get("cost", 0.0)\n        for row', 'prev_close = 0.0\n        for row'),
    ("exits: except fallback 0", '            except Exception:\n                    prev_close = pos.get("cost", 0.0)', '            except Exception:\n                    prev_close = 0.0'),
    ("exits: price fallback 0", 'price = prices.get(sym, pos.get("cost"))', 'price = prices.get(sym, 0.0)'),
    ("exits: sma always read", 'sma50 = game._sma50(sym) if game.sell_rules.soft_exit(s10, l60) else None', 'sma50 = game._sma50(sym)'),
    ("exits: tech-exit default", 'exit_reason = entry["rules_reason"] or "Technical exit"', 'exit_reason = entry["rules_reason"]'),
    ("exits: no log_decisions", '    if decision_entries:\n        game.decision_eval.log_decisions(decision_entries)', '    pass'),
    ("exits: review not logged", 'game._log.info(f"🌸 AI HOLD (winner-protected)', 'game._log.debug(f"🌸 AI HOLD (winner-protected)'),
    ("exits: scale-out no tx", '                    state["history"].append(tx)\n                    new_transactions.append(tx)\n                    game._log.info(f"🏦', '                    game._log.info(f"🏦'),
    ("exits: scale-out after hours", 'and pos.get("qty", 0) > 1 and game.is_market_hours():', 'and pos.get("qty", 0) > 1:'),
    ("exits: conviction log off", 'if so_frac <= 0 and so_reason.startswith("held: high-conviction"):', 'if False:'),
    ("exits: original-lot sizing off", 'original_qty = pos["qty"] / (1.0 - banked_pct)', 'original_qty = pos["qty"]'),
]

MUTANTS += [  # execute_exits (SELL execution loop)
    ("sell: price fallback 0", 'price = prices.get(sym, pos["cost"])', 'price = prices.get(sym, 0.0)'),
    ("sell: no call unwind", 'pos["cost"])\n        game.options.unwind_option_liability_if_held(sym, pos, state, price, today)',
     'pos["cost"])\n        pass'),
    ("sell: position kept", '        state["positions"].pop(sym)\n\n        # Slippage', '        pass\n\n        # Slippage'),
    ("sell: stp <= -> <", 'stop_fill = stop_loss > 0.0 and price <= stop_loss', 'stop_fill = stop_loss > 0.0 and price < stop_loss'),
    ("sell: stp ignores zero stop", 'stop_fill = stop_loss > 0.0 and price', 'stop_fill = stop_loss >= 0.0 and price'),
    ("sell: stp fills at market", '            price = stop_loss\n\n        proceeds', '            pass\n\n        proceeds'),
    ("sell: no credit", '        proceeds = pos["qty"] * price\n        state["balance"] += proceeds',
     '        proceeds = pos["qty"] * price\n        state["balance"] += 0'),
    ("sell: no stp tag", '(" [STP LMT fill]" if stop_fill else "")', '""'),
    ("sell: tx drops stop_loss", '"stop_loss": pos.get("stop_loss")}', '"stop_loss": None}'),
    ("sell: no history", '        state["history"].append(tx)\n        new_transactions.append(tx)\n        game._log.info(f"🤖 AI LIVE SELL', '        new_transactions.append(tx)\n        game._log.info(f"🤖 AI LIVE SELL'),
    ("sell: no dna", '(Time: {now_time})")\n        game.log_closed_trade_dna(sym, pos, price, today)',
     '(Time: {now_time})")\n        pass'),
]

MUTANTS += [  # screen_buys (BUY screening)
    ("buy: cash buffer off", 'min_cash_required = state["equity"] * rules["cash_buffer_pct"]', 'min_cash_required = 0.0'),
    ("buy: slots ignore held", '    available_slots = max_positions - len(state["positions"])\n\n    # Enforce',
     '    available_slots = max_positions\n\n    # Enforce'),
    ("buy: expansion ratio 1.0->0", 'max_positions = game.determine_max_positions(cash_ratio',
     'max_positions = game.determine_max_positions(1.0'),
    ("buy: held score not kept", 'active_position_scores[sym] = total_score', 'pass'),
    ("buy: exclusion off", '        if game.instruments.is_excluded(sym):\n            continue', '        if False:\n            continue'),
    ("buy: floor cash_pct x10", '* 100.0 if state.get("equity", 0) > 0 else 0.0', '* 10.0 if state.get("equity", 0) > 0 else 0.0'),
    ("buy: floor < -> <=", 'if short10 < required_floor:', 'if short10 <= required_floor:'),
    ("buy: setup gate off", "confirmed bottom\n        if (setup in ('1', 'OK', 1)) and price > 0:",
     "confirmed bottom\n        if price > 0:"),
    ("buy: legacy setup dropped", "confirmed bottom\n        if (setup in ('1', 'OK', 1)) and price > 0:",
     "confirmed bottom\n        if (setup in ('OK',)) and price > 0:"),
    ("buy: no heal", '_healed = game._heal_symbol_cache(sym)', '_healed = False'),
    ("buy: freshness reject off", 'levels from untrustworthy data.")\n                    continue',
     'levels from untrustworthy data.")\n                    pass'),
    ("buy: gap prev row[10]->row[8]", 'prev_close = row[10]', 'prev_close = row[8]'),
    ("buy: gap 8%->10%", 'if gap_pct <= -0.08 and not bottom_ok:', 'if gap_pct <= -0.10 and not bottom_ok:'),
    ("buy: gap 8%->5%", 'if gap_pct <= -0.08 and not bottom_ok:', 'if gap_pct <= -0.05 and not bottom_ok:'),
    ("buy: gap zero prev not guarded", 'if prev_close and prev_close > 0:', 'if prev_close is not None:'),
    ("buy: gap ignores bottom", 'if gap_pct <= -0.08 and not bottom_ok:', 'if gap_pct <= -0.08:'),
    ("buy: elite waiver off", 'is_elite_breakout = game.risk_utils.is_elite_breakout_candidate(total_score, short10)',
     'is_elite_breakout = False'),
    ("buy: rr gate on partial S/R", 'if stop_val > 0 and target_val > 0:', 'if stop_val > 0 or target_val > 0:'),
    ("buy: rr < -> <=", 'if rr_ratio < min_rr:', 'if rr_ratio <= min_rr:'),
    ("buy: rr reject off", 'Downside: ${round(downside, 2)}).")\n                        continue',
     'Downside: ${round(downside, 2)}).")\n                        pass'),
    ("buy: target gain 5->4", 'if target_gain_pct < 5.0:', 'if target_gain_pct < 4.0:'),
    ("buy: target reject off", '(Upside: ${round(upside, 2)}).")\n                        continue',
     '(Upside: ${round(upside, 2)}).")\n                        pass'),
    ("buy: threshold >= -> >", 'if total_score >= rules["min_score_threshold"] or bottom_ok:',
     'if total_score > rules["min_score_threshold"] or bottom_ok:'),
    ("buy: bottom ignored", 'if total_score >= rules["min_score_threshold"] or bottom_ok:',
     'if total_score >= rules["min_score_threshold"]:'),
    ("buy: pgr no default", '"pgr": row[6] or "Neutral",', '"pgr": row[6],'),
    ("buy: penalty 1.5->1.0", 'buy_cand["total"] -= 1.5', 'buy_cand["total"] -= 1.0'),
    ("buy: penalty and->or", '(strength_count < 1 and timing_count < 1)', '(strength_count < 1 or timing_count < 1)'),
    ("buy: weak ignored", ' or industry_rating == "Weak":', ':'),
    ("buy: no sort", 'top_buys.sort(key=lambda x: x["total"], reverse=True)', 'pass'),
]


# Runs in a child process so each mutant gets a fresh import of steps.py.
_RUNNER = r"""
import io, json, sys, unittest
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
failed = sorted({t.id().split(" (")[0] for t, _ in res.failures + res.errors})
sys.stdout.write(json.dumps({"failed": failed, "run": res.testsRun}) + "\n")
"""


def _run_tests():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", _RUNNER, *MODULES], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"test runner produced no result:\n{out.stderr[-2000:]}")
    return json.loads(lines[-1])


def _all_test_ids():
    sys.path.insert(0, str(ROOT))
    def walk(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                yield from walk(t)
            else:
                yield t.id()
    return sorted(walk(unittest.defaultTestLoader.loadTestsFromNames(MODULES)))


def _steps_is_clean():
    res = subprocess.run(["git", "diff", "--quiet", "--", str(STEPS)], cwd=ROOT)
    return res.returncode == 0


def _is_main_checkout():
    """True in the repo's main working tree (where the live app runs), False in a worktree.

    A linked worktree has its own git dir under <common>/worktrees/<name>; the main
    checkout's git dir IS the common dir.
    """
    def rev_parse(flag):
        out = subprocess.run(["git", "rev-parse", flag], cwd=ROOT, capture_output=True, text=True)
        return os.path.normcase(os.path.realpath(os.path.join(ROOT, out.stdout.strip())))
    return rev_parse("--git-dir") == rev_parse("--git-common-dir")


def main(argv=None):
    # Parse first: --help or a mistyped flag must exit before anything is mutated (an ignored
    # unknown argument used to start a full run that rewrites steps.py).
    ap = argparse.ArgumentParser(description="Mutation check for aether/scenario/steps.py "
                                 "(rewrites steps.py while it runs; restores it byte for byte).")
    ap.add_argument("--allow-main-checkout", action="store_true",
                    help="run in the main checkout (only if no live process imports steps.py)")
    args = ap.parse_args(argv)
    # The script rewrites aether/scenario/steps.py with deliberate bugs while it runs.
    # Once the REPLACE phase wires steps.py into the live game, doing that in the main
    # checkout would put buggy code under live processes, so it only runs in a worktree.
    if _is_main_checkout() and not args.allow_main_checkout:
        sys.stderr.write("Refusing to run in the main checkout: this script temporarily rewrites "
                         "aether/scenario/steps.py. Run it in a git worktree "
                         "(or pass --allow-main-checkout if no live process imports it).\n")
        return 2
    if not _steps_is_clean():
        sys.stderr.write("steps.py has uncommitted changes; commit or revert them first.\n")
        return 2
    original = STEPS.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    text = original.decode("utf-8")

    baseline = _run_tests()
    if baseline["failed"]:
        sys.stderr.write(f"baseline is not green: {baseline['failed']}\n")
        return 2

    caught_by = {}
    survived, broken = [], []
    try:
        for label, old, new in MUTANTS:
            if text.count(old) != 1:
                broken.append(label)
                sys.stdout.write(f"ANCHOR   {label}  (matches {text.count(old)}x — update the mutant)\n")
                continue
            STEPS.write_bytes(text.replace(old, new).encode("utf-8"))
            failed = _run_tests()["failed"]
            for test_id in failed:
                caught_by.setdefault(test_id, []).append(label)
            if failed:
                sys.stdout.write(f"caught   {label}  ({len(failed)} tests)\n")
            else:
                survived.append(label)
                sys.stdout.write(f"SURVIVED {label}\n")
    finally:
        STEPS.write_bytes(original)
    if hashlib.sha256(STEPS.read_bytes()).hexdigest() != digest:
        raise RuntimeError("steps.py was not restored byte-for-byte")

    never = [t for t in _all_test_ids() if t not in caught_by]
    sys.stdout.write(f"\n{len(MUTANTS) - len(survived) - len(broken)}/{len(MUTANTS)} mutants caught; "
                     f"{len(survived)} survived; {len(broken)} stale anchors.\n")
    sys.stdout.write(f"{len(never)} stage tests never failed under any mutant:\n")
    for t in never:
        sys.stdout.write(f"  {t}\n")
    return 1 if survived or broken else 0


if __name__ == "__main__":
    sys.exit(main())
