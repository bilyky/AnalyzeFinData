"""Development > Project Model (R&D #75): the progress file and the live code metrics.

The progress file (plans/aether-services-progress.json) is edited by hand in every R&D #75 PR,
so its shape is pinned here: a typo in a status or a missing field should fail a test, not
render a broken page. The metrics are measured from the source tree; they are checked on a
small synthetic tree (exact counts) and on the real repo (sane values).
"""
import json
import tempfile
import unittest
from pathlib import Path

from aether import dev_progress

_REPO = Path(__file__).resolve().parent.parent


def _write(root: Path, rel: str, text: str):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestProgressFile(unittest.TestCase):
    def setUp(self):
        self.progress = dev_progress.load_progress(_REPO)

    def test_every_item_has_an_allowed_status(self):
        allowed = set(self.progress["statuses"])
        for group in ("steps", "fixes", "services"):
            for item in self.progress[group]:
                with self.subTest(group=group, item=item.get("id")):
                    self.assertIn(item["status"], allowed)

    def test_ids_are_unique_and_fields_present(self):
        for group, fields in (("steps", ("id", "title", "status", "pr", "prod")),
                              ("fixes", ("id", "title", "trading", "evidence", "status", "pr")),
                              ("services", ("id", "title", "owns", "status", "step"))):
            ids = [item["id"] for item in self.progress[group]]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate id in {group}")
            for item in self.progress[group]:
                with self.subTest(group=group, item=item["id"]):
                    for field in fields:
                        self.assertIn(field, item)

    def test_pr_numbers_are_ints_or_null(self):
        for item in self.progress["steps"] + self.progress["fixes"]:
            with self.subTest(item=item["id"]):
                self.assertTrue(item["pr"] is None or (isinstance(item["pr"], int) and item["pr"] > 0))

    def test_each_service_names_an_existing_step(self):
        steps = {s["id"] for s in self.progress["steps"]}
        for service in self.progress["services"]:
            with self.subTest(service=service["id"]):
                self.assertIn(service["step"], steps)

    def test_a_done_step_has_its_pr(self):
        for item in self.progress["steps"] + self.progress["fixes"]:
            if item["status"] in ("done", "in_review"):
                with self.subTest(item=item["id"]):
                    self.assertIsNotNone(item["pr"])


class TestCodeMetrics(unittest.TestCase):
    def test_counts_on_a_synthetic_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "ai_portfolio_game.py", "a = row[3]\nb = 1\n")
            _write(root, "aether/scenario/steps.py",
                   "def _pkg():\n    pass\n\ndef s():\n    game = _pkg()\n    game.x(row[24], row[25])\n    game.y\n")
            _write(root, "aether/other.py", "def _pkg():\n    pass\n")
            m = dev_progress.code_metrics(root)
        self.assertEqual(m, {"root_file_lines": 2, "scenario_lines": 7, "services_lines": 0,
                             "game_attr_calls": 2, "magic_row_indexes": 3, "pkg_shims": 1})

    def test_missing_folders_count_as_zero(self):
        with tempfile.TemporaryDirectory() as d:
            m = dev_progress.code_metrics(Path(d))
        self.assertEqual(set(m.values()), {0})

    def test_real_repo_values_are_sane(self):
        m = dev_progress.code_metrics(_REPO)
        self.assertGreater(m["root_file_lines"], 0)
        self.assertTrue(all(isinstance(v, int) and v >= 0 for v in m.values()), m)


class TestProjectModel(unittest.TestCase):
    def test_model_has_progress_summary_and_metrics(self):
        model = dev_progress.project_model(_REPO)
        self.assertEqual(model["project"], dev_progress.load_progress(_REPO)["project"])
        self.assertEqual(sum(model["summary"]["steps"].values()), len(model["steps"]))
        self.assertEqual(sum(model["summary"]["fixes"].values()), len(model["fixes"]))
        self.assertIn("root_file_lines", model["metrics"])
        json.dumps(model)  # the endpoint returns it as JSON

    def test_missing_progress_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                dev_progress.project_model(Path(d))


if __name__ == "__main__":
    unittest.main()
