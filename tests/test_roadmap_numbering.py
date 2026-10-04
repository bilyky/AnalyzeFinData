"""Every R&D item in plans/roadmap.md has its own number.

Code, docs and PRs cite items as "R&D #N", so two items sharing a number make those
citations ambiguous. Four collisions slipped in unnoticed until #153 renumbered them:
#44 (scenario refactor vs the log.out() writer, now #50) and #35/#36/#37 (stability
items vs the Defensive Overlay Rules C/B/A, now #47/#48/#49). This test fails on any
duplicate, and on an item whose leading number disagrees with its own "(R&D #N ...)"
label, so a new collision is caught when it is introduced.
"""
import os
import re
import unittest
from collections import Counter
from pathlib import Path

_ROADMAP = Path(os.path.dirname(__file__)).parent / "plans" / "roadmap.md"
_ITEM = re.compile(r"^(\d+)\. \*\*")
_LABEL = re.compile(r"\(R&D #(\d+)")


def _items():
    out = []
    for line in _ROADMAP.read_text(encoding="utf-8").splitlines():
        m = _ITEM.match(line)
        if m:
            label = _LABEL.search(line)
            out.append((int(m.group(1)), int(label.group(1)) if label else None, line[:80]))
    return out


class TestRoadmapNumbering(unittest.TestCase):
    def test_no_duplicate_item_numbers(self):
        counts = Counter(n for n, _, _ in _items())
        dupes = {n for n, c in counts.items() if c > 1}
        self.assertEqual(dupes, set(),
                         "duplicate R&D item number(s): give the newer item the next free number")

    def test_leading_number_matches_its_own_label(self):
        bad = [line for n, label, line in _items() if label is not None and label != n]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
