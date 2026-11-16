# PROD: enable the AI tiers after PR #192 (R&D #53)

PR #192 (`feat/ai-provider-tiers`, head `3277bb5`, CI green) makes advisory AI calls ask for a
tier (`cheap` / `strong`) instead of a provider. Which provider answers is set in config only.
Design: `AETHER_REFERENCE.md` §2.F. Roadmap: item 53.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`.
PowerShell 5.1 syntax. Nothing here needs an Administrator shell.

**Status 2026-10-13: PR #192 is OPEN, not merged.** Do step 0 first; everything else comes after
the merge and deploy.

**Decisions (owner, 2026-10-13):**
- Now (Phase 0, $0): **Groq first, then Gemini** (free tiers). OpenRouter's free models are turned off.
- Phase 2: **a real Anthropic key**: Haiku 4.5 for `cheap`, Sonnet 5.5 for `strong`, free ones kept as fallbacks.
- Trigger for Phase 2: **as soon as any realized, cash gain covers the model's cost.**
- Scheduled agent skills: **trial Goose** (PR 2, separate).

The AI only advises. If every step below goes wrong, trades are not affected: each caller falls
back to its rule-based answer.

---

## What PROD has today (from the 10-12 `Data\config.json` copy)

| Provider | Enabled | Model | Key |
|---|---|---|---|
| `openrouter` (primary) | yes | `nvidia/nemotron-3-super-120b-a12b:free` | literal in the file |
| `openrouter_fb` | yes | `apodex/apodex-1.1-mini:free` | literal in the file |
| `github_gpt` | no | retired 2026-07-30 | |
| `claude` | no | `claude-opus-4-8` | `env:ANTHROPIC_API_KEY` |
| `gemini`, `gemini_cli`, `gemini_oai` | no | `gemini-3.8-flash` | |

- The OpenRouter endpoint host is now spelled right (`openrouter.ai`). The typo `openrpouter.ai`
  stopped every AI call from 2026-09-24 to 2026-10-05.
- There is no `ai.tiers` block yet. Without one, #192 keeps the old order (primary, then the
  other enabled providers), so merging #192 alone changes nothing about who answers.
- The 10-05 to 10-09 logs show 429s from the free OpenRouter route. I don't know whether that was
  the 50/day cap or a busy upstream host.

---

## 0. Merge and deploy #192

Merge on GitHub when you are happy with it. Then on PROD, at a time no job is running (check that
no `Data\*.lock` file is fresh and no `python.exe` from the repo is running):

```powershell
cd C:\Develop\StockTrading\AnalyzeFinData
Get-Date
git fetch origin
git log --oneline HEAD..origin/main          # read every merge you are about to deploy
git pull --ff-only origin main
git log -1 --format="%h %s"
# The new example must be there; the patch script reads it:
venv_new\Scripts\python.exe -c "import json; print('tiers' in json.load(open('config.json.example', encoding='utf-8'))['ai'])"
```

The last line must print `True`.

## 1. Find the config file the app actually reads

> **Update 2026-11-13:** the notes and the patch scripts now use only the root `config.json` (the file
> `aether\config.py` loads). `Data\config.json` is a stray copy unless `config.json` is a link to it;
> see `2026-11-13-prod-config-root-only.md`.

`aether\config.py` loads **`config.json` in the repo root**. The 10-08 notes edited
**`Data\config.json`**. Check whether they are the same file before changing either:

```powershell
venv_new\Scripts\python.exe -c "import aether.config as c; print(c._CFG_PATH)"
Get-Item config.json, Data\config.json -ErrorAction SilentlyContinue | Select-Object FullName, LinkType, Target, Length, LastWriteTime
Get-FileHash config.json, Data\config.json -ErrorAction SilentlyContinue | Select-Object Hash, Path
```

- If `config.json` is a link to `Data\config.json`, or the hashes match and you keep them in sync,
  patch the file the first line printed.
- If they differ, the root `config.json` is the live one. Patch that, and copy it to
  `Data\config.json` afterwards if you keep that copy for review.

Below, `<cfg>` means the file the first line printed.

## 2. Back up

The patch script makes its own backup, but the project rule is a backup before any write:

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force "Data\Backup\$ts`_pre_ai192" | Out-Null
Copy-Item <cfg>, Data\ai_portfolio_game.json, Data\state_of_the_day.xlsx "Data\Backup\$ts`_pre_ai192\"
Get-ChildItem "Data\Backup\$ts`_pre_ai192"
```

## 3. Add the tiers and the ready provider blocks (free only, nothing new enabled)

```powershell
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg>            # dry run
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg> --apply    # backup + write
```

What it does (tested on a copy of the 10-12 PROD config):
- adds `ai.tiers` from `config.json.example`: `cheap = [groq, gemini_lite]`, `strong = [groq, gemini_oai]`;
- adds the missing blocks **disabled**: `groq`, `gemini_lite`, `together_deepseek`, `fireworks`,
  `mistral`, `azure_openai`, `bedrock`, `claude_haiku`;
- never changes an existing block except the `enabled` flag you ask for; never prints a key;
- a second run changes nothing.

After this step the tiers have no usable provider yet, so calls fall through to `primary`
(`openrouter`) and `openrouter_fb`, the same as today. The script says so.

## 4. Switch to Groq + Gemini and turn OpenRouter off

Decision 2026-10-13: free providers only, **Groq first, then Gemini**. OpenRouter's free models are
dropped because about half of their hosts may train on prompts, and these prompts hold positions.
Google may also use Gemini free-tier prompts for training; that is why Gemini is the last fallback.
I don't know Groq's free-tier data policy: read it on console.groq.com before step 4.3.

**Order matters:** enable the new providers first. The script refuses to turn OpenRouter off while a
tier would be left with no usable provider, and writes nothing in that case.

1. **Groq:** sign up at console.groq.com and create an API key. Check that `openai/gpt-oss-120b` is
   in its model list; if not, change `providers.groq.model` in `<cfg>`.
2. **Gemini:** create a key in Google AI Studio (aistudio.google.com, **personal** Google account;
   the organization account was blocked by its policy in September). Check the model ids
   `gemini-3.1-flash-lite` (`gemini_lite`) and `gemini-3.8-flash` (`gemini_oai`) in AI Studio and
   fix them in `<cfg>` if they differ. Check the live free limits at aistudio.google.com/rate-limit:
   the cheap tier needs room for about 35 calls/day (56 at most), so Flash-Lite, not Flash.
3. Put both keys in `.env` in the repo root (gitignored; `ai_client` reads `env:` keys from it):
   ```powershell
   Add-Content .env "GROQ_API_KEY=<paste key>"
   Add-Content .env "GEMINI_API_KEY=<paste key>"
   ```
4. Enable them and turn OpenRouter off in one run (dry run first, then `--apply`):
   ```powershell
   venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg> --enable groq --enable gemini_lite --enable gemini_oai --disable openrouter --disable openrouter_fb
   venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg> --enable groq --enable gemini_lite --enable gemini_oai --disable openrouter --disable openrouter_fb --apply
   ```
   Expect `ai.primary: 'openrouter' -> 'groq'` and both tiers listing their providers as usable.
   Tested on a copy: without the keys it stops and the file is unchanged; with them it writes.

## 5. Check it

Config check, no model request (the same check preflight now runs; WARN only, it never blocks):

```powershell
venv_new\Scripts\python.exe -c "import sys; sys.path.insert(0, 'scripts/diagnostics'); import preflight_validator as p; print(p.check_ai_providers())"
```

Expect `(True, [])`. Anything else lists what to fix (a tier with no usable provider, a non-https
endpoint, a host that does not resolve).

One live call per tier (uses 2 free requests):

```powershell
venv_new\Scripts\python.exe -c "from aether import ai_client as a; print(a.evaluate_with_provider('Reply with the word OK.', 'ping', max_tokens=20, tier='cheap')); print(a.evaluate_with_provider('Reply with the word OK.', 'ping', max_tokens=20, tier='strong'))"
```

Each line prints `('OK', '<provider that answered>')`.

## 6. Watch the logs the next day

Successes are now logged. Count who answered and what failed:

```powershell
# aether.jsonl rotates (aether.jsonl.1, .2, ...): read them all.
$j = Get-ChildItem Data\logs\aether.jsonl* | ForEach-Object { Get-Content $_.FullName -Encoding UTF8 } |
     ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } | Where-Object { $_.module -eq 'aether.ai_client' }
