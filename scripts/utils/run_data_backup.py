"""Standalone entry point for the AETHER_Data_Backup scheduled task.

Runs the network Data-folder backup (robocopy sync) as its own process so the
Windows Task Scheduler can invoke it directly without an inline ``python -c``
one-liner (whose nested quotes do not survive the registrar's
``-Command "<cmd>"`` wrapper). Registered by ``register_agent_tasks.ps1``.

Exits non-zero when the sync fails so the scheduler records a failed run
(LastTaskResult != 0) rather than a silent success.
"""

import os
import sys

# The scheduler runs this file directly, which puts only scripts/utils on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import watchdog


def main() -> int:
    ok = watchdog.sync_data_folder()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
