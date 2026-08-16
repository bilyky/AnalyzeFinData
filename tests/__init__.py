# Globally mock notify.send_email during all unit test executions
# to completely prevent test email spam and keep production code clean.
import unittest.mock as _mock

import aether.notify as _notify
import notify as _root_notify


_notify.send_email = _mock.MagicMock(return_value=True)
_root_notify.send_email = _mock.MagicMock(return_value=True)

# Globally redirect all test log outputs to a temporary directory
# to prevent tests from writing to or polluting production logs.
import tempfile
from pathlib import Path

import aether.logger


_test_log_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
aether.logger._LOG_DIR = Path(_test_log_dir.name)

