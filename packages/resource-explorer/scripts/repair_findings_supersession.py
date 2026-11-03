#!/usr/bin/env python
"""Repair: mark OLDER runs' findings superseded for telemetry_scan_findings,
contribution_provenance_findings and sla_content_findings.

Until the writers set `supersedes_previous=True` (docs/design-notes/implemented/
FINDINGS-SUPERSESSION-IMPLEMENTED.md), every earlier run's rows stayed
`superseded_at IS NULL` and a bare "non-superseded" count added every run ever
written. This stamps, for each (resource, kind, scope_locator), every
non-superseded row older than that group's newest run with
`superseded_at = <newest run's surveyed_at>` -- exactly what the writer now does
at the moment a new run lands. Nothing is deleted.

DRY-RUN IS THE DEFAULT. It reads and prints counts only (never finding text).
`--apply` writes, in ONE TRANSACTION PER RESOURCE, and is idempotent (a second
apply finds nothing left to mark).

The registry is named explicitly (`--database-url`, else REGISTRY_DATABASE_URL);
there is deliberately no fall-back to the app's configured registry, because
that is the shared Postgres. Any non-SQLite URL is treated as the shared
registry and refused unless `--i-know-this-is-the-shared-registry` is passed.
This script does not run the registry's schema migrations: it needs the existing
`superseded_at` column and stops if it is absent.

    REGISTRY_DATABASE_URL=sqlite:///tmp.db python scripts/repair_findings_supersession.py
    ... --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

from sqlalchemy import create_engine, text

KINDS = (
    "telemetry_scan_findings",
    "contribution_provenance_findings",
    "sla_content_findings",
)
# ISO date shape; bound as a parameter (a literal % collides with psycopg).
_ISO = "____-__-__%"
SHARED_FLAG = "--i-know-this-is-the-shared-registry"


def is_shared_registry_url(url: str) -> bool:
    """Anything that is not a SQLite URL is a real database server."""
    return not url.lower().startswith("sqlite")


def plan(conn) -> dict:
    """{slug: [{kind, scope, kept_run, older_runs, rows_to_supersede}]} --
    counts and run ids only."""
    kinds_sql = ",".join(f":k{i}" for i in range(len(KINDS)))
    params = {f"k{i}": k for i, k in enumerate(KINDS)}
    params["iso"] = _ISO
    rows = conn.execute(text(
        "SELECT project_slug, kind, COALESCE(scope_locator, '') AS scope, "
        "surveyed_at, COUNT(*) AS n, "
        "SUM(CASE WHEN superseded_at IS NULL THEN 1 ELSE 0 END) AS live "
        "FROM project_analysis_findings "
        f"WHERE kind IN ({kinds_sql}) AND surveyed_at LIKE :iso "
        "GROUP BY project_slug, kind, COALESCE(scope_locator, ''), surveyed_at"
    ), params).fetchall()
    groups = defaultdict(list)
    for r in rows:
        groups[(r[0], r[1], r[2])].append((r[3], int(r[5] or 0)))
    out: dict = defaultdict(list)
    for (slug, kind, scope), runs in sorted(groups.items()):
        newest = max(ts for ts, _ in runs)
        older = [(ts, live) for ts, live in runs if ts < newest and live]
        if not older:
            continue
        out[slug].append({
            "kind": kind, "scope": scope, "kept_run": newest,
            "older_runs": len(older),
            "rows_to_supersede": sum(live for _, live in older),
        })
    return dict(out)


def apply_plan(engine, the_plan: dict) -> int:
    """One transaction per resource. Returns rows marked."""
    total = 0
    for slug, items in the_plan.items():
        with engine.begin() as conn:
            for it in items:
                res = conn.execute(text(
                    "UPDATE project_analysis_findings SET superseded_at = :newest "
                    "WHERE project_slug = :slug AND kind = :kind "
                    "AND COALESCE(scope_locator, '') = :scope "
                    "AND superseded_at IS NULL AND surveyed_at < :newest "
                    "AND surveyed_at LIKE :iso"
                ), {"newest": it["kept_run"], "slug": slug, "kind": it["kind"],
                    "scope": it["scope"], "iso": _ISO})
                total += res.rowcount or 0
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="write (default is a dry run that only prints counts)")
    ap.add_argument("--dry-run", action="store_true", help="the default; accepted for clarity")
    ap.add_argument(SHARED_FLAG, action="store_true", dest="shared_ok")
    args = ap.parse_args(argv)
    if args.apply and args.dry_run:
        ap.error("--apply and --dry-run are mutually exclusive")

    url = args.database_url or os.environ.get("REGISTRY_DATABASE_URL", "")
    if not url:
        print("refusing: no registry named. Pass --database-url or set "
              "REGISTRY_DATABASE_URL (no fall-back to the configured registry).",
              file=sys.stderr)
        return 2
    if is_shared_registry_url(url) and not args.shared_ok:
        print(f"refusing: the registry URL is not a SQLite file, so it is treated as the "
              f"shared registry. Re-run with {SHARED_FLAG} only after a peer check.",
              file=sys.stderr)
        return 2
    if url.lower().startswith("sqlite:///") and url[len("sqlite:///"):] not in (":memory:", ""):
        path = url[len("sqlite:///"):]
        if not os.path.exists(path):
            print("refusing: the SQLite file does not exist (this script never creates one).",
                  file=sys.stderr)
            return 2

    engine = create_engine(url)
    if True:
        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(project_analysis_findings)"))} \
                if url.lower().startswith("sqlite") else {
                r[0] for r in conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'project_analysis_findings'"))}
            if "superseded_at" not in cols:
                print("stopping: project_analysis_findings has no superseded_at column.",
                      file=sys.stderr)
                return 2
            the_plan = plan(conn)

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] kinds: {', '.join(KINDS)}")
    grand = 0
    for slug, items in the_plan.items():
        for it in items:
            grand += it["rows_to_supersede"]
            scope = f" scope#{len(it['scope'])}chars" if it["scope"] else ""
            print(f"  {slug}  {it['kind']}{scope}: {it['rows_to_supersede']} row(s) from "
                  f"{it['older_runs']} older run(s); keeping run {it['kept_run']}")
    print(f"resources affected: {len(the_plan)}; rows that "
          f"{'were' if args.apply else 'would be'} marked superseded: {grand}")
    if args.apply:
        done = apply_plan(engine, the_plan)
        print(f"applied: {done} row(s) marked superseded in {len(the_plan)} transaction(s)")
    else:
        print("dry run: nothing written. Pass --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
