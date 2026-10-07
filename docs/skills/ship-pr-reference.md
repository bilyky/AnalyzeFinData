# ship-pr — reference (open on demand)

Supporting detail for [`.claude/commands/ship-pr.md`](../../.claude/commands/ship-pr.md). The skill
links to a section here only when a step needs it, so this file is not loaded on every run.

## A. Setting up the Auto-PR workflow (one-time, per repo)

Drop this into `.github/workflows/auto-pr.yml`. On push to a prefixed branch it opens or reuses a PR to
the default branch — idempotent (later pushes update the existing PR, never duplicate). Set `--base` to
the repo's default branch.

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

1. **Repo setting** — *Settings → Actions → General → Workflow permissions →* check **"Allow GitHub
   Actions to create and approve pull requests"** (off by default in many orgs; without it `open-pr`
   fails with a `403`).
2. **Run CI on _push_, not only on the PR.** A PR opened by `GITHUB_TOKEN` does **not** trigger
   `pull_request` workflow runs (GitHub's recursion guard), so CI must also trigger on push to the same
   prefixed branches, or bot-opened PRs land ungated:
   ```yaml
   on:
     pull_request: { branches: [ main ] }
     push:
       branches: [ main, 'feat/**','fix/**','chore/**','refactor/**','ci/**','docs/**','perf/**' ]
   concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }
   ```
   The bot-opened PR's own `pull_request` run may then show `action_required` (awaiting approval);
   the push run is the gate that counts.

## B. gh CLI setup

- **Install:** varies by OS (`winget`/`brew`/`apt`). On winget, pin the source to avoid an interactive
  store prompt: `winget install --id GitHub.cli -e --source winget --accept-source-agreements
  --accept-package-agreements`.
- **PATH:** a freshly installed `gh` may not be on the running shell's PATH until it restarts — resolve
  it portably (`command -v gh`, or `Get-Command gh` on PowerShell) instead of hardcoding an OS path.
- **Auth is interactive:** `gh auth login` needs a browser/prompt a non-interactive shell can't drive.
  Ask the user to run it in their own terminal, or use a token they supply via
  `gh auth login --with-token`.
- **Behind a corporate proxy** `gh`'s dialer may ignore the system proxy and time out; set
  `HTTPS_PROXY` to the proxy `git` uses (see review-prs reference §D).
