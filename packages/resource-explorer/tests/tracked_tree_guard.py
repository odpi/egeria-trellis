"""A test run must not modify the tracked tree under packages/resource-explorer.

Found 2026-10-11: the survey-definition generator tests rewrote
docs/dr-egeria/survey-definitions-database/*.md in place (restoring afterwards), so any
interrupted run, or a run beside a developer's edits, left tracked files changed.

`snapshot()` fingerprints every file git reports as modified, added, deleted or untracked
under the package (path + status + content hash), so a change to a file that was ALREADY
dirty before the run is still seen. `changed()` diffs two snapshots. conftest takes one at
session start and one at session finish and fails the run on any difference. Returns None
when git is unavailable (or the package is not in a work tree), and the check is skipped.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent


def snapshot(package: Path = PACKAGE) -> dict[str, str] | None:
    try:
        top = subprocess.run(["git", "-C", str(package), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
        if top.returncode != 0:
            return None
        out = subprocess.run(
            ["git", "-C", str(package), "status", "--porcelain=v1", "-z",
             "--untracked-files=all", "--", "."],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    root = Path(top.stdout.strip())
    result: dict[str, str] = {}
    entries = out.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, rel = entry[:2], entry[3:]
        if status[0] in "RC":  # rename/copy: the next NUL field is the origin path
            i += 1
        path = root / rel
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"
        except OSError:
            digest = "unreadable"
        result[rel] = f"{status}:{digest}"
    return result


def changed(before: dict[str, str] | None, after: dict[str, str] | None) -> list[str]:
    """Paths whose status or content differs between two snapshots (empty if either is None)."""
    if before is None or after is None:
        return []
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
