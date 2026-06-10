# AnalyzeFinData — Claude Instructions

## General Principles

Before questioning data freshness or correctness, ALWAYS verify by checking file timestamps, reading the actual file contents, or running the relevant script first. Do not raise doubts based on assumptions.

## Workflow Conventions

When the user asks to "send", "push", "create", or "save" something (e.g., Gmail draft, commit, file), execute the full action — do not just preview or show content for review unless explicitly asked.

## Excel / openpyxl

For Excel/openpyxl work: when a fix is claimed to be working, verify by actually reopening the file (or simulating the reopen path) and checking the warning/state is gone before reporting success.

## Project Conventions

Standard ranking/query conventions: Setup field uses `'OK'`/`''` strings (not 1/0), and Win% is stored as a decimal (multiply by 100 for display).
