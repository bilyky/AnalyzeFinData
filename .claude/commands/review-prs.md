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

> **Worked example (PR #27, verified):** R&D #13 "High-Score PGR Bypass" is DEFINED as the two-factor
> gate `risk_utils.is_elite_breakout_candidate(total_score, short10)` (both a score floor AND an s10
> floor, CFG-sourced; see `test_high_score_pgr_bypass` + the `FEATURE_CHECKS` anchor). PR #27
> re-implemented it inline as a **hardcoded `score >= 10.0` alone** — no s10, not CFG-sourced, not the
> canonical helper. A code-only review passed it; a definition-first review catches the divergence.
> Start from the definition.

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

**Pull the merge gate — and know what "green" means.** The PR's own CI is primary evidence; a defect
that a code read cannot see (a lint/validator rule the diff trips) is sitting in the failing log.
Read it, don't assume:
```bash
gh pr checks <n> --repo <owner>/<repo>                       # pass/fail per required check
gh api repos/<owner>/<repo>/commits/<head-sha>/check-runs \
  --jq '.check_runs[] | "\(.name): \(.status)/\(.conclusion)"'
gh run view <run-id> --repo <owner>/<repo> --log-failed      # the actual failing lines
```
If a required check is red, **the exact failure IS a finding** — quote the offending line. Then read
*what the CI config runs*: a green gate proves only what it exercises. If it byte-compiles + lints but
skips the test suite, "CI green" is not "code tested" — say so precisely rather than implying coverage
the gate never ran. When you can, reproduce the gate locally (run the same validator/linter against
the change set the way CI does) so your verdict matches what the merge button will do.

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
  - **Churn with no semantics** is its own smell: a large `+N/−N` whose *normalized* diff is tiny
    means a whole-file **line-ending / encoding / whitespace reflow** (LF↔CRLF, BOM, tab↔space) is
    hiding the real change and will collide with the next PR that touches the file. Detect it — diff
    `git show <head>:<path>` against `git show origin/main:<path>` and compare CR/byte counts (a
    symmetric `+N/−N` with CR-count flipping 0↔N is the fingerprint) — and call for a re-commit as a
    clean minimal diff (plus a `.gitattributes eol` rule if the repo lacks one).
- **Re-review discipline (fixes regress).** On a follow-up push, re-verify each prior finding is
  *actually* resolved AND review the new delta on its own merits — a fix routinely introduces a fresh
  blocker (e.g. a cleanup that adds a banned inline import, or re-flows line endings). "Addressed your
  comments" is a claim to verify, not a state to assume.
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

## 4. House format

Use the existing `reviews/PR-*.md` as templates:

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
Post **one PR at a time by hand** — it's the safest default, since it forces the §6 PII check on each
body before it goes public. Only batch the loop behind a script if that script runs the §6 guard per
file (see below); a blind `for f in reviews/*.md` that mis-maps a filename to the wrong PR number, or
skips the PII scrub, is worse than posting by hand.

## 6. PII guard before posting to a PUBLIC repo

The reviews **quote** the internal IP / username / UNC path / CUSIP-like id they flag as findings.
Posting them verbatim to a public repo re-leaks exactly the PII the review calls out. Before posting:
```bash
gh repo view <owner>/<repo> --json visibility --jq '.visibility'
```
If `PUBLIC`, scrub those strings (mask to `10.0.0.x` / `<user>` / `<account-id>`) first, or keep the
findings but replace the literal evidence with a masked form. If you wrap posting in a script, it MUST
run this visibility check and abort on `PUBLIC` unless an explicit override is set — never post a
batch to a public repo without the per-file scrub.

## 7. Network reality — don't trust a naive `curl` "unreachable"

Behind a revocation-blocking corporate proxy, **the GitHub API is reachable even when `curl` says it
isn't.** Windows `curl` uses schannel, which does an online cert-revocation check; when the proxy
can't reach the revocation responder, `curl https://api.github.com/...` fails with
`CRYPT_E_REVOCATION_OFFLINE` — a TLS-handshake failure, *not* a block. Prove reachability by skipping
just that check: `curl -sS -m 15 --ssl-no-revoke -x <proxy> https://api.github.com/zen` returns a
quote → the host is up.

Practical consequence: **use `gh` and `git`, not `curl`, for GitHub.** `gh` (Go `crypto/tls`, no
online revocation) reaches the API through the proxy, and `git ls-remote origin` uses git's own proxy
path — both work when schannel `curl` can't. If `gh` still dials direct and times out, set
`HTTPS_PROXY`/`HTTP_PROXY` to the same proxy `git` uses, then retry.

## 8. gh auth — supply it cleanly, never scrape it

`gh` may be installed but **not on PATH**, and/or **not authenticated**. If `gh` isn't found,
resolve it portably (`command -v gh`, or `Get-Command gh` on PowerShell) and call it by that path —
don't hardcode an OS path. Authenticate via one of:
- `gh auth login` (interactive — ask the user to run `! gh auth login`), or
- `export GH_TOKEN=<PAT>` the user supplies.

> ⚠️ **Security boundary (hard rule):** NEVER scrape/extract the push token embedded in the `origin`
> remote URL (`git remote -v` may expose a `ghp_…` PAT) to authenticate the API — your agent
> runtime's security policy should block this, and correctly so. If you notice such a token, advise
> **rotating** it and moving to a credential helper — do not print or reuse its value.

## 9. Verification checklist

Confirm each before finishing — the full rule lives in the cited section:
- **§0** goal read first; each change judged against the R&D definition, and divergence from the
  canonical gate/helper is itself a finding.
- **§2 / §4** every finding cites branch-verified `file:line` + quoted code; assumptions labeled.
- **§3** tests audited — none fully-mocked/valueless, same-contract cases flagged, branch suite
  **actually run** with the real `Ran N … OK` reported.
- **§3 capstone** prod-readiness stated (both gates); no risk-changing PR waved through on green tests.
- **§6** no unmasked PII in anything hitting a public channel.
- **§5** posted as **comments** (`gh pr comment`, not `--request-changes`).
- **§7 / §8** reachability proven with `--ssl-no-revoke` before claiming the API up/down; push token
  never scraped (rotation advised if seen).
