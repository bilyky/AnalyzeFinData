"""
Tests for config.py — priority logic, null-safety, malformed JSON, missing file.
No filesystem mutation: all tests use temp files or monkeypatching.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config as config_module


def _make_cfg(raw: dict | None, env: dict | None = None):
    """Build a _Config object from a raw dict and optional env overrides."""
    original_env = os.environ.copy()
    if env:
        os.environ.update(env)
    try:
        orig_load = config_module._load_file
        config_module._load_file = lambda: raw if raw is not None else {}
        cfg = config_module._Config()
        return cfg
    finally:
        config_module._load_file = orig_load
        os.environ.clear()
        os.environ.update(original_env)


class TestConfigPriority(unittest.TestCase):
    def test_env_var_overrides_file(self):
        cfg = _make_cfg(
            {"chaikin": {"email": "file@example.com", "password": "file_pass"}},
            env={"CHAIKIN_EMAIL": "env@example.com"},
        )
        self.assertEqual(cfg.chaikin_email, "env@example.com")
        self.assertEqual(cfg.chaikin_password, "file_pass")

    def test_file_used_when_no_env_var(self):
        cfg = _make_cfg({"chaikin": {"email": "file@example.com", "password": "file_pass"}})
        self.assertEqual(cfg.chaikin_email, "file@example.com")

    def test_empty_env_var_falls_through_to_file(self):
        # Empty-string env var is falsy — file value wins (intentional `or` behavior)
        cfg = _make_cfg(
            {"chaikin": {"email": "file@example.com", "password": "p"}},
            env={"CHAIKIN_EMAIL": ""},
        )
        self.assertEqual(cfg.chaikin_email, "file@example.com")

    def test_rapidapi_key_from_file(self):
        cfg = _make_cfg({"rapidapi": {"api_key": "test_key_123"}})
        self.assertEqual(cfg.rapidapi_key, "test_key_123")

    def test_rapidapi_key_from_env(self):
        cfg = _make_cfg({"rapidapi": {"api_key": "file_key"}}, env={"RAPIDAPI_KEY": "env_key"})
        self.assertEqual(cfg.rapidapi_key, "env_key")


class TestConfigNullSafety(unittest.TestCase):
    def test_null_chaikin_does_not_crash(self):
        cfg = _make_cfg({"chaikin": None})
        self.assertEqual(cfg.chaikin_email, "")
        self.assertEqual(cfg.chaikin_password, "")

    def test_null_etrade_does_not_crash(self):
        cfg = _make_cfg({"etrade": None})
        self.assertEqual(cfg.etrade_sandbox_key, "")
        self.assertEqual(cfg.etrade_production_key, "")

    def test_null_sandbox_does_not_crash(self):
        cfg = _make_cfg({"etrade": {"sandbox": None, "production": None}})
        self.assertEqual(cfg.etrade_sandbox_key, "")
        self.assertEqual(cfg.etrade_production_secret, "")

    def test_null_rapidapi_does_not_crash(self):
        cfg = _make_cfg({"rapidapi": None})
        self.assertEqual(cfg.rapidapi_key, "")

    def test_null_web_does_not_crash(self):
        cfg = _make_cfg({"web": None})
        self.assertEqual(cfg.web_port, 8888)   # falls back to default
        self.assertEqual(cfg.web_host, "0.0.0.0")
        self.assertEqual(cfg.web_api_key, "")

    def test_empty_config_returns_empty_strings(self):
        cfg = _make_cfg({})
        self.assertEqual(cfg.chaikin_email, "")
        self.assertEqual(cfg.etrade_production_key, "")
        self.assertEqual(cfg.rapidapi_key, "")


class TestConfigWeb(unittest.TestCase):
    def test_web_defaults(self):
        cfg = _make_cfg({})
        self.assertEqual(cfg.web_port, 8888)
        self.assertEqual(cfg.web_host, "0.0.0.0")
        self.assertEqual(cfg.web_api_key, "")

    def test_web_from_file(self):
        cfg = _make_cfg({"web": {"port": 9090, "host": "127.0.0.1", "api_key": "secret"}})
        self.assertEqual(cfg.web_port, 9090)
        self.assertEqual(cfg.web_host, "127.0.0.1")
        self.assertEqual(cfg.web_api_key, "secret")

    def test_web_port_env_override(self):
        cfg = _make_cfg({"web": {"port": 9090}}, env={"WEB_PORT": "7000"})
        self.assertEqual(cfg.web_port, 7000)

    def test_web_port_is_int(self):
        cfg = _make_cfg({"web": {"port": "9090"}})   # string in JSON coerced to int
        self.assertIsInstance(cfg.web_port, int)
        self.assertEqual(cfg.web_port, 9090)


class TestConfigMissingFile(unittest.TestCase):
    def test_missing_file_returns_empty_strings(self):
        orig = config_module._load_file
        config_module._load_file = lambda: {}  # simulate missing file (returns {})
        try:
            cfg = config_module._Config()
            self.assertEqual(cfg.chaikin_email, "")
            self.assertEqual(cfg.rapidapi_key, "")
        finally:
            config_module._load_file = orig

    def test_real_missing_file_returns_empty(self):
        orig_path = config_module._CFG_PATH
        config_module._CFG_PATH = "/nonexistent/config.json"
        try:
            result = config_module._load_file()
            self.assertEqual(result, {})
        finally:
            config_module._CFG_PATH = orig_path


class TestConfigMalformedJSON(unittest.TestCase):
    def test_malformed_json_raises_runtime_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{bad json,,}")
            path = f.name
        orig_path = config_module._CFG_PATH
        config_module._CFG_PATH = path
        try:
            with self.assertRaises(RuntimeError):
                config_module._load_file()
        finally:
            config_module._CFG_PATH = orig_path
            os.unlink(path)


class TestConfigRequire(unittest.TestCase):
    def test_require_raises_when_attr_empty(self):
        cfg = _make_cfg({})
        with self.assertRaises(RuntimeError):
            cfg.require("chaikin_email")

    def test_require_passes_when_attr_set(self):
        cfg = _make_cfg({"chaikin": {"email": "x@x.com", "password": "pw"}})
        cfg.require("chaikin_email", "chaikin_password")  # should not raise

    def test_require_multiple_missing_listed(self):
        cfg = _make_cfg({})
        try:
            cfg.require("chaikin_email", "chaikin_password")
            self.fail("Expected RuntimeError")
        except RuntimeError as e:
            self.assertIn("chaikin_email", str(e))
            self.assertIn("chaikin_password", str(e))


if __name__ == "__main__":
    unittest.main()
