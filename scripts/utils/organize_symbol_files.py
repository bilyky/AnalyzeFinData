"""Move Symbol flat files into per-symbol subdirectories."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import console_safe
console_safe.install()



import os
import shutil
from pathlib import Path
from collections import defaultdict

from aether_logger import get_logger as _get_logger

_log = _get_logger("organize_symbol_files")

SYMBOL_DIR = Path(r"C:\Develop\StockTrading\AnalyzeFinData\Data\Symbol")


def organize():
    files = [f for f in SYMBOL_DIR.iterdir() if f.is_file() and "_" in f.name]
    by_symbol = defaultdict(list)
    for f in files:
        symbol = f.name.split("_")[0]
        by_symbol[symbol].append(f)

    total = len(files)
    moved = 0
    for symbol, symbol_files in sorted(by_symbol.items()):
        dest_dir = SYMBOL_DIR / symbol
        dest_dir.mkdir(exist_ok=True)
        for src in symbol_files:
            shutil.move(str(src), str(dest_dir / src.name))
            moved += 1
            if moved % 10000 == 0:
                _log.console(f"  {moved}/{total} moved...")

    _log.info(f"Done: {moved} files moved into {len(by_symbol)} symbol folders.")


if __name__ == "__main__":
    organize()
