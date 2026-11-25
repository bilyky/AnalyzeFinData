# Earnings press-release labels (R&D #98 Part B data), nightly task

New: `aether/earnings_8k.py`, entry point `scripts/monitoring/earnings_8k_labels.py`, task `AETHER_Earnings8K` (daily 18:30).

## What it does

- Re-reads SEC 8-K item 2.02 filings: `Data/earnings_history.json` is rebuilt, with the recent pages refreshed if older than 20 h.
- For each report not yet labelled, it fetches the press release exhibit and asks the configured **cheap** AI tier (Groq on PROD) for three labels with one fixed prompt (`PROMPT_VERSION = "v1"`): results, guidance and tone.
- It appends one row per report to `Data/earnings_8k_labels.jsonl`; this job is the only writer.
- Order and limits:
  - new reports first (filed in the last 10 days), then the 2021+ backfill;
  - at most 150 per night;
  - it stops at the first AI skip, and failures are retried up to 3 times.
- Only public SEC documents go to the model.
- Trading is unchanged; nothing live reads the labels yet.

## Steps

1. Pull main.
2. The SEC contact: the client uses `AETHER_SEC_CONTACT` or `email_sender.address` from config. Without either, SEC returns 403.
3. Register the task (admin PowerShell): `powershell -ExecutionPolicy Bypass -File scripts/utils/register_agent_tasks.ps1 -Only AETHER_Earnings8K`
4. Optional, first run by hand: `python scripts/monitoring/earnings_8k_labels.py --refresh-history --max 20`

## Evidence to expect

- `earnings_8k_task.log` shows a JSON summary such as `{"due": ..., "written": ..., "by_status": {"ok": ...}}`.
- `Data/earnings_8k_labels.jsonl` grows by up to 150 rows a night, most with `"status": "ok"`.
- About 11,000 reports are queued (2021 on). At 150 a night the backfill takes about 2.5 months. Raise `--max` only if Groq's daily limits allow; the AI step logs a skip when a limit is hit.
