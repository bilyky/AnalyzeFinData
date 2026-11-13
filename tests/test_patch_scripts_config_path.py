"""The PROD config patch scripts edit the file the code reads: the repo-root config.json.

They used to default to Data\\config.json, which aether/config.py never loads (PROD review 2026-11-13).
"""
import os
import re
import runpy
import unittest
from pathlib import Path

import aether.config as cfg

_NOTES = Path(__file__).resolve().parent.parent / "docs" / "prod-notes"


class PatchScriptsConfigPathTest(unittest.TestCase):
    def test_every_patch_script_defaults_to_the_loaded_config(self):
        scripts = sorted(p for p in _NOTES.glob("patch_*.py")
                         if "DEFAULT_CONFIG" in p.read_text(encoding="utf-8")
                         or 'default=os.path.join("Data"' in p.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(scripts), 3)
        for script in scripts:
            with self.subTest(script=script.name):
                g = runpy.run_path(str(script), run_name="not_main")
                # The harness redirects cfg._CFG_PATH to a temp file, so compare with the path
                # aether/config.py builds: config.json next to the aether package.
                loaded = Path(cfg.__file__).resolve().parent.parent / "config.json"
                self.assertEqual(os.path.normcase(os.path.abspath(g["DEFAULT_CONFIG"])),
                                 os.path.normcase(str(loaded)))

    def test_no_prod_note_script_defaults_to_data_config(self):
        for script in _NOTES.glob("*.py"):
            with self.subTest(script=script.name):
                self.assertNotIn('os.path.join("Data", "config.json")', script.read_text(encoding="utf-8"))


    def test_live_prod_notes_name_the_root_config(self):
        r"""A note that tells PROD to edit Data\config.json edits a file the code never reads. Historical
        notes (10-08 ARCHIVE copies, the 10-12 note that explains the two files, and the 11-13 note that
        removes the stray copy) may still name it."""
        allowed = ("ARCHIVE", "2026-10-08-prod-audit-actions", "2026-10-12-ai-tiers-192-enable",
                   "2026-11-13-prod-config-root-only")
        stray = re.compile(r"Data[\\/]config\.json")
        for note in _NOTES.glob("2026-*.md"):
            if any(a in note.name for a in allowed):
                continue
            # Data\position_watch\config.json is a different, private file.
            text = re.sub(r"Data[\\/]position_watch[\\/]config\.json", "", note.read_text(encoding="utf-8"))
            with self.subTest(note=note.name):
                self.assertIsNone(stray.search(text), stray.search(text) and text[stray.search(text).start() - 60:][:120])

if __name__ == "__main__":
    unittest.main()
