"""
Red-green tests for console_safe (the Windows CP1252 console fallback).

console_safe.SafeStreamWrapper exists because a headless Windows Task Scheduler
run prints on a cp1252 console, and a non-ASCII glyph (the bullish arrow, an
emoji) raises UnicodeEncodeError mid-print and crashes the job. The wrapper
degrades that one write to a '?' placeholder instead of crashing.

These tests exercise the real fallback path with a fake cp1252 stream — no
mocking of the method under test — and assert both directions:
  * GREEN: an encodable write passes straight through, unchanged.
  * RED:   a write that would raise UnicodeEncodeError is caught and replaced,
           NOT propagated.
  * delegation: unknown attributes fall through to the wrapped stream.
  * install(): idempotent (no double-wrap) and tolerant of a None stream.
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import console_safe
from console_safe import SafeStreamWrapper


class _Cp1252Stream:
    """A text stream that can only encode cp1252 — writing a char outside that
    codepage raises UnicodeEncodeError, exactly like a real Windows console."""

    encoding = "cp1252"

    def __init__(self):
        self.written = []

    def write(self, s):
        s.encode(self.encoding)  # raises UnicodeEncodeError on non-cp1252 glyphs
        self.written.append(s)
        return len(s)

    def flush(self):
        self.flushed = True


class SafeStreamWrapperTest(unittest.TestCase):
    def test_encodable_write_passes_through_unchanged(self):
        """GREEN: cp1252-safe text is written verbatim, no replacement."""
        raw = _Cp1252Stream()
        w = SafeStreamWrapper(raw)
        n = w.write("plain ascii OK")
        self.assertEqual(raw.written, ["plain ascii OK"])
        self.assertEqual(n, len("plain ascii OK"))

    def test_unencodable_write_is_replaced_not_raised(self):
        """RED: the bullish arrow (U+2191) is not cp1252 — without the wrapper
        this raises UnicodeEncodeError and crashes the run. The wrapper must
        swallow the error and write a replacement instead."""
        raw = _Cp1252Stream()
        w = SafeStreamWrapper(raw)
        try:
            w.write("RBR↑ fired")
        except UnicodeEncodeError:
            self.fail("SafeStreamWrapper.write must not propagate UnicodeEncodeError")
        # something was written, and it no longer contains the offending glyph
        self.assertEqual(len(raw.written), 1)
        self.assertNotIn("↑", raw.written[0])
        self.assertIn("RBR", raw.written[0])

    def test_raw_stream_would_actually_raise(self):
        """Guards the test above from rotting: prove the unwrapped stream really
        does raise on the same input, so the wrapper is doing real work."""
        raw = _Cp1252Stream()
        with self.assertRaises(UnicodeEncodeError):
            raw.write("RBR↑ fired")

    def test_getattr_delegates_to_wrapped_stream(self):
        """Unknown attributes (flush, encoding, ...) fall through to the stream."""
        raw = _Cp1252Stream()
        w = SafeStreamWrapper(raw)
        self.assertEqual(w.encoding, "cp1252")
        w.flush()
        self.assertTrue(raw.flushed)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self._saved = (sys.stdout, sys.stderr, sys.platform)

    def tearDown(self):
        sys.stdout, sys.stderr, sys.platform = self._saved

    def test_install_wraps_and_is_idempotent_on_win32(self):
        sys.platform = "win32"
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        console_safe.install()
        self.assertIsInstance(sys.stdout, SafeStreamWrapper)
        self.assertIsInstance(sys.stderr, SafeStreamWrapper)
        first = sys.stdout
        console_safe.install()  # second call must not double-wrap
        self.assertIs(sys.stdout, first)

    def test_install_tolerates_none_stream(self):
        """pythonw.exe / fully headless: sys.stdout is None. install() must not
        wrap None (which would turn every later print into an AttributeError)."""
        sys.platform = "win32"
        sys.stdout = None
        sys.stderr = None
        console_safe.install()  # must not raise
        self.assertIsNone(sys.stdout)
        self.assertIsNone(sys.stderr)

    def test_install_is_noop_off_windows(self):
        sys.platform = "linux"
        marker = io.StringIO()
        sys.stdout = marker
        console_safe.install()
        self.assertIs(sys.stdout, marker)  # untouched off Windows


if __name__ == "__main__":
    unittest.main()
