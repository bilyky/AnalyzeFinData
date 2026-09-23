"""Project AETHER: prune local git worktrees and branches for finished PRs.

Companion automation for the ``/review-prs`` skill (section 9, "Clean up the
local footprint once a PR is MERGED"). A review/prepare campaign leaves a
per-PR ``_wt_*`` worktree and local branch behind; once the backing PR is
merged or closed, that footprint is dead weight. This tool retires it, gated
strictly on the PR's own state (never on git ancestry, which is unreliable for
squash-merged branches).

Safe by design:
  * Dry-run unless ``--apply`` is passed (prints the plan, changes nothing).
  * Acts on a worktree/branch only when its backing PR is ``merged==true`` OR
    closed-unmerged (``state==closed and merged==false``). Open PRs are kept.
  * Never removes a dirty worktree: ``git worktree remove`` is run WITHOUT
    ``--force``, so an uncommitted worktree self-refuses and is reported.
  * Never touches the primary (main) worktree, the current branch, ``main``,
    or any worktree whose path lives under ``.claude/`` (harness-managed;
    auto-cleaned by the harness).
  * Never deletes a local branch that still backs an OPEN PR.
  * A branch whose name matches no PR head ref is left alone (possible unpushed
    work) rather than guessed at.

Network: this talks to GitHub through ``gh``. Behind a corporate proxy, export
``HTTPS_PROXY``/``HTTP_PROXY`` (the same proxy ``git`` uses) before running; the
tool inherits them from the environment and never hardcodes a proxy host.

Usage::

    python scripts/utils/prune_merged_worktrees.py              # dry-run report
    python scripts/utils/prune_merged_worktrees.py --apply      # execute prune
    python scripts/utils/prune_merged_worktrees.py --closed-only # only closed-unmerged
    python scripts/utils/prune_merged_worktrees.py --merged-only # only merged==true
"""
import argparse
import os
import shutil
import subprocess
import sys

REPO_DEFAULT = "bilyky/AnalyzeFinData"


def _out(msg=""):
    """User-facing report line (sys.stdout.write per the CLI-output convention)."""
    sys.stdout.write(msg + "\n")


def _resolve_gh():
    """Locate the gh CLI portably; fall back to the known Windows install path."""
    found = shutil.which("gh")
    if found:
        return found
    fallback = r"C:\Program Files\GitHub CLI\gh.exe"
    if os.path.exists(fallback):
        return fallback
    raise RuntimeError("gh CLI not found on PATH or the default install path")


def _run(cmd, check=True):
    """Run a command, returning (rc, stdout, stderr) with text decoding."""
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "command failed (%d): %s\n%s" % (proc.returncode, " ".join(cmd), proc.stderr.strip())
        )
    return proc.returncode, proc.stdout, proc.stderr


def _gh_json(gh, repo, path, jq):
    """Query the gh REST API and return decoded lines from a jq expression."""
    _, out, _ = _run([gh, "api", "repos/%s/%s" % (repo, path), "--jq", jq])
    return [ln for ln in out.splitlines() if ln.strip()]


def pr_state_map(gh, repo):
    """Map each PR head ref -> dict(number, state, merged).

    A head ref can back more than one PR (a branch reopened under a new PR,
    or a closed PR whose branch was reused for a fresh open one). OPEN state
    MUST dominate: a branch still backing any open PR is never prunable. So
    'open' is queried LAST and overwrites any 'closed' entry for the same ref
    — never the reverse — which is the safety guarantee, not a cosmetic order.
    """
    state = {}
    for scope in ("closed", "open"):
        rows = _gh_json(
            gh,
            repo,
            "pulls?state=%s&per_page=100" % scope,
            r'.[] | "\(.head.ref)\t\(.number)\t\(.state)\t\(.merged)"',
        )
        for row in rows:
            parts = row.split("\t")
            if len(parts) != 4:
                continue
            ref, number, st, merged = parts
            state[ref] = {
                "number": int(number),
                "state": st,
                "merged": merged.strip().lower() == "true",
            }
    return state


def list_worktrees():
    """Parse ``git worktree list --porcelain`` into dicts."""
    _, out, _ = _run(["git", "worktree", "list", "--porcelain"])
    trees, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                trees.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": None, "detached": False}
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].replace("refs/heads/", "", 1)
        elif line.strip() == "detached":
            cur["detached"] = True
    if cur:
        trees.append(cur)
    return trees


def current_branch():
    _, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip()


def is_dirty(path):
    rc, out, _ = _run(["git", "-C", path, "status", "--porcelain"], check=False)
    return rc != 0 or bool(out.strip())


