import sys
import unittest
import py_compile
from pathlib import Path

class TestExecutables(unittest.TestCase):
    def test_compilation(self):
        """Verify that all root-level executable scripts compile without syntax errors (import errors not checked)."""
        base_dir = Path(__file__).resolve().parent.parent
        executables = [
            "run_history.py",
            "daily_task.py",
            "real_copilot.py",
            "watchdog.py",
            "ai_portfolio_game.py",
            "server.py",
            "external_intel.py",
            "data_api.py"
        ]
        for script in executables:
            path = base_dir / script
            self.assertTrue(path.exists(), f"Executable script {script} is missing from root directory!")
            try:
                # Compile with doraise=True to raise any compilation/syntax errors
                py_compile.compile(str(path), doraise=True)
            except Exception as e:
                self.fail(f"Script {script} failed compilation check: {e}")

if __name__ == "__main__":
    unittest.main()