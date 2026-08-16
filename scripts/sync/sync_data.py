"""Sync files from AnalyzeFinData_1/Data to this project's Data folder.

Copies files that are missing or newer in the source.
Never deletes files from the destination.
"""
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))



import os
import shutil
from pathlib import Path


SRC = Path(r"D:\Develop\AnalyzeFinData_1\Data")
DST = Path(r"D:\Develop\AnalyzeFinData\Data")


def sync():
    copied = 0
    skipped = 0
    errors = 0

    for src_file in SRC.rglob("*"):
        if not src_file.is_file():
            continue

        rel = src_file.relative_to(SRC)
        dst_file = DST / rel

        if dst_file.exists():
            src_mtime = src_file.stat().st_mtime
            dst_mtime = dst_file.stat().st_mtime
            if src_mtime <= dst_mtime:
                skipped += 1
                continue
        else:
            pass

        dst_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src_file, dst_file)
            copied += 1
        except Exception:
            errors += 1



if __name__ == "__main__":
    sync()
