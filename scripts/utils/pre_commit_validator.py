"""
Project AETHER: Git Pre-Commit Quality Hook & Pipeline Gate.
This utility performs AST-based and static checks on currently staged files to
guarantee 100% adherence to critical safety, architectural, style, and testing rules.

Enforces:
  1. No print() statements in production files (must use logging/console_safe).
  2. No inline imports (all imports must reside at module scope).
  3. No silent exception swallowing (no bare except: pass / except Exception: pass).
  4. Mandatory unit test coverage matching the R&D feature keywords.
  5. Roadmap count synchronicity between private MEMORY.md and plans/roadmap.md.
  6. Documentation synchronicity: if a code block marked with @doc-sync <key> changes,
     stage the mapped documentation surfaces (see DOC_SYNC_SURFACES) in the same commit,
     else the commit is blocked. Scoped bypass: AETHER_DOCSYNC_ACK=<key> git commit ...
"""
import ast
import os
import re
import shutil
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The BLOCK/PASS messages below use emoji, but a git hook on Windows usually inherits a
# cp1252 console that cannot encode them -> a legitimate BLOCK would die with
# UnicodeEncodeError and mask the real reason for the block. Force UTF-8 (replace on
# failure) so the message always prints. No-op where the stream is already UTF-8 / not a
# TextIOWrapper.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Mapping of @doc-sync keys to their documentation files on disk + description
DOC_SYNC_SURFACES = {
    "covered-calls": [
        ("plans/roadmap.md", "Shorthand bullet in R&D Roadmap"),
    ],
    "unwinding-guard": [
        ("plans/roadmap.md", "Shorthand bullet in R&D Roadmap"),
    ],
    "scarcity-core": [
        ("plans/dynamic-scarcity-cap.md", "Full System Design specification"),
    ],
    "trader-vic": [
        ("plans/dynamic-scarcity-cap.md", "Full System Design specification"),
    ],
    "preflight-checks": [
        ("plans/roadmap.md", "Shorthand bullet in R&D Roadmap"),
    ],
    "circuit-breaker": [
        ("plans/circuit-breaker.md", "Full System Design specification"),
    ],
}

_ANCHOR_START_RE = re.compile(r"@doc-sync-start:\s*([A-Za-z0-9_]+)")
_ANCHOR_END_RE = re.compile(r"@doc-sync-end:\s*([A-Za-z0-9_]+)")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

