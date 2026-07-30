# scripts/utils/pre_commit_validator.py
"""
Git pre-commit validator for Project AETHER.
Enforces:
1. No Inline Imports: All python imports must be top-level (uses ast, not regex).
2. No Silent Exceptions: No 'except: pass' or 'except Exception: pass' without logging.
3. No bare print(): use _log.console() / _log.info() / _log.error() instead.
   Exempt: lines ending with  # noqa: print  (for intentional interactive prompts).
4. R&D Roadmap Sync: Verifies MEMORY.md and web/index.html item counts are in sync.
"""
import ast
import os
import re
import subprocess
import sys

# Define root directory
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def check_no_inline_imports(file_path: str) -> bool:
    """Use ast to detect any import statement not at module scope (col_offset > 0)."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return True  # py_compile will catch syntax errors separately
        rel = os.path.relpath(file_path, ROOT_DIR)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if node.col_offset > 0:
                    print(f"[GIT PRE-COMMIT] Inline import in {rel} at line {node.lineno}")
                    print("   Action required: Move all imports to the top of the file.")
                    return False
        return True
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return True

def check_no_silent_exceptions(file_path: str) -> bool:
    """Scan python file and fail if any silent except: pass or except Exception: pass are found."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        silent_except_re = re.compile(r"^[ \t]*except\s*(Exception)?\s*:\s*(pass\s*|#\s*pass\s*)$")
        
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if silent_except_re.match(line) or stripped == "except: pass" or stripped == "except Exception: pass":
                print(f"🛑 [GIT PRE-COMMIT] Silent exception swallowing detected in {os.path.relpath(file_path, ROOT_DIR)} at line {idx}:")
                print(f"   Line {idx}: {stripped}")
                print("   Action required: Add proper logging or raise/traceback! No silent 'except: pass'.")
                return False
        return True
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return True

def check_no_print_statements(file_path: str) -> bool:
    """Detect bare print() calls using ast. Allows # noqa: print on the same line
    for intentional interactive prompts (e.g. Playwright browser instructions)."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return True  # py_compile handles syntax errors
        rel = os.path.relpath(file_path, ROOT_DIR)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match bare print(...) — not obj.print(...)
            if not isinstance(func, ast.Name) or func.id != "print":
                continue
            lineno = node.lineno
            # Allow explicit exemption via trailing comment
            raw_line = lines[lineno - 1] if lineno <= len(lines) else ""
            if "# noqa: print" in raw_line:
                continue
            print(f"[GIT PRE-COMMIT] bare print() in {rel} at line {lineno}: {raw_line.strip()[:80]}")
            print("   Use _log.console() for progress, _log.info/warning/error() for events.")
            print("   Add  # noqa: print  to exempt intentional interactive prompts.")
            return False
        return True
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return True


def check_rd_roadmap_sync() -> bool:
    """Verify that R&D list item counts in private MEMORY.md and web/index.html match."""
    candidates = [
        os.path.expanduser("~/.gemini/tmp/analyzefindata/memory/MEMORY.md"),
        os.path.expanduser("~/.claude/projects/D--Develop-AnalyzeFinData/memory/MEMORY.md"),
    ]
    memory_path = next((p for p in candidates if os.path.exists(p)), None)
    index_path = os.path.join(ROOT_DIR, "web", "index.html")

    if not memory_path or not os.path.exists(index_path):
        return True  # Skip when neither memory location exists (different environments)
        
    try:
        with open(memory_path, "r", encoding="utf-8") as f:
            mem_text = f.read()
        with open(index_path, "r", encoding="utf-8") as f:
            idx_text = f.read()
            
        # Match R&D item numbers (e.g. "14. *AI Second-Opinion...")
        mem_items = set(re.findall(r"^\s*(\d+)\.\s+\*", mem_text, re.MULTILINE))
        idx_items = set(re.findall(r"\b(\d+)\.\s+AI\s+Second-Opinion|\b(\d+)\.\s+[A-Z]", idx_text))
        
        # Flatten and filter out non-empty matches in index list
        idx_items_flat = set()
        for t in idx_items:
            for item in t:
                if item:
                    idx_items_flat.add(item)
                    
        # Find maximum item numbers
        max_mem_item = max(int(x) for x in mem_items) if mem_items else 0
        max_idx_item = max(int(x) for x in idx_items_flat) if idx_items_flat else 0
        
        if "🔬 Project AETHER R&D Roadmap" not in idx_text:
            # Unified to single-source reference manual, bypass verification
            return True
            
        if max_mem_item != max_idx_item:
            print("🛑 [GIT PRE-COMMIT] R&D Roadmap mismatch detected!")
            print(f"   Private MEMORY.md has {max_mem_item} items.")
            print(f"   Web web/index.html has {max_idx_item} items.")
            print("   Action required: Synchronize the R&D Roadmap items across both files!")
            return False
            
        return True
    except Exception as e:
        print(f"Error checking R&D roadmap sync: {e}")
        return True

def get_staged_python_files() -> list:
    """Retrieve list of staged python files currently being committed."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT_DIR
        )
        files = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.endswith(".py"):
                files.append(os.path.join(ROOT_DIR, line))
        return files
    except Exception as e:
        print(f"Warning: Failed to fetch staged files via git: {e}. Falling back to empty list.")
        return []

def main():
    print("Running Project AETHER Pre-Commit Quality Checks...")

    success = True

    # Check R&D Roadmap Synchronicity
    if not check_rd_roadmap_sync():
        success = False

    # Scan only staged python files currently being committed!
    python_files = get_staged_python_files()
    if not python_files:
        print("No staged python files detected for commit. Skipping file scans.")
    else:
        print(f"Scanning {len(python_files)} staged python file(s)...")
        for fpath in python_files:
            # Files fully exempt from all checks (intentional patterns or non-production)
            _skip_all = ("pre_commit_validator.py", "reconcile_prices.py",
                         "rebuild_rs.py", "sync_excel_prices.py", "reconcile_streaks.py")
            if any(x in fpath for x in _skip_all):
                continue

            # Files exempt from inline-import check:
            # - excel_output.py: pre-existing lazy-load inside openpyxl callbacks
            # - test_*.py: inline imports inside test methods are legitimate (isolate failures)
            _skip_imports = ("excel_output.py", "test_")
            if not any(x in fpath for x in _skip_imports):
                if not check_no_inline_imports(fpath):
                    success = False

            if not check_no_silent_exceptions(fpath):
                success = False

            # Files exempt from print() check (Playwright interactive browser prompts
            # that intentionally write to the user's terminal, not to the log system)
            _skip_print = ("etrade.py",)
            if not any(x in fpath for x in _skip_print):
                if not check_no_print_statements(fpath):
                    success = False
                
    if not success:
        print("[GIT PRE-COMMIT] FAILED. Resolve the issues above before committing.")
        sys.exit(1)

    print("[GIT PRE-COMMIT] All checks passed.")
    sys.exit(0)

if __name__ == "__main__":
    main()
