# 🔎 Pull Request #73 Code Review & Hardening Audit

**PR Goal:** Implement `LastTaskResult` scheduled-task exit-code auditing inside `watchdog.py`, fix the `MultipleInstances` PowerShell enum error, and eliminate duplicate task warnings on the AETHER desk.
**Review Date:** Friday, September 11, 2026
**Review Verdict:** 🛑 **AMEND REQUIRED (Blocker Typo & False-Positive Bug)**

---

## 🚨 Critical Blockers (Must Amend Before Merging)

### 1. Fatal `NameError` (Double Underscore Typo) in `watchdog.py`
In `watchdog.py`'s main routine, when appending failed task results to `recovery_actions` (around line 612):
```python
    if failed_tasks:
        recovery_actions.append(
            f"⚠️ Tasks failed their last execution (manual review — NOT auto-re-registered): {', '.join(failed__tasks)}"
        )
```
*   **The Bug:** The variable is typed as `failed__tasks` (with two underscores), but `check_task_scheduler()` returns `failed_tasks` (single underscore).
*   **The Impact:** If any scheduled task fails, the hourly watchdog run will crash with `NameError: name 'failed__tasks' is not defined` instead of notifying the user, completely disabling the watchdog's self-healing mechanisms.
*   **Correction:** Change `failed__tasks` to `failed_tasks` in the f-string.

### 2. False-Positive Duplicate Re-Introduction in `preflight_validator.py`
In `preflight_validator.py`'s duplicate scheduled task checks:
```python
-                 elif "ai_portfolio_game.py" in to_run or "daily-run.md" in to_run:
-                     script_key = "ai_portfolio_game.py (Trading Desk)"
+                 elif "ai_portfolio_game.py" in to_run or "daily-run.md" in to_run:
+                     script_key = "autonomous_pipeline.py"
```
*   **The Bug:** The PR maps `.claude/commands/daily-run.md` (the AI daily qualitative driver) to the `"autonomous_pipeline.py"` script key.
*   **The Impact:** Since the morning data-fetching task (`AETHER_Morning`) actually runs `autonomous_pipeline.py`, mapping `daily-run.md` to `autonomous_pipeline.py` makes `preflight_validator.py` group both of these separate tasks under the same script key. This will trigger a new, false-positive duplication warning:
    `Duplicate tasks found running autonomous_pipeline.py: \AETHER_Agents\AETHER_Morning, \AETHER_Agents\AETHER_DailyDriver`.
*   **Correction:** Map them to separate, explicit keys to respect the Pure/AI execution split of the desk:
    ```python
                    elif "ai_portfolio_game.py" in to_run:
                        script_key = "ai_portfolio_game.py (Trading Desk)"
                    elif "daily-run.md" in to_run:
                        script_key = "AETHER_DailyDriver (AI-Qualitative)"
    ```

---

## 📈 Strengths & Structural Commendations

1.  **Watchdog Task Auditing:** The introduction of `Get-ScheduledTaskInfo | select -ExpandProperty LastTaskResult` via PowerShell is an excellent and highly proactive security gate to detect silent task failures on headless systems.
2.  **Path-Agnostic Foldered Queries:** Splitting the absolute task path (`abs_task.rsplit("\\", 1)`) so that PowerShell queries the correct `-TaskPath` depending on whether a task is root or foldered is highly robust and prevents silent query misses.
3.  **IgnoreNew Pivot:** Aligning `MultipleInstances` to `IgnoreNew` is mathematically and operationally correct for our local environments. It successfully prevents overlapping state writes to `ai_portfolio_game.json` and `state_of_the_day.xlsx` while maintaining PowerShell compatibility.
4.  **Exceptional Test Coverage:** The newly added `tests/test_watchdog_scheduler.py` is beautifully modular, isolates subprocess commands cleanly using mock side-effects, and provides excellent coverage for benign vs. fatal Task Scheduler exit codes.

---

## 🏁 Recommended Path to Green
Once the NameError typo is repaired in `watchdog.py` and the duplicate script mapping is separated in `preflight_validator.py`, this PR will be a **100% stable, green, and highly valuable hardening contribution** to Project AETHER.
