"""
Tests for aether_logger — three-channel rotating logger.
Uses a temp directory so no real log files are written during tests.
"""
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestLoggerInit(unittest.TestCase):
    def setUp(self):
        # Patch the log dir so tests don't write to Data/logs/
        self.tmp = tempfile.TemporaryDirectory()
        import aether_logger
        self._orig_log_dir = aether_logger._LOG_DIR
        aether_logger._LOG_DIR = Path(self.tmp.name)
        aether_logger._initialised = False
        # Remove any existing handlers on the root aether logger
        root = logging.getLogger("aether")
        root.handlers.clear()

    def tearDown(self):
        import aether_logger
        aether_logger._LOG_DIR = self._orig_log_dir
        aether_logger._initialised = False
        root = logging.getLogger("aether")
        for h in list(root.handlers):
            h.close()
        root.handlers.clear()
        self.tmp.cleanup()

    def test_three_handlers_created(self):
        import aether_logger
        aether_logger._init()
        root = logging.getLogger("aether")
        handler_types = {type(h).__name__ for h in root.handlers}
        self.assertIn("RotatingFileHandler", handler_types)
        self.assertIn("StreamHandler", handler_types)
        # Two rotating files (txt + jsonl) = two RotatingFileHandler instances
        rotating = [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertEqual(len(rotating), 2)

    def test_get_logger_prefixes_name(self):
        import aether_logger
        log = aether_logger.get_logger("server")
        self.assertEqual(log.name, "aether.server")

    def test_get_logger_aether_prefix_unchanged(self):
        import aether_logger
        log = aether_logger.get_logger("aether.pipeline")
        self.assertEqual(log.name, "aether.pipeline")

    def test_jsonl_output_is_valid_json(self):
        import aether_logger, logging.handlers
        aether_logger._init()
        log = aether_logger.get_logger("test")
        log.info("hello world", extra={"sym": "INTC"})
        jsonl_path = Path(self.tmp.name) / "aether.jsonl"
        self.assertTrue(jsonl_path.exists())
        line = jsonl_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        obj = json.loads(line)
        self.assertEqual(obj["level"], "INFO")
        self.assertIn("hello world", obj["msg"])
        self.assertEqual(obj["extra"]["sym"], "INTC")

    def test_txt_log_written(self):
        import aether_logger
        aether_logger._init()
        log = aether_logger.get_logger("test2")
        log.warning("test warning")
        txt_path = Path(self.tmp.name) / "aether.log"
        self.assertTrue(txt_path.exists())
        content = txt_path.read_text(encoding="utf-8")
        self.assertIn("test warning", content)
        self.assertIn("WARNING", content)

    def test_double_init_is_idempotent(self):
        import aether_logger
        aether_logger._init()
        aether_logger._init()
        root = logging.getLogger("aether")
        self.assertEqual(len([h for h in root.handlers
                               if isinstance(h, logging.handlers.RotatingFileHandler)]), 2)

    def test_log_level_env_var_respected(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "WARNING"}):
            import aether_logger
            aether_logger._init()
            root = logging.getLogger("aether")
            stream = next(h for h in root.handlers if isinstance(h, logging.StreamHandler)
                          and not isinstance(h, logging.handlers.RotatingFileHandler))
            self.assertEqual(stream.level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
