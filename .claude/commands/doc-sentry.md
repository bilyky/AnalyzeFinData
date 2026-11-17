# Feature Docs — keep the feature list true in the same PR

Use when a PR changes what the app does as a user sees it: a new module or engine, an email or
report section, a dashboard tab, card or endpoint, a scheduled job, a new gate or alert, or the
removal of any of these. Agent-agnostic. The full procedure (schema, card wiring, drift test) is
[.skills/aether-documentation-sentry/SKILL.md](../../.skills/aether-documentation-sentry/SKILL.md);
this page is the short version and the rule that ties it to every PR.

## Before building: match it to the list

Read the feature list (`Data/wiki.json` keys and summaries, `AETHER_REFERENCE.md`) and grep the code
for anything that already does part of the new job. Extend or reuse it rather than adding a parallel
feature; name what it builds on in the PR body. The list can lag the code, so search both.

## The rule: choose one, in the PR itself

- **Feature docs updated** in the same PR, all three surfaces:
  1. `AETHER_REFERENCE.md`: one line under the shipped features (what it does, the PR, the date).
  2. `Data/wiki.json`: the entry (`title`, `summary`, `origin`, `body`, `config`), in plain words.
  3. `web/index.html`: the About-tab card with `data-wiki="<key>"`. Every key has exactly one card,
     and every card one key (`tests/test_about_wiki_sync.py`; the pre-commit hook runs it).
- **Or** the PR body says **"No feature-doc change"** and, in a few words, why (a bug fix, a study,
  internal refactor, tests only).

A removed or retired feature updates its entry's status too: a card that still describes it as live
is a stale doc.

## Checks

- Run `python -m unittest tests.test_about_wiki_sync` after editing either surface.
- Public repo: no account digits, balances, names or plan values in any of the three files.
- Describe what the feature does and its limits, verified against the code; don't call a bug fix a
  feature or claim effects nobody measured.