$j | Where-Object { $_.msg -like 'AI call ok*' } | Group-Object { "$($_.ts.Substring(0,10)) $($_.extra.ai_provider)" } | Select-Object Count, Name
$j | Where-Object { $_.level -eq 'WARNING' } | Group-Object { "$($_.ts.Substring(0,10)) $($_.extra.provider)" } | Select-Object Count, Name
```

Warnings with no provider name are the one-time 429 retries. If a trading day has no
`AI call ok` lines at all, the AI is down again: check step 5. (Tested on the 10-12 PROD log copy:
the query finds the old failure warnings; there are no `AI call ok` lines yet because that code
is new.)

## 7. (Optional) Save real prompts to compare providers

The bake-off picks providers on real answers. The captured file holds position data: keep it on
PROD, never commit or email it, delete it when done.

1. Set the variable for the account that runs the scheduled tasks, for one trading day:
   ```powershell
   [Environment]::SetEnvironmentVariable("AETHER_AI_CAPTURE", "C:\Develop\StockTrading\AnalyzeFinData\Data\ai_prompts.jsonl", "User")
   ```
   I don't know whether the scheduled tasks see a new user variable without a sign-out or reboot.
   Check that the file grows after the next AI job runs; if it does not, sign out and back in
   (or reboot) and check again.
2. Remove it afterwards:
   ```powershell
   [Environment]::SetEnvironmentVariable("AETHER_AI_CAPTURE", $null, "User")
   ```
3. With 2 or more providers enabled, run (each prompt is one request per provider):
   ```powershell
   venv_new\Scripts\python.exe scripts\diagnostics\ai_provider_bakeoff.py --prompts Data\ai_prompts.jsonl --providers groq,gemini_lite,gemini_oai --limit 20 --out Data\ai_bakeoff.md
   ```
   Read `Data\ai_bakeoff.md`, then put the provider with the best answers first in each tier.

## 8. Later: move to paid Claude models (Phase 2)

**When (decision 2026-10-13):** as soon as any realized gain, in cash, covers the model's cost.
At about 35 calls a day that is roughly a few dollars a month (Haiku 4.5 $1/$5 and Sonnet 5.5
$2/$10 per 1M tokens, Anthropic's list prices; my per-call token sizes are assumed until the
step 7 capture measures them).

**What (decision 2026-10-13): a real Anthropic API key.** Cheap tier: `claude_haiku`
(`claude-haiku-4-5`). Strong tier: the existing `claude` block on Sonnet 5.5. The GNAI dev-relay
token is not an Anthropic key (api.anthropic.com answers 401 to it).

1. Create a key at console.anthropic.com, add billing, and put it in `.env`:
   ```powershell
   Add-Content .env "ANTHROPIC_API_KEY=<paste key>"
   ```
   If an `ANTHROPIC_API_KEY` user or machine environment variable is set on PROD, it wins over
   `.env`; check with `[Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','User')`.
2. In `<cfg>`, change the existing `claude` block (the script never edits existing blocks):
   `"model": "claude-sonnet-5-5"` and add `"extra_body": {"thinking": {"type": "between_tools"}}`.
   Sonnet 5.5 thinks by default, and thinking counts against the small `max_tokens` these calls
   use; `between_tools` turns it off. Same as the example's `claude` block.
3. Enable both:
   ```powershell
   venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg> --enable claude_haiku --enable claude --apply
   ```
4. Edit `ai.tiers` in `<cfg>` by hand and keep the free providers after them as fallbacks:
   `"cheap": ["claude_haiku", "groq", "gemini_lite"]`, `"strong": ["claude", "groq", "gemini_oai"]`.
5. Steps 5 and 6. In the logs, `ai_provider` should now be `claude_haiku` / `claude`.

Other paid blocks stay ready in the example (Together/US DeepSeek, Fireworks, Mistral, Azure,
Bedrock): enable the same way if Claude ever needs a backup. Never use DeepSeek's own API for this
data (it is stored in China).

## 9. Tidy-ups (any time, optional)

- `github_gpt` is retired; you can delete its block.
- Once OpenRouter is off (step 4), its key is not needed. Its two blocks hold the key as a literal
  string, which also lands in config copies like `Data\config.json`: replace it with
  `"api_key_source": "env:OPENROUTER_API_KEY"` (leave the variable unset), and revoke the key on
  openrouter.ai. An older note says it was once pasted into a chat.

## Rollback

- Config: copy back `Data\Backup\<timestamp>_config_ai192\config.json` (or the step 2 copy) over `<cfg>`.
- Keep the code but drop the tiers: delete the `ai.tiers` block. Calls then use the old order.
- Code: revert the #192 merge commit on main and redeploy.
