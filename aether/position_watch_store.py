"""Position-watch's private data: one home for the folder, the config, the lots and the journal path.

Data/position_watch/ (under ``paths.data_dir()``) holds position-watch's private files: config.json,
<SYMBOL>_lots.json (the watched holding's tax lots), <SYMBOL>_journal.jsonl and invest_plan.json.
``scripts/monitoring/position_watch.py``, ``oceanview_api.py`` and ``aether/lots_refresh.py`` all
read or write them, and each used to build the path and parse the files itself (PR #271 design
review). They now go through this module, which is also the only writer of the lots file: a
backup first (Data/Backup/<time>_pre_lots_refresh), then an atomic replace.

``folder`` arguments exist for tests and for callers that pass a data folder explicitly; by default
it is ``watch_dir()``.
"""
import datetime
import json
import os
import shutil

from aether import paths

FOLDER_NAME = "position_watch"


def watch_dir() -> str:
    return os.path.join(paths.data_dir(), FOLDER_NAME)


def config_path(folder: str | None = None) -> str:
    return os.path.join(folder or watch_dir(), "config.json")


def lots_path(symbol: str, folder: str | None = None) -> str:
    return os.path.join(folder or watch_dir(), f"{symbol}_lots.json")


def journal_path(symbol: str, folder: str | None = None) -> str:
    return os.path.join(folder or watch_dir(), f"{symbol}_journal.jsonl")


def plan_path(folder: str | None = None) -> str:
    return os.path.join(folder or watch_dir(), "invest_plan.json")


def read_config(folder: str | None = None, path: str | None = None) -> dict | None:
    """The private config, or None when there is no file. A file that exists but can't be read
    raises (OSError / ValueError): callers decide whether that is fatal."""
    p = path or config_path(folder)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read_lots(symbol: str, folder: str | None = None) -> list | None:
    """The lots list, or None when there is no file. Raises (OSError / ValueError) when it exists
    but can't be read."""
    p = lots_path(symbol, folder)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _canon(lots: list) -> list:
    """Order-free form of a lots list (qty and price compared as floats)."""
    return sorted(json.dumps({**x, "qty": float(x.get("qty") or 0), "price": float(x.get("price") or 0)},
                             sort_keys=True) for x in lots)


def same_lots(a: list, b: list) -> bool:
    """The same lots, in any order."""
    return _canon(a) == _canon(b)


def lots_diff(old: list, new: list) -> tuple:
    """(added, removed) counts between two lots lists, order-free."""
    before, after = set(_canon(old)), set(_canon(new))
    return len(after - before), len(before - after)


def write_lots(symbol: str, lots: list, folder: str | None = None,
               now: datetime.datetime | None = None) -> str | None:
    """Replace the lots file: copy the old one to <data dir>/Backup/<time>_pre_lots_refresh, then an
    atomic replace. Returns the backup folder (None when there was no old file)."""
    folder = folder or watch_dir()
    path = lots_path(symbol, folder)
    bdir = None
    if os.path.exists(path):
        stamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
        bdir = os.path.join(os.path.dirname(folder), "Backup", f"{stamp}_pre_lots_refresh")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lots, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):          # the old file and its backup are intact; drop the partial copy
            os.remove(tmp)
        raise
    return bdir
