"""Tests for scripts/diagnostics/prod_snapshot.py, the read-only PROD snapshot reader the prod-review skill uses."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
import prod_snapshot as ps  # noqa: E402

_LOG = [
    {"ts": "2026-11-05 06:00:00", "level": "WARNING", "module": "m", "msg": "old 1"},
    {"ts": "2026-11-05 07:30:00", "level": "WARNING", "module": "aether.rapidapi", "msg": "MS: fetch failed 12 of 400"},
    {"ts": "2026-11-05 08:30:00", "level": "WARNING", "module": "aether.rapidapi", "msg": "AMD: fetch failed 3 of 400"},
    {"ts": "2026-11-05 09:00:00", "level": "ERROR", "module": "aether.data_backup", "msg": "Data backup did not complete (result: timeout).\nmore"},
    {"ts": "2026-11-05 09:30:00", "level": "INFO", "module": "aether.ai_game", "msg": "[Slot Expansion] Cash 51.1% is above the 20% buffer"},
    {"ts": "2026-11-05 10:00:00", "level": "WARNING", "module": "aether.ai_client", "msg": "AI evaluation failed",
     "error": "Model ran out of tokens before answering"},
]


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = self.td.name

    def jsonl(self, rows=_LOG):
        p = os.path.join(self.dir, "aether.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
            f.write("not json\n")
        return p


class TestExtract(_Tmp):
    def _zip(self, members):
        p = os.path.join(self.dir, "logs.zip")
        with zipfile.ZipFile(p, "w") as z:
            for name, date in members:
                z.writestr(zipfile.ZipInfo(name, date_time=date), "x")
        return p

    def test_extracts_newest_first(self):
        z = self._zip([("a.log", (2026, 11, 5, 7, 0, 0)), ("agent_runs/b.log", (2026, 11, 6, 7, 3, 0))])
        rows = ps.extract(z, os.path.join(self.dir, "out"))
        self.assertEqual([r[2] for r in rows], ["agent_runs/b.log", "a.log"])
        self.assertEqual(rows[0][0], "11-06 07:03")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "out", "agent_runs", "b.log")))

    def test_refuses_a_member_outside_dest(self):
        z = self._zip([("../evil.txt", (2026, 1, 1, 0, 0, 0))])
        with self.assertRaises(SystemExit):
            ps.extract(z, os.path.join(self.dir, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "evil.txt")))

    def test_refuses_a_non_empty_dest(self):
        z = self._zip([("a.log", (2026, 1, 1, 0, 0, 0))])
        dest = os.path.join(self.dir, "out")
        os.makedirs(dest)
        open(os.path.join(dest, "keep"), "w").close()
        with self.assertRaises(SystemExit):
            ps.extract(z, dest)


class TestSchedule(_Tmp):
    def test_first_time_per_day_last_n_days(self):
        runs = os.path.join(self.dir, "agent_runs")
        os.makedirs(runs)
        with open(os.path.join(runs, "execute_trades_agent.log"), "w", encoding="utf-8") as f:
            f.write("2026-11-01 06:00:05 [INFO] x\n2026-11-01 06:30:00 [INFO] y\n"   # DST: an hour early
                    "noise line\n2026-11-02 07:00:03 [INFO] z\n[2026-11-03 07:00:01] w\n")
        out = ps.schedule(runs, days=2)
        self.assertEqual(out["execute_trades_agent.log"], [("2026-11-02", "07:00"), ("2026-11-03", "07:00")])
        self.assertEqual(ps.schedule(runs, days=5)["execute_trades_agent.log"][0], ("2026-11-01", "06:00"))


class TestErrors(_Tmp):
    def test_groups_by_normalized_message_since_ts(self):
        levels, rows = ps.errors(self.jsonl(), "2026-11-05 07:00")
        self.assertEqual(levels["INFO"], 1)
        self.assertNotIn("old N", [r[5] for r in rows])                      # before --since
        fetch = [r for r in rows if "fetch failed" in r[5]]
        self.assertEqual(len(fetch), 2)                                      # different symbols stay apart
        self.assertTrue(all("N of N" in r[5] for r in fetch))                 # digits normalized
        backup = [r for r in rows if r[1] == "ERROR"][0]
        self.assertEqual(backup[5], "Data backup did not complete (result: timeout).")   # first line only

    def test_counts_and_first_last(self):
        rows = [{"ts": f"2026-11-05 0{h}:00:00", "level": "WARNING", "module": "m", "msg": f"x {h}"} for h in (7, 8, 9)]
        _, out = ps.errors(self.jsonl(rows), "2026-11-05 07:00")
        self.assertEqual(out, [(3, "WARNING", "m", "2026-11-05 07:00:00", "2026-11-05 09:00:00", "x N")])


class TestSignatures(_Tmp):
    def test_counts_message_and_extra_fields(self):
        sig = ps.signatures(self.jsonl(), "2026-11-05 07:00", {
            "#238 slot": "is above the", "old slot": "Plentiful cash", "ran out": "ran out of tokens"})
        self.assertEqual(sig["#238 slot"], (1, "2026-11-05 09:30:00", "2026-11-05 09:30:00"))
        self.assertEqual(sig["old slot"], (0, None, None))
        self.assertEqual(sig["ran out"][0], 1)                                # found in the "error" extra


class TestConfig(_Tmp):
    def test_masks_secrets_and_reports_missing(self):
        p = os.path.join(self.dir, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"ai": {"providers": {"groq": {"model": "openai/gpt-oss-120b", "api_key_source": "env:GROQ",
                                                     "min_max_tokens": 1024, "extra_body": {"reasoning_effort": "low"}}}},
                       "email_sender": {"pass": "hunter2", "user": "someone@example.com"},
                       "oracle": {"account": 12345678}}, f)
        out = ps.config_keys(p, ["ai.providers.groq.min_max_tokens", "ai.providers.groq.model",
                                 "ai.providers.groq.extra_body", "ai.providers.groq.api_key_source",
                                 "email_sender.pass", "email_sender.user", "system.max_trade_risk_pct",
                                 "oracle.account"])
        self.assertEqual(out["ai.providers.groq.min_max_tokens"], 1024)
        self.assertEqual(out["ai.providers.groq.model"], "openai/gpt-oss-120b")
        self.assertEqual(out["ai.providers.groq.extra_body"], "<dict with 1 entries>")
        self.assertEqual(out["ai.providers.groq.api_key_source"], "<set>")
        self.assertEqual(out["email_sender.pass"], "<set>")
        self.assertEqual(out["email_sender.user"], "<set>")
        self.assertEqual(out["system.max_trade_risk_pct"], "<missing>")
        self.assertEqual(out["oracle.account"], "<set>")                       # a number under a secret key
        self.assertNotIn("hunter2", json.dumps(out))

    def test_text_is_shown_only_under_known_safe_keys(self):
        # Deny by default: a short login under "username" (not a key/pass word, no secret shape) and a
        # free-text note must not print; a model or provider type may.
        p = os.path.join(self.dir, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"etrade": {"username": "jdoe1x", "proxy": "", "login_strategy": "headless"},
                       "ai": {"primary": "groq", "providers": {"groq": {"type": "openai_compatible",
                                                                        "_note": "free tier, ask Jo first"}}}}, f)
        out = ps.config_keys(p, ["etrade.username", "etrade.proxy", "etrade.login_strategy", "ai.primary",
                                 "ai.providers.groq.type", "ai.providers.groq._note"])
        self.assertEqual(out["etrade.username"], "<set>")
        self.assertEqual(out["ai.providers.groq._note"], "<set>")
        self.assertEqual(out["etrade.proxy"], "<empty>")
        self.assertEqual(out["etrade.login_strategy"], "headless")
        self.assertEqual(out["ai.primary"], "groq")
        self.assertEqual(out["ai.providers.groq.type"], "openai_compatible")
        self.assertNotIn("jdoe1x", json.dumps(out))

    def test_a_number_under_a_user_key_is_masked(self):
        p = os.path.join(self.dir, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"broker": {"user_number": 4815162, "max_tokens": 1024}}, f)
        out = ps.config_keys(p, ["broker.user_number", "broker.max_tokens"])
        self.assertEqual(out["broker.user_number"], "<set>")
        self.assertEqual(out["broker.max_tokens"], 1024)

    def test_a_number_is_shown_only_under_a_known_safe_key(self):
        # Deny by default for numbers too: oracle.start_equity is a real account's baseline (PII per
        # aether/config.py); limits, ports and timings may print.
        p = os.path.join(self.dir, "config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"oracle": {"start_equity": 123456.78}, "web": {"port": 8888},
                       "system": {"max_trade_risk_pct": 1.5}, "ai": {"intel_deadline_sec": 30}}, f)
        out = ps.config_keys(p, ["oracle.start_equity", "web.port", "system.max_trade_risk_pct",
                                 "ai.intel_deadline_sec"])
        self.assertEqual(out, {"oracle.start_equity": "<set>", "web.port": 8888,
                               "system.max_trade_risk_pct": 1.5, "ai.intel_deadline_sec": 30})
        self.assertNotIn("123456", json.dumps(out))


class TestCli(_Tmp):
    def test_errors_command_prints_a_summary(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ps.main(["errors", self.jsonl(), "--since", "2026-11-05 07:00", "--top", "2"])
        lines = buf.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("levels since 2026-11-05 07:00:"))
        self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
