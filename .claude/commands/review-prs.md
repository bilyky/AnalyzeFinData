# Code-Review Open PRs → paste-ready findings → post as PR comments

Review the repo's **open pull requests one at a time** against the fixed rubric below, write
**paste-ready markdown** to `reviews/PR-<n>-<slug>.md`, then post each as a **PR comment**.
Companion to [ship-pr.md](./ship-pr.md) (that one *opens* PRs; this one *reviews* them).
Agent-agnostic: any agent can read and follow this file; shell snippets are POSIX `git`/`gh`.
Environment-specific troubleshooting and the full worked examples live in
[docs/skills/review-prs-reference.md](../../docs/skills/review-prs-reference.md) — open it only when
a step below points you there.

Input: a PR number or list (e.g. `5`, or `2 3 4 5`), however your agent passes it. None → every open
PR. A review of a change you authored has little independent value — say so in the write-up.

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
  a red check's exact line *is* a finding. A gate proves only what it runs: check the CI config before
  writing "CI green". Here CI byte-compiles, runs `pre_commit_validator.py` on the PR's diff
  (`quality-gate`) and ruff (`lint`) — **it does not run the unit tests**, so your own suite run (§3)
  is the only test evidence. A red `quality-gate` is usually an inline import or silent except;
  reproduce it locally (the validator stops at the first hit per file and exempts `test_*.py`). A
  branch far behind `main` may predate whole CI jobs.
- **Other open PRs touching the same files:** test-merge them pairwise (`git merge-tree --write-tree
  <headA> <headB>`, commits not trees) and state any conflict and the merge order in both reviews.

## 3. The fixed rubric — address every perspective

- **Corner cases, bad smell, over-engineering.**
- **Design / architecture / performance / security / simplification / unification.**
  - **Architecture fit — respect the project's actual seams** (this repo's are listed in reference §H).
    Flag a call site that bypasses a port, a reach into another package's internals, logic duplicated
    instead of routed to its single home, an eager import where the codebase resolves lazily, and
    **copy-not-move** stages. A "verbatim extraction" claim is checkable: normalize (strip
    comments/indent/module prefix) and diff the body against the live source block by script; a copy
    that must track a live source needs a **parity test**. Recommend the seam the project already uses.
  - **Check against PRODUCTION, not the repo's defaults.** Which backend a factory returns under
    production's real config (#144); and the environment facts the change relies on — scheduled-task
    names, time windows (token expiry, market close), data present on that machine. Production logs are
    evidence: quote them, with timestamps (e.g. PROD's tasks had been renamed, so the watchdog audited
    names that no longer existed — #176).
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
    regression would and confirm a test fails. Red must be a failing **behavior assertion** — an error
    in setup because old code lacks a new seam is not evidence; mutate each behavior instead. Ask of
    every fixture: *would the old code satisfy it too?* If so the test proves nothing.
  - **Did it exercise the path it claims?** A test or incident replay can pass for the wrong reason.
    Assert the calls that prove the path ran (counts, arguments), and patch the module's own
    reference — never a shared library global (`datetime.datetime`, `time.time`) that unrelated code
    also reads.
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
  - **Trace every consumer of a changed output** — exit code, file, log level, alert, schedule,
    response shape. A job that starts exiting 1 feeds whatever audits task results; a new alert feeds
    a throttle that may not key on it the way you expect.
  - Verdict: **prod-ready** · **prod-ready pending author sign-off on <risk>** · **not prod-ready:
    <blocker>**. Name what only production can confirm.

## 4. House format (see existing `reviews/PR-*.md`)

**Verdict** (`Approve` / `Request changes` / `Comment` + gist + prod-readiness) → short summary →
findings by severity (🔴 blocker / 🟠 major / 🟡 minor), each at `file:line` with a quoted snippet →
what's verified (what ran, what's good) → what was **not** checked; correct any earlier error
explicitly. Verified facts, one line each; FYI last.

**Prove findings with a probe.** For a suspected bug, run the real function with only the boundary
mocked (subprocess, HTTP, clock, email) and quote the output in the finding — a reproduced bug isn't
argued with. What you could not reproduce is labelled a suspicion, not a fact.

## 5. Post as a PR comment — one PR at a time

