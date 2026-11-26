#!/usr/bin/env python3
"""Write the resource inventory to a JSON file.

Deliberately a script rather than a CLI subcommand for now: `cli/main.py` is
being edited concurrently by other work, and this needs no shared state.
Promote it to `resource-explorer export-inventory` once that settles.

Usage:
    python scripts/export_inventory.py                       # stdout
    python scripts/export_inventory.py -o inventory.json     # to a file

Exits non-zero if the export fails verification, so it is safe in a pipeline:
a backup that captured nothing should not look like a successful backup.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", help="write here instead of stdout")
    ap.add_argument("--no-timestamp", action="store_true",
                    help="omit generated_at, so two exports of an unchanged "
                         "inventory are byte-identical and diff clean")
    args = ap.parse_args()

    from resource_explorer.inventory_export import export_inventory, notes, verify
    from resource_explorer.registry import ProjectRegistry

    stamp = "" if args.no_timestamp else datetime.now(timezone.utc).isoformat()
    payload = export_inventory(ProjectRegistry(), generated_at=stamp)

    problems = verify(payload)
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    for note in notes(payload):
        print(f"NOTE: {note}", file=sys.stderr)

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)

    counts = ", ".join(f"{k}={v}" for k, v in sorted(payload["manifest"].items()))
    print(counts, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
