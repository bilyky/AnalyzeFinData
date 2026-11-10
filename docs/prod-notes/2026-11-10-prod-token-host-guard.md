# E*TRADE token from another machine is ignored: what you need to do

Each saved E*TRADE token now records the machine that saved it (`"host"` in
`Data\etrade_tokens.json`). A machine ignores a token file saved on another one, with a
warning in the log, and doesn't renew it either.

Why: a backup copy of PROD's `Data\` brought PROD's live token onto dev twice (2026-11-05 and
2026-11-10). Dev and PROD share one app key, so using or renewing that token from dev can end
PROD's session. The copy script's exclusions (`etrade_*`) are the first line; this is the
second.

## 1. Deploy (PROD and dev)

Merge, pull, restart the dashboard (`venv_new\Scripts\python.exe server.py restart`).

Nothing else. A token file saved before this change has no `"host"` and is used as before.
The next save (the next renew or login) adds it.

## 2. Check

After PROD's next renew or login, `Data\etrade_tokens.json` on PROD has a `"host"` field
with PROD's computer name. On dev, a copied PROD token then logs:

```
E*TRADE token file was saved on another machine (<PROD name>), not this one (<dev name>): ignoring it.
```

## 3. If you move PROD to another computer

The new computer ignores the old one's token: log in once on the new computer
(`aether etrade-login`).

## Roll back

Revert the PR and restart. Token files with a `"host"` field still load on the old code.
