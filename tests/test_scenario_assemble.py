"""B6 tests for aether.scenario.steps.assemble_symbol_universe.

``assemble_symbol_universe`` is the second stateful stage lifted out of
``run_daily_ai_management`` — the block between the profile decision and the live
price fetch. It builds the day's symbol universe and pre-heals stale caches. These
tests pin the four behaviours it carries over from the root, unchanged:

1. **Positions list + universe assembly** — ``symbols_to_check`` = held keys, and
   ``all_syms`` = the deduped union held ∪ active-setup ∪ queued ∪ {SPY}, with SPY
   always present for the circuit breaker.
2. **Pre-flight OHLCV heal** — only positions whose cache is stale are healed, and
   the freshness horizon ``_MAX_STALE_DAYS`` is forwarded to ``_cache_stale``
   verbatim. No stale positions ⇒ no heal call.
3. **Legacy-position scarcity heal** — a held position lacking ``is_scarcity`` is
   stamped from ``instruments.is_scarcity_asset``; one that already has the key is
   left untouched (and never re-classified); a Research-sheet symbol that is not a
   held position is ignored.
4. **Call-time seam** (``_pkg()``): a ``mock.patch.object(game, ...)`` still
   intercepts through the function, so the pinned patch is the one used.

Every collaborator (``_cache_stale``, ``_heal_symbol_cache``, ``_MAX_STALE_DAYS``,
``_active_setup_symbols``, ``instruments.is_scarcity_asset``, ``_log``) is patched
on the live module, and ``ws`` is a hermetic fake — no openpyxl, network, or disk.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import assemble_symbol_universe as assemble_reexport  # noqa: E402
from aether.scenario.steps import assemble_symbol_universe  # noqa: E402


class _FakeWS:
    """Minimal stand-in for an openpyxl worksheet: ``iter_rows`` yields tuples.

    Returns a fresh list each call so the scarcity-classification loop and (were
    it not patched) ``_active_setup_symbols`` would each get an un-consumed scan.
    """

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=2, values_only=True):
        return list(self._rows)


def _row(sym, industry=""):
    """A Research-sheet row tuple with symbol at index 3 and industry at index 4."""
    r = [None] * 26
    r[3] = sym
    r[4] = industry
    return tuple(r)


_SENTINEL_STALE_DAYS = 4242  # a value nothing else could produce, to prove forwarding


def _patch(*, stale=None, active=None, is_scarcity=None):
    """Patch the collaborators assemble_symbol_universe resolves via _pkg().

    ``stale`` maps symbol -> bool for ``_cache_stale``; ``active`` is the
    ``_active_setup_symbols`` return; ``is_scarcity`` maps symbol -> bool for
    ``instruments.is_scarcity_asset``. Returns the patch context managers plus the
    heal/scarcity mocks so callers can assert on them.
    """
    stale = stale or {}
    active = active or []
    is_scarcity = is_scarcity or {}
    m_heal = mock.MagicMock()
    m_scar = mock.MagicMock(side_effect=lambda sym, industry: is_scarcity.get(sym, False))
    ctxs = [
        mock.patch.object(game, "_cache_stale",
                          side_effect=lambda s, max_stale_days=None: stale.get(s, False)),
        mock.patch.object(game, "_MAX_STALE_DAYS", _SENTINEL_STALE_DAYS),
        mock.patch.object(game, "_heal_symbol_cache", m_heal),
        mock.patch.object(game, "_active_setup_symbols", return_value=list(active)),
        mock.patch.object(game.instruments, "is_scarcity_asset", m_scar),
        mock.patch.object(game, "_log", mock.MagicMock()),
    ]
    return ctxs, m_heal, m_scar


def _run(state, ws, **kw):
    ctxs, m_heal, m_scar = _patch(**kw)
    with ctxs[0] as m_stale, ctxs[1], ctxs[2], ctxs[3] as m_active, ctxs[4], ctxs[5]:
        result = assemble_symbol_universe(state, ws)
    return result, m_stale, m_heal, m_scar, m_active


class TestUniverseAssembly(unittest.TestCase):
    def test_symbols_to_check_are_position_keys(self):
        state = {"positions": {"AAA": {}, "BBB": {}}}
        result, *_ = _run(state, _FakeWS([]))
        self.assertEqual(sorted(result["symbols_to_check"]), ["AAA", "BBB"])

    def test_all_syms_is_dedup_union_with_spy(self):
        state = {
            "positions": {"AAA": {}, "BBB": {}},
            "queued_orders": [{"symbol": "DDD"}],
        }
        # research overlaps BBB and adds CCC.
        result, *_ = _run(state, _FakeWS([]), active=["BBB", "CCC"])
        self.assertEqual(sorted(result["all_syms"]), ["AAA", "BBB", "CCC", "DDD", "SPY"])
        self.assertEqual(sorted(result["research_symbols"]), ["BBB", "CCC"])
        self.assertEqual(result["queued_syms"], ["DDD"])

    def test_spy_always_present_even_when_empty(self):
        state = {"positions": {}}
        result, *_ = _run(state, _FakeWS([]))
        self.assertEqual(result["all_syms"], ["SPY"])

    def test_queued_orders_missing_defaults_empty(self):
        state = {"positions": {"AAA": {}}}  # no queued_orders key
        result, *_ = _run(state, _FakeWS([]))
        self.assertEqual(result["queued_syms"], [])
        self.assertEqual(sorted(result["all_syms"]), ["AAA", "SPY"])

    def test_return_keys(self):
        state = {"positions": {}}
        result, *_ = _run(state, _FakeWS([]))
        self.assertEqual(
            set(result),
            {"symbols_to_check", "research_symbols", "queued_syms", "all_syms"},
        )


class TestPreflightHeal(unittest.TestCase):
    def test_only_stale_positions_are_healed(self):
        state = {"positions": {"AAA": {}, "BBB": {}, "CCC": {}}}
        result, m_stale, m_heal, _, _ = _run(
            state, _FakeWS([]), stale={"AAA": True, "CCC": True})
        healed = [c.args[0] for c in m_heal.call_args_list]
        self.assertEqual(sorted(healed), ["AAA", "CCC"])

    def test_no_heal_when_nothing_stale(self):
        state = {"positions": {"AAA": {}, "BBB": {}}}
        result, _, m_heal, _, _ = _run(state, _FakeWS([]))
        m_heal.assert_not_called()

    def test_max_stale_days_is_forwarded(self):
        # The freshness horizon must reach _cache_stale as max_stale_days, verbatim.
        state = {"positions": {"AAA": {}}}
        result, m_stale, _, _, _ = _run(state, _FakeWS([]))
        _, kwargs = m_stale.call_args
        self.assertEqual(kwargs.get("max_stale_days"), _SENTINEL_STALE_DAYS)


class TestScarcityHeal(unittest.TestCase):
    def test_missing_is_scarcity_is_stamped(self):
        state = {"positions": {"AAA": {}}}
        ws = _FakeWS([_row("AAA", "Semiconductors")])
        _run(state, ws, is_scarcity={"AAA": True})
        self.assertTrue(state["positions"]["AAA"]["is_scarcity"])

    def test_existing_is_scarcity_untouched_and_not_reclassified(self):
        state = {"positions": {"BBB": {"is_scarcity": True}}}
        ws = _FakeWS([_row("BBB", "Finance")])
        # is_scarcity_asset would return False, but the existing True must survive.
        _, _, _, m_scar, _ = _run(state, ws, is_scarcity={"BBB": False})
        self.assertTrue(state["positions"]["BBB"]["is_scarcity"])
        m_scar.assert_not_called()  # already classified ⇒ classifier never consulted

    def test_research_symbol_not_held_is_ignored(self):
        state = {"positions": {"AAA": {}}}
        ws = _FakeWS([_row("AAA", "Tech"), _row("ZZZ", "Energy")])
        _run(state, ws, is_scarcity={"AAA": False, "ZZZ": True})
        self.assertIn("AAA", state["positions"])
        self.assertNotIn("ZZZ", state["positions"])

    def test_industry_none_coerced_to_empty_string(self):
        # row[4] is None → the root passes "" to the classifier, never None.
        state = {"positions": {"AAA": {}}}
        row = list(_row("AAA"))
        row[4] = None
        ws = _FakeWS([tuple(row)])
        _, _, _, m_scar, _ = _run(state, ws, is_scarcity={"AAA": True})
        m_scar.assert_called_once_with("AAA", "")


class TestSeamAndReexport(unittest.TestCase):
    def test_active_setup_symbols_resolved_at_call_time(self):
        # The whole point of _pkg(): the patch applied now is the one used.
        state = {"positions": {}}
        result, _, _, _, m_active = _run(state, _FakeWS([]), active=["SENTINEL"])
        self.assertIn("SENTINEL", result["all_syms"])
        m_active.assert_called_once()

    def test_reexported_from_package_root(self):
        self.assertIs(assemble_reexport, assemble_symbol_universe)


if __name__ == "__main__":
    unittest.main()
