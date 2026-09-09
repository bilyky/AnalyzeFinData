"""
Tests for aether.workbook_write.backup_xlsx — fail-soft on a locked workbook.

Regression guard for the review finding: on a persistent lock, backup_xlsx must return
None (so fix_comment_shape_ids skips comment recovery) and must NEVER return a stale
prior-run backup, which would silently restore outdated comments into the saved file.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether import workbook_write


class TestBackupXlsx(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.xlsx = os.path.join(self.root, "investment.xlsx")
        with open(self.xlsx, "wb") as f:
            f.write(b"live-workbook-bytes")
        # A pre-existing prior-run backup the stale-fallback used to return.
        self.year_dir = os.path.join(self.root, "Backup", str(__import__("datetime").date.today().year))
        os.makedirs(self.year_dir, exist_ok=True)
        self.stale = os.path.join(self.year_dir, "investment_20000101_000000.xlsx")
        with open(self.stale, "wb") as f:
            f.write(b"STALE-prior-run-bytes")

    def tearDown(self):
        self._tmp.cleanup()

    def test_success_returns_fresh_copy(self):
        with mock.patch.object(workbook_write, "_log"):
            dst = workbook_write.backup_xlsx(self.xlsx, retry_delay=0)
        self.assertIsNotNone(dst)
        self.assertTrue(os.path.exists(dst))
        with open(dst, "rb") as f:
            self.assertEqual(f.read(), b"live-workbook-bytes")
        self.assertNotEqual(dst, self.stale)

    def test_persistent_lock_returns_none_not_stale_backup(self):
        with mock.patch.object(workbook_write, "_log"), \
             mock.patch.object(workbook_write.shutil, "copy2",
                               side_effect=PermissionError("locked")) as m_copy:
            dst = workbook_write.backup_xlsx(self.xlsx, max_retries=3, retry_delay=0)
        self.assertIsNone(dst)                      # never a stale backup
        self.assertNotEqual(dst, self.stale)
        self.assertEqual(m_copy.call_count, 3)      # retried

    def test_transient_lock_then_success(self):
        real_copy = workbook_write.shutil.copy2
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 2:
                raise PermissionError("locked")
            return real_copy(src, dst)

        with mock.patch.object(workbook_write, "_log"), \
             mock.patch.object(workbook_write.shutil, "copy2", side_effect=flaky):
            dst = workbook_write.backup_xlsx(self.xlsx, max_retries=3, retry_delay=0)
        self.assertIsNotNone(dst)
        self.assertEqual(calls["n"], 2)

    def test_missing_source_returns_none(self):
        with mock.patch.object(workbook_write, "_log"):
            self.assertIsNone(workbook_write.backup_xlsx(
                os.path.join(self.root, "nope.xlsx"), retry_delay=0))


if __name__ == "__main__":
    unittest.main()
