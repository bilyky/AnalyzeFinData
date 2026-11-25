"""Chaikin login: the headed renewal waits for a person's click; unattended runs don't open a browser;
the session alert is throttled across processes.

PROD 2026-11-24: the hourly watchdog's headless login hit Turnstile ("button[type=submit]:not([disabled])"
not visible in 10 s) and emailed every hour, because the email throttle lived in process memory and the
watchdog is a new process each hour. The headed renewal filled the form but waited only 60 s for the
Login click.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # noqa: F401  hermetic harness
import powergauge as pg


class _Page:
    """Fake Playwright page: the path and the enabled submit button change after N polls."""

    def __init__(self, leave_after=None, enable_after=None):
        self.polls, self.clicked = 0, []
        self.leave_after, self.enable_after = leave_after, enable_after

    def evaluate(self, js):
        return "/publications" if self.leave_after is not None and self.polls >= self.leave_after else "/login"

    def query_selector(self, sel):
        return object() if self.enable_after is not None and self.polls >= self.enable_after else None

    def click(self, sel):
        self.clicked.append(sel)

    def wait_for_timeout(self, ms):
        self.polls += 1


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _TickPage(_Page):
    """Each poll advances the fake clock by 1 s, like the real 1 s wait."""

    def __init__(self, clock, **kw):
        super().__init__(**kw)
        self.clock = clock

    def wait_for_timeout(self, ms):
        super().wait_for_timeout(ms)
        self.clock.t += ms / 1000


class WaitNoticeTest(unittest.TestCase):
    def test_no_notice_when_turnstile_passes_by_itself(self):
        clock, told = _Clock(), []
        page = _TickPage(clock, enable_after=3)
        pg._wait_for_login_or_submit(page, clock() + 600, on_waiting=lambda: told.append(1), clock=clock)
        self.assertEqual(told, [])

    def test_one_notice_after_15_s_without_progress(self):
        clock, told = _Clock(), []
        page = _TickPage(clock, leave_after=40)
        self.assertEqual(pg._wait_for_login_or_submit(page, clock() + 600, on_waiting=lambda: told.append(clock()),
                                                      clock=clock), "human")
        self.assertEqual(told, [1015.0])                     # once, at 15 s


class WaitForLoginTest(unittest.TestCase):
    def test_a_person_logging_in_ends_the_wait(self):
        page = _Page(leave_after=3)
        self.assertEqual(pg._wait_for_login_or_submit(page, time.time() + 60), "human")
        self.assertEqual((page.polls, page.clicked), (3, []))      # nothing clicked for them

    def test_an_enabled_button_is_clicked(self):
        page = _Page(enable_after=2)
        self.assertEqual(pg._wait_for_login_or_submit(page, time.time() + 60), "clicked")
        self.assertEqual(page.clicked, ['button[type="submit"]'])

    def test_the_wait_ends_at_the_deadline(self):
        page = _Page()
        with self.assertRaises(TimeoutError):
            pg._wait_for_login_or_submit(page, time.time() - 1)


class UnattendedLoginTest(unittest.TestCase):
    def _login(self, env=None):
        with mock.patch.object(pg, "_load_session_from_file", return_value={"jsessionid": "x" * 20}), \
             mock.patch.object(pg, "_probe_session", return_value="invalid"), \
             mock.patch.object(pg, "_login_via_browser", return_value={"jsessionid": "y"}) as browser, \
             mock.patch.dict(os.environ, env or {}, clear=False):
            if env is None:
                os.environ.pop("CHAIKIN_HEADLESS_LOGIN", None)
            try:
                return pg.login(interactive=False), browser
            except EnvironmentError as e:
                return e, browser

    def test_an_unattended_run_opens_no_browser(self):
        out, browser = self._login()
        self.assertIsInstance(out, EnvironmentError)
        self.assertIn("does not open a browser", str(out))
        browser.assert_not_called()

    def test_the_env_override_still_forces_a_headless_attempt(self):
        out, browser = self._login({"CHAIKIN_HEADLESS_LOGIN": "1"})
        browser.assert_called_once_with(headless=True)

    def test_an_interactive_run_still_opens_the_headed_browser(self):
        with mock.patch.object(pg, "_load_session_from_file", return_value={"jsessionid": "x" * 20}), \
             mock.patch.object(pg, "_probe_session", return_value="invalid"), \
             mock.patch.object(pg, "_login_via_browser", return_value={"jsessionid": "y"}) as browser, \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHAIKIN_HEADLESS_LOGIN", None)
            pg.login(interactive=True)
        browser.assert_called_once_with(headless=False)


class HarnessRedirectTest(unittest.TestCase):
    def test_the_suite_never_writes_the_real_alert_state(self):
        """#324 review: a test 'sending' the alert (send_email mocked) stamped the real
        Data/chaikin_alert_state.json, which would silence the next real alert for 6 h."""
        real_data = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "Data"))
        path = os.path.realpath(pg._alert_state_file())
        self.assertFalse(path.startswith(real_data + os.sep), path)
        with mock.patch.object(pg, "send_email"):
            pg._send_session_alert("test")
        self.assertFalse(os.path.exists(os.path.join(real_data, "chaikin_alert_state.json"))
                         and os.path.getmtime(os.path.join(real_data, "chaikin_alert_state.json")) > time.time() - 60)


class SessionAlertTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = os.path.join(self._tmp.name, "chaikin_alert_state.json")
        p = mock.patch.object(pg, "_alert_state_file", return_value=self.state)
        p.start()
        self.addCleanup(p.stop)

    def test_one_email_across_processes_within_the_window(self):
        with mock.patch.object(pg, "send_email") as send:
            self.assertTrue(pg._send_session_alert("expired"))
            # a later process: no shared memory, only the file
            self.assertFalse(pg._send_session_alert("expired"))
        self.assertEqual(send.call_count, 1)
        body = send.call_args.kwargs["body"]
        self.assertIn("Chaikin - Login & Refresh Token (opens Chrome)", body)
        self.assertNotIn("JSESSIONID", body)                      # the old copy-from-DevTools steps

    def test_an_old_alert_lets_a_new_one_through(self):
        with open(self.state, "w") as f:
            json.dump({"sent_at": time.time() - 7 * 3600}, f)
        with mock.patch.object(pg, "send_email") as send:
            self.assertTrue(pg._send_session_alert("expired"))
        send.assert_called_once()

    def test_a_failed_send_is_not_recorded(self):
        with mock.patch.object(pg, "send_email", side_effect=OSError("smtp down")):
            self.assertFalse(pg._send_session_alert("expired"))
        self.assertFalse(os.path.exists(self.state))



if __name__ == "__main__":
    unittest.main()
