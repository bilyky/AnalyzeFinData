# Code-Review Open PRs → paste-ready findings → post as PR comments

Rigorously code-review the repo's **open pull requests, one at a time**, against a fixed rubric,
produce **paste-ready markdown** under `reviews/`, then post each as a **PR comment**. Companion to
[ship-pr.md](./ship-pr.md) (that one *opens* PRs; this one *reviews* them).

Optional args: a PR number or list (`/review-prs 5` or `/review-prs 2 3 4 5`). With none, review
every open PR **except the one you authored**.

## 0. Understand the PR's GOAL and REASON before reviewing a single line

Never open the diff first. A review that only checks whether the code is internally *consistent*
will happily bless code that is consistent **but does the wrong thing**. Anchor on intent, then
judge the code against that intent — not against itself.

1. **Read the stated goal.** PR title + body, commit messages
   (`git log origin/main..<head> --pretty=full`), and any linked issue / roadmap item.
2. **Find the AUTHORITATIVE definition of the feature.** In this repo, features are numbered
   **R&D items**, and their definition is single-sourced in three places — read all three before
   forming an opinion:
   - `CLAUDE.md` → **Saturday R&D Roadmap**: the prose intent of each R&D # / session.
   - `scripts/utils/pre_commit_validator.py` → **`FEATURE_CHECKS`**: the machine contract —
     each R&D feature's canonical code anchor (`signature`) + its required `test_keyword`. This
     tells you *which helper/gate the feature is supposed to live in.*
   - the feature's **own unit test** (e.g. `test_high_score_pgr_bypass`) — the executable spec
     of what the feature is DEFINED to do.
3. **Review the code against the definition.** The key question is not "does this code work?" but
   "does it implement what the R&D item says, **using the canonical gate/helper the definition
   names** — or does it re-implement the behavior inline with different thresholds?" A second,
   divergent copy of a defined feature is a multiple-source-of-truth bug even when it compiles and
   its own tests pass.

