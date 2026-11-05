# Silent failures (#239): what you need to do

Two changes:
- Chaikin's auth files (`session.json`, `chaikin_chrome_profile\`, `chaikin_reauth.lock`) are
  now found through `AETHER_DATA_DIR` (else `<repo>\Data`), the same rule E*TRADE's token
  already follows. They used to be pinned to `<repo>\Data` regardless.
- A failed E*TRADE quote batch (e.g. a dead token, HTTP 401) now logs a WARNING instead of
  silently returning nothing. Prices still come back for the batches that worked.

## 1. Before deploying: check AETHER_DATA_DIR (important)

In the PowerShell that runs the tasks (and for the task user):

```powershell
$env:AETHER_DATA_DIR
[Environment]::GetEnvironmentVariable("AETHER_DATA_DIR", "User")
[Environment]::GetEnvironmentVariable("AETHER_DATA_DIR", "Machine")
```

- **All empty, or the repo's own `Data` folder:** nothing to do. The files stay where they are.
- **Set to another folder:** after this deploy, Chaikin looks there. Copy these first, or
  the next run will try a fresh Chaikin login (the Chrome profile holds the long-lived
  login cookie):

```powershell
$dst = $env:AETHER_DATA_DIR   # or the User/Machine value above
Copy-Item Data\session.json $dst
Copy-Item Data\chaikin_chrome_profile $dst -Recurse   # with Chrome closed: a profile in use copies partially
```

## 2. Deploy

Merge #239, pull on PROD.

## 3. Check

- The next morning/evening run fetches Chaikin data as usual (no `Session rejected` or
  browser login in the log).
- If an E*TRADE token dies during the day, the log now shows
  `E*TRADE quotes failed for N symbols (...): 401 ...` instead of nothing. That also helps
  with the open item in the #229 note (tokens dying within ~40 minutes on 11-03).

## Roll back

Revert #239. If you copied files in step 1, the originals are still in `Data\`.
