import unittest
import os
import sys
import re
from pathlib import Path

# Ensure project root is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watchdog

class TestOperationalIntegrity(unittest.TestCase):
    def test_task_command_lengths_under_windows_limit(self):
        """Operational: Assert that no Task Scheduler command string exceeds the legacy Windows 261-character limit."""
        for task, (tr_cmd, sc, st) in watchdog._TASK_DEFS.items():
            cmd_len = len(tr_cmd)
            self.assertLessEqual(
                cmd_len, 
                261, 
                f"🚨 CRITICAL: Command for scheduled task '{task}' is {cmd_len} characters long, "
                f"which exceeds the Windows 261-character limit! Command: '{tr_cmd}'"
            )
            
    def test_scheduled_task_files_exist(self):
        """Operational: Assert that all python script files targeted in scheduled tasks physically exist on disk."""
        for task, (tr_cmd, sc, st) in watchdog._TASK_DEFS.items():
            # Extract the script paths (handling absolute and relative paths)
            py_matches = re.findall(r"['\"]([^'\"]+\.py)['\"]", tr_cmd)
            for py_path_str in py_matches:
                py_path = Path(py_path_str)
                self.assertTrue(
                    py_path.exists(),
                    f"🚨 CRITICAL: Scheduled task '{task}' targets a non-existent script file on disk: {py_path_str}"
                )

    def test_watchdog_audits_all_core_tasks(self):
        """Operational: the core jobs are in the watchdog's audit list, under the names
        register_agent_tasks.ps1 registers (the old AnalyzeFinData_* root tasks were duplicates)."""
        for task in [r"\AETHER_Agents\AETHER_Morning", r"\AETHER_Agents\AETHER_AftermarketReport",
                     r"\AETHER_Agents\AETHER_ExecuteTrades", r"\AETHER_Agents\AETHER_AI_Summary"]:
            self.assertIn(
                task,
                watchdog.TASKS,
                f"🚨 CRITICAL: Scheduled task '{task}' is defined in templates but missing from the Watchdog's TASKS auto-healing checklist!"
            )

if __name__ == '__main__':
    unittest.main()
