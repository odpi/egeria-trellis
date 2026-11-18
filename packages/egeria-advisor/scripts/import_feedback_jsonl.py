#!/usr/bin/env python3
"""Copy EA's JSONL feedback into the trellis-feedback store, once (Phase 2, A-F1).

    python scripts/import_feedback_jsonl.py --data-dir ~/egeria-advisor-data --dry-run
    python scripts/import_feedback_jsonl.py --data-dir ~/egeria-advisor-data

Reads `<data dir>/feedback/feedback_extended.jsonl` and `user_feedback.jsonl`. The rules (ids,
pairing one vote written to both files, triage status) are in `advisor/feedback_import.py`.

- Safe to run again: a second run imports nothing and reports every line as already imported.
- The files are not changed or removed.
- `--dry-run` writes nothing. It reads the store only to say which lines it already holds.

The store is the one EA uses: `ADVISOR_FEEDBACK_DATABASE_URL` / `ADVISOR_FEEDBACK_SCHEMA`, else EA's
own Postgres (`advisor/feedback_store.py`). `--database-url` and `--schema` override both.

Exit status:
- 0: every line was imported, already held, or skipped for a reason the report gives.
- 1: the store refused a line, or could not be opened.
- 2: the data dir has no feedback folder at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="EA's data root (default: the one EA resolves, ADVISOR_DATA_PATH)")
    ap.add_argument("--dry-run", action="store_true", help="report what would be imported; write nothing")
    ap.add_argument("--database-url", default=None, help="override the feedback store URL")
    ap.add_argument("--schema", default=None, help="override the feedback store schema")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)

    from advisor.feedback_import import FILES, run_import
    from advisor.feedback_store import StoreUnavailable, _redact, feedback_database_url, feedback_schema, get_store

    if args.data_dir is None:
        from advisor.config import resolve_advisor_data_root
        args.data_dir = resolve_advisor_data_root()
    data_dir = args.data_dir.expanduser()
    if not (data_dir / "feedback").is_dir():
        print(f"No feedback folder under {data_dir}: nothing to import (looked for {data_dir / 'feedback'}).")
        return 2

    url = args.database_url or feedback_database_url()
    schema = args.schema or feedback_schema()
    try:
        store = get_store(url, schema)
    except StoreUnavailable as exc:
        print(f"Store not opened: {exc}")
        return 1

    report = run_import(store, data_dir, dry_run=args.dry_run)
    report.store = f"{_redact(url)} schema={schema}"
    out = report.to_dict()
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        verb = "would import" if args.dry_run else "imported"
        print(f"{'DRY RUN: ' if args.dry_run else ''}store {report.store}")
        for name in FILES:
            f = out["files"].get(name, {})
            if not f.get("present"):
                print(f"  {name}: not present")
                continue
            print(f"  {name}: {f['read']} line(s) read, {f['candidates']} importable, {f['skipped']} skipped")
        print(f"  {verb}: {out['imported']} item(s), {out['events']} with a carried-over triage status")
        for reason, n in sorted(out["skipped_by_reason"].items()):
            print(f"  skipped, {reason}: {n}")
        for s in out["skipped"]:
            print(f"    {s['file']} line {s['line']}: {s['reason']}")
        for n in out["notes"]:
            print(f"    {n['file']} line {n['line']}: {n['note']}")
    refused = any(s["reason"].startswith("refused by the store") for s in out["skipped"])
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