def _is_harness_path(path):
    norm = path.replace("\\", "/")
    return "/.claude/" in norm or norm.endswith("/.claude")


def _finished(info, want_merged, want_closed):
    """A PR is 'finished' (prunable) if merged, or closed-unmerged, per flags."""
    if info is None:
        return False
    if info["merged"]:
        return want_merged
    if info["state"] == "closed":
        return want_closed
    return False


def plan(gh, repo, want_merged, want_closed):
    states = pr_state_map(gh, repo)
    cur = current_branch()
    main_wt = os.path.realpath(_run(["git", "rev-parse", "--show-toplevel"])[1].strip())

    actions = []  # (kind, target, reason)
    keep = []     # (target, reason)
    attached_branches = set()

    for wt in list_worktrees():
        path, branch = wt["path"], wt["branch"]
        if branch:
            attached_branches.add(branch)
        rp = os.path.realpath(path)
        if rp == main_wt:
            continue
        if _is_harness_path(path):
            keep.append((path, "harness-managed (.claude/) — left to the harness"))
            continue
        if wt["detached"] or not branch:
            keep.append((path, "detached/no-branch — review by hand"))
            continue
        info = states.get(branch)
        if not _finished(info, want_merged, want_closed):
            label = "open PR #%d" % info["number"] if info and info["state"] == "open" else "no finished PR"
            keep.append((path, "%s — kept" % label))
            continue
        if is_dirty(path):
            keep.append((path, "PR #%d finished but worktree DIRTY — skipped (review)" % info["number"]))
            continue
        verb = "merged" if info["merged"] else "closed-unmerged"
        actions.append(("worktree", path, branch, "PR #%d %s" % (info["number"], verb)))

    # Branch-only prune: local branches with no worktree, backed by a finished PR.
    _, out, _ = _run(["git", "branch", "--format=%(refname:short)"])
    for branch in [b.strip() for b in out.splitlines() if b.strip()]:
        if branch == "main" or branch == cur or branch in attached_branches:
            continue
        info = states.get(branch)
        if not _finished(info, want_merged, want_closed):
            continue
        verb = "merged" if info["merged"] else "closed-unmerged"
        actions.append(("branch", None, branch, "PR #%d %s (branch-only)" % (info["number"], verb)))

    return actions, keep


def execute(actions):
    done, failed = [], []
    for kind, path, branch, reason in actions:
        if kind == "worktree":
            rc, _, err = _run(["git", "worktree", "remove", path], check=False)
            if rc != 0:
                failed.append(("worktree", path, err.strip()))
                continue
            done.append(("worktree", path, reason))
        rc, _, err = _run(["git", "branch", "-D", branch], check=False)
        if rc != 0:
            failed.append(("branch", branch, err.strip()))
        else:
            done.append(("branch", branch, reason))
    _run(["git", "worktree", "prune"], check=False)
    return done, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prune worktrees/branches for merged or closed PRs.")
    ap.add_argument("--apply", action="store_true", help="execute the prune (default: dry-run)")
    ap.add_argument("--repo", default=REPO_DEFAULT, help="owner/repo (default: %s)" % REPO_DEFAULT)
    ap.add_argument("--merged-only", action="store_true", help="only prune merged==true PRs")
    ap.add_argument("--closed-only", action="store_true", help="only prune closed-unmerged PRs")
    args = ap.parse_args(argv)

    want_merged = not args.closed_only
    want_closed = not args.merged_only

    gh = _resolve_gh()
    actions, keep = plan(gh, args.repo, want_merged, want_closed)

    _out("=== KEEP (%d) ===" % len(keep))
    for target, reason in keep:
        _out("  KEEP     %s  [%s]" % (target, reason))
    _out("")
    _out("=== PRUNE candidates (%d) ===" % len(actions))
    for kind, path, branch, reason in actions:
        tgt = path if kind == "worktree" else branch
        _out("  %-8s %s  [%s]" % (kind.upper(), tgt, reason))

    if not args.apply:
        _out("")
        _out("DRY-RUN — nothing changed. Re-run with --apply to prune the %d candidate(s)." % len(actions))
        return 0

    _out("")
    _out("--- APPLYING ---")
    done, failed = execute(actions)
    for kind, tgt, reason in done:
        _out("  removed  %-8s %s  [%s]" % (kind, tgt, reason))
    for kind, tgt, err in failed:
        _out("  FAILED   %-8s %s  -> %s" % (kind, tgt, err))
    _out("")
    _out("Done: %d removed, %d failed." % (len(done), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
