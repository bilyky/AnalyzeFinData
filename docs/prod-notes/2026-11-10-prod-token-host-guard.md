# E*TRADE token from another machine is ignored: what you need to do

Each saved E*TRADE token now records the machine that saved it (`"host"` in
`Data\etrade_tokens.json`). A machine ignores a token file saved on another one, with a
warning in the log, and doesn't renew it either. It also doesn't log itself in automatically
while that file is there: the jobs get no token (prices fall back to Google), and the scheduled
re-auth stops with `reason=foreign_token`. A person can still log in on that machine.

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
E*TRADE: the token file was saved on another machine (<PROD name>); no automated login on this one.
```

On that machine `server.py etrade-reauth --scheduled` exits 1 with `reason=foreign_token` (no
alert email). If that machine should trade, log in there once by hand (`aether etrade-login` or the
System page re-auth button); its own token then replaces the copied file.

## 3. If you move PROD to another computer

The new computer ignores the old one's token and doesn't log itself in automatically, so the
E*TRADE calls there get no token (prices fall back to Google) until you log in once on the new
computer (`aether etrade-login`).
The same applies if PROD's computer name changes.

## Roll back

Revert the PR and restart. Token files with a `"host"` field still load on the old code.
