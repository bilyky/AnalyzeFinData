"""Every R&D item in plans/roadmap.md has its own number.

Code, docs and PRs cite items as "R&D #N", so two items sharing a number make those
citations ambiguous. Four collisions slipped in unnoticed until #153 renumbered them:
#44 (scenario refactor vs the log.out() writer, now #50) and #35/#36/#37 (stability
items vs the Defensive Overlay Rules C/B/A, now #47/#48/#49).

The check itself is pre_commit_validator.roadmap_numbering_issues(), so it runs at commit
time and in CI's quality-gate job (which runs the validator but not this suite). These
tests pin it, and the file imports only the standard library plus the validator, so it can
also run on its own: python tests/test_roadmap_numbering.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "utils"))
import pre_commit_validator as pcv  # noqa: E402


def _roadmap(*items, filler=12):
    """A roadmap text with the given item lines plus `filler` unique items #100, #101, ..."""
    rest = [f"{100 + i}. **Filler item (R&D #{100 + i}):** text" for i in range(filler)]
    return "\n".join(["# Roadmap", "", *items, *rest])


class TestRealRoadmap(unittest.TestCase):
    def test_current_roadmap_has_no_issues(self):
        with open(pcv.ROADMAP_PATH, encoding="utf-8") as f:
            self.assertEqual(pcv.roadmap_numbering_issues(f.read()), [])

    def test_validator_check_passes_on_current_roadmap(self):
        self.assertTrue(pcv.check_rd_item_numbers())


class TestRoadmapNumberingIssues(unittest.TestCase):
    def test_duplicate_number_is_reported(self):
        issues = pcv.roadmap_numbering_issues(_roadmap("44. **Scenario refactor (R&D #44):** a",
                                                       "44. **log.out writer (R&D #44):** b"))
        self.assertEqual(len(issues), 1)
        self.assertIn("duplicate R&D item number(s) #44", issues[0])

    def test_number_disagreeing_with_its_own_label_is_reported(self):
        issues = pcv.roadmap_numbering_issues(_roadmap("47. **Rule C (R&D #35):** a"))
        self.assertEqual(len(issues), 1)
        self.assertIn("item 47 is labeled R&D #35", issues[0])

    def test_cross_reference_after_the_title_is_not_read_as_the_label(self):
        line = "52. **Foo (R&D #52):** builds on (R&D #44) and R&D #19"
        self.assertEqual(pcv.roadmap_numbering_issues(_roadmap(line)), [])

    def test_cross_reference_in_an_unlabeled_item_is_not_read_as_its_label(self):
        line = "54. **Unlabeled title:** follows up (R&D #44) from the refactor"
        self.assertEqual(pcv.roadmap_numbering_issues(_roadmap(line)), [])

    def test_unlabeled_item_is_fine(self):
        self.assertEqual(pcv.roadmap_numbering_issues(_roadmap("53. **No label here:** text")), [])

    def test_indented_sub_lists_are_not_items(self):
        sub = "    1.  *The Failure-Type Classifier:* text"
        self.assertEqual(pcv.roadmap_numbering_issues(_roadmap(sub, sub)), [])

    def test_too_few_items_is_reported_so_a_format_change_cannot_pass_silently(self):
        issues = pcv.roadmap_numbering_issues("# Roadmap\n\n1. *Item without bold* text\n")
        self.assertEqual(len(issues), 1)
        self.assertIn("only 0 numbered R&D items found", issues[0])


if __name__ == "__main__":
    unittest.main()
