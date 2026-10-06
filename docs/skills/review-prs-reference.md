# review-prs — reference (open on demand)

Supporting detail for [`.claude/commands/review-prs.md`](../../.claude/commands/review-prs.md). The skill
links to a section here only when a step needs it, so this file is not loaded on every run.

## A. Worked examples (all verified)

**Definition divergence — PR #27.** R&D #13 "High-Score PGR Bypass" is defined as the two-factor gate
`risk_utils.is_elite_breakout_candidate(total_score, short10)` (score floor AND s10 floor, CFG-sourced;
see `test_high_score_pgr_bypass` and its `FEATURE_CHECKS` anchor). The PR re-implemented it inline as a
hardcoded `score >= 10.0` alone. A code-only review passed it; a definition-first review catches it.

**Relocated, not removed — PR #54.** Goal: stop `git reset --hard` clobbering the manual-input
workbook by splitting an untracked working copy from a tracked template. The code copied the untracked
manual file *into* the new tracked `…_src.xlsx` and the pipeline then *read the tracked file*, so a
hard reset still wiped manual inputs. A tracked↔untracked split only works if the *live/read* file is
the untracked one. Same PR: the added `.gitignore` line was a literal PowerShell `` `n `` escape — read
the raw added line of any config/ignore change.

**Promised cross-links that aren't there — PR #84.** `plans/systemic-failure-retro.md` said its
remediation was "tracked as numbered items in `plans/roadmap.md` … so the two files don't drift", but
only one step was tracked; another fell in the seam between two R&D items, owned by neither, and every
reference was one-way. Fix in the same PR: extend an existing item to own the orphan and add links both
ways.

**Copy drift in a strangler-fig stage — PRs #136 / #138.** #138 added `decide_exits` as a verbatim copy
of the root SELL loop. #136 then changed the root loop to return `{symbol: reason}`; the copy kept
returning a list and its isolated tests stayed green. A normalized line diff of stage vs root (strip
comments, indentation and the `game.` prefix; ignore the trailing `return`) found it; a parity test
would have caught it on the day #136 landed.

## B. Detecting a line-ending / encoding reflow

A large symmetric `+N/−N` whose content barely changed is usually LF↔CRLF, a BOM, or tab↔space. Diff
`git show <head>:<path>` against `git show origin/main:<path>` and compare CR and byte counts (CR count
flipping 0↔N is the fingerprint). Ask for a clean minimal re-commit, plus a `.gitattributes` `eol` rule
if the repo lacks one.

## C. Posting without `gh` (Windows PowerShell, verified)

When `gh`'s own TLS dial times out but the OS HTTP stack reaches the API through the proxy:
```powershell
$tok = (gh auth token | Select-Object -First 1).Trim()
$h = @{ Authorization = "token $tok"; 'User-Agent' = 'pr-review'; Accept = 'application/vnd.github+json' }
# Read the body as a plain .NET string: Get-Content -Raw adds PS note-properties that ConvertTo-Json
# serializes as {"body":{"value":…,"PSPath":…}} (422 "is not a string"). ReadAllText also avoids mojibake.
$body  = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
$bytes = [System.Text.Encoding]::UTF8.GetBytes((@{ body = $body } | ConvertTo-Json -Depth 3))
Invoke-RestMethod -Uri "https://api.github.com/repos/<owner>/<repo>/issues/<n>/comments" `
  -Method Post -Headers $h -Body $bytes -ContentType 'application/json; charset=utf-8'
```
Errors: **400** = non-UTF-8 or non-string body; **422 "is not a string"** = the note-property bug above.
Read the real message from `$_.ErrorDetails.Message`. The PII scrub still applies.

## D. Environment gotchas

- **TLS behind a revocation-blocking proxy.** Windows `curl` (schannel) fails with
  `CRYPT_E_REVOCATION_OFFLINE` when it can't reach the revocation responder — a handshake failure, not
  a block (`curl --ssl-no-revoke` proves the host is up). `gh`'s Go dialer may ignore the system proxy
  and time out; set `HTTPS_PROXY` to the proxy `git` uses, or use a proxy-aware client (§C).
- **Git Bash (MSYS) path conversion** rewrites arguments such as `refs/x:.claude/file` into Windows
  paths, producing `ambiguous argument` errors. Prefix commands with `MSYS_NO_PATHCONV=1`.
- **Windows directory locks.** `git worktree remove` fails with *Permission denied* if any process —
  including your own shell — has its cwd inside the worktree. `cd` out first; if git already
  unregistered it, delete the now-empty directory.
- **`git rev-parse --short A B`** fails (`--short` implies `--verify`, one revision only); call it per
  revision.

## E. `scripts/utils/prune_merged_worktrees.py` — known limits

Check these against the current script before relying on it:
- **Transport is `gh api` only.** Where `gh` can't dial, it exits with a traceback; fall back to the
  manual procedure in the skill's §9.
- **Pagination.** It must read every page of `pulls?state=closed`; a single `per_page=100` request
  silently drops older PRs once the repo has more than 100 closed PRs, leaving their branches
  "unmapped".
- **Merged-ness** must come from `merged_at` (the list endpoint has no `merged` field) — fixed in #143.
- **Scope.** It handles worktrees and the branches they hold, not standalone local branches; use the
  lossless test in the skill's §9 for those.
