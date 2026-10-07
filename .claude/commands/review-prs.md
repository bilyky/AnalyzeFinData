# Code-Review Open PRs → paste-ready findings → post as PR comments

Review the repo's **open pull requests one at a time** against the fixed rubric below, write
**paste-ready markdown** to `reviews/PR-<n>-<slug>.md`, then post each as a **PR comment**.
Companion to [ship-pr.md](./ship-pr.md) (that one *opens* PRs; this one *reviews* them).
Agent-agnostic: any agent can read and follow this file; shell snippets are POSIX `git`/`gh`.
Environment-specific troubleshooting and the full worked examples live in
[docs/skills/review-prs-reference.md](../../docs/skills/review-prs-reference.md) — open it only when
a step below points you there.

Args: a PR number or list (`/review-prs 5`, `/review-prs 2 3 4 5`). None → every open PR. A review of a
change you authored has little independent value — say so in the write-up when it applies.

## 0. Understand the GOAL before reading a single line of diff

A review that only checks the code is internally *consistent* will bless code that does the wrong
thing. Anchor on intent, then judge the code against that intent.

1. **Read the stated goal:** title, body, commit messages (`git log origin/main..<head> --pretty=full`),
   linked issue / roadmap item.
2. **Find the authoritative definition.** Here features are numbered **R&D items**, single-sourced in:
   `plans/roadmap.md` (prose intent) · `scripts/utils/pre_commit_validator.py::FEATURE_CHECKS` (the
   canonical code anchor + required test keyword) · the feature's own unit test (the executable spec).
3. **Judge against the definition:** does it implement what the item says *through the canonical
   helper/gate it names*, or re-implement it inline with different thresholds? A divergent second
   copy is a multiple-source-of-truth bug even when it compiles and its own tests pass.
4. **For "protect / decouple / isolate X" goals, trace the real data flow** — which file is *written*,
   which is *read* — and confirm the mechanism removes the problem instead of relocating it.
