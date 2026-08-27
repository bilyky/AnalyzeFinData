import os
import sys
from pathlib import Path

# Repo root directory
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

HOOK_CONTENT = """#!/usr/bin/env python
\"\"\"
AETHER Defensive Pre-Commit Hook.
Blocks unsafe commits to main, accidental commits of .xlsx workbooks,
and ensures strict Unix LF line endings across the repository.
\"\"\"
import sys
import subprocess

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

    print("✅ [AETHER HOOK PASS] All defensive checks passed!")
    sys.exit(0)

if __name__ == "__main__":
    main()
"""

def install_hooks():
    hooks_dir = ROOT_DIR / ".git" / "hooks"
    if not hooks_dir.exists():
        # Handle cases where we are in a worktree and .git is a file referencing the main repo
        dot_git = ROOT_DIR / ".git"
        if dot_git.is_file():
            # Parse worktree gitdir pointer: "gitdir: C:/Develop/StockTrading/AnalyzeFinData/.git/worktrees/feat-process-supervisor"
            try:
                with open(dot_git, "r") as f:
                    gitdir = f.read().strip().split("gitdir: ")[-1]
                # The shared hooks are in the central repo's .git/hooks directory
                # gitdir is inside .git/worktrees/<name>; the main .git folder is parent to the worktrees folder
                main_git = Path(gitdir).parent.parent
                hooks_dir = main_git / "hooks"
            except Exception as e:
                print(f"Could not locate central .git hooks directory: {e}")
                return False
        else:
            print("Could not find .git hooks directory.")
            return False

    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit_file = hooks_dir / "pre-commit"
    
    try:
        with open(pre_commit_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(HOOK_CONTENT)
        
        # On non-Windows platforms, make the hook executable
        if sys.platform != "win32":
            os.chmod(pre_commit_file, 0o755)
            
        print(f"✅ Successfully installed defensive pre-commit hook to: {pre_commit_file}")
        return True
    except Exception as e:
        print(f"❌ Failed to install pre-commit hook: {e}")
        return False

if __name__ == "__main__":
    install_hooks()
