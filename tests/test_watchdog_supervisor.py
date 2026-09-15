"""Unit tests for watchdog.py process-supervisor kill-selection logic.

These cover the *pure* decision helpers that decide which processes the
supervisor is allowed to force-kill. The whole safety contract of the orphan
sweep lives in `_select_orphans_to_purge`: it must NEVER select a process that
is not attributable to this repo, no matter how old or idle — so the sweep can
never touch the user's own shells or unrelated console hosts.
"""
import unittest

import watchdog


class SelectOrphansToPurge(unittest.TestCase):
    REPO = r"C:/Develop/AnalyzeFinData"

    def _proc(self, pid, age, cmd, children=0):
        return {"ProcessId": pid, "AgeMinutes": age, "CommandLine": cmd, "ChildCount": children}

    def test_aether_owned_childless_and_old_is_purged(self):
        procs = [self._proc(101, 45, r"powershell.exe -File C:\Develop\AnalyzeFinData\watchdog.py")]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, self.REPO), [101])

    def test_non_aether_process_is_never_purged(self):
        # Old + childless but NOT ours: the user's own shell must be left alone.
        procs = [
            self._proc(201, 999, r"powershell.exe -NoExit -Command Get-Process"),
            self._proc(202, 999, r"C:\Windows\System32\conhost.exe 0x4"),
            self._proc(203, 999, r"powershell.exe -File C:\OtherProject\build.ps1"),
        ]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, self.REPO), [])

    def test_process_with_children_is_not_purged(self):
        procs = [self._proc(301, 90, r"python C:\Develop\AnalyzeFinData\autonomous_pipeline.py", children=2)]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, self.REPO), [])

    def test_young_process_is_not_purged(self):
        procs = [self._proc(401, 5, r"python C:\Develop\AnalyzeFinData\daily_task.py")]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, self.REPO), [])

    def test_boundary_age_exactly_at_ttl_is_not_purged(self):
        # Strictly greater-than TTL required; exactly-30 must survive.
        procs = [self._proc(501, 30, r"python C:\Develop\AnalyzeFinData\x.py")]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, self.REPO), [])

    def test_empty_repo_path_selects_nothing(self):
        procs = [self._proc(601, 999, r"python C:\Develop\AnalyzeFinData\x.py")]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, ""), [])

    def test_case_and_separator_insensitive_match(self):
        # Backslash cmdline vs forward-slash repo path, mixed case → still ours.
        procs = [self._proc(701, 60, r"PYTHON C:\DEVELOP\ANALYZEFINDATA\SERVER.PY")]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, r"C:/Develop/AnalyzeFinData"), [701])

    def test_malformed_rows_are_skipped_not_fatal(self):
        procs = [
            {"ProcessId": None, "AgeMinutes": 99, "CommandLine": r"C:/Develop/AnalyzeFinData/x.py", "ChildCount": 0},
            {"CommandLine": r"C:/Develop/AnalyzeFinData/y.py"},  # missing keys
            self._proc(803, 60, r"C:/Develop/AnalyzeFinData/z.py"),
        ]
        self.assertEqual(watchdog._select_orphans_to_purge(procs, self.REPO), [803])


class SelectStaleServerPids(unittest.TestCase):
    def test_single_binder_keeps_it(self):
        self.assertEqual(watchdog._select_stale_server_pids([(1, "2026-01-01")]), [])

    def test_keeps_newest_kills_rest(self):
        procs = [(1, "2026-09-01"), (2, "2026-09-03"), (3, "2026-09-02")]
        # Newest is pid 2 (2026-09-03); kill the two older ones.
        self.assertEqual(sorted(watchdog._select_stale_server_pids(procs)), [1, 3])

    def test_empty_is_empty(self):
        self.assertEqual(watchdog._select_stale_server_pids([]), [])


class ParseListenerPids8888(unittest.TestCase):
    def test_listening_row_by_socket_shape_not_localized_word(self):
        # State column carries a NON-English word (simulating a localized Windows):
        # detection must rely on the null foreign endpoint, not the word "LISTENING".
        out = "  TCP    0.0.0.0:8888    0.0.0.0:0    ABHOEREN    12345\n"
        self.assertEqual(watchdog._parse_listener_pids_8888(out), {12345})

    def test_ipv6_listener(self):
        out = "  TCP    [::]:8888    [::]:0    ABHOEREN    222\n"
        self.assertEqual(watchdog._parse_listener_pids_8888(out), {222})

    def test_established_connection_excluded(self):
        out = "  TCP    127.0.0.1:8888    127.0.0.1:52345    HERGESTELLT    333\n"
        self.assertEqual(watchdog._parse_listener_pids_8888(out), set())

    def test_other_port_excluded(self):
        out = "  TCP    0.0.0.0:9999    0.0.0.0:0    ABHOEREN    444\n"
        self.assertEqual(watchdog._parse_listener_pids_8888(out), set())

    def test_multiple_listeners_collected(self):
        out = (
            "  TCP    0.0.0.0:8888    0.0.0.0:0    ABHOEREN    10\n"
            "  TCP    [::]:8888       [::]:0       ABHOEREN    11\n"
            "  UDP    0.0.0.0:8888    *:*          12\n"  # not TCP → ignored
        )
        self.assertEqual(watchdog._parse_listener_pids_8888(out), {10, 11})


if __name__ == "__main__":
    unittest.main()
