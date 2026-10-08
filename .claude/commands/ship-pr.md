# Ship a Change → PR → CI (auto-PR on push)

A repo-agnostic workflow for landing a change: branch, validate, commit, push — and let **GitHub open
the pull request itself**. A GitHub Actions workflow opens (or reuses) the PR on push using the
runner's `GITHUB_TOKEN`, so `git push` is the whole "open a PR" step. Agent-agnostic: any agent can
follow this file; snippets are POSIX `git`/`gh`. One-time setup and tool installation live in
[docs/skills/ship-pr-reference.md](../../docs/skills/ship-pr-reference.md) — open it only when a step
points you there.

Substitute `<owner>/<repo>`, `<branch>` and the default base branch for the current repo. Project
pre-flight (test command, linters, commit conventions) comes from the repo's own docs
(`CONTRIBUTING`, `AGENT.md`/`CLAUDE.md`, `README`); this skill owns only the PR/CI plumbing.

## 1. Branch, validate, commit

- **Start from the latest default branch, in its own worktree or branch** — never commit feature work
  to `main`: `git fetch origin && git switch -c feat/<slug> origin/main`. Use a conventional prefix
  (`feat/ fix/ chore/ refactor/ ci/ docs/ perf/`); the Auto-PR workflow keys off it. Pick a branch name
  no other session uses (`git branch -a --list '*<slug>*'` first).
- **Run the repo's pre-flight and paste real output** — test suite, linters, type-checks, build. Never
  `--no-verify`; if a hook fails, fix the cause.
- **Prove every new test is red without the change.** Commit (or copy) your work first, swap the
  pre-change file in with `git show origin/main:<path> > <path>`, run the test, then restore with
  `git checkout HEAD -- <path>` (or from your private copy). Never restore from the shared stash or from
  `stash@{0}` — another session's entry may be on top. A test that passes either way proves nothing.
- **No new lint debt.** The change must not add a raw `print()` (use the project logger, or
  `sys.stdout.write` for intentional CLI output), an inline/mid-file import, a bare `except`, or a
  silent `except: … pass`. Touching a file makes its pre-existing violations yours when the gate checks
  whole files — fix them rather than parking them. **No new suppressions** (`# noqa`, new `ignore` /
  `per-file-ignores` entries) without the user's explicit say-so, reason in the config comment.
- **Stage deliberately** (`git add <paths>`, never `-A`) so scratch, generated and secret files stay
  out. Never commit secrets, tokens or PII.
- **Text written by a script is unreviewed text.** String escapes silently rewrite Windows paths
  (`"Data\notes"` gains a newline, `"Data\rapidapi.lock"` a carriage return). Prefer the editor
  tool or raw strings, then scan the result for control characters and re-read every path in it.
- **Follow the repo's commit-message convention**, including any trailer the environment mandates. The
  first commit's subject becomes the PR title (§2), so make it describe the whole change.
- **Always actually push** once the gate is green — showing a diff and waiting is not shipping.

```bash
git add <paths> && git commit -m "<type>: <summary>" && git push -u origin HEAD
```

## 2. The PR the push opens

- Auto-PR opens a PR **to the default branch** titled with the latest commit subject, and reuses it on
  later pushes. Not set up in this repo? See reference §A, or the fallbacks in §4.
- **Stacked change** (depends on an unmerged PR)? Auto-PR still targets `main` (or may not open one —
  use §4); retarget right after the push — `gh pr edit <n> --base <parent-branch>` — and note the
  dependency in the body. When the parent squash-merges, retarget to `main` and merge `main` in
  (review-prs §8 covers the conflict pattern).
  - **Keep it current** by merging down in order: `main` into the bottom, then each parent into its
    child. Each PR's diff stays its own; no rebase.
  - **Land it** in order, or fold it: merge each child PR into its parent from the top, check the
    bottom's `^{tree}` equals the reviewed top head's, then sync with `main`. If a higher PR fixes a
    lower one's bug, say on the lower PR that they land together.
- **Keep title and body true.** When a later push changes the payload or the conclusion (a study's
  verdict, a scope cut), edit them: `gh pr edit <n> --title … --body-file …`.
- **Several open PRs at once?** Each merge puts the others behind. Re-merge `main` into each, and when
  git auto-merged a file both edited, check that both sides' content survived before re-running tests.
- **CI:** a bot-opened PR may not trigger `pull_request` runs; the push-triggered run is the gate.
  Read the check-runs for the head SHA, not the PR badge. A failed job that ran **zero steps** never
  checked the code: `gh run rerun <run-id>`, don't debug it.
- **After it merges,** clean up branches and worktrees losslessly (review-prs reference §F).

## 3. Respond to review (author side)

1. Fetch **all three** comment surfaces (`issues/<n>/comments`, `pulls/<n>/reviews`,
   `pulls/<n>/comments`) — reviews here are often plain PR comments.
2. Treat each finding as a checklist item: fix it, or reply why not. A finding that changes the
   conclusion (a statistic, a verdict) changes the title, body and docs too.
3. Re-prove what the review questioned: red-check new tests, re-run the mutation the reviewer named,
   re-run the suite on the branch merged with today's `main`.
4. Reply once with an item → commit map and the evidence (real `Ran N … OK`, red-check output). Claim
   only what you ran; say what you could not verify.
5. Behind `main`? Merge it in (review-prs §8) — never rebase + force-push.

## 4. Manual fallbacks (no Auto-PR, or a non-prefixed branch)

- **`gh`:** `gh pr create --base <default> --head <branch> --title "<t>" --body-file <f>` (install,
  auth and proxy notes: reference §B).
- **No auth at all:** open `https://github.com/<owner>/<repo>/pull/new/<branch>` in a browser.

> ⚠️ Don't scrape a token out of the `origin` remote URL or any credential store. Use the Actions
> `GITHUB_TOKEN`, `gh auth login`, or `gh auth token` (gh's own stored credential).

## 5. Verify state — including behind a restrictive network

When the REST API is unreachable but `git` works, verify over the git transport:

- **Open PRs:** `git ls-remote origin 'refs/pull/*/head'`. **A push landed:**
  `git ls-remote origin 'refs/heads/<branch>'`.
- **A merge landed — by effect, not the badge.** Get the merge commit
  (`gh api repos/<owner>/<repo>/pulls/<n> --jq .merge_commit_sha`), check
  `git merge-base --is-ancestor <sha> origin/main`, and confirm the change's content is on `main`
  (`git show origin/main:<path>`). A history rewrite can drop a "merged" commit.
- **Merged ≠ deployed.** A deploy that pulls `main` ships every merge since the target last updated —
  list them (`git log --oneline <deployed-sha>..origin/main`) before calling a change "ready for PROD".
  Read each one for steps the pull does not perform: scheduler or service re-registration, new config
  keys whose **defaults change behavior**, migrations, env vars. Time the deploy by checking that no
  job is running (lock files, processes), not by a clock window — schedules change with the code.
- **CI logs** the agent can't reach: point the user at `https://github.com/<owner>/<repo>/actions`.

"Can't fetch GitHub" is usually the tool's egress lacking the proxy, not an outage — verify with
`git`, which has it.