> **Worked example (PR #27, verified):** R&D #13 "High-Score PGR Bypass" is DEFINED as the
> two-factor gate `risk_utils.is_elite_breakout_candidate(total_score, short10)` — BOTH
> `total_score >= CFG.system_bypass_score_floor` (8.0) **and** `short10 >= CFG.system_bypass_s10_floor`
> (2.0), single-sourced from CFG (see `test_high_score_pgr_bypass`, and `FEATURE_CHECKS` anchors R&D
> #13 to that exact helper). PR #27's new PGR waiver in `check_failure_rules` instead bypassed on a
> **hardcoded `score >= 10.0` alone** — no s10 factor, not CFG-sourced, not the canonical helper. A
> code-only review (checking the waiver's own logic + its own test) passed it; a **definition-first**
> review catches the divergence immediately. Start from the definition.

Only once the goal is understood do you apply the §3 rubric.

## 1. Enumerate open PRs (works behind a blocked API)

Don't assume the GitHub API is reachable — verify over the git transport, which has the proxy `git`
already uses:
```bash
git ls-remote origin 'refs/pull/*/head'      # every open PR advertises refs/pull/<n>/head
```
Map each `<n>` to its branch/head SHA. **Exclude the PR you authored** (the review is for *others'*
changes; a self-review has no independent value and `gh pr review --request-changes` is blocked on
your own PRs anyway).

## 2. Review the BRANCH source, not the working tree

The working tree is usually on `main`; the PR isn't. Read the actual changed code from the ref:
```bash
git diff --stat origin/main...<head-sha>          # scope: files + churn
git log origin/main..<head-sha> --pretty='- %s'   # commit-by-commit story
git show <head-sha>:<path>                         # a file AS IT IS ON THE BRANCH
git show <sha> -- <path>                           # one commit's change to a file
```
**Rule of Zero-Trust applies to review too:** verify every claim against branch source before
writing it. If you assert a key doesn't exist, a sign is negative, a call site is missing — prove it
with `git show`, and say in the write-up that you verified it. Never infer from the diff alone what
the whole-file context would disprove.

**Stacked PRs:** if PR #B branches off PR #A (not `main`), `git diff main...B` re-includes A's whole
payload. Check with `git merge-base --is-ancestor <A-sha> <B-sha>`. A stacked PR **inherits every
blocker of the PR beneath it** — call that out as its own finding and review only its incremental
commits on their merits.

## 3. The fixed rubric — review from ALL these perspectives

For each PR, look for and address every one of these (this is the standing review contract):

- **Corner cases, bad smell, over-engineering.**
- **Design / architecture / performance / security / simplification / unification.**
- **DB / IO: extra calls, N+1, redundant fetches.**
- **Anti-patterns, missing patterns, parallelism** left on the table.
- **Code duplication & multiple sources of truth** — one fact, one home.
- **Confidential/secure data** — ids, emails, usernames, passwords, tokens, internal IPs, UNC
  paths, account/CUSIP ids. Flag every leak AND do not reproduce it unmasked in a public channel
  (see §6).
- **Documentation** — missing, obsolete, or contradicting the code.
- **Tests have value** — they test *real behavior*, are **red-green** (would fail against the
  pre-change code), not tautologies that restate the implementation or pass for the wrong reason.
  Three questions to ask of every test file in the diff:
  - **Any test with no value / everything mocked?** If a test mocks out the very unit under test (or
    every collaborator), it asserts only that the mock returns what the mock was told to — it proves
    nothing about production. Mocking is legitimate ONLY to isolate expensive/non-deterministic IO
    (filesystem, network, clock) while the real SUT still runs. Flag "fully-mocked" tests; keep
    "IO-isolated" ones. (Example done right: `test_breakout_overrides` mocks only
    `is_bottom_confirmed`'s file read, but calls the real `check_failure_rules` against a real
    on-disk rules file.)
  - **Can we combine some tests?** Look for methods that exercise the *same contract* at different
    inputs (same SUT, same setup, only the fixture/expected value differs) — those should be one
    table-driven/parametrized test, not N near-duplicate methods (watch for a case in method 1 that
    is identical to a case in method 2). Do NOT merge tests with different SUTs or different setup
    just to cut line count — combine for shared contract, not for brevity.
  - **Are we green locally?** Actually RUN the branch's suite (`python -m unittest discover tests`,
    `PYTHONIOENCODING=utf-8` on Windows) and report the real count (`Ran N … OK (skipped=k)`), not
    an inferred "should pass." A review that claims tests pass without running them is a Zero-Trust
    violation. Also flag test-hygiene noise the run surfaces (e.g. `ResourceWarning: unclosed file`
    from `open().read()` without a context manager).
- **AI-ish smell** — hype/grandiose framing ("Enshrine", "Mandate", "100% trustable"), decorative
  emoji in production logs, bot co-author trailers, docstrings that lie about the code.
- **Packaging honesty** — does the title/commit message match the payload? A "move one import"
  commit that rewrites 15 files is unreviewable and unrevertable; call for a split.
- **Confidence** — are you sure? List assumptions and anything **not** verified.
- **Is this PR/change ready for PROD?** — the capstone question every review must answer explicitly,
  not leave implied by the severity list. Separate two axes and state both:
  - **Code quality gate** (objective): validator green, branch suite green (run, not inferred), no
    open 🔴, packaging honest, tests real. If any fails, it is NOT prod-ready — say so plainly.
  - **Behavioral/risk gate** (judgment): what does this change actually DO in production, and is that
    the intended posture? A change can be flawless code and still be a risk-appetite decision the
    author must consciously accept — e.g. loosening a buy-side filter, widening a stop, raising an
    allocation cap. Name the live effect (per CLAUDE.md Rule of Loss-Minimization, flag anything that
    increases exposure or reduces capital protection), and whether the backtest/data supports it.
    Never wave a risk-changing PR through on green tests alone — green tests prove it does what it
    says, not that what it says is the right risk. State the verdict as: **prod-ready**,
    **prod-ready pending author sign-off on <the risk decision>**, or **not prod-ready: <blocker>**.

House format (see the existing `reviews/PR-*.md` as templates):

1. **Verdict** line — `Request changes` / `Approve` / `Comment` + one-sentence gist, AND an
   explicit **prod-readiness** call phrased per the §3 "ready for PROD?" bullet (code-quality gate
   AND risk gate).
2. **Summary comment** — the top-level narrative (packaging, the 2-3 dominant problems).
3. **Inline comments** — findings ordered by severity, each **anchored to `file:line` + a quoted
   code snippet** so it pastes into GitHub's "Files changed" view. Severity: 🔴 blocker / 🟠 major /
   🟡 minor.
4. **What's good** — credit real improvements; a review that only lists faults is untrustworthy.
5. **Confidence / scope notes** — what was runtime/branch-verified vs. inferred; what you did NOT
   check (e.g. "did not run the branch test suite", "did not scan every `Data/*.json`"). Correct
   any earlier fabrication explicitly (e.g. a mis-quoted docstring) rather than silently.

## 5. Post each review as a PR **comment**

Use `gh pr comment` — it works on **any** PR including self-authored, unlike
`gh pr review --request-changes` (blocked on your own). One comment per PR, body from the file:
```bash
gh pr comment <n> --repo <owner>/<repo> --body-file reviews/PR-<n>-<slug>.md
```
`reviews/post_reviews.sh` wraps this for the whole set with a public-repo PII guard — run it once
`gh` is authenticated.

## 6. PII guard before posting to a PUBLIC repo

The reviews **quote** the internal IP / username / UNC path / CUSIP-like id they flag as findings.
Posting them verbatim to a public repo re-leaks exactly the PII the review calls out. Before posting:
```bash
gh repo view <owner>/<repo> --json visibility --jq '.visibility'
```
If `PUBLIC`, scrub those strings (mask to `10.0.0.x` / `<user>` / `<account-id>`) first, or keep the
findings but replace the literal evidence with a masked form. `post_reviews.sh` aborts on a PUBLIC
repo unless `FORCE=1`.

## 7. Network reality — the schannel revocation trap (verified 2026-08-17)

Behind the Intel corporate proxy, **the GitHub API is reachable — a naive `curl` test lies.**

- Windows `curl` uses **schannel**, which does an online cert-revocation check. Behind the proxy the
  revocation responder is unreachable, so `curl https://api.github.com/...` fails with
  `CRYPT_E_REVOCATION_OFFLINE` — a **TLS-handshake** failure, not a routing/block. This earlier led
  to a wrong "api.github.com is unreachable" conclusion. It is not.
- Isolate it by disabling just the revocation check:
  ```bash
  curl -sS -m 15 --ssl-no-revoke -x http://proxy-us.intel.com:912/ https://api.github.com/zen
  ```
  This returns a zen quote → the host is reachable; only schannel's revocation step was failing.
- **`gh` is a Go binary using Go's crypto/tls, which does not do online revocation by default** — so
  `gh` reaches the API through the proxy even though schannel `curl` can't. `gh pr comment` works
  from this network **once `gh` is authenticated.**
- `git ls-remote origin` also works (git's own proxy path) — use it to verify PR/branch state.

## 8. gh auth — supply it cleanly, never scrape it

`gh` may be installed but **not on PATH** (Windows: `"C:\Program Files\GitHub CLI\gh.exe"`) and/or
**not authenticated**. Authenticate via one of:
- `gh auth login` (interactive — ask the user to run `! gh auth login`), or
- `export GH_TOKEN=<PAT>` the user supplies.

> ⚠️ **Security boundary (hard rule):** NEVER scrape/extract the push token embedded in the `origin`
> remote URL (`git remote -v` may expose a `ghp_…` PAT) to authenticate the API. The Claude Code
> security classifier blocks this, correctly. If you notice such a token, advise **rotating** it and
> moving to a credential helper — do not print or reuse its value.

## 9. Verification checklist

- **Goal read FIRST (§0)** — each change judged against the authoritative R&D definition, not its
  own internal consistency; divergence from the canonical gate/helper is a finding.
- Every finding cites branch-verified `file:line` + quoted code; assumptions labeled in §5 notes.
- **Tests audited (§3)** — no fully-mocked/valueless tests; same-contract cases flagged for
  combining; branch suite **actually run** with the real `Ran N … OK` count reported, hygiene noted.
- **Prod-readiness answered explicitly (§3 capstone)** — both gates stated; a risk-changing PR is
  never waved through on green tests alone.
- No unmasked PII in any file that will hit a public channel.
- Reviews posted as **comments** (confirmed: `gh pr comment`, not `--request-changes`).
- Reachability proven with the `--ssl-no-revoke` isolation test before claiming the API is up/down.
- Push token never scraped; if seen, rotation advised.
