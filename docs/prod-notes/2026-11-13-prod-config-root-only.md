# PROD: use only the root config.json (2026-11-13)

`aether\config.py` loads **only** `config.json` in the repo root (`_CFG_PATH`). The config patch scripts in this folder
(`patch_groq_reasoning_2026_11_04.py`, `patch_ai_config_175.py`, `patch_ai_models_2026_10_20.py`) used to default to
`Data\config.json`. Unless the root file is a link to it, those patches went to a file the code never reads. The scripts
and the notes now use the root `config.json`. The steps below check PROD and remove the stray copy.

None of these commands prints a key or a secret.

## 1. Is the root config.json a separate file or a link?
```powershell
Get-Item config.json, Data\config.json -ErrorAction SilentlyContinue | Select-Object FullName, LinkType, Target, Length, LastWriteTime
Get-FileHash config.json, Data\config.json -ErrorAction SilentlyContinue | Select-Object Hash, Path
```
- **`config.json` has a LinkType pointing at `Data\config.json`:** the patches did reach the live config. Go to step 4b.
- **Two separate files:** go to steps 2 and 3, then 4a.
- **No `Data\config.json`:** nothing to remove. Only step 2.

## 2. Does the live (root) config have the patched keys?
```powershell
venv_new\Scripts\python.exe -c "import json; c=json.load(open('config.json',encoding='utf-8')); g=c.get('ai',{}).get('providers',{}).get('groq',{}); w=c.get('web',{}); print('groq.min_max_tokens', 'min_max_tokens' in g); print('groq.reasoning_effort', 'reasoning_effort' in g.get('extra_body',{})); print('web.admins', bool(w.get('admins')))"
```
If a Groq line prints `False`, apply the patch to the root file (the scripts now default to it):
```powershell
venv_new\Scripts\python.exe docs\prod-notes\patch_groq_reasoning_2026_11_04.py           # dry run
venv_new\Scripts\python.exe docs\prod-notes\patch_groq_reasoning_2026_11_04.py --apply
```
If `web.admins` prints `False`, add the admin login as in `2026-11-05-prod-dashboard-auth.md`, in `config.json`.

## 3. Which settings exist only in Data\config.json?
This prints key paths only, never values:
```powershell
venv_new\Scripts\python.exe -c "import json; L=lambda f: json.load(open(f,encoding='utf-8')); W=lambda o,p='': [k for kk,v in o.items() for k in (W(v,p+kk+'.') if isinstance(v,dict) else [p+kk])]; r=set(W(L('config.json'))); d=set(W(L('Data/config.json'))); print('only in Data\\config.json:', sorted(d-r) or 'none')"
```
For each path it lists, decide whether the live config needs it, and add it to `config.json` by hand.

## 4. Remove the stray copy
**a. Separate files**, after steps 2 and 3 are clean: keep one backup, then delete.
```powershell
$ts = Get-Date -Format yyyyMMdd_HHmmss; $b = "Data\Backup\${ts}_stray_data_config"; New-Item -ItemType Directory $b | Out-Null
Copy-Item Data\config.json $b; Remove-Item Data\config.json
```
**b. `config.json` is a link to `Data\config.json`:** replace the link with a real file first, then delete the target.
```powershell
$ts = Get-Date -Format yyyyMMdd_HHmmss; $b = "Data\Backup\${ts}_config_link"; New-Item -ItemType Directory $b | Out-Null
Copy-Item Data\config.json $b
Remove-Item config.json; Copy-Item Data\config.json config.json; Remove-Item Data\config.json
Get-Item config.json | Select-Object LinkType, Length      # LinkType must be empty
```
Stop AETHER jobs first: check that no pipeline or trade lock is held, and run this outside 05:00–07:30 and 17:00–19:30 PT.
Then run step 2 again.

## 5. Dev drops
When copying PROD files to dev, put the **root** `config.json` in `Data\prod_copy\<date>\config.json`. It is read masked.
Never copy it over the dev machine's own `config.json` or into the dev `Data\`: it carries PROD's E*TRADE key and TOTP.
