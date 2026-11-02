"""Purge test-fixture records from PROD's learning ledgers (session 2026-10-06 note, step 9).

    python docs\\prod-notes\\purge_test_fixtures.py            # dry run: counts only, writes nothing
    python docs\\prod-notes\\purge_test_fixtures.py --apply    # rewrite both files without the fixtures

Back up Data\\decision_log.jsonl and Data\\trade_history_dna.json first (note step 2).
Fixtures: decision-log lines for symbols the game never held (it only logs exit decisions
for held positions), and CIRCUIT_BREAKER_TRIGGER records carrying the test position
TSCO cost 100.0 / stop 80.0.
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "Data"   # docs/prod-notes/ -> <repo>/Data
GAME, LOG, DNA = DATA / "ai_portfolio_game.json", DATA / "decision_log.jsonl", DATA / "trade_history_dna.json"


def _is_fixture_breaker(rec: dict) -> bool:
    return rec.get("type") == "CIRCUIT_BREAKER_TRIGGER" and any(
        p.get("symbol") == "TSCO" and p.get("cost") == 100.0 and p.get("stop_loss") == 80.0
        for p in rec.get("open_positions", []))


def main(apply: bool) -> None:
    game = json.loads(GAME.read_text(encoding="utf-8"))
    held = {t["symbol"] for t in game["history"]} | set(game["positions"])
    lines = [ln for ln in LOG.read_text(encoding="utf-8").splitlines(keepends=True) if ln.strip()]
    keep_lines = [ln for ln in lines if json.loads(ln).get("symbol") in held]
    dropped = sorted({json.loads(ln).get("symbol") for ln in lines} - held)
    dna = json.loads(DNA.read_text(encoding="utf-8"))
    keep_dna = [r for r in dna if not _is_fixture_breaker(r)]

    sys.stdout.write(f"decision_log.jsonl: {len(lines)} lines -> keep {len(keep_lines)}, "
                     f"drop {len(lines) - len(keep_lines)} (symbols never held: {', '.join(dropped)})\n")
    sys.stdout.write(f"trade_history_dna.json: {len(dna)} records -> keep {len(keep_dna)}, "
                     f"drop {len(dna) - len(keep_dna)} fixture breaker records\n")
    if not apply:
        sys.stdout.write("dry run — nothing written. Re-run with --apply after checking the symbols above.\n")
        return
    LOG.write_text("".join(keep_lines), encoding="utf-8")
    DNA.write_text(json.dumps(keep_dna, indent=4), encoding="utf-8")
    sys.stdout.write("written.\n")


if __name__ == "__main__":
    main("--apply" in sys.argv[1:])
