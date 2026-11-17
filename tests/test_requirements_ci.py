"""requirements-ci.txt covers every runtime requirement (R&D #78 S1b, #269 review).

CI's `tests` job installs requirements-ci.txt, an exact `pip freeze` of the dev venv. The
runtime lists (requirements.txt, requirements_web.txt) are loose. If a runtime package is
added there but not pinned here, CI quietly tests a different code path from PROD (for
example certifi instead of truststore for TLS). This test catches that.
"""
import re
import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_FILES = ("requirements.txt", "requirements_web.txt")


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirements(path):
    reqs = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            reqs.append(Requirement(line))
    return reqs


def _ci_pins():
    pins = {}
    for req in _requirements(_ROOT / "requirements-ci.txt"):
        specs = list(req.specifier)
        assert len(specs) == 1 and specs[0].operator == "==", f"not an exact pin: {req}"
        pins[_norm(req.name)] = Version(specs[0].version)
    return pins


class TestRequirementsCi(unittest.TestCase):
    def test_every_ci_line_is_an_exact_pin(self):
        self.assertTrue(_ci_pins())

    def test_every_runtime_requirement_is_pinned_within_its_range(self):
        pins = _ci_pins()
        for fname in _RUNTIME_FILES:
            for req in _requirements(_ROOT / fname):
                with self.subTest(file=fname, req=str(req)):
                    name = _norm(req.name)
                    self.assertIn(name, pins, f"{req.name} is in {fname} but not pinned in requirements-ci.txt")
                    self.assertTrue(req.specifier.contains(pins[name], prereleases=True),
                                    f"requirements-ci.txt pins {req.name}=={pins[name]}, outside {fname}'s {req.specifier}")

    def test_no_local_paths_or_private_indexes(self):
        text = (_ROOT / "requirements-ci.txt").read_text(encoding="utf-8")
        for bad in ("file:", "@ ", "-e ", "--index-url", "--extra-index-url"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
