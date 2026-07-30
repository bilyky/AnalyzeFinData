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

    def test_server_cli_smoke(self):
        """Verify that server.py CLI commands (status, stop) execute without crashing on NameErrors or runtime scope errors."""
        import subprocess
        base_dir = Path(__file__).resolve().parent.parent
        server_py = base_dir / "server.py"
        
        # Test 1: server.py status
        res = subprocess.run([sys.executable, str(server_py), "status"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"server.py status failed with exit code {res.returncode}:\n{res.stdout}\n{res.stderr}")
        self.assertNotIn("Traceback", res.stdout)
        self.assertNotIn("NameError", res.stdout)
        self.assertNotIn("Traceback", res.stderr)
        self.assertNotIn("NameError", res.stderr)

        # Test 2: server.py stop
        res_stop = subprocess.run([sys.executable, str(server_py), "stop"], capture_output=True, text=True)
        self.assertEqual(res_stop.returncode, 0, f"server.py stop failed with exit code {res_stop.returncode}:\n{res_stop.stdout}\n{res_stop.stderr}")
        self.assertNotIn("Traceback", res_stop.stdout)
        self.assertNotIn("NameError", res_stop.stdout)
        self.assertNotIn("Traceback", res_stop.stderr)
        self.assertNotIn("NameError", res_stop.stderr)

if __name__ == "__main__":
    unittest.main()