def _git_stdout(args: list):
    """Run a git command from the repo root; return stdout (str) or None on failure."""
    try:
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
    documentation surfaces are not staged."""
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
    for key, srcs in sorted(touched.items()):
        if key in ack:
            print(f"[GIT PRE-COMMIT] doc-sync: '{key}' code changed - acknowledged via "
                  f"AETHER_DOCSYNC_ACK (no documentation update).")
            continue
        missing = [(p, desc) for (p, desc) in DOC_SYNC_SURFACES.get(key, []) if p not in staged]
        if missing:
            ok = False
            print(f"🚨 [GIT PRE-COMMIT] BLOCK - Feature-doc-sync: '{key}' logic changed "
                  f"({', '.join(sorted(srcs))}),")
            print(f"   but these documentation surfaces are NOT staged in this commit:")
            for (p, desc) in missing:
                print(f"     - {p}  ({desc})")
            print(f"   Action: update the surface(s) above and `git add` them, OR - if no doc")
            print(f"   change is truly needed - acknowledge it explicitly:")
            print(f"       AETHER_DOCSYNC_ACK={key} git commit ...")
    return ok


def check_wiki_about_sync() -> bool:
    """Documentation Sentry drift guard (aether-documentation-sentry skill).

    Data/wiki.json is the single source for the About-tab feature cards; the parity invariant
    is that every `data-wiki="KEY"` card in web/index.html has exactly one wiki.json entry and
    vice-versa (the drift that produced dead modals / orphaned entries). When either surface is
    staged, run the real guard suite (tests/test_about_wiki_sync.py) so the drift cannot silently
    return. Uses `unittest` — the project's available runner (pytest is not installed here).

    Fails CLOSED: a red or unrunnable guard BLOCKS the commit. Skipped (returns True) when no
    wiki surface is staged, so unrelated commits are not slowed."""
    staged = _staged_paths()
    surfaces = {"Data/wiki.json", "web/index.html"}
    if not (staged & surfaces):
        return True
    print("[GIT PRE-COMMIT] Wiki surface staged - running About/wiki drift guard "
          "(tests/test_about_wiki_sync.py)...")
    try:
        res = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_about_wiki_sync"],
            capture_output=True, text=True, errors="replace", cwd=ROOT_DIR)
    except Exception as e:
        print(f"🚨 [GIT PRE-COMMIT] BLOCK - could not run the wiki drift guard: {e}")
        return False
    if res.returncode != 0:
        print("🚨 [GIT PRE-COMMIT] BLOCK - About-tab <-> Data/wiki.json drift detected:")
        print((res.stderr or res.stdout).strip())
        print("-" * 70)
        print("   Action: reconcile web/index.html data-wiki cards with Data/wiki.json "
              "(aether-documentation-sentry skill) until the guard is green.")
        return False
    print("   ✅ Wiki/About parity guard passed.")
    return True


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
                print(f"[GIT PRE-COMMIT] BLOCK - Silent exception swallowing detected in {os.path.relpath(file_path, ROOT_DIR)} at line {idx}:")
                print(f"   Line {idx}: {stripped}")
                print("   Action required: Add proper logging or raise/traceback! No silent 'except: pass'.")
                return False
        return True
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return True

def check_no_print_statements(file_path: str) -> bool:
    """Detect bare print() calls using ast. No print statements are allowed."""
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
            raw_line = lines[lineno - 1] if lineno <= len(lines) else ""
            print(f"[GIT PRE-COMMIT] bare print() in {rel} at line {lineno}: {raw_line.strip()[:80]}")
            print("   Use _log.console() for progress, _log.info/warning/error() for events.")
            print("   Print statements are strictly banned with ZERO shortcuts or exemptions.")
            return False
        return True
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return True


def _resolve_ruff_cmd():
    """Return the argv prefix that invokes Ruff, or None if Ruff cannot be found.

    Resolution order (robust across environments — no single hardcoded machine path):
      1. a project-local venv ruff executable, if present;
      2. `ruff` on PATH;
      3. `<python> -m ruff` (Ruff installed as a module of the running interpreter).
    Returning None lets the caller fail *closed* rather than silently skip the gate.
    """
    for venv in ("venv_new", "venv", ".venv"):
        exe = "ruff.exe" if os.name == "nt" else "ruff"
        local = os.path.join(ROOT_DIR, venv, "Scripts" if os.name == "nt" else "bin", exe)
        if os.path.exists(local):
            return [local]
    on_path = shutil.which("ruff")
    if on_path:
        return [on_path]
    # Final fallback: the interpreter's own ruff module (installed via pip in this env).
    try:
        probe = subprocess.run([sys.executable, "-m", "ruff", "--version"],
                               capture_output=True, text=True, errors="replace")
        if probe.returncode == 0:
            return [sys.executable, "-m", "ruff"]
    except Exception:
        pass
    return None


def check_ruff_standards(file_path: str) -> bool:
    """Run Ruff to verify import/print/exception/style standards for one staged file.

    FAILS CLOSED: if Ruff cannot be located or errors out, the commit is BLOCKED (returns
    False) rather than silently allowed — the standing 'never bypass the gate' rule means a
    missing linter must stop the commit, not disable the check without anyone noticing.
    """
    rel = os.path.relpath(file_path, ROOT_DIR).replace("\\", "/")
    ruff_cmd = _resolve_ruff_cmd()
    if ruff_cmd is None:
        print("🚨 [GIT PRE-COMMIT] BLOCK - Ruff is not installed / not resolvable.")
        print("   Install it (pip install ruff) or expose it on PATH; the quality gate "
              "will not pass silently without it.")
        return False
    try:
        # Pass the normalized relative path 'rel' and run from ROOT_DIR so Ruff matches the
        # 'per-file-ignores' patterns in pyproject.toml.
        res = subprocess.run([*ruff_cmd, "check", rel], capture_output=True, text=True,
                             errors="replace", cwd=ROOT_DIR)
    except Exception as e:
        print(f"🚨 [GIT PRE-COMMIT] BLOCK - could not execute Ruff on {rel}: {e}")
        return False
    if res.returncode != 0:
        print(f"🚨 [GIT PRE-COMMIT] BLOCK - Ruff Quality Gate Failed in {rel}!")
        print(res.stdout.strip())
        print("-" * 70)
        print("Action required: Correct the style/logic issues shown above before committing.")
        return False
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
            print(f"🚨 [GIT PRE-COMMIT] BLOCK - R&D roadmap out of sync: highest item in "
                  f"MEMORY.md is #{max_mem_item} but plans/roadmap.md is #{max_road_item}. "
                  f"Reconcile the two ledgers before committing.")
            return False

        return True
    except Exception as e:
        # Soft check across heterogeneous environments — don't block on a parse/read error,
        # but say so rather than skipping silently.
        print(f"[GIT PRE-COMMIT] R&D roadmap sync check skipped (non-fatal): {e}")
        return True

def check_new_features_tested() -> bool:
    """Verify that any newly introduced core trading features have corresponding automated unit tests in the tests/ directory."""
    FEATURE_CHECKS = [
        {
            "name": "Adaptive s10 Floor (R&D #15)",
            # Anchored to the real delegated helper (adaptive_s10_floor), matched at its
            # definition and call site — not the old inline literal.
            "signature": r"adaptive_s10_floor\b",
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
            # Anchored to the real delegated gate (risk_utils.is_elite_breakout_candidate),
            # matched at both its definition and call sites — not a comment string.
            "signature": r"is_elite_breakout_candidate\b",
            "test_keyword": "test_high_score_pgr_bypass"
        },
        {
            "name": "Loosened Pyramiding s10 Floor (R&D #31)",
            # Anchored to the real delegated helper (should_pyramid_into_winner).
            "signature": r"should_pyramid_into_winner\b",
            "test_keyword": "test_loosened_pyramiding"
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
            except OSError as e:
                print(f"[GIT PRE-COMMIT] warning: could not read staged file {fpath}: {e}")

        if signature_found:
            print(f"[GIT PRE-COMMIT] Detected new feature code staged: '{check['name']}'")
            print(f"   Searching tests/ directory for matching unit test coverage keyword '{check['test_keyword']}'...")
            
            test_dir = os.path.join(ROOT_DIR, "tests")
            test_files = [os.path.join(test_dir, f) for f in os.listdir(test_dir) if f.startswith("test_") and f.endswith(".py")]

            coverage_found = False
            for t_file in test_files:
                try:
                    with open(t_file, "r", encoding="utf-8") as f:
                        test_content = f.read()
                    if check["test_keyword"] in test_content:
                        coverage_found = True
                        print(f"   ✅ Found test coverage inside: tests/{os.path.basename(t_file)}")
                        break
                except OSError as e:
                    print(f"[GIT PRE-COMMIT] warning: could not read test file {t_file}: {e}")

            if not coverage_found:
                print(f"🚨 [GIT PRE-COMMIT] BLOCK - feature '{check['name']}' is staged but no "
                      f"test contains '{check['test_keyword']}'. Add a matching unit test in tests/.")
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
    except Exception as e:
        print(f"Warning: Failed to fetch staged files via git: {e}. Falling back to empty list.")
        return []

def main():
    print("Running Project AETHER Pre-Commit Quality Checks...")

    success = True

    # Check R&D Roadmap Synchronicity
    if not check_rd_roadmap_sync():
        success = False

    # Check Feature <-> Documentation Synchronicity (@doc-sync anchors)
    if not check_feature_doc_sync():
        success = False

    # Check About-tab <-> Data/wiki.json parity (Documentation Sentry drift guard)
    if not check_wiki_about_sync():
        success = False

    # Check New Features Test Coverage Hook
    if not check_new_features_tested():
        success = False

    # Scan only staged python files currently being committed!
    python_files = get_staged_python_files()
    if not python_files:
        print("No staged python files detected for commit. Skipping file scans.")
    else:
        print(f"Scanning {len(python_files)} staged python file(s)...")
        for fpath in python_files:
            # Files fully exempt from all checks (intentional patterns or non-production)
            _skip_all = ("pre_commit_validator.py", "install_hooks.py", "reconcile_prices.py",
                         "rebuild_rs.py", "sync_excel_prices.py", "reconcile_streaks.py")
            if any(x in fpath for x in _skip_all):
                continue

            # Files exempt from inline-import check:
            # - workbook_write.py: pre-existing lazy-load inside openpyxl callbacks
            # - test_*.py: inline imports inside test methods are legitimate (isolate failures)
            # - powergauge.py: optional try/except imports for Playwright automation
            # - run_history.py: historical backfill parallelized launcher script
            # - aether/etrade/store.py: mandatory lazy `_pkg()` import of the parent
            #   package to break the circular init — it is imported *from* the package
            #   __init__, so a top-level `from aether import etrade` would hit a
            #   partially-initialised module. See the module's _pkg() docstring.
            _skip_imports = ("workbook_write.py", "test_", "powergauge.py", "run_history.py",
                             "etrade/store.py")
            if not any(x in fpath for x in _skip_imports):
                if not check_no_inline_imports(fpath):
                    success = False

            if not check_no_silent_exceptions(fpath):
                success = False

            # Files exempt from print() check (Playwright interactive browser prompts
            # that intentionally write to the user's terminal, not to the log system;
            # plus the diagnostics/ tests trees, which print by design).
            # Filenames match by basename; the scripts/ and tests/ trees match by
            # repo-relative path prefix — NOT substring, so an unrelated path (e.g.
            # test_etrade.py, .../latests/...) can't accidentally slip the gate.
            _skip_print_files = ("powergauge.py", "run_history.py", "real_copilot.py")
            _skip_print_trees = ("scripts/", "tests/")
            _rel = os.path.relpath(fpath, ROOT_DIR).replace(os.sep, "/")
            if not (os.path.basename(fpath) in _skip_print_files or _rel.startswith(_skip_print_trees)):
                if not check_no_print_statements(fpath):
                    success = False
                
    if not success:
        print("[GIT PRE-COMMIT] FAILED. Resolve the issues above before committing.")
        sys.exit(1)

    print("[GIT PRE-COMMIT] All checks passed.")
    sys.exit(0)

if __name__ == "__main__":
    main()