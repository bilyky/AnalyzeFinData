"""Project-wide soft-delete "garbage can" (aether.trash) — the single deletion API.

Red-green anchor: ``soft_delete`` / ``purge_trash`` are the whole contract — on code
that still calls ``os.remove`` directly they don't exist, so this module fails to
import (the "red"). Guarantees under test:
  * default = MOVE to Data/.trash (recoverable), content intact, never a hard delete;
  * ``force=True`` = real hard delete (the explicit opt-out the interface provides);
  * every discarded copy kept distinct (nothing silently overwritten);
  * missing source is a no-op; and only ``purge_trash`` past its retention window
    truly removes a file, keeping anything still inside the window.
``TRASH_DIR`` is isolated to a tempdir, so the real Data/.trash is never touched.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aether import trash


class _TrashTmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(trash, "TRASH_DIR", os.path.join(self._tmp.name, ".trash"))
        p.start()
        self.addCleanup(p.stop)

    def _write(self, name, content="x"):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w") as f:
            f.write(content)
        return path


class TestSoftDelete(_TrashTmp):
    def test_soft_delete_moves_and_preserves_content(self):
        src = self._write("etrade_tokens.json", '{"oauth_token": "keepme"}')
        dest = trash.soft_delete(src, reason="rejected-401")
        self.assertFalse(os.path.exists(src))          # gone from the live path
        self.assertIsNotNone(dest)
        self.assertTrue(os.path.exists(dest))          # recoverable in the trash
        self.assertIn("rejected-401", os.path.basename(dest))
        with open(dest) as f:
            self.assertIn("keepme", f.read())          # content intact — not a hard delete

    def test_force_is_a_real_hard_delete(self):
        src = self._write("some.lock")
        out = trash.soft_delete(src, reason="lock", force=True)
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(src))          # actually gone
        # nothing was moved to the trash — force bypasses the garbage can entirely
        self.assertFalse(os.path.isdir(trash.TRASH_DIR) and os.listdir(trash.TRASH_DIR))

    def test_accepts_pathlike(self):
        src = Path(self._write("frompath.json"))
        dest = trash.soft_delete(src, reason="revoked")
        self.assertTrue(os.path.exists(dest))
        self.assertFalse(src.exists())

    def test_missing_source_is_noop(self):
        self.assertIsNone(trash.soft_delete(os.path.join(self._tmp.name, "nope.json")))
        self.assertIsNone(trash.soft_delete(os.path.join(self._tmp.name, "nope.json"), force=True))

    def test_keeps_every_discarded_copy_distinct(self):
        # Three deletions in the same wall-clock second must not clobber each other.
        for i in range(3):
            trash.soft_delete(self._write("etrade_tokens.json", str(i)), reason="revoked")
        self.assertEqual(len(os.listdir(trash.TRASH_DIR)), 3)


class TestRetentionPurge(_TrashTmp):
    def test_purge_removes_only_aged_out_files(self):
        os.makedirs(trash.TRASH_DIR, exist_ok=True)
        old = os.path.join(trash.TRASH_DIR, "old.json")
        new = os.path.join(trash.TRASH_DIR, "new.json")
        for p in (old, new):
            with open(p, "w") as f:
                f.write("x")
        now = time.time()
        os.utime(old, (now - 40 * 86400, now - 40 * 86400))   # 40d old > 30d retention
        os.utime(new, (now - 2 * 86400, now - 2 * 86400))     # 2d old, within retention
        purged = trash.purge_trash(retention_days=30)
        self.assertEqual(purged, 1)
        self.assertFalse(os.path.exists(old))                 # aged out → really deleted
        self.assertTrue(os.path.exists(new))                  # within window → kept

    def test_purge_no_trash_dir_is_zero(self):
        self.assertEqual(trash.purge_trash(), 0)              # nothing to do, no crash

    def test_default_retention_is_one_month(self):
        self.assertEqual(trash.RETENTION_DAYS, 30)


if __name__ == "__main__":
    unittest.main()
