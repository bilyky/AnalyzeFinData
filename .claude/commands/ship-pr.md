# Ship a Change → PR → CI

The end-to-end workflow for landing a code change in AETHER: branch, validate, commit,
push, and open a pull request that the CI gate will check. Captures the repo-specific
gotchas so none of them have to be rediscovered.

## 0. Preconditions (do these BEFORE editing)

- **On `main`? Branch first.** Never commit feature work straight to `main`:
  ```bash
  git switch -c feat/<short-slug>
  ```
- **Editing `web/app.js`? Bump the cache-buster.** `web/index.html` loads
  `<script src="/static/app.js?v=X.Y.Z">`. ANY `app.js` edit REQUIRES bumping `?v=` or
  browsers serve the stale file. (Currently `1.0.10`.)
- **Changed a Python module the server imports?** Restart the server to see it. `server.py`
  calls `uvicorn.run(app, ...)` with the app **object**, so auto-reload is OFF despite the
  comment near `server.py:1039`. Static/`app.js`/`index.html` edits need only a browser
  refresh (they're read from disk per request) — no restart.

## 1. Verify locally (zero-trust — paste real output)

```bash
python -m unittest discover tests          # all green, incl. any new red-green test
python -c "import workbook_read, server"   # imports clean
```
New feature ⇒ new test (the pre-commit validator enforces this). Excel/openpyxl fixes:
re-run the exact reproduction and paste the output before claiming success.

## 2. Commit (the pre-commit hook is law)

- **Never** `--no-verify`; never bypass signing. If the hook fails, fix the cause.
- **Never** stage `backtest_output_new.txt` (untracked scratch output).
- The hook runs `scripts/utils/pre_commit_validator.py` on the **staged** diff
  (`git diff --cached`): bans bare `print()`, inline imports, silent `except: pass`;
  enforces doc-sync anchors, R&D-roadmap sync, and new-feature test coverage.
- Commit message ends with:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

```bash
git add <specific paths>        # not `git add -A` — keep scratch files out
git commit -m "<msg>"
git push -u origin HEAD
```

## 3. Open the PR — it opens itself

**Just push.** `.github/workflows/auto-pr.yml` opens (or reuses) a PR to `main` on every
push to a conventionally-named branch (`feat/** fix/** chore/** refactor/** ci/** docs/**
perf/**`). GitHub does it with the runner's built-in `GITHUB_TOKEN` — no local `gh` auth,
no token handling on our side. Name the branch with one of those prefixes and step 2's
`git push` IS the "open a PR" step. The workflow is idempotent (later pushes update the
existing PR, never duplicate it) and stamps the `🤖 Generated with Claude Code` footer.

> ⚠️ Do NOT scrape the push token out of the `origin` remote URL to hit the GitHub API —
> the Claude Code security classifier blocks it, correctly. The Auto-PR workflow is the
> sanctioned path: the token is injected by GitHub into the runner, never into our context.

Manual fallbacks, only if you need a PR for a non-prefixed branch or the workflow is off:
- `gh` (once authenticated, see §4): `"C:\Program Files\GitHub CLI\gh.exe" pr create
  --base main --head <branch> --title "<t>" --body "<b>"` (body ends with the Claude Code footer).
- No auth at all: the browser link `https://github.com/bilyky/AnalyzeFinData/pull/new/<branch>`.

## 4. gh CLI on Windows — setup gotchas (only needed for the manual fallback)

- **Install** (winget's default source prompts interactively and fails in the `!` session —
  pin the source):
  ```powershell
  winget install --id GitHub.cli -e --source winget \
    --accept-source-agreements --accept-package-agreements
  ```
- **Not on PATH yet:** after install, the tool-host process won't see `gh` until it
  restarts — invoke by full path `"C:\Program Files\GitHub CLI\gh.exe"`.
- **Auth is interactive:** `gh auth login` needs a browser/prompt the `!` session can't
  drive. Ask the user to run it themselves (suggest they type `! gh auth login`), or use
  a PAT they provide via `gh auth login --with-token`. Do not hunt for a hidden token.

## 5. CI — what runs on the PR

`.github/workflows/ci.yml` = one dependency-free `quality-gate` job (ubuntu-latest) on
`pull_request` + `push` to `main`:
1. Byte-compile all tracked Python (`git ls-files '*.py' | xargs -r python -m py_compile`).
2. Run the pre-commit validator against the change set, recreating the "staged diff" view
   in CI with `git reset --soft <base>` (leaves the PR's commits staged) — same checks as
   the local hook, zero edits to the validator.

**The full `unittest` suite is intentionally NOT in CI.** Discovery imports every test
module, and the app top-level-imports `MetaTrader5` (Windows-only, un-pip-installable on
Linux) plus other unpinned native deps (pyetrade, playwright, sqlalchemy, pandas, numpy;
`requirements_web.txt` only pins fastapi+uvicorn). Making it CI-runnable needs a curated
`requirements-ci.txt` + Linux-guarded native imports, or a `windows-latest` runner —
tracked follow-up.
