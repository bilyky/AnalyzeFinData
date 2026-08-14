# Ship a Change → PR → CI (auto-PR on push)

A repo-agnostic workflow for landing a change: branch, validate, commit, push — and let
**GitHub open the pull request itself**. The key idea: don't open PRs from the agent side
at all. A GitHub Actions workflow opens (or reuses) the PR on push, using the runner's
built-in `GITHUB_TOKEN` — no local `gh` auth, no credential handling on your side. Once set
up, `git push` is the entire "open a PR" step, in any repo.

Substitute `<owner>/<repo>`, `<branch>`, and the default base branch (`main`/`master`) for
the current repo. Delegate all project-specific pre-flight to the repo's own docs
(`CONTRIBUTING`, `CLAUDE.md`/`AGENTS.md`, `README`) — this skill only owns the PR/CI plumbing.

## 1. Branch, validate, commit (generic hygiene)

- **On the default branch? Branch first.** Never commit feature work straight to `main`:
  `git switch -c feat/<short-slug>`. Use a conventional prefix (`feat/ fix/ chore/ refactor/
  ci/ docs/ perf/`) — the Auto-PR workflow below keys off these.
- **Run the repo's own pre-flight before committing** and paste real output (don't claim
  green without it): its test suite, linters/formatters, type-checks, and build — whatever
  the repo defines. If it has a pre-commit hook, let it run; **never** `--no-verify` or
  bypass signing. If a hook fails, fix the cause.
- **Stage deliberately** (`git add <paths>`, not `git add -A`) so scratch/generated/secret
  files stay out. Never commit secrets, tokens, or PII.
- **Follow the repo's commit-message convention** (Conventional Commits + any required
  trailer/footer the environment mandates).

```bash
git add <paths>
git commit -m "<type>: <summary>"
git push -u origin HEAD
```

## 2. The Auto-PR pattern (the reusable core)

Drop this workflow into `.github/workflows/auto-pr.yml`. On push to a prefixed branch it
opens or reuses a PR to the default branch — idempotent (later pushes update the existing
PR, never duplicate). Set `--base` to the repo's default branch.

```yaml
name: Auto-PR
on:
  push:
    branches: ['feat/**','fix/**','chore/**','refactor/**','ci/**','docs/**','perf/**']
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: auto-pr-${{ github.ref }}
  cancel-in-progress: true
jobs:
  open-pr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Open or reuse a PR to the default branch
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          BR="${GITHUB_REF_NAME}"
          existing="$(gh pr list --head "$BR" --state open --json number --jq '.[0].number')"
          if [ -n "$existing" ]; then echo "PR #$existing already open"; exit 0; fi
          title="$(git log -1 --pretty=%s)"
          commits="$(git log origin/main..HEAD --pretty='- %s')"   # adjust base if not 'main'
          body="$(printf 'Auto-opened on push to `%s`.\n\n## Commits\n%s\n' "$BR" "$commits")"
          gh pr create --base main --head "$BR" --title "$title" --body "$body"
```

**Two things that make or break it:**

1. **One-time repo setting** — *Settings → Actions → General → Workflow permissions →*
   check **"Allow GitHub Actions to create and approve pull requests"**. Defaults to **off**
   in many orgs; without it the `open-pr` step fails with a `403` / "not permitted to create
   pull requests". (`https://github.com/<owner>/<repo>/settings/actions`.)

2. **Run CI on _push_, not only on the PR.** A PR opened by `GITHUB_TOKEN` does **not**
   trigger downstream `pull_request` workflow runs (GitHub's recursion guard). So any CI/
   quality-gate workflow must also trigger on push to the same prefixed branches, or
   bot-opened PRs land ungated. e.g.:
   ```yaml
   on:
     pull_request: { branches: [ main ] }
     push:
       branches: [ main, 'feat/**','fix/**','chore/**','refactor/**','ci/**','docs/**','perf/**' ]
   concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }
   ```

## 3. Manual fallbacks (no Auto-PR set up, or a non-prefixed branch)

- **`gh` CLI** (once authenticated — see §4): `gh pr create --base <default> --head <branch>
  --title "<t>" --body "<b>"`.
- **No auth at all** (works from any proxy-aware browser): open
  `https://github.com/<owner>/<repo>/pull/new/<branch>` and paste the title/body.

> ⚠️ **Security boundary:** do NOT scrape a push token out of the `origin` remote URL (or
> any credential store) to hit the GitHub API — the Claude Code security classifier blocks
> this, correctly. Use the Actions `GITHUB_TOKEN` (Auto-PR) or interactive `gh auth login`.

## 4. gh CLI setup notes

- **Install:** varies by OS (`winget`/`brew`/`apt`). On winget, pin the source to avoid an
  interactive store prompt: `winget install --id GitHub.cli -e --source winget
  --accept-source-agreements --accept-package-agreements`.
- **PATH:** a freshly-installed `gh` may not be on the running tool-host's PATH until it
  restarts — invoke by full path if `gh: command not found` (e.g. Windows:
  `"C:\Program Files\GitHub CLI\gh.exe"`).
- **Auth is interactive:** `gh auth login` needs a browser/prompt a non-interactive shell
  can't drive. Ask the user to run it (`! gh auth login`), or use a PAT they supply via
  `gh auth login --with-token`. Don't hunt for a hidden token.

## 5. Verifying PR/CI state behind a restrictive network

Some environments block the GitHub REST API and/or the agent's web-fetch egress (corporate
proxy) even though `git` and the user's browser reach GitHub fine. When the API is
unreachable, **verify over the git transport**, which uses the same proxy git already has:

- **Open PRs:** `git ls-remote origin 'refs/pull/*/head'` — every open PR advertises a
  `refs/pull/<n>/head` ref; empty output = no open PR.
- **Confirm a push landed:** `git ls-remote origin 'refs/heads/<branch>'`.
- **CI run logs** (if the API/browser is blocked from the agent): only the user can see
  them — point them at `https://github.com/<owner>/<repo>/actions`.

Don't assume "can't fetch GitHub" = outage/regression; it's usually the tool's egress path
lacking the proxy, not the network being down. Verify with `git`, which does have it.
