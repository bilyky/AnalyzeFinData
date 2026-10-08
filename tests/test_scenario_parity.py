"""Stage <-> root parity guard for the B6 scenario stages.

During BUILD each ``aether.scenario.steps`` stage is a *copy* of a block that still
lives (and still runs) in ``ai_portfolio_game.run_daily_ai_management``. The
per-stage tests characterise each copy in isolation, so a change to the root
leaves them all green while the copy silently goes stale. That already happened
once: after #136 the root's SELL loop returned ``{sym: exit_reason}`` while
``decide_exits`` still returned a list, and the queued-SELL tx gained
``stop_loss`` in the root only, until both were re-synced by hand.

This test fails the moment a stage drifts from its root block. Both sides are
normalised through ``ast.unparse`` (drops comments and formatting), then:

* ``game.X`` -> ``X`` (the stage's ``_pkg()`` call-time lookup), and the
  ``game = _pkg()`` binding is removed;
* the stage's trailing ``return`` (its hand-off to the caller) is removed;
* an early-return guard ``if not X: return`` followed by the rest of the body is
  mapped back to the root's ``if X: <rest>`` wrapper.

The normalised stage must then appear in the root as ONE contiguous block of
lines at a fixed indent offset, so a moved, dropped, re-ordered or re-nested
statement fails. Every function in ``steps.py`` must be registered below, either
as checked or as exempt with a reason, so a new stage can't skip the guard.

Delete this file, together with the root copies, once the REPLACE phase routes
the root through the stages.
"""
import ast
import inspect
import os
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import steps  # noqa: E402


# Stages whose body must match a contiguous block of run_daily_ai_management.
_CHECKED = (
    "_apply_cash_deployment_gate",
    "assemble_symbol_universe",
    "price_and_settle",
    "execute_queued_orders",
    "decide_exits",
    "execute_exits",
)

# Functions deliberately NOT checked, with the reason.
_EXEMPT = {
    "_pkg": "the call-time import seam itself, not an extracted block",
    "determine_profile": (
        "intentionally restructured (B5 dedup): the root's two byte-identical "
        "cash-deployment gates became one _apply_cash_deployment_gate call, which "
        "IS checked above"
    ),
}


class _StripGame(ast.NodeTransformer):
    """``game.X`` -> ``X``: undo the stage's ``_pkg()`` call-time lookup."""

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "game":
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node


def _is_pkg_binding(stmt):
    return (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "game"
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "_pkg")


def _unwrap_early_return(body):
    """Map ``if not X: return`` + rest back to ``if X: rest`` (the root's shape)."""
    for i, stmt in enumerate(body):
        if (isinstance(stmt, ast.If) and not stmt.orelse
                and isinstance(stmt.test, ast.UnaryOp) and isinstance(stmt.test.op, ast.Not)
                and len(stmt.body) == 1 and isinstance(stmt.body[0], ast.Return)
                and stmt.body[0].value is None):
            rest = _unwrap_early_return(body[i + 1:])
            return body[:i] + [ast.If(test=stmt.test.operand, body=rest, orelse=[])]
    return body


def _lines(stmts):
    module = ast.fix_missing_locations(ast.Module(body=list(stmts), type_ignores=[]))
    return [ln for ln in ast.unparse(module).splitlines() if ln.strip()]


def _func_def(fn):
    return ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]


