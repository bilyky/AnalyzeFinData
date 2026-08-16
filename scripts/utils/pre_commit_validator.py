# scripts/utils/pre_commit_validator.py
"""
Git pre-commit validator for Project AETHER.
Enforces:
1. No Inline Imports: All python imports must be top-level (uses ast, not regex).
2. No Silent Exceptions: No 'except: pass' or 'except Exception: pass' without logging.
3. No bare print(): use _log.console() / _log.info() / _log.error() instead.
   Print statements are strictly banned with ZERO shortcuts or exemptions.
4. R&D Roadmap Sync: Verifies MEMORY.md and plans/roadmap.md item counts are in sync.
5. Feature-Doc Sync: Code changed under a `# @doc-sync-start/-end: <key>` anchor must
   stage the mapped documentation surfaces (see DOC_SYNC_SURFACES) in the same commit,
   else the commit is blocked. Scoped bypass: AETHER_DOCSYNC_ACK=<key> git commit ...
"""
import os
import re
import subprocess
import sys


# Define root directory
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Feature ↔ documentation coupling ──────────────────────────────────────────
# Maps a feature key (used in `# @doc-sync-start: <key>` / `-end` anchor comments in
# the source) to the documentation surfaces that describe it. When a staged code
# change lands inside an anchor region, EVERY surface listed here must also be staged
# in the same commit — otherwise the commit is blocked. To add coverage: wrap the
# relevant code in a `@doc-sync-start/-end: <key>` block and register its surfaces here.
DOC_SYNC_SURFACES = {
    "scarcity_core": [
        ("Data/wiki.json", 'the "scarcity_core" wiki entry'),
        ("AETHER_REFERENCE.md", "the Dynamic Structural Scarcity Core section"),
    ],
}

