"""The market-data caches resolve through aether.paths.cache_dir().

A worktree reads the real caches by setting AETHER_CACHE_DIR, never through a directory
junction (removing a junctioned worktree deleted the real caches on 2026-10-06). Checked
in FRESH processes so neither the test harness nor an inherited variable can mask it:
  * unset          -> every cache constant is exactly the legacy <checkout>/Data path;
  * AETHER_CACHE_DIR -> every cache constant moves there;
  * AETHER_DATA_DIR only -> the caches do NOT move (that variable also moves the E*TRADE
    token and the trash, so it must never be the way a worktree reaches the caches).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Each consumer's cache constants, read in a fresh interpreter.
_PROBE = r"""
import json, os, sys
sys.path[:0] = [os.getcwd(), os.path.join(os.getcwd(), "scripts", "backtesting")]
import ai_portfolio_game, powergauge, rapidapi, backtest_ratings
from aether import risk_utils
print(json.dumps({
    "risk_utils.OHLCV_DIR": str(risk_utils.OHLCV_DIR),
    "rapidapi.OHLCV_DIR": str(rapidapi.OHLCV_DIR),
    "powergauge.OHLCV_DIR": str(powergauge.OHLCV_DIR),
    "ai_portfolio_game.SYMBOL_FULL_DIR": str(ai_portfolio_game.SYMBOL_FULL_DIR),
    "backtest_ratings.OHLCV_DIR": str(backtest_ratings.OHLCV_DIR),
    "backtest_ratings.SYM_DIR": str(backtest_ratings.SYM_DIR),
}))
"""


def _probe(**env_overrides):
    env = {k: v for k, v in os.environ.items() if k not in ("AETHER_CACHE_DIR", "AETHER_DATA_DIR")}
    env.update(env_overrides, PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=_REPO, env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise AssertionError(f"probe failed:\n{out.stderr[-1500:]}")
    return {k: os.path.normcase(os.path.normpath(v)) for k, v in json.loads(lines[-1]).items()}


def _expected(root):
    def norm(p):
        return os.path.normcase(os.path.normpath(p))
    full, day = norm(os.path.join(str(root), "Symbol_full")), norm(os.path.join(str(root), "Symbol"))
    return {"risk_utils.OHLCV_DIR": full, "rapidapi.OHLCV_DIR": full, "powergauge.OHLCV_DIR": full,
            "ai_portfolio_game.SYMBOL_FULL_DIR": full, "backtest_ratings.OHLCV_DIR": full,
            "backtest_ratings.SYM_DIR": day}


class TestCacheDirResolution(unittest.TestCase):
    def test_unset_is_exactly_the_legacy_checkout_path(self):
        self.assertEqual(_probe(), _expected(_REPO / "Data"))

    def test_aether_cache_dir_moves_every_cache_constant(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_probe(AETHER_CACHE_DIR=d), _expected(d))

    def test_aether_data_dir_alone_does_not_move_the_caches(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_probe(AETHER_DATA_DIR=d), _expected(_REPO / "Data"))


if __name__ == "__main__":
    unittest.main()