def _stage_lines(fn):
    body = list(_func_def(fn).body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring
    body = [s for s in body if not _is_pkg_binding(s)]
    if body and isinstance(body[-1], ast.Return):
        body = body[:-1]  # hand-off to the caller
    body = [_StripGame().visit(s) for s in body]
    return _lines(_unwrap_early_return(body))


def _root_lines():
    return _lines(_func_def(game.run_daily_ai_management).body)


def _find_block(needle, haystack):
    """Index where ``needle`` occurs contiguously in ``haystack`` at one indent offset."""
    first = needle[0]
    for start in range(len(haystack) - len(needle) + 1):
        cand = haystack[start]
        if not cand.endswith(first) or cand[:len(cand) - len(first)].strip():
            continue
        pad = cand[:len(cand) - len(first)]
        if all(haystack[start + k] == pad + needle[k] for k in range(len(needle))):
            return start
    return -1


def _drift_report(needle, haystack):
    """Best-effort diagnostic: how much of ``needle`` still matches from each end.

    Returns ``(prefix, suffix, first_bad)``: the longest leading and trailing runs of
    the stage found contiguously in the root, and the first line after the leading
    run. Checking both ends matters because a drift in the very first statement
    would otherwise report "0 lines match" even when nearly all of the body does.
    """
    def longest(run_of):
        for n in range(len(needle), 0, -1):
            if _find_block(run_of(n), haystack) >= 0:
                return n
        return 0
    prefix = longest(lambda n: needle[:n])
    suffix = longest(lambda n: needle[len(needle) - n:])
    first_bad = needle[prefix] if prefix < len(needle) else None
    return prefix, suffix, first_bad


class TestStageRootParity(unittest.TestCase):
    def test_every_stage_matches_a_contiguous_root_block(self):
        root = _root_lines()
        for name in _CHECKED:
            with self.subTest(stage=name):
                stage = _stage_lines(getattr(steps, name))
                self.assertTrue(stage, f"{name}: empty normalised body")
                if _find_block(stage, root) < 0:
                    prefix, suffix, bad = _drift_report(stage, root)
                    self.fail(
                        f"{name} has drifted from run_daily_ai_management: of {len(stage)} "
                        f"normalised lines, the first {prefix} and the last {suffix} still "
                        f"match the root; first differing line: {bad!r}. Re-sync the stage "
                        "with the root block.")

    def test_every_steps_function_is_registered(self):
        defined = {n for n, obj in vars(steps).items()
                   if inspect.isfunction(obj) and obj.__module__ == steps.__name__}
        registered = set(_CHECKED) | set(_EXEMPT)
        self.assertEqual(defined - registered, set(),
                         "new steps.py function(s) not registered in the parity guard")
        self.assertEqual(registered - defined, set(), "stale parity-guard registration(s)")


class TestParityHelpers(unittest.TestCase):
    """Pin the normaliser so the guard can't pass vacuously."""

    def test_detects_a_changed_statement(self):
        root = ["if a:", "    x = 1", "    y = 2"]
        self.assertEqual(_find_block(["x = 1", "y = 2"], root), 1)
        self.assertEqual(_find_block(["x = 1", "y = 3"], root), -1)

    def test_detects_a_renested_statement(self):
        root = ["for s in xs:", "    x = 1", "y = 2"]
        self.assertEqual(_find_block(["x = 1", "y = 2"], root), -1)

    def test_early_return_guard_maps_to_wrapper(self):
        body = ast.parse("if not q:\n    return\nfor o in q:\n    f(o)").body
        self.assertEqual(_lines(_unwrap_early_return(body)),
                         ["if q:", "    for o in q:", "        f(o)"])

    def test_drift_report_counts_from_both_ends(self):
        # The first line drifted but the rest still matches: a prefix-only search would
        # say 0 of 4 match; the suffix search shows 3 of 4 still do.
        root = ["a = 1", "b = 2", "c = 3", "d = 4"]
        self.assertEqual(_drift_report(["a = 0", "b = 2", "c = 3", "d = 4"], root),
                         (0, 3, "a = 0"))
        self.assertEqual(_drift_report(["a = 1", "b = 2", "x = 9", "d = 4"], root),
                         (2, 1, "x = 9"))

    def test_game_prefix_stripped(self):
        stmt = _StripGame().visit(ast.parse("game._log.info(game.CFG.x)").body[0])
        self.assertEqual(_lines([stmt]), ["_log.info(CFG.x)"])


if __name__ == "__main__":
    unittest.main()
