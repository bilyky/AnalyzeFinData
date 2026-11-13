"""The PROD config patch scripts edit the file the code reads: the repo-root config.json.

They used to default to Data\\config.json, which aether/config.py never loads (PROD review 2026-11-13).
"""
import importlib.util
import io
import json
import os
import re
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_the_backup_goes_to_data_backup_wherever_the_config_is(self):
        """The backup used to be written next to the config (dirname + Backup), so patching the root
        config.json would have put it in <repo>\\Backup instead of Data\\Backup (#267 PROD smoke)."""
        scripts = ("patch_groq_reasoning_2026_11_04", "patch_ai_config_175", "patch_ai_models_2026_10_20")
        repo = Path(cfg.__file__).resolve().parent.parent
        fixture = {"ai": {"providers": {"groq": {"model": "m"}, "openrouter": {"api_key_source": "env:X"}}}}
        for name in scripts:
            with self.subTest(script=name), tempfile.TemporaryDirectory() as td:
                spec = importlib.util.spec_from_file_location(f"_t_{name}", _NOTES / f"{name}.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self.assertEqual(Path(mod.BACKUP_ROOT), repo / "Data" / "Backup")
                conf = Path(td, "cfgdir", "config.json")
                conf.parent.mkdir()
                conf.write_text(json.dumps(fixture), encoding="utf-8")
                mod.BACKUP_ROOT = str(Path(td, "backroot"))           # hermetic: never the real Data\Backup
                with mock.patch.object(sys, "argv", [name, "--config", str(conf), "--apply"]), \
                     mock.patch.object(sys, "stdout", io.StringIO()):
                    self.assertEqual(mod.main(), 0)            # a refusal (SystemExit) fails the test
                self.assertFalse(Path(td, "cfgdir", "Backup").exists())
                self.assertEqual(len(list(Path(td, "backroot").glob("*/config.json"))), 1)

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
