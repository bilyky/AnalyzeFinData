#!/usr/bin/env python
"""Install AETHER git hooks into this clone's hooks directory.

`.git/hooks` is NOT version-controlled, so each fresh clone must run this once:

    python scripts/utils/install_hooks.py

It writes a `pre-commit` hook that runs `scripts/utils/pre_commit_validator.py`
(import hygiene, silent-except ban, print ban, R&D roadmap sync, and feature-doc
sync). Re-running is safe (idempotent overwrite). The hook is a POSIX `sh` script;
Git for Windows runs it via its bundled `sh`, so it works cross-platform.
"""
import os
import stat
import subprocess
import sys

# LF-only: a `#!/bin/sh` script with CRLF line endings fails to launch under sh.
HOOK = (
    "#!/bin/sh\n"
    "# AETHER pre-commit hook — installed by scripts/utils/install_hooks.py.\n"
    "# Do not edit here; edit scripts/utils/pre_commit_validator.py and re-run the installer.\n"
    "if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi\n"
    'exec "$PY" scripts/utils/pre_commit_validator.py\n'
)


def _git(*args):
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main():
    top = _git("rev-parse", "--show-toplevel")
    if not top:
        print("Not inside a git repository — nothing to install.")
        return 1

    hooks_dir = _git("rev-parse", "--git-path", "hooks")
    if not hooks_dir:
        hooks_dir = os.path.join(top, ".git", "hooks")
    if not os.path.isabs(hooks_dir):
        hooks_dir = os.path.join(top, hooks_dir)
    os.makedirs(hooks_dir, exist_ok=True)

    hook_path = os.path.join(hooks_dir, "pre-commit")
    with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK)
    try:
        st = os.stat(hook_path)
        os.chmod(hook_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass  # Windows ignores the exec bit; git runs the hook regardless.

    print(f"Installed pre-commit hook -> {hook_path}")
    print("It runs scripts/utils/pre_commit_validator.py on every commit.")
    print("Scoped doc-sync bypass (when a code change genuinely needs no doc update):")
    print("    AETHER_DOCSYNC_ACK=<feature-key> git commit ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
