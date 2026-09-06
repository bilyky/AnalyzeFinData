import os
import sys
import stat

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOKS_DIR = os.path.join(ROOT_DIR, ".git", "hooks")

# 1. Pre-commit Hook Content
# It executes the pre_commit_validator.py script
PRE_COMMIT_CONTENT = """#!/bin/sh
echo "Executing Git pre-commit hook..."
python scripts/utils/pre_commit_validator.py
"""

# 2. Pre-push Hook Content
# It blocks direct pushes to main/master branches unless authorized, and enforces tests are green
PRE_PUSH_CONTENT = """#!/bin/sh
echo ""
echo "🚨 [GIT PRE-PUSH] Running local unit tests before push..."
echo "----------------------------------------------------------"

# Enforce that code MUST be completely green locally first
# Check and run pytest using the correct virtual environment
if [ -f "venv_new/Scripts/pytest" ]; then
    venv_new/Scripts/pytest
elif [ -f "venv_new/bin/pytest" ]; then
    venv_new/bin/pytest
else
    python -m pytest
fi

test_exit_code=$?
echo "----------------------------------------------------------"

if [ $test_exit_code -ne 0 ]; then
    echo "🚨 [GIT PRE-PUSH] BLOCK - Local unit tests failed!"
    echo "   Your code must be 100% green locally before pushing to any branch on GitHub."
    echo "   Please fix the failing tests and try pushing again."
    echo ""
    exit 1
fi

echo "✅ [GIT PRE-PUSH] All local unit tests passed green!"
echo ""

# Fetch the active branch being pushed
branch=$(git branch --show-current)

if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
    if [ "$AETHER_ALLOW_DIRECT_MAIN_PUSH" != "1" ]; then
        echo "🚨 [GIT PRE-PUSH] BLOCK - Direct pushes to the stable '$branch' branch are strictly forbidden."
        echo "   Your buggy, low-level code must never be merged to main before thorough Pull Request review."
        echo "   Please push to your dedicated feature/PR branch (e.g. \`git push origin feat/my-fix\`) instead."
        echo ""
        echo "   To override this lock for emergency administrative force-resets only, run:"
        echo "       export AETHER_ALLOW_DIRECT_MAIN_PUSH=1"
        echo ""
        exit 1
    fi
fi

echo "Pre-push branch check passed. Proceeding to push..."
exit 0
"""

def make_executable(filepath):
    """Set execution permissions for Unix-like systems."""
    try:
        st = os.stat(filepath)
        os.chmod(filepath, st.st_mode | stat.S_IEXEC)
    except Exception:
        pass

def main():
    if not os.path.exists(HOOKS_DIR):
        print(f"Error: Git hooks directory not found at {HOOKS_DIR}")
        sys.exit(1)

    # Write pre-commit hook
    pre_commit_path = os.path.join(HOOKS_DIR, "pre-commit")
    with open(pre_commit_path, "w", encoding="utf-8") as f:
        f.write(PRE_COMMIT_CONTENT)
    make_executable(pre_commit_path)
    print(f"Installed Git pre-commit hook at {pre_commit_path}")

    # Write pre-push hook
    pre_push_path = os.path.join(HOOKS_DIR, "pre-push")
    with open(pre_push_path, "w", encoding="utf-8") as f:
        f.write(PRE_PUSH_CONTENT)
    make_executable(pre_push_path)
    print(f"Installed Git pre-push hook at {pre_push_path}")

    print("Git hooks installation completed successfully!")

if __name__ == "__main__":
    main()