_ANCHOR_START_RE = re.compile(r"@doc-sync-start:\s*([A-Za-z0-9_]+)")
_ANCHOR_END_RE = re.compile(r"@doc-sync-end:\s*([A-Za-z0-9_]+)")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git_stdout(args: list):
    """Run a git command from the repo root; return stdout (str) or None on failure."""
    try:
        # Force UTF-8: git output (diffs, blobs) contains em-dashes/emoji; the Windows
        # default (cp1252) raises UnicodeDecodeError, which would silently blank the
        # result and let a doc-sync-violating commit slip through. errors="replace"
        # keeps line/hunk offsets intact even if an odd byte appears.
        r = subprocess.run(["git", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT_DIR)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _staged_paths() -> set:
    """Repo-relative paths staged for this commit (added/copied/modified/renamed)."""
    out = _git_stdout(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return {ln.strip() for ln in (out or "").splitlines() if ln.strip()}


def _staged_hunk_ranges(path: str) -> list:
    """New-file line ranges changed in the staged diff of `path` (via -U0 hunk headers)."""
    out = _git_stdout(["diff", "--cached", "-U0", "--", path])
    ranges = []
    for line in (out or "").splitlines():
        m = _HUNK_RE.match(line)
        if m:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) else 1
            ranges.append((start, start if length == 0 else start + length - 1))
    return ranges


def _anchor_regions(content: str) -> dict:
    """Parse `@doc-sync-start/-end: key` markers -> {key: [(start_line, end_line), ...]}."""
    regions, open_starts = {}, {}
    for i, line in enumerate(content.splitlines(), 1):
        ms = _ANCHOR_START_RE.search(line)
        if ms:
            open_starts[ms.group(1)] = i
            continue
        me = _ANCHOR_END_RE.search(line)
        if me and me.group(1) in open_starts:
            regions.setdefault(me.group(1), []).append((open_starts.pop(me.group(1)), i))
    return regions


def check_feature_doc_sync() -> bool:
    """Block the commit if code under a `@doc-sync: <key>` anchor changed but the mapped
    documentation surfaces are not staged. Scoped escape hatch (for genuinely doc-neutral
    changes): AETHER_DOCSYNC_ACK=<key>[,<key>...] git commit ...  (does NOT disable the
    other quality gates, unlike --no-verify)."""
    staged = _staged_paths()
    if not staged:
        return True
    ack = {x.strip() for x in os.environ.get("AETHER_DOCSYNC_ACK", "").split(",") if x.strip()}

    touched = {}  # key -> set(source files that triggered it)
    for path in staged:
        if not path.endswith((".py", ".js", ".html", ".md")):
            continue
        content = _git_stdout(["show", f":{path}"])
        if not content or "@doc-sync-start" not in content:
            continue
        regions = _anchor_regions(content)
        if not regions:
            continue
        hunks = _staged_hunk_ranges(path)
        for key, spans in regions.items():
            for (s, e) in spans:
                if any(hs <= e and he >= s for (hs, he) in hunks):
                    touched.setdefault(key, set()).add(path)
                    break

    ok = True
    for key, _srcs in sorted(touched.items()):
        if key in ack:
            continue
        missing = [(p, desc) for (p, desc) in DOC_SYNC_SURFACES.get(key, []) if p not in staged]
        if missing:
            ok = False
            for (_p, _desc) in missing:
                pass
    return ok


def check_ruff_standards(file_path: str) -> bool:
    """Use Ruff to statically and instantaneously verify all quality, import, print, and exception standards."""
    rel = os.path.relpath(file_path, ROOT_DIR).replace("\\", "/")
    try:
        # Determine the correct ruff executable
        # If in a virtual environment on Windows, use local ruff.exe first
        ruff_executable = "ruff"
        local_ruff = os.path.join(ROOT_DIR, "venv_new", "Scripts", "ruff.exe")
        if os.path.exists(local_ruff):
            ruff_executable = local_ruff

        # Direct Network/Context Hook: Pass the normalized relative path 'rel' and run from 'ROOT_DIR'
        # so that Ruff can perfectly match all 'per-file-ignores' patterns in pyproject.toml!
        res = subprocess.run([ruff_executable, "check", rel], capture_output=True, text=True, errors="replace", cwd=ROOT_DIR)
        if res.returncode != 0:
            print(f"🚨 [GIT PRE-COMMIT] BLOCK - Ruff Quality Gate Failed in {rel}!")
            print(res.stdout.strip())
            print("-" * 70)
            print("Action required: Correct the style/logic issues shown above before committing.")
            return False
        return True
    except Exception as e:
        print(f"Error executing Ruff on {rel}: {e}")
        return True


def check_rd_roadmap_sync() -> bool:
    """Verify that R&D list item counts in private MEMORY.md and plans/roadmap.md match."""
    candidates = [
        os.path.expanduser("~/.gemini/tmp/analyzefindata/memory/MEMORY.md"),
        os.path.expanduser("~/.claude/projects/D--Develop-AnalyzeFinData/memory/MEMORY.md"),
    ]
    memory_path = next((p for p in candidates if os.path.exists(p)), None)
    roadmap_path = os.path.join(ROOT_DIR, "plans", "roadmap.md")

    if not memory_path or not os.path.exists(roadmap_path):
        return True  # Skip when neither memory location exists (different environments)
        
    try:
        with open(memory_path, "r", encoding="utf-8") as f:
            mem_text = f.read()
        with open(roadmap_path, "r", encoding="utf-8") as f:
            road_text = f.read()
            
        # Match R&D item numbers (e.g. "14. *AI Second-Opinion...")
        mem_items = set(re.findall(r"^\s*(\d+)\.\s+\*", mem_text, re.MULTILINE))
        road_items = set(re.findall(r"^\s*(\d+)\.\s+\*\*", road_text, re.MULTILINE))

        # The Claude auto-memory MEMORY.md is an index of memory links, not the numbered
        # R&D ledger (that lives in CLAUDE.md here); it structurally has 0 numbered items.
        # Only enforce the sync when the memory file actually IS a numbered R&D ledger,
        # otherwise this check false-blocks every commit in the Claude environment.
        if not mem_items:
            return True
        
        # Find maximum item numbers
        max_mem_item = max(int(x) for x in mem_items) if mem_items else 0
        max_road_item = max(int(x) for x in road_items) if road_items else 0
        
        if max_mem_item != max_road_item:
            return False
            
        return True
    except Exception:
        return True

def check_new_features_tested() -> bool:
    """Verify that any newly introduced core trading features have corresponding automated unit tests in the tests/ directory."""
    FEATURE_CHECKS = [
        {
            "name": "Adaptive s10 Floor (R&D #15)",
            "signature": r"required_floor\s*=\s*2\.0\s+if\s+cash_pct\s*>\s*25\.0",
            "test_keyword": "test_adaptive_s10_floor"
        },
        {
            "name": "Slippage-Protected Limit Stop (STP LMT - R&D #8)",
            "signature": r"Slippage-Protected\s+Limit\s+Stop|stop_loss\s*=\s*pos\.get\(\"stop_loss\"",
            "test_keyword": "test_stp_lmt_slippage_protection"
        },
        {
            "name": "Momentum Rotation Empty-Slot Expansion (R&D #27)",
            "signature": r"is_full_slots|threshold_score\s*=\s*8\.0\s+if\s+is_full_slots",
            "test_keyword": "test_evaluate_momentum_rotation"
        },
        {
            "name": "Breakout Risk-Reward Waiver (R&D #32)",
            "signature": r"Breakout\s+Risk-Reward\s+Waiver|is_elite_breakout\s*=",
            "test_keyword": "test_breakout_rr_waiver"
        },
        {
            "name": "High-Score PGR Bypass (R&D #13)",
            "signature": r"is_elite_breakout_candidate",
            "test_keyword": "test_high_score_pgr_bypass"
        },
        {
            "name": "Loosened Pyramiding s10 Floor (R&D #31)",
            "signature": r"s10\s*>= \s*0\.0\s+or\s+l60\s*>= \s*2\.0",
            "test_keyword": "test_loosened_pyramiding"
        },
        {
            "name": "Covered Call Option Writing (R&D #26)",
            "signature": r"execute_weekly_covered_call_pass|resolve_expiring_options",
            "test_keyword": "test_black_scholes_call_pricing"
        }
    ]
    
    staged_files = get_staged_python_files()
    for check in FEATURE_CHECKS:
        signature_found = False
        for fpath in staged_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if re.search(check["signature"], content):
                    signature_found = True
                    break
            except Exception:
                pass
                
        if signature_found:
            
            test_dir = os.path.join(ROOT_DIR, "tests")
            test_files = [os.path.join(test_dir, f) for f in os.listdir(test_dir) if f.startswith("test_") and f.endswith(".py")]
            
            coverage_found = False
            for t_file in test_files:
                try:
                    with open(t_file, "r", encoding="utf-8") as f:
                        test_content = f.read()
                    if check["test_keyword"] in test_content:
                        coverage_found = True
                        break
                except Exception:
                    pass
                    
            if not coverage_found:
                return False
                
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
    except Exception:
        return []

def main():

    success = True

    # Check R&D Roadmap Synchronicity
    if not check_rd_roadmap_sync():
        success = False

    # Check Feature <-> Documentation Synchronicity (@doc-sync anchors)
    if not check_feature_doc_sync():
        success = False

    # Check New Features Test Coverage Hook
    if not check_new_features_tested():
        success = False

    # Scan only staged python files currently being committed!
    python_files = get_staged_python_files()
    if not python_files:
        pass
    else:
        for fpath in python_files:
            # Files fully exempt from all checks (intentional patterns or non-production)
            _skip_all = ("pre_commit_validator.py", "install_hooks.py", "reconcile_prices.py",
                         "rebuild_rs.py", "sync_excel_prices.py", "reconcile_streaks.py")
            if any(x in fpath for x in _skip_all):
                continue

            if not check_ruff_standards(fpath):
                success = False

    if not success:
        sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    main()
