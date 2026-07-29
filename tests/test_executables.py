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

    def test_daily_task_linter_self_validation(self):
        """Verify that daily_task.py passes its own Ruff-based self-validation lint checks."""
        import subprocess
        base_dir = Path(__file__).resolve().parent.parent
        daily_task_py = base_dir / "daily_task.py"
        
        # Determine the exact same ruff command daily_task.py uses
        ruff_cmd = [sys.executable, "-m", "ruff"]
        venv_new_python = base_dir / "venv_new" / "Scripts" / "python.exe"
        if not venv_new_python.exists():
            venv_new_python = base_dir / "venv" / "Scripts" / "python.exe"
            
        try:
            # Check if primary interpreter has ruff
            test_res = subprocess.run(ruff_cmd + ["--version"], capture_output=True)
            if test_res.returncode != 0:
                raise FileNotFoundError()
        except Exception:
            if venv_new_python.exists():
                ruff_cmd = [str(venv_new_python), "-m", "ruff"]
                
        # Run ruff check on daily_task.py
        res = subprocess.run(ruff_cmd + ["check", "--select", "F,E9,F63,F7,F82", str(daily_task_py)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"daily_task.py failed Ruff self-validation linter checks:\n{res.stdout}\n{res.stderr}")

if __name__ == "__main__":
    unittest.main()