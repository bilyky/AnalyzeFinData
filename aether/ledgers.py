"""Paths of the learning ledgers: the one home for every reader, writer and the test harness.

Read these as module attributes at call time (``ledgers.TRADE_DNA_FILE``), never
``from aether.ledgers import ...``, so a redirect (tests/__init__.py) reaches every user.
"""
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "Data"

TRADE_DNA_FILE = _DATA / "trade_history_dna.json"        # closed trades + circuit-breaker triggers
FAILURE_RULES_FILE = _DATA / "failure_dna_rules.json"    # retrospective_analyzer -> buy-time veto
RETRO_REPORT_FILE = _DATA / "retrospective_report.txt"   # retrospective_analyzer human report
