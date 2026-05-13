"""Sync files from AnalyzeFinData_1/Data to this project's Data folder.

Copies files that are missing or newer in the source.
Never deletes files from the destination.
"""

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
            reason = "newer"
        else:
            reason = "new"

        dst_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src_file, dst_file)
            print(f"[{reason:6}] {rel}")
            copied += 1
        except Exception as e:
            print(f"[ERROR ] {rel}: {e}")
            errors += 1

    print(f"\nDone: {copied} copied, {skipped} skipped (up-to-date), {errors} errors")


if __name__ == "__main__":
    sync()
