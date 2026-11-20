# Lots refresh in the workbook update: what you need to do

`check_from_xls()` (the workbook update) now also refreshes position-watch's lots file
(`Data\position_watch\<SYMBOL>_lots.json`) from E*TRADE, in the same session it already uses for
the Short_Long sheet. It reads only, never logs in, and never fails the workbook update: on any
problem it logs one `[Lots] ...` warning and leaves the file alone.

It refreshes **only the accounts you map**. Lots under any other label (for example a stock-plan
account the E*TRADE API doesn't list) stay exactly as you keep them. Nothing is written unless
each mapped account's lots add up to E*TRADE's position quantity, and the old file is backed up
first (`Data\Backup\<time>_pre_lots_refresh`).

## 1. Deploy

Pull. Without step 2 nothing changes (the refresh reports `skipped`).

## 2. Map the account (private config, never in git)

Back up `Data\position_watch\config.json`, then add, with the label used in your lots file and the
last 4 digits of that E*TRADE account:

```json
"lot_sources": {"<label in the lots file>": "<last 4 digits>"}
```

## 3. First run: a dry run by hand

With a valid E*TRADE token for today on that machine:

```powershell
venv_new\Scripts\python.exe -m aether.lots_refresh
```

It prints what it would do and writes nothing:
- `unchanged`: the file already matches E*TRADE.
- `would_update` with `added` / `removed` counts: the lots E*TRADE reports differ from the file.
  Small numbers (a recent buy, sale or vest) are expected. If nearly every lot of that account
  shows as removed and added, the broker's figures differ from how the file was written (for
  example, E*TRADE's per-share price vs a hand-entered cost): compare before writing.
- `blocked`: lots don't add up to the position, or the account isn't in E*TRADE's list. The reason
  says which; nothing is written.

Then either run it once with `--write`, or let the next workbook update do it.

## 4. Check

The workbook update's log shows `[Lots] updated: [...]` or `[Lots] unchanged: ...`. A
`[Lots] blocked` or `[Lots] error` line means the file was left as it was.

## Roll back

Remove `lot_sources` from the config (the refresh then skips), or revert the PR. Restore an old
lots file from `Data\Backup\<time>_pre_lots_refresh` if needed.
