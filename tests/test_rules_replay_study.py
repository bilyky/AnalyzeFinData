"""R&D #126 rules replay: version loading, the pair rates, the comparison and the verdict."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import backtest_ratings as br  # noqa: E402
import rules_replay_study as rr  # noqa: E402


def _have_history() -> bool:
    """CI checks out a shallow clone: the old commits the replay reads are not there."""
    try:
        return all(subprocess.run(["git", "cat-file", "-e", f"{c}^{{commit}}"], cwd=rr.BASE_DIR,
                                  capture_output=True).returncode == 0 for _t, c in rr.VERSIONS)
    except OSError:
        return False


HISTORY = _have_history()


def _m(dd, cagr):
    return {"max_drawdown_pct": dd, "cagr_pct": cagr}


def _draw(**rows):
    return {"train": dict(rows), "test": dict(rows)}


class TestVersions(unittest.TestCase):
    @unittest.skipUnless(HISTORY, "needs the full git history (CI clones shallow)")
    def test_every_version_has_the_score_functions(self):
        for tag, commit in rr.VERSIONS:
            src = rr.version_source(commit)
            with self.subTest(tag=tag):
                self.assertIn("def short_score(", src)
                self.assertIn("def long_score(", src)

    def test_worker_swaps_the_score_functions(self):
        saved = br._short_score_fn, br._long_score_fn
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "v.py")
            with open(p, "w") as f:
                f.write("def short_score(f):\n    return 1.5\n\ndef long_score(f):\n    return -2.5\n")
            try:
                rr._init_worker(p, "scoring_test_v")
                self.assertEqual((br._short_score_fn({}), br._long_score_fn({})), (1.5, -2.5))
            finally:
                br._short_score_fn, br._long_score_fn = saved

    @unittest.skipUnless(HISTORY, "needs the full git history (CI clones shallow)")
    def test_old_july_formula_differs_from_today(self):
        # a Strong lt_trend / Strong industry stock with no other signal: v0721 Long60 penalised it
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "v0721.py")
            with open(p, "w", encoding="utf-8") as f:
                f.write(rr.version_source(rr.VERSIONS[0][1]))
            old = rr.load_version(p, "scoring_v0721_test")
        from aether import scoring as now
        f = {"lt_trend": "Strong", "industry_strength": "Strong"}
        self.assertLess(old.long_score(f), now.long_score(f))


class TestVerdict(unittest.TestCase):
    def test_pair_rates(self):
        draws = [_draw(a=_m(-10, 8), b=_m(-12, 6)), _draw(a=_m(-15, 5), b=_m(-12, 6)),
                 _draw(a=_m(-12, 7), b=_m(-12, 6))]
        r = rr.pair_rates(draws, "a", "b")
        self.assertEqual(r["train"], {"n": 3, "dd": 0.667, "ret": 0.667})   # ties on drawdown count as not deeper

    def test_compare(self):
        good = {"train": {"dd": 0.9, "ret": 0.85}, "test": {"dd": 0.8, "ret": 0.8}}
        self.assertEqual(rr.compare(good), "BETTER_THAN")
        self.assertEqual(rr.compare({"train": {"dd": 0.9, "ret": 0.9}, "test": {"dd": 0.9, "ret": 0.7}}), "INCONCLUSIVE")
        self.assertEqual(rr.compare({"train": {"dd": 0.1, "ret": 0.2}, "test": {"dd": 0.0, "ret": 0.1}}), "WORSE_THAN")
        self.assertEqual(rr.compare({"train": {"dd": None, "ret": 0.9}, "test": {"dd": 0.9, "ret": 0.9}}), "INCONCLUSIVE")

    def test_regression_and_culprit_step(self):
        # v1 beats v2 and now; v2 ~ now: the culprit is the v1 -> v2 step
        draws = [_draw(v1=_m(-8, 10), v2=_m(-12, 5), now=_m(-12, 5) if i % 2 else _m(-11, 6)) for i in range(10)]
        v = rr.verdict(draws, ["v1", "v2", "now"])
        self.assertEqual(v["label"], "REGRESSION_FOUND")
        self.assertEqual(v["older_better_than_now"], ["v1"])
        self.assertEqual(v["culprit_steps"], ["v1->v2"])

    def test_no_regression(self):
        draws = [_draw(v1=_m(-12, 5), now=_m(-10, 7)) for _ in range(5)]
        v = rr.verdict(draws, ["v1", "now"])
        self.assertEqual(v["label"], "NO_REGRESSION")
        self.assertEqual(v["vs_now"], {"v1": "WORSE_THAN"})


if __name__ == "__main__":
    unittest.main()