```bash
gh pr comment <n> --repo <owner>/<repo> --body-file reviews/PR-<n>-<slug>.md
```
A comment works on any PR, including your own (a `--request-changes` review does not). Post one PR at
a time so the §6 scrub runs on every body. **Before posting, re-check every factual sentence** —
counts, dates, "already on `main`", "CI runs X", "the logs end before Y" — against a command run now.
A correction after posting is public; if you find one, edit the comment and say what changed. A
comment does not block a merge: say "must fix before merge" in the verdict line, where it's seen. If `gh` cannot reach the API, POST the same body to
`repos/<owner>/<repo>/issues/<n>/comments` with any HTTP client using the token from `gh auth token`
(reference §C has a verified PowerShell recipe and its error decoder).

## 6. PII guard for public repos

`gh repo view <owner>/<repo> --json visibility`. If public, mask everything the review quotes as a
leak (`10.0.0.x`, `<user>`, `<account-id>`, local paths) before posting; a posting script must abort on
`PUBLIC` without a per-file scrub. Reading config or credential files: `AGENT.md` "Never Print Secrets".

## 7. Network & auth

- Don't trust one client's "unreachable": TLS stacks differ behind corporate proxies (reference §D) —
  try `git ls-remote`, `gh`, and an HTTP client given the proxy **explicitly** (`$HTTPS_PROXY`)
  before concluding the API is down.
- Auth: `gh auth login` (the user runs it; it is interactive), a `GH_TOKEN` they supply, or
  `gh auth token` to reuse gh's stored credential (PATH and push details: reference §D).
- ⚠️ **Never** extract a token embedded in a remote URL (`git remote -v`) — advise rotating it instead.

## 8. Bring a PR current with `main` — merge in, never rebase

- **`git merge --no-ff origin/main`** on the PR branch, re-run the suite, then a plain fast-forward
  `git push`. A rebase needs a force-push, which agent runtimes typically (and rightly) gate as
  destructive. If you already rebased locally, `git checkout -B <branch> <pushed-sha>` and merge
  instead. Verify `behind=0` and a clean merge state afterwards.
- **Parent was squash-merged (stacked PR retargeted to `main`)?** Every shared line conflicts; follow
  reference §I (prove `main` holds the parent's final content, then `-X ours` from the child).
- **Whole-file conflict resolution (`--ours`/`--theirs`) also drops that file's non-conflicting
  hunks.** Re-apply each of the PR's hunks (to its new home if `main` reorganized the file), then check
  `git diff --cached origin/main` removes only lines the PR meant to replace.
- **Don't push to a branch checked out in another worktree** — another session may be mid-edit.
  Test-merge in a detached scratch worktree and report the exact command instead.
- **"Prepare <PR> for PROD"** means all of: current with `main` (above); the suite and a red-green on
  the merged tree; a read-only smoke run of the changed entry point on a *copy* of real data, showing
  the old code fails where the new one passes; the PR body states the deploy steps (restart?
  task re-registration? new config keys and what their defaults change? data steps? or "pull only")
  and how to roll back; and `mergeable_state=clean` with checks green on the pushed head.

## 9. After merges: clean up, then audit docs

- **Clean up only losslessly**, and never with `--force`. Before removing any worktree, scan it for
  links into `Data/` (`AGENT.md` §4); removing one with a link deletes through it. Full procedure
  (merged-ness from the API, the worktree rules, the lossless-branch test, the backup log):
  reference §F.
- **After a batch of merges**, run the post-merge doc audit: reference §G.

## 10. Checklist

- §0 goal and authoritative definition first; PR/commit claims checked against their evidence.
- §1 all three comment surfaces; prior findings marked fixed/open/never-valid; heads compared before a
  re-review; merged-unreviewed fixes get a post-merge review.
- §2 detached fail-stop scratch worktree, private `refs/review/*`; red checks read and reproduced;
  what CI does *not* run stated; overlapping open PRs test-merged.
- §3 every perspective; docs re-verified for each changed behavior; tests red-green or mutation-checked
  and actually run; architecture fit; production evidence; every consumer of a changed output traced;
  prod-readiness on both gates.
- §4–§6 findings proven by a probe (or labelled suspicions); every factual sentence re-checked before
  posting; house format; PII masked.
- §8 merge-in, never force-push; whole-file conflict resolutions re-checked hunk by hunk.
- Cleanup per reference §F: merged-ness from the API (all pages), lossless deletions only, logged, no
  `--force`, worktrees scanned for links first.
