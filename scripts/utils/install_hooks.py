import os
import subprocess
import sys
from pathlib import Path

# Windows consoles/pipes default to cp1252, which raises UnicodeEncodeError on the
# status emoji below (and on any emoji in re-printed validator output). Reconfigure
# to UTF-8 defensively so a cosmetic glyph can never abort the install / a commit.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Repo root directory (fallback only; the git query below is authoritative)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _git(*args):
    """Run a git command from ROOT_DIR and return its stripped stdout, or None
    on failure. Used to resolve the real hooks directory so we honor worktrees
    and a repo-configured core.hooksPath instead of hardcoding .git/hooks."""
    try:
        res = subprocess.run(
            ["git", *args], cwd=str(ROOT_DIR),
            capture_output=True, text=True, errors="ignore",
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None

HOOK_CONTENT = """#!/usr/bin/env python
\"\"\"
AETHER Defensive Pre-Commit Hook.
Blocks unsafe commits to main, accidental commits of .xlsx workbooks,
and ensures strict Unix LF line endings across the repository.
\"\"\"
import sys
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

def run_cmd(args):
    res = subprocess.run(args, capture_output=True, text=True, errors="ignore")
    return res.returncode, res.stdout.strip()

def main():
    print("🛡️ Running AETHER Defensive Pre-Commit Hook...")

    # 1. Block accidental commits of .xlsx files
    code, stdout = run_cmd(["git", "diff", "--cached", "--name-only"])
    staged_files = stdout.splitlines() if code == 0 else []
    
    xlsx_files = [f for f in staged_files if f.endswith(".xlsx")]
    if xlsx_files:
        print(f"❌ [BLOCKED] Accidental commit of Excel workbook(s) detected: {xlsx_files}")
        print("Never stage or commit Excel workbooks. Run 'git restore --staged <file>' to unstage.")
        sys.exit(1)

    # 2. Block direct commits to 'main' in the PROD workspace
    code, current_branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if current_branch == "main" and staged_files:
        # Check if the staged files contain code files (excluding docs or .gitattributes)
        code_staged = [f for f in staged_files if f.endswith(".py") or f.endswith(".json")]
        if code_staged:
            print("❌ [BLOCKED] Direct code commit to 'main' branch detected in PROD folder!")
            print("Feature development must occur on separate branches. Please create a branch and commit there.")
            sys.exit(1)

    # 3. Ensure LF line endings for all staged text files
    crlf_files = []
    for f in staged_files:
        if f.endswith(".py") or f.endswith(".md") or f.endswith(".json"):
            # Check if file has CRLF
            try:
                # Read file in binary mode and check for b'\\r\\n'
                with open(f, "rb") as file_obj:
                    content = file_obj.read()
                if b"\\r\\n" in content:
                    crlf_files.append(f)
            except Exception:
                pass
                
    if crlf_files:
        print(f"❌ [BLOCKED] Windows CRLF line endings detected in text files: {crlf_files}")
        print("Please configure your editor to use Unix LF, or run 'git add --renormalize .' to normalize.")   
        sys.exit(1)

    # 4. Run the pre-existing pre_commit_validator.py
    print("🔄 Running AETHER Pre-Commit Quality Validator...")
    code, stdout = run_cmd([sys.executable, "scripts/utils/pre_commit_validator.py"])
    if code != 0:
        print(stdout)
        sys.exit(1)

    print("✅ [AETHER HOOK PASS] All defensive checks passed!")
    sys.exit(0)

if __name__ == "__main__":
    main()
"""

def _resolve_hooks_dir():
    """Resolve the pre-commit hooks directory the way git itself would.

    `git rev-parse --git-path hooks` honors core.hooksPath AND resolves correctly
    from inside a linked worktree (returns the shared common hooks dir) — so we no
    longer hand-parse the `.git` worktree pointer. Falls back to <root>/.git/hooks
    only when git is unavailable."""
    hooks = _git("rev-parse", "--git-path", "hooks")
    if hooks:
        hp = Path(hooks)
        if not hp.is_absolute():
            top = _git("rev-parse", "--show-toplevel")
            hp = (Path(top) if top else ROOT_DIR) / hp
        return hp
    return ROOT_DIR / ".git" / "hooks"


def install_hooks():
    hooks_dir = _resolve_hooks_dir()
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"❌ Could not create hooks directory {hooks_dir}: {e}")
        return False

    pre_commit_file = hooks_dir / "pre-commit"

    # Preserve a pre-existing, unrelated pre-commit hook instead of silently
    # clobbering it: back it up to pre-commit.local.bak (only if it isn't already
    # ours — detected by the validator reference every AETHER hook carries).
    try:
        if pre_commit_file.exists():
            existing = pre_commit_file.read_text(encoding="utf-8", errors="ignore")
            if "pre_commit_validator.py" not in existing:
                backup = pre_commit_file.with_suffix(".local.bak")
                backup.write_text(existing, encoding="utf-8")
                print(f"ℹ️  Backed up existing unrelated pre-commit hook to: {backup}")
    except Exception as e:
        print(f"⚠️  Could not back up existing pre-commit hook (continuing): {e}")

    try:
        with open(pre_commit_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(HOOK_CONTENT)

        # On non-Windows platforms, make the hook executable
        if sys.platform != "win32":
            os.chmod(pre_commit_file, 0o755)

        print(f"✅ Successfully installed defensive pre-commit hook to: {pre_commit_file}")
        print("   Bypass a single doc-sync block with: "
              "AETHER_DOCSYNC_ACK=<feature-key> git commit …")
        return True
    except Exception as e:
        print(f"❌ Failed to install pre-commit hook: {e}")
        return False


if __name__ == "__main__":
    sys.exit(0 if install_hooks() else 1)
