"""A dashboard task's output is read through GET /api/tasks/output/{run_id}. It used to answer
anyone with the run id, so an admin-only task's log (e.g. the private plan check) was readable
without the login. Admin-only runs (and runs registered without the flag) now need it; a task
open to everyone stays open. The plan-check button is an admin-only task.
Route functions are called directly from create_app()'s route table; the subprocess is faked
and the log goes to a temp folder."""
import asyncio
import pathlib
import tempfile
import types
import unittest
from unittest import mock

from fastapi import HTTPException

import data_api
import server

SECRET = "test-secret"


def _route(app, path, method):
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) == path and method in r.methods)


class TestTaskOutputAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        for t in data_api.MANUAL_TASKS:
            (root / t["script"]).parent.mkdir(parents=True, exist_ok=True)
            (root / t["script"]).write_text("", encoding="utf-8")
        cfg = types.SimpleNamespace(web_secret=SECRET, admins=[])
        proc = mock.MagicMock(pid=1, returncode=0)
        proc.poll.return_value = 0
        self.patches = [mock.patch.object(server, "_cfg", return_value=cfg),
                        mock.patch.object(server, "_DIR", root),
                        mock.patch.object(server.subprocess, "Popen", return_value=proc)]
        for p in self.patches:
            p.start()
        app = server.create_app()
        self.run_task = _route(app, "/api/tasks/run", "POST")
        self.output = _route(app, "/api/tasks/output/{run_id}", "GET")
        self.token = "Bearer " + server.make_token("admin", SECRET)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _start(self, task_id):
        r = asyncio.run(self.run_task(task_id=task_id, input_value="", authorization=self.token))
        self.assertEqual(r["status"], "started", r)
        return r["run_id"]

    def test_plan_check_is_an_admin_only_task(self):
        task = next(t for t in data_api.MANUAL_TASKS if t["id"] == "invest_plan")
        self.assertEqual((task["script"], task["admin_only"]), ("scripts/monitoring/invest_plan.py", True))

    def test_admin_only_output_needs_the_login(self):
        run_id = self._start("invest_plan")
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(self.output(run_id=run_id, offset=0, authorization=""))
        self.assertEqual(cm.exception.status_code, 401)
        with self.assertRaises(HTTPException):
            asyncio.run(self.output(run_id=run_id, offset=0, authorization="Bearer wrong"))
        r = asyncio.run(self.output(run_id=run_id, offset=0, authorization=self.token))
        self.assertEqual((r["done"], r["exit_code"]), (True, 0))

    def test_open_task_output_stays_open(self):
        open_tasks = [t for t in data_api.MANUAL_TASKS if not t.get("admin_only") and not t.get("input")]
        if not open_tasks:
            self.skipTest("no task open to everyone in the registry")
        run_id = self._start(open_tasks[0]["id"])
        self.assertTrue(asyncio.run(self.output(run_id=run_id, offset=0, authorization=""))["done"])


if __name__ == "__main__":
    unittest.main()