5. **Claims in commit messages and PR bodies are claims too** — an incident, a dataset ("rejected in
   both runs"), a "no behavior change". Check them against the logs/data/code they cite; say which you
   could not verify.
6. **Statistical claims (studies, backtests):** check the unit of independence — observations sharing a
   date, or overlapping horizons, inflate a plain t (see `run-study.md`). If the decision statistic
   changed after the result was seen, the write-up must state the verdict under the original rule.

(Worked examples: definition divergence PR #27, relocated vulnerability PR #54 → reference §A.)

## 1. Enumerate PRs and read the existing conversation FIRST

```bash
git ls-remote origin 'refs/pull/*/head'      # works even when the REST API is unreachable
```
For each PR fetch **all three** comment surfaces — they are distinct endpoints and do not overlap:
```bash
gh api repos/<owner>/<repo>/issues/<n>/comments   # PR-level discussion
gh api repos/<owner>/<repo>/pulls/<n>/reviews     # APPROVED / CHANGES_REQUESTED verdicts
gh api repos/<owner>/<repo>/pulls/<n>/comments    # inline, line-anchored
```
- Every prior 🔴/🟠/🟡 is a **re-review checklist**: mark each **fixed / still open / never valid**
  against current branch source. Never silently drop one; if you were wrong earlier, say so.
- Carry unresolved **author sign-offs** (risk posture, merge order, ops timing) into the §3 verdict as
  open questions — they are the author's call, not yours to close. Don't re-file a finding the thread
  already resolved; if you disagree with the resolution, reference it explicitly.
- **"Re-review after fixes" starts by comparing heads.** If the head is still the SHA you reviewed,
  there is nothing to re-review — report that (and any *new comments*), don't re-run the review.
- **Merged since your last review?** If a fix push landed and merged unreviewed, do a **post-merge
  review** of `<last-reviewed-head>..<merged-head>` and post it on the merged PR; new behavior that
  reached `main` without review is the highest-priority read.

## 2. Review the BRANCH source, in a scratch worktree

```bash
git fetch <remote> '+refs/pull/<n>/head:refs/review/pull/<n>'   # private namespace, deleted afterwards
git worktree add --detach <scratch-dir> refs/review/pull/<n>
git diff --stat origin/main...<head>     # scope       git show <head>:<path>   # file as on the branch
```
- **Fail-stop if you are not where you think.** In any multi-step script: `set -e`, then after `cd`
  assert `[ "$(git rev-parse --show-toplevel)" = "<scratch-dir>" ]` before the first mutating command.
  A failed `worktree add` followed by unguarded commands mutates whatever checkout you are still in.
- **Use `--detach` scratch worktrees** for running and red-checking; a branch already checked out in
  another worktree (possibly another session's) makes `worktree add <branch>` fail.
- **Shared repos change under you.** Other sessions fetch, prune and push concurrently: fetch into
  your own `refs/review/*` namespace, and treat `bad revision` / `ambiguous argument` as a *tooling*
  error to fix and re-run — never as evidence of a diff. Re-fetch `main` before every verdict.
- **Zero-Trust applies to review:** prove every assertion (missing key, wrong sign, absent caller) with
  branch source, and say in the write-up that you verified it.
- **Stacked PRs** re-include the parent's payload in `main...B`; check
  `git merge-base --is-ancestor <A> <B>`, review only the incremental commits, and note the PR
  inherits every blocker beneath it.
- **Know what "green" means.** Read the check-runs and the failing log (`gh run view <id> --log-failed`);
  a red check's exact line *is* a finding. A gate proves only what it runs — if CI lints but skips
  tests, say so. **Check which gate runs the commit-time rules:** here CI's `quality-gate` job now runs
  `pre_commit_validator.py` on the PR's diff (soft-reset to the base, then validate the staged change),
  so a red `quality-gate` is often an inline import or silent except. Reproduce it the same way
  locally; the validator stops at the first hit per file and exempts `test_*.py`, so confirm a fix by
  re-running it. Ruff `E402` is module-level only and does not catch a function-body import. A branch far behind `main` may predate
  whole CI jobs, so its green set is smaller than today's gate. Reproduce the gate locally when you can.

## 3. The fixed rubric — address every perspective

- **Corner cases, bad smell, over-engineering.**
- **Design / architecture / performance / security / simplification / unification.**
  - **Architecture fit — respect the project's actual seams** (AETHER: a modular monolith, ports &
    adapters around external dependencies such as the E*TRADE `store`, a strangler-fig extraction
    `ai_portfolio_game.py → aether/scenario/`, call-time `_pkg()` lazy resolution). Flag a call site
    that bypasses a port, a reach into another package's internals, logic duplicated instead of routed
    to its single home, an eager import where the codebase resolves lazily, and **copy-not-move**
    stages. A "verbatim extraction" claim is checkable: normalize (strip comments/indent/module
    prefix) and diff the extracted body against the live source block; a copy that must track a
    still-live source needs a **parity test**, or it drifts silently. Recommend the seam the project
    already uses — don't invent one.
  - **Config-dependent wiring — check it under PRODUCTION config.** When a change routes calls through a
    factory or adapter selector, confirm which backend it returns with production's real settings, not
    only the test/dev default (e.g. a store factory keyed off an app-wide `DATABASE_URL` silently picked
    an unimplemented backend on PROD — #144). A test that runs the real factory under a prod-like config
    is the guard.
- **DB / IO:** extra calls, N+1, redundant fetches.
- **Anti-patterns, missing patterns, parallelism** left on the table.
- **Duplication & multiple sources of truth** — one fact, one home (incl. repeated constants: a value
  that "matches" another literal elsewhere should reference it).
- **Confidential data** — ids, emails, usernames, tokens, internal IPs, UNC paths, account ids. Flag
  every leak, and never reproduce one unmasked in a public channel (§6).
- **Documentation stays true — verify it, don't assume it.** For every behavior the PR changes, grep
  every doc surface for the *old* behavior: README and `*.md`, design/plan docs, roadmap **status
  lines**, wiki/About text, skills, docstrings, comments, and log/alert text. A doc left describing the
  old behavior is a finding in the same PR. Check first any doc that states a **safety invariant**
  ("never opens a browser", "zero calls", "only a human may…"): those are the docs people trust. Also
  check that identifiers meant to be unique (e.g. roadmap item numbers) still are, and that a doc
  promising cross-references actually carries them both ways (reference §A, PR #84).
- **Tests have value** — real behavior, not tautologies:
  - **Fully mocked?** Mocking the unit under test proves nothing; mock only expensive or
    non-deterministic IO (fs, network, clock) while the real code runs.
  - **Red-green.** Swap the pre-change file back in and show the new test fails. **No pre-change code**
    (additive module, extraction)? Do a **mutation check**: break the code the way a realistic
    regression would and confirm a test fails.
  - **Combinable?** Same contract at different inputs → one table-driven test; don't merge different
    contracts just to cut lines.
  - **Run it.** Report the real `Ran N … OK (skipped=k)` from the branch (merged with today's `main`
    when it is behind) — never an inferred "should pass". Note hygiene noise (`ResourceWarning`, test
    runs writing into real data dirs).
- **AI-ish smell** — hype framing, decorative emoji in logs, docstrings or log lines that assert
  causes the code doesn't know.
- **Packaging honesty** — title/commit match the payload; split unreviewable mixes. A big symmetric
  `+N/−N` with a tiny normalized diff is a line-ending/encoding reflow hiding the change (reference §B).
- **Re-review discipline** — fixes regress: re-verify each prior finding *and* review the new delta on
  its own merits. "Addressed" is a claim to verify.
- **Confidence** — list assumptions and what you did **not** verify.
- **Is it ready for PROD?** — answer explicitly, on two axes:
  - **Code-quality gate** (objective): suite run green, validator green, no open 🔴, tests real,
    packaging honest.
  - **Behavioral/risk gate** (judgment): what does it actually change in production, and is that the
    intended posture? Per the project's Rule of Loss-Minimization, flag anything that increases
    exposure or weakens protection (looser filters, wider stops, more broker logins). Green tests prove
    it does what it says, not that what it says is the right risk.
  - Verdict: **prod-ready** · **prod-ready pending author sign-off on <risk>** · **not prod-ready:
    <blocker>**. Name what only production can confirm.

## 4. House format (see existing `reviews/PR-*.md`)

1. **Verdict** — `Approve` / `Request changes` / `Comment` + one-line gist + the prod-readiness call.
2. **Summary** — packaging and the 2-3 dominant points.
3. **Findings by severity** (🔴 blocker / 🟠 major / 🟡 minor), each anchored to `file:line` with a
   quoted snippet.
4. **What's good** — credit real improvements.
5. **Confidence / scope** — what was run vs inferred, what was not checked; correct any earlier error
   explicitly.

Keep it short: verified facts, one line each; FYI items last.

## 5. Post as a PR comment — one PR at a time

```bash
gh pr comment <n> --repo <owner>/<repo> --body-file reviews/PR-<n>-<slug>.md
```
A comment works on any PR, including your own (a `--request-changes` review does not). Post one PR at
a time so the §6 scrub runs on every body. If `gh` cannot reach the API, POST the same body to
`repos/<owner>/<repo>/issues/<n>/comments` with any HTTP client using the token from `gh auth token`
(reference §C has a verified PowerShell recipe and its error decoder).

## 6. PII guard for public repos

`gh repo view <owner>/<repo> --json visibility`. If public, mask everything the review quotes as a
leak (`10.0.0.x`, `<user>`, `<account-id>`, local paths) before posting; a posting script must abort on
`PUBLIC` without a per-file scrub. When printing any config or credential file
(even to a console), mask by value shape as well as key name — a key-name list misses keys like `pass` —
and test the mask on a fixture before running it on the real file.

## 7. Network & auth

- Don't trust one client's "unreachable": TLS stacks differ behind corporate proxies (reference §D) —
  try `git ls-remote`, `gh`, and a proxy-aware HTTP client before concluding the API is down.
- `gh` not on PATH? Resolve it portably (`command -v gh` / `Get-Command gh`) — never hardcode a path.
- Authenticate with `gh auth login` (ask the user to run it in their own terminal — it is interactive)
  or a `GH_TOKEN` they supply. `gh auth token` is a sanctioned way to reuse gh's stored credential.
- ⚠️ **Never** extract a token embedded in a remote URL (`git remote -v`) — advise rotating it instead.

## 8. Bring a PR current with `main` — merge in, never rebase

- **`git merge --no-ff origin/main`** on the PR branch, re-run the suite, then a plain fast-forward
  `git push`. A rebase needs a force-push, which agent runtimes typically (and rightly) gate as
  destructive. If you already rebased locally, `git checkout -B <branch> <pushed-sha>` and merge
  instead. Verify `behind=0` and a clean merge state afterwards.
- **Parent was squash-merged (stacked PR being retargeted to `main`)?** Every line both PRs touched
  will conflict, because the merge base predates the squash. First prove `main`'s copy of each file the
  child touches equals the parent branch's final tip; only then resolve **from the child branch** with
  `git merge -X ours origin/main` (ours = the checked-out branch; `-X theirs` here would silently put
  the parent's old lines back). Confirm the result's PR files equal the child's head.
- **Don't push to a branch checked out in another worktree** — another session may be mid-edit.
  Test-merge in a detached scratch worktree and report the exact command instead.

## 9. Clean up once PRs are merged or closed

- **"Merged" comes from the PR API, never from ancestry** — squash-merged tips are never ancestors of
  `main`. In list responses use `merged_at != null` (the list endpoint has no `merged` field); read
  **every page** of results.
- **Links first (data-loss rule, `AGENT.md` §4 "No Links Into Data/"):** `git status` cannot see a
  junction or symlink under gitignored `Data/`, and removing a worktree deletes *through* it — on
  2026-10-06 that wiped the main checkout's `Data/Symbol` + `Data/Symbol_full`. Before removing any
  worktree, scan it for links (`prune_merged_worktrees.links_inside(<wt>)`, or
  `Get-ChildItem <wt> -Recurse -Force -Attributes ReparsePoint`). If any: remove the **link itself**
  (`cmd /c rmdir <link>` — removes only the link), re-scan, and get the owner's OK for any link that
  pointed into `Data/`. Never remove a worktree that still contains one.
- **Worktrees:** remove only clean ones, **without `--force`** (a refusal is the safety net —
  `git -C <wt> status --porcelain` non-empty ⇒ leave it and report it). Keep open-PR, dirty, locked,
  and other-session worktrees. **An open PR dominates a shared head ref:** a branch that backs any open
  PR is kept even if another PR with the same head ref was merged or closed. `scripts/utils/prune_merged_worktrees.py` automates this (dry-run first,
  then `--apply`); its known limits are in reference §E.
- **Local branches (with or without a worktree):** delete only when **lossless** — every commit still
  exists on the remote:
  1. tip reachable from `origin/main` or any `refs/pull/*/head`
     (`git for-each-ref --count=1 --contains <sha> refs/remotes/origin/main refs/review/pull` — one call
     per branch; per-ref `merge-base` loops are far too slow);
  2. else merging it into `main` changes nothing (`git merge-tree --write-tree origin/main <b>` equals
     `main`'s tree);
  3. else every file it touched is byte-identical to some commit in its **merged** PR's history.

  Anything else holds unique work → keep and list it. `-D` is correct only for branches proven
  lossless (squash-merged branches make `-d` refuse). Log `name sha` to a backup file first so any
  deletion is one `git branch <name> <sha>` away from undo.
- Batch removals are irreversible local changes: present the classified plan, get the go-ahead, and
  delete your own scratch (temp refs, worktrees, files) when done.

## 9b. Post-merge doc audit (after a batch of merges, or when asked)

Docs drift between PRs even when each review was careful. After a batch lands:
1. Bring the main checkout current (`git merge --ff-only origin/main`, only when the user asks, and only
   if no local edit conflicts).
2. List the merges since the last audit and, for each, the behavior it changed.
3. Grep every doc surface (§3 *Documentation*) for each old behavior, plus status lines of tracked items
   ("UNBUILT" for something built) and duplicate identifiers.
4. Verify each replacement claim against code on `main` (callers, defaults, gates, thresholds) before
   writing it. A behavior gap you find (code doing something unintended) is reported for a decision,
   not silently "fixed" inside the docs PR.
5. Land the fixes as one docs PR from a fresh branch off latest `main`.

## 10. Checklist

- §0 goal + authoritative definition read first; PR/commit claims checked against their evidence.
- §1 all three comment surfaces read; prior findings resolved fixed/open/never-valid; heads compared
  before any "re-review"; merged-unreviewed fixes get a post-merge review.
- §2 branch source in a detached, fail-stop scratch worktree; private `refs/review/*`; a red check's log
  read and the gate reproduced locally.
- §3 docs re-verified for every changed behavior (old-behavior grep, status lines, safety invariants,
  unique identifiers); §9b audit run after a batch of merges.
- §3 every perspective covered; tests red-green or mutation-checked and actually run; architecture-fit
  and extraction faithfulness checked; prod-readiness stated on both gates.
- §4/§5/§6 house format, posted as a comment, PII masked.
- §8 brought current by merge-in (never force-push); squash-parent retargets resolved with proof.
- §9 merged-ness from the API (all pages); only lossless deletions, logged; no `--force`.
