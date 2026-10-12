import unittest
import os
import sys

# Ensure project root is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watchdog

class TestOperationalIntegrity(unittest.TestCase):
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
