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
    def test_explicit_null_blocks_do_not_crash(self):
        # Every top-level block set to JSON null must not crash and must yield safe defaults.
        cases = {
            "chaikin":  ({"chaikin": None},  [("chaikin_email", ""), ("chaikin_password", "")]),
            "etrade":   ({"etrade": None},   [("etrade_sandbox_key", ""), ("etrade_production_key", "")]),
            "subblocks":({"etrade": {"sandbox": None, "production": None}},
                                              [("etrade_sandbox_key", ""), ("etrade_production_secret", "")]),
            "rapidapi": ({"rapidapi": None}, [("rapidapi_key", "")]),
            "web":      ({"web": None},      [("web_port", 8888), ("web_host", "0.0.0.0"),
                                              ("web_admins", []), ("web_secret", "")]),
        }
        for name, (raw, checks) in cases.items():
            with self.subTest(block=name):
                cfg = _make_cfg(raw)
                for attr, expected in checks:
                    self.assertEqual(getattr(cfg, attr), expected)

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
        self.assertEqual(cfg.web_admins, [])
        self.assertEqual(cfg.web_secret, "")

    def test_web_from_file(self):
        cfg = _make_cfg({"web": {
            "port": 9090, "host": "127.0.0.1", "secret": "sk",
            "admins": [{"user": "a", "pass": "b"}],
        }})
        self.assertEqual(cfg.web_port, 9090)
        self.assertEqual(cfg.web_host, "127.0.0.1")
        self.assertEqual(cfg.web_secret, "sk")
        self.assertEqual(cfg.web_admins, [{"user": "a", "pass": "b"}])

    def test_web_port_env_override(self):
        cfg = _make_cfg({"web": {"port": 9090}}, env={"WEB_PORT": "7000"})
        self.assertEqual(cfg.web_port, 7000)

    def test_web_port_is_int(self):
        cfg = _make_cfg({"web": {"port": "9090"}})   # string in JSON coerced to int
        self.assertIsInstance(cfg.web_port, int)
        self.assertEqual(cfg.web_port, 9090)

    def test_web_admins_env_override(self):
        cfg = _make_cfg({"web": {"admins": [{"user": "file", "pass": "x"}]}},
                        env={"WEB_ADMINS": '[{"user":"env","pass":"y"}]'})
        self.assertEqual(cfg.web_admins, [{"user": "env", "pass": "y"}])

    def test_web_admins_malformed_env_falls_back_empty(self):
        cfg = _make_cfg({}, env={"WEB_ADMINS": "not json"})
        self.assertEqual(cfg.web_admins, [])


class TestConfigOverlay(unittest.TestCase):
    """Defensive Risk-Management Overlay namespace (Rule B scale-out keys this pass)."""

    def test_scale_out_defaults(self):
        cfg = _make_cfg({})
        self.assertEqual(cfg.overlay_scale_out_tiers, [1.5, 3.0])
        self.assertEqual(cfg.overlay_scale_out_fracs, [0.30, 0.30])

    def test_scale_out_from_file(self):
        cfg = _make_cfg({"overlay": {"scale_out_tiers": [1.0, 2.0, 4.0],
                                     "scale_out_fracs": [0.25, 0.25, 0.25]}})
        self.assertEqual(cfg.overlay_scale_out_tiers, [1.0, 2.0, 4.0])
        self.assertEqual(cfg.overlay_scale_out_fracs, [0.25, 0.25, 0.25])

    def test_scale_out_env_override(self):
        cfg = _make_cfg({"overlay": {"scale_out_tiers": [1.5, 3.0]}},
                        env={"AETHER_OVERLAY_SCALE_OUT_TIERS": "[2.0, 4.0]"})
        self.assertEqual(cfg.overlay_scale_out_tiers, [2.0, 4.0])

    def test_scale_out_null_block_safe(self):
        cfg = _make_cfg({"overlay": None})
        self.assertEqual(cfg.overlay_scale_out_tiers, [1.5, 3.0])
        self.assertEqual(cfg.overlay_scale_out_fracs, [0.30, 0.30])

    def test_scale_out_values_are_float(self):
        cfg = _make_cfg({"overlay": {"scale_out_tiers": ["1.5", "3.0"],
                                     "scale_out_fracs": ["0.3", "0.3"]}})
        self.assertTrue(all(isinstance(x, float) for x in cfg.overlay_scale_out_tiers))
        self.assertTrue(all(isinstance(x, float) for x in cfg.overlay_scale_out_fracs))

    def test_scale_out_malformed_env_falls_back(self):
        # A bad env override must never crash config load — fall back to the default ladder.
        cfg = _make_cfg({}, env={"AETHER_OVERLAY_SCALE_OUT_TIERS": "not json"})
        self.assertEqual(cfg.overlay_scale_out_tiers, [1.5, 3.0])

    def test_scale_out_mismatched_lengths_fall_back(self):
        # tiers and fracs must be parallel; a mismatch reverts BOTH to defaults.
        cfg = _make_cfg({"overlay": {"scale_out_tiers": [1.0, 2.0, 3.0],
                                     "scale_out_fracs": [0.5]}})
        self.assertEqual(cfg.overlay_scale_out_tiers, [1.5, 3.0])
        self.assertEqual(cfg.overlay_scale_out_fracs, [0.30, 0.30])

    def test_scale_out_over_100pct_falls_back(self):
        # Cumulative bank fraction > 1.0 is nonsensical -> defaults.
        cfg = _make_cfg({"overlay": {"scale_out_tiers": [1.0, 2.0],
                                     "scale_out_fracs": [0.7, 0.7]}})
        self.assertEqual(cfg.overlay_scale_out_fracs, [0.30, 0.30])


class TestConfigMissingFile(unittest.TestCase):
    # Note: "mocked empty file -> empty attrs" is already covered by
    # TestConfigNullSafety.test_empty_config_returns_empty_strings. This class
    # tests the REAL _load_file() behavior (not mocked) on a nonexistent path.
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
