"""The one JSONL writer for the ledgers (#317 review): append, trim, and an atomic trim."""
import json
import os
import tempfile
import unittest
from unittest import mock

from aether import ledgers


class TestAppendAndTrim(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "log.jsonl")

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def test_append_creates_the_folder_and_keeps_order(self):
        ledgers.append_jsonl(self.path, [{"a": 1}, {"a": 2}])
        ledgers.append_jsonl(self.path, [{"a": 3}])
        self.assertEqual([e["a"] for e in self.read()], [1, 2, 3])

    def test_trim_keeps_the_newest_lines(self):
        ledgers.append_jsonl(self.path, [{"a": i} for i in range(5)], max_lines=3)
        self.assertEqual([e["a"] for e in self.read()], [2, 3, 4])
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_a_failed_trim_leaves_the_old_file_whole(self):
        ledgers.append_jsonl(self.path, [{"a": i} for i in range(5)])
        with mock.patch.object(ledgers.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                ledgers.trim_jsonl(self.path, 2)
        self.assertEqual([e["a"] for e in self.read()], [0, 1, 2, 3, 4])
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_under_the_cap_nothing_is_rewritten(self):
        ledgers.append_jsonl(self.path, [{"a": 1}])
        with mock.patch.object(ledgers.os, "replace") as rp:
            ledgers.trim_jsonl(self.path, 10)
        rp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
