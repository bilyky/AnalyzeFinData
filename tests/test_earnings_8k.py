"""Earnings press-release labels (aether/earnings_8k): exhibit choice, text, parsing, the queue, one label, the run."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aether import ai_buildout, earnings_8k as k8, earnings_history, ledgers

CROX_ITEMS = [
    {"name": "0001334036-26-000058-index.html", "size": ""},
    {"name": "0001334036-26-000058.txt", "size": "900000"},
    {"name": "crox-20261029.htm", "size": "33369"},
    {"name": "croxq32026-pressrelease.htm", "size": "494994"},
    {"name": "crox-20261029_lab.xml", "size": "21880"},
]
TEXT = "Crocs reports record third quarter revenue. " * 20


def _ev(acc, filed, primary="x.htm"):
    return {"filed": filed, "first_session": filed, "accession": acc, "primary_doc": primary}


class TestPieces(unittest.TestCase):
    def test_pick_exhibit(self):
        self.assertEqual(k8.pick_exhibit(CROX_ITEMS, "crox-20261029.htm"), "croxq32026-pressrelease.htm")
        items = [{"name": "a-ex991.htm", "size": "100"}, {"name": "results-release.htm", "size": "900"}]
        self.assertEqual(k8.pick_exhibit(items, "main.htm"), "a-ex991.htm")          # ex-99 beats a name hint
        self.assertEqual(k8.pick_exhibit([{"name": "main.htm", "size": "5"}], "main.htm"), None)
        self.assertEqual(k8.pick_exhibit([], None), None)

    def test_html_to_text(self):
        t = k8.html_to_text("<html><style>x{}</style><p>Revenue&nbsp;up&amp;away</p><div>Guidance raised</div></html>")
        self.assertEqual(t.splitlines(), ["Revenue up&away", "Guidance raised"])

    def test_parse_label(self):
        self.assertEqual(k8.parse_label('{"results":"beat","guidance":"raised","tone":2}'),
                         {"results": "beat", "guidance": "raised", "tone": 2})
        self.assertEqual(k8.parse_label('```json\n{"results":"Miss","guidance":"lowered","tone":"-1"}\n```'),
                         {"results": "miss", "guidance": "lowered", "tone": -1})
        for bad in ('{"results":"great","guidance":"raised","tone":1}', '{"results":"beat","guidance":"up","tone":1}',
                    '{"results":"beat","guidance":"raised","tone":3}', "no json here", None, ""):
            with self.subTest(bad=bad):
                self.assertIsNone(k8.parse_label(bad))


class TestQueue(unittest.TestCase):
    def test_recent_first_then_backfill_and_retry_limit(self):
        hist = {"symbols": {"AAA": {"cik": 1, "events": [_ev("a1", "2026-11-20"), _ev("a2", "2026-11-24"),
                                                          _ev("a3", "2022-03-01"), _ev("a4", "2021-06-01"),
                                                          _ev("a5", "2019-01-01")]},
                            "BBB": {"cik": 2, "events": [_ev("b1", "2021-06-01"), _ev("b2", "2023-01-01")]}}}
        recs = [{"accession": "a3", "status": "ok", "prompt_version": k8.PROMPT_VERSION},
                {"accession": "b2", "status": "unparsed", "prompt_version": k8.PROMPT_VERSION}] + \
               [{"accession": "b1", "status": "fetch_failed", "prompt_version": k8.PROMPT_VERSION}] * k8.MAX_TRIES + \
               [{"accession": "a4", "status": "ok", "prompt_version": "v0"}]           # another prompt: not done
        got = [e["accession"] for _s, _c, e in k8.due(hist, recs, "2026-11-25")]
        self.assertEqual(got, ["a2", "a1", "a4", "b2"])      # recent newest first; backfill oldest first; a5 too old


class TestLabelOne(unittest.TestCase):
    def _run(self, idx=None, doc=TEXT, answer='{"results":"beat","guidance":"raised","tone":1}', skipped=None):
        idx = {"directory": {"item": CROX_ITEMS}} if idx is None else idx
        with mock.patch.object(ai_buildout, "filing_index", return_value=idx), \
             mock.patch.object(ai_buildout, "filing_document", return_value=doc):
            return k8.label_one("CROX", 1334036, _ev("0001334036-26-000058", "2026-10-29", "crox-20261029.htm"),
                                ask=lambda s, u: SimpleNamespace(text=answer, provider="groq", skipped=skipped))

    def test_ok(self):
        r = self._run()
        self.assertEqual((r["status"], r["results"], r["guidance"], r["tone"], r["provider"]), ("ok", "beat", "raised", 1, "groq"))
        self.assertEqual(r["doc"], "croxq32026-pressrelease.htm")
        self.assertEqual(r["prompt_version"], k8.PROMPT_VERSION)

    def test_failures_are_records(self):
        self.assertEqual(self._run(idx={"directory": {"item": []}})["status"], "no_exhibit")
        self.assertEqual(self._run(doc=None)["status"], "fetch_failed")
        self.assertEqual(self._run(doc="short")["status"], "no_exhibit")
        self.assertEqual(self._run(answer=None, skipped="no provider")["status"], "ai_skipped")
        self.assertEqual(self._run(answer="I think it beat")["status"], "unparsed")

    def test_text_is_cut(self):
        seen = {}
        with mock.patch.object(ai_buildout, "filing_index", return_value={"directory": {"item": CROX_ITEMS}}), \
             mock.patch.object(ai_buildout, "filing_document", return_value="x" * 50000):
            k8.label_one("CROX", 1, _ev("0001334036-26-000058", "2026-10-29"),
                         ask=lambda s, u: seen.update(u=u) or SimpleNamespace(text=None, provider=None, skipped="x"))
        self.assertLessEqual(seen["u"].count("x"), k8.TEXT_CHARS)


class TestRun(unittest.TestCase):
    def test_run_writes_records_and_stops_on_ai_skip(self):
        hist = {"symbols": {"AAA": {"cik": 1, "events": [_ev("0000000001-26-000001", "2026-11-24"),
                                                          _ev("0000000001-26-000002", "2026-11-23")]}},
                "no_cik": [], "no_reports": [], "fetch_failed": []}
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "labels.jsonl"
            with mock.patch.object(ledgers, "EARNINGS_8K_LABELS_FILE", out_file), \
                 mock.patch.object(earnings_history, "load", return_value=hist), \
                 mock.patch.object(ai_buildout, "filing_index", return_value={"directory": {"item": CROX_ITEMS}}), \
                 mock.patch.object(ai_buildout, "filing_document", return_value=TEXT):
                out = k8.run(10, today="2026-11-25",
                             ask=lambda s, u: SimpleNamespace(text=None, provider=None, skipped="no provider"))
                rows = [json.loads(x) for x in out_file.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(out["written"], 1)                 # stopped after the first AI skip
        self.assertEqual(rows[0]["status"], "ai_skipped")


class TestSecHelpers(unittest.TestCase):
    def test_accession_and_name_checks(self):
        with self.assertRaises(ValueError):
            ai_buildout.filing_index(1, "not-an-accession")
        with self.assertRaises(ValueError):
            ai_buildout.filing_document(1, "0001334036-26-000058", "../x.htm")

    def test_history_events_carry_the_accession(self):
        page = {"form": ["8-K"], "items": ["2.02"], "filingDate": ["2026-10-29"],
                "acceptanceDateTime": ["2026-10-29T11:02:04.000Z"], "accessionNumber": ["0001334036-26-000058"],
                "primaryDocument": ["crox-20261029.htm"]}
        ev = earnings_history.events_from_pages([page], ["2026-10-29"])[0]
        self.assertEqual((ev["accession"], ev["primary_doc"]), ("0001334036-26-000058", "crox-20261029.htm"))


if __name__ == "__main__":
    unittest.main()
