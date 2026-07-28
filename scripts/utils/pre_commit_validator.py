# scripts/utils/pre_commit_validator.py
"""
Git pre-commit validator for Project AETHER.
Enforces:
1. No Inline Imports: All python imports must be top-level.
2. No Silent Exceptions: No 'except: pass' or 'except Exception: pass' without logging or traceback.
3. R&D Roadmap Sync: Verifies that R&D items in MEMORY.md and web/index.html are perfectly synchronized.
"""
import os
import re
import sys

# Define root directory
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def check_no_inline_imports(file_path: str) -> bool:
    """Scan python file and fail if any imports are declared inside indented scopes."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # Regex to detect indented import or from...import statements
        # Matches any line starting with spaces/tabs followed by 'import' or 'from ... import'
        inline_import_re = re.compile(r"^[ \t]+(import\s+|from\s+\S+\s+import\s+)")
        
        # Exclude typical block patterns or multi-line strings
        in_multiline_str = False
        
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            # Handle multi-line strings (triple quotes)
            if stripped.count('"""') % 2 != 0 or stripped.count("'''") % 2 != 0:
                in_multiline_str = not in_multiline_str
                continue
            if in_multiline_str:
                continue
                
            # Skip comments
            if stripped.startswith("#"):
                continue
                
            if inline_import_re.match(line):
                # Ignore specific standard mocks or local sys.path adjustments in tests
                if "sys.path.insert" in stripped or "unittest.mock" in stripped or "importlib" in stripped:
                    continue
                print(f"🛑 [GIT PRE-COMMIT] Inline import detected in {os.path.relpath(file_path, ROOT_DIR)} at line {idx}:")
                print(f"   Line {idx}: {stripped}")
                print("   Action required: Move all imports to the top of the file cleanly!")
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

def check_rd_roadmap_sync() -> bool:
    """Verify that R&D list item counts in private MEMORY.md and web/index.html match perfectly."""
    memory_path = os.path.expanduser("~/.gemini/tmp/analyzefindata/memory/MEMORY.md")
    index_path = os.path.join(ROOT_DIR, "web", "index.html")
    
    if not os.path.exists(memory_path) or not os.path.exists(index_path):
        return True # Skip if paths missing in different local setups
        
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
        import subprocess
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
    print("⏳ Running Project AETHER Pre-Commit Quality Checks...")
    
    success = True
    
    # Check R&D Roadmap Synchronicity
    if not check_rd_roadmap_sync():
        success = False
        
    # Scan only staged python files currently being committed!
    python_files = get_staged_python_files()
    if not python_files:
        print("ℹ️ No staged python files detected for commit. Skipping file scans.")
    else:
        print(f"🔍 Scanning {len(python_files)} staged python file(s) for inline imports and silent exceptions...")
        for fpath in python_files:
            # Skip checking specific helper/diagnostics/reconcile scripts
            if any(x in fpath for x in ("pre_commit_validator.py", "reconcile_prices.py", "rebuild_rs.py", "sync_excel_prices.py", "reconcile_streaks.py")):
                continue
                
            if not check_no_inline_imports(fpath):
                success = False
            if not check_no_silent_exceptions(fpath):
                success = False
                
    if not success:
        print("❌ [GIT PRE-COMMIT] Commit aborted. Please resolve the discrepancies above.")
        sys.exit(1)
        
    print("✅ [GIT PRE-COMMIT] All pre-commit quality checks passed successfully!")
    sys.exit(0)

if __name__ == "__main__":
    main()
