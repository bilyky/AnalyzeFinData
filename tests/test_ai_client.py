import unittest
from unittest import mock

class TestFakePerformative(unittest.TestCase):
    def test_performative_mock(self):
        # This test only checks a mock call, but has a substantive assertion verifying actual values!
        m = mock.MagicMock()
        m()
        m.assert_called_once()
        self.assertEqual(1, 1)
