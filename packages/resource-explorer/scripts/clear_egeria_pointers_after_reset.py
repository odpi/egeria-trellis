#!/usr/bin/env python
"""Clear Resource Explorer's Egeria POINTERS after the owner resets Egeria's metadata database, and keep its
DECISIONS and HISTORY.

Architect's rule (2026-10-08): clear pointers, keep decisions and history. One guarded script, dry-run first,
DDL-free. NOT the per-repository "Forget Egeria links" button (29 presses, and it also drops survey history).

DEFAULT IS A DRY RUN: it prints, per table, what it would clear or mark, writes NOTHING to the registry, writes
a plan file (JSON, no credentials) and prints the plan's hash.

    cd packages/resource-explorer
    REGISTRY_DATABASE_URL=... uv run python scripts/clear_egeria_pointers_after_reset.py \\
        --reset-at 2026-10-08T18:30Z [--old-collection-id X --new-collection-id Y]

Run it THROUGH `uv run`, from packages/resource-explorer. A bare `python scripts/...` fails with
ModuleNotFoundError on the resource_explorer import (catalogue_commit): run it through uv run so the
resource_explorer package is importable.

The owner's run sequence: (1) the dry run; (2) read the header's schema and current_schema line; (3) apply, with
the exact "to apply:" line it printed; (4) read the snapshot's recorded current_schema; (5) a second dry run,
which must be empty. The markers' collection ids read "unknown → unknown" until the metadataCollectionId slice
lands: that is expected and honest, not a fault.

APPLY needs ALL of these, and refuses (exit 2, saying which) when any is missing or false:

    --apply  --plan-file <the dry run's file>  --plan-hash <hash it printed>
    --database <name>              the registry database name, passed again; must equal the one in
                                   REGISTRY_DATABASE_URL
    --schema <name>                the schema from that URL's ?options=-csearch_path=..., passed again (a
                                   Postgres URL with none is refused, dry run and apply; on SQLite the schema is
                                   'main' and the flag is optional, or must equal 'main')
    --cleared-by <who>/<UTC>       e.g. dwolfson/2026-10-08T19:05Z; recorded in an activity_log row
    and: the plan file is recent (--max-plan-age-minutes, default 15) and was made for this database; the plan
    recomputed from the live data now has the SAME hash (anything that changed since the dry run refuses); no
    RE process looks active (activity_log rows still 'running' whose owner is alive or cannot be judged, runs
    claimed or running, outbox rows running).

What it does (all in RE's registry; no Egeria call; no DDL; no proof row is ever deleted or edited):

  CLEAR a pointer column (row kept):      projects/databases/file_systems.egeria_asset_guid, sub_resources.egeria_guid,
        investigations.egeria_project_guid (+ egeria_project_status becomes `unbound`, only for rows that were
        `linked`), entity_egeria_project_context.egeria_project_guid (+ status becomes the VALUE `unbound`;
        "unbound by reset · rebind to recreate" is display text only, and a publish is gated on it as on `unset`), working_sets.egeria_collection_guid, work_lists.egeria_guid + published_at,
        doc_sources (2 guids), rfa_actions (2 guids), and the report-guid column of the publish-state tables
        (database_surveys, filesystem_surveys, project_egeria_surveys, project_published_analyses,
        project_published_annotation_types). The rows, their times and who made them stay.
  REMOVE rows that only cache an Egeria element: architecture_materialized_blueprints / _components / _ports,
        survey_definition_cache, egeria_linkage_status, and the app_settings keys egeria_register_claim::*,
        egeria_server_claim::*, egeria_server_unconfirmed::*, egeria.github_source_control_library_guid.
  OUTBOX: rows that are not terminal (not done, dead, superseded or cancelled) become 'superseded' with the
        reason "Egeria reset <when>". 'done' rows are kept untouched; 'dead' rows are left 'dead' (listed as
        "dead before the reset · untouched").
  MARKERS: ONE catalogue_commit_proofs row per resource that has proofs or a "Published" badge row (a badge-only
        marker says "published claim before the reset (no proof rows)": a project_published_* row is a CLAIM that
        a publish happened, not proof the resource was in Egeria, so the badge may say "published earlier" for
        one that was already gone; it re-compares at read time, and the wording says what it rests on), proof kind 'egeria_reset', read_at = the
        reset time, text "Egeria reset <when> · old collection id → new". Status derives from it (see
        catalogue_commit.derive_commit_state).

KEPT ON PURPOSE: the qualified-name mirrors (investigations.egeria_project_qualified_name,
working_sets.egeria_collection_qualified_name, entity_egeria_project_context.egeria_project_qualified_name). A
rebind recreates the Egeria project or collection under the SAME name (architect's ruling), so the name is the
decision and only the GUID is the pointer. Status VALUES are not this script's business beyond what is written
here; the stacked branch that owns the status vocabulary decides them.

ORDER: the egeria_reset markers are written FIRST. They are INSERT-only and idempotent, so if a later group
fails the screens already say "Egeria was reset" instead of showing cleared pointers with no explanation; a
re-run (after a fresh dry run) skips the markers already there and finishes the rest. A failure names the failing
group and the groups that had committed, and prints no traceback.

--skip-missing-table work_lists: work_lists is created lazily by its own module, so on a fresh schema it may not
exist. The run refuses by default; the flag skips it (only if it is really missing), and the skip is recorded in
the plan (so the hash), the dry-run text and the activity row.

KEPT UNTOUCHED: every proof row, verdicts, both scope tables, enrichment and answers, journal,
reclassifications, groups, work list members, native_survey_annotations (GUIDs included: they are measurements
read at a time), step_runs (their survey_report_guid keys those annotations), survey history, findings,
metrics, activity history, feedback (its element_guid is an answer key, not an Egeria GUID).

A snapshot of every row it will change is written (mode 0600) BEFORE the first change. Each table group is one
transaction with rollback on any error. A second run finds nothing to clear and writes no second marker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from resource_explorer.catalogue_commit import PROJECT_UNBOUND as UNBOUND_STATUS, _known, _ts  # noqa: E402

#: Written into every plan file and into its hash. A plan made by another version of this script is refused with
#: its own sentence (not the registry-changed one). Bump it when the plan's shape or meaning changes.
SCRIPT_VERSION = "2026-10-09.1"
#: The exact prefix of the printed "to apply:" line: what actually works (see the docstring).
RUN_PREFIX = "uv run python scripts/clear_egeria_pointers_after_reset.py"

# UNBOUND_STATUS is the status VALUE `unbound` (architect's ruling 2026-10-08), written to
# entity_egeria_project_context.status and investigations.egeria_project_status. The words
# "unbound by reset · rebind to recreate" are display text only (catalogue_commit.PROJECT_UNBOUND_WORDS);
# the publish gate treats `unbound` exactly as `unset`.

OUTBOX_TERMINAL = ("done", "dead", "superseded", "cancelled")
SETTING_PREFIXES = ("egeria_register_claim::", "egeria_server_claim::", "egeria_server_unconfirmed::",
                    "egeria_server_guid::", "egeria_server_cred_slug::", "egeria_asset_guid::")
#: Tables created lazily by their own module; the only ones --skip-missing-table accepts.
LAZY_TABLES = ("work_lists",)
SETTING_KEYS = ("egeria.github_source_control_library_guid",)
PROOF_KIND = "egeria_reset"

#: (table, key columns, {column: new value}, trigger columns (the row is touched when one is non-empty)
#: [, extra SQL condition that must also hold, [, id suffix]])
CLEAR_SPECS = [
    ("projects", ["slug"], {"egeria_asset_guid": None}, None),
    ("databases", ["slug"], {"egeria_asset_guid": ""}, None),
    ("file_systems", ["slug"], {"egeria_asset_guid": ""}, None),
    ("sub_resources", ["id"], {"egeria_guid": ""}, None),
    # Only a row that was `linked` becomes `unbound`: a status word `linked` with an empty GUID would be a word
    # without a proof. A row with a GUID but some other status keeps its status and loses only the GUID.
    ("investigations", ["slug"], {"egeria_project_guid": "", "egeria_project_status": UNBOUND_STATUS}, ["egeria_project_guid"],
     "egeria_project_status = 'linked'", "linked"),
    ("investigations", ["slug"], {"egeria_project_guid": ""}, None,
     "coalesce(egeria_project_status, '') <> 'linked'", "other"),
    ("entity_egeria_project_context", ["entity_type", "entity_slug", "user_id"],
     {"egeria_project_guid": "", "status": UNBOUND_STATUS}, ["egeria_project_guid"]),
    ("working_sets", ["slug"], {"egeria_collection_guid": ""}, None),
    ("work_lists", ["slug"], {"egeria_guid": "", "published_at": ""}, None),
    # a pointer: the Egeria NotificationType element (empty on every row today, so normally nothing to clear)
    ("notification_subscriptions", ["id"], {"egeria_notification_type_guid": ""}, None),
    ("doc_sources", ["id"], {"egeria_external_ref_guid": "", "egeria_link_relationship_guid": ""}, None),
    ("rfa_actions", ["id"], {"egeria_todo_guid": "", "egeria_notelog_guid": ""}, None),
    ("database_surveys", ["id"], {"egeria_report_guid": ""}, None),
    ("filesystem_surveys", ["id"], {"egeria_report_guid": ""}, None),
    ("project_egeria_surveys", ["id"], {"egeria_report_guid": ""}, None),
    ("project_published_analyses", ["id"], {"egeria_report_guid": ""}, None),
    ("project_published_annotation_types", ["id"], {"egeria_report_guid": ""}, None),
]
#: (table, key columns): rows that are only a cache of an Egeria element
DELETE_SPECS = [
    ("architecture_materialized_blueprints", ["id"]),
    ("architecture_materialized_components", ["id"]),
    ("architecture_materialized_ports", ["id"]),
    ("survey_definition_cache", ["entity_type", "entity_slug", "technology_type"]),
    ("egeria_linkage_status", ["entity_type", "entity_slug"]),
]
#: what the script reads, for the loud schema check (table -> columns)
EXTRA_READS = {
    "app_settings": ["key", "value"], "egeria_outbox": ["id", "status", "element_kind", "entity_slug"],
    "catalogue_commit_proofs": ["id", "database_slug", "proof", "read_at", "node_kind"],
    "egeria_outbox_snapshot": ["claimed_at", "next_attempt_at", "completed_at", "last_error"],
    "activity_log": ["id", "status", "detail"], "runs": ["id", "state"],
    "databases": ["slug"], "projects": ["slug"], "file_systems": ["slug"],
}

NODE_DATABASE, NODE_REPO, NODE_OTHER = "database", "repo_report", "resource"


FINALISE_GROUP = "activity_log:finalise"


class ApplyFailed(Exception):
    """A table group failed. Names it and the groups that had already committed."""

    def __init__(self, group: str, committed: list, exc: Exception):
        super().__init__(f"{type(exc).__name__} in group {group}")
        self.group, self.committed, self.exc_type = group, committed, type(exc).__name__


class Refused(Exception):
    """A guard stopped the run. Nothing was written (or, if a later group failed, the message says what was)."""


# ── connection (no ProjectRegistry: opening one runs the schema ensure, and this script is DDL-free) ─────────

def registry_url() -> str:
    url = os.environ.get("REGISTRY_DATABASE_URL", "")
    if not url:
        raise Refused("REGISTRY_DATABASE_URL is not set; the script names its target only through it")
    return url


def database_name(url: str) -> str:
    """The NAME only, never the credentials."""
    if url.startswith("sqlite"):
        path = url.split("://", 1)[-1].split("?", 1)[0].lstrip("/")
        return path.rsplit("/", 1)[-1] or "?"
    return (urlsplit(url).path or "/").lstrip("/") or "?"


def target_schema(url: str) -> str:
    """The schema unqualified table names resolve in: the search_path named by the URL's options (the
    registry's own parser). SQLite is always 'main'. A Postgres URL with none is refused: the tables would
    resolve in `public`, which is not where RE keeps them."""
    if url.startswith("sqlite"):
        return "main"
    from urllib.parse import parse_qs, unquote
    tokens = " ".join(unquote(o) for o in parse_qs(urlsplit(url).query).get("options", [])).split()
    found = []
    for i, tok in enumerate(tokens):
        if tok.startswith("-csearch_path=") or tok.startswith("--search_path="):
            found.append(tok.split("=", 1)[1])
        elif tok == "-c" and i + 1 < len(tokens) and tokens[i + 1].startswith("search_path="):
            found.append(tokens[i + 1].split("=", 1)[1])
    if not found:
        raise Refused("REGISTRY_DATABASE_URL names no schema (no ?options=-csearch_path=<schema>); the script "
                      "will not run against whatever schema the server defaults to")
    if len(found) > 1:
        raise Refused("REGISTRY_DATABASE_URL names search_path more than once; libpq would use the last one and "
                      "this script will not guess which")
    schema = found[0].strip().strip('"')
    if "," in schema or not schema:
        raise Refused("the search_path in REGISTRY_DATABASE_URL must be exactly one schema (no comma list): "
                      "unqualified table names would resolve across the whole list")
    from resource_explorer.registry_label import _search_path
    if _search_path(urlsplit(url).query) != schema:
        raise Refused("the registry's own parser reads the search_path differently from this script; refusing")
    return schema


def check_live_schema(conn, url: str, schema: str) -> str:
    """The URL's search_path text is not evidence of where writes go; the open connection is. Postgres: ask it
    (`current_schema()`) and refuse unless it equals the schema the URL and --schema named. SQLite is 'main'."""
    if url.startswith("sqlite"):
        return "main"
    live = conn.execute("SELECT current_schema() AS s").fetchone()["s"]
    if live != schema:
        raise Refused(f"the open connection's current_schema() is {live!r}, not {schema!r} (the schema named by "
                      "the URL and --schema); unqualified table names would resolve there. Nothing was written.")
    return live


def connect(url: str):
    from sqlalchemy import create_engine
    from resource_explorer.registry import ConnectionWrapper
    import sqlite3

    is_pg = url.startswith("postgresql")
    raw = create_engine(url, pool_pre_ping=True).raw_connection()
    if not is_pg:
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys=ON")
    return ConnectionWrapper(raw, is_pg)


def _missing_table(conn, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} WHERE 1 = 0").fetchall()
        return False
    except Exception as exc:
        conn.raw_conn.rollback()
        if getattr(exc, "pgcode", None) == "42P01" or "no such table" in str(exc).lower():
            return True
        raise Refused(f"could not check whether table {table} exists ({type(exc).__name__}); only an undefined-table "
                      "error counts as missing. Nothing was written.") from exc


def check_schema(conn, skip: tuple = ()) -> list[str]:
    """Every table and column the script touches must exist. A missing one stops the run, naming it.
    A lazily-created table (LAZY_TABLES) that is really missing and named in `skip` is skipped and returned."""
    skipped = [t for t in skip if t in LAZY_TABLES and _missing_table(conn, t)]
    for t in LAZY_TABLES:
        if t not in skipped and _missing_table(conn, t):
            raise Refused(f"table {t} does not exist yet (created by its own module): nothing to clear there; "
                          f"re-run after creating it, or pass --skip-missing-table {t}")
    want: dict[str, set] = {}
    for table, keys, setcols, trig, *_ in CLEAR_SPECS:
        if table in skipped:
            continue
        want.setdefault(table, set()).update(keys, setcols, trig or [])
    for table, keys in DELETE_SPECS:
        want.setdefault(table, set()).update(keys)
    for table, cols in EXTRA_READS.items():
        want.setdefault("egeria_outbox" if table == "egeria_outbox_snapshot" else table, set()).update(cols)
    for table in sorted(want):
        for col in sorted(want[table]):
            try:
                conn.execute(f"SELECT {col} FROM {table} WHERE 1 = 0").fetchall()
            except Exception as exc:
                conn.raw_conn.rollback()
                raise Refused(f"schema check failed: {table}.{col} is not readable "
                              f"({type(exc).__name__}); the script will not guess. Nothing was written.") from exc
    return skipped


# ── time ─────────────────────────────────────────────────────────────────────

_TS = re.compile(r"^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d)(:\d\d)?(\.\d+)?Z?$")


def parse_utc(text: str, what: str) -> str:
    m = _TS.match((text or "").strip())
    if not m:
        raise Refused(f"{what} must be a UTC time like 2026-10-08T18:30Z, got {text!r}")
    return f"{m.group(1)}T{m.group(2)}{m.group(3) or ':00'}"


def utcnow() -> datetime:
    return datetime.utcnow()


def human_when(reset_at: str) -> str:
    return reset_at[:16].replace("T", " ") + " UTC"


# ── the plan ─────────────────────────────────────────────────────────────────

def _nonempty(col: str) -> str:
    return f"({col} IS NOT NULL AND {col} <> '')"


def _rows(conn, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_plan(conn, db_name: str, reset_at: str, old_id: str, new_id: str, schema: str = "main",
               skipped: list | None = None, live_schema: str = "main") -> dict:
    actions = []
    skipped = sorted(skipped or [])
    for table, keys, setcols, trig, *more in CLEAR_SPECS:
        if table in skipped:
            continue
        extra = more[0] if more else ""
        suffix = f":{more[1]}" if len(more) > 1 else ""
        trig = trig or list(setcols)
        cols = list(dict.fromkeys(keys + list(setcols)))
        where = "(" + " OR ".join(_nonempty(c) for c in trig) + ")" + (f" AND {extra}" if extra else "")
        rows = _rows(conn, f"SELECT {', '.join(cols)} FROM {table} WHERE {where} ORDER BY {', '.join(keys)}")
        actions.append({"id": f"clear:{table}{suffix}", "kind": "clear_columns", "table": table, "keys": keys,
                        "set": setcols, "trigger": trig, "extra": extra, "rows": rows, "count": len(rows)})
    for table, keys in DELETE_SPECS:
        rows = _rows(conn, f"SELECT {', '.join(keys)} FROM {table} ORDER BY {', '.join(keys)}")
        # the whole row is the snapshot for a removed cache row (these tables hold no credentials)
        full = _rows(conn, f"SELECT * FROM {table} ORDER BY {', '.join(keys)}")
        actions.append({"id": f"delete:{table}", "kind": "delete_rows", "table": table, "keys": keys,
                        "rows": full, "count": len(rows)})
    keyrows = _rows(conn, "SELECT key, value FROM app_settings ORDER BY key")
    hit = [r for r in keyrows if r["key"].startswith(SETTING_PREFIXES) or r["key"] in SETTING_KEYS]
    actions.append({"id": "delete:app_settings", "kind": "delete_rows", "table": "app_settings", "keys": ["key"],
                    "rows": hit, "count": len(hit)})

    ob = _rows(conn, "SELECT id, entity_slug, element_kind, status, last_error, claimed_at, next_attempt_at, "
               "completed_at FROM egeria_outbox ORDER BY id")
    pending = [r for r in ob if r["status"] not in OUTBOX_TERMINAL]
    dead = [r for r in ob if r["status"] == "dead"]
    actions.append({"id": "supersede:egeria_outbox", "kind": "supersede_outbox", "table": "egeria_outbox",
                    "keys": ["id"], "rows": pending, "count": len(pending),
                    "reason": f"Egeria reset {human_when(reset_at)}"})

    proofs = _rows(conn, "SELECT id, database_slug, proof, read_at FROM catalogue_commit_proofs ORDER BY id")
    by: dict[str, list[dict]] = {}
    for p in proofs:
        by.setdefault(p["database_slug"], []).append(p)
    kinds = {}
    for table, node in (("databases", NODE_DATABASE), ("projects", NODE_REPO), ("file_systems", NODE_OTHER)):
        for r in _rows(conn, f"SELECT slug FROM {table}"):
            kinds.setdefault(r["slug"], node)
    # A resource whose only trace of a publish is a "Published" badge row (project_published_annotation_types /
    # project_published_analyses) has no proof rows, yet its badge would still say Published for elements the
    # reset removed. It gets a marker too, because the badge reads the marker.
    pub_earlier: dict[str, int] = {}
    for table in ("project_published_annotation_types", "project_published_analyses"):
        # compared as PARSED times in Python, never as SQL text ('Z', '+00:00', spaces and fractions misorder)
        for r in _rows(conn, f"SELECT project_slug, published_at FROM {table} "
                             "WHERE published_at IS NOT NULL AND published_at <> ''"):
            if _known(r["published_at"]) and _ts(r["published_at"]) < _ts(reset_at):
                pub_earlier[r["project_slug"]] = pub_earlier.get(r["project_slug"], 0) + 1
    markers, after = [], []
    for slug in sorted(set(by) | set(pub_earlier)):
        rows_for = by.get(slug, [])
        real = [p for p in rows_for if p["proof"] != PROOF_KIND]
        have = [p for p in rows_for if p["proof"] == PROOF_KIND and _known(p["read_at"])
                and _ts(p["read_at"]) == _ts(reset_at)]
        if any(_known(p["read_at"]) and _ts(p["read_at"]) > _ts(reset_at) for p in real):
            after.append(slug)
        n_earlier = sum(1 for p in real if _known(p["read_at"]) and _ts(p["read_at"]) < _ts(reset_at))
        if have or not (n_earlier or pub_earlier.get(slug)):
            continue
        markers.append({"slug": slug, "node_kind": kinds.get(slug, NODE_OTHER),
                        "earlier_proofs": n_earlier, "earlier_published_rows": pub_earlier.get(slug, 0),
                        "text": f"Egeria reset {human_when(reset_at)} · {old_id or 'unknown'} → {new_id or 'unknown'}"
                                + ("" if n_earlier else " · published claim before the reset (no proof rows)")})
    actions.append({"id": "marker:catalogue_commit_proofs", "kind": "write_markers",
                    "table": "catalogue_commit_proofs", "rows": markers, "count": len(markers)})
    plan = {"script_version": SCRIPT_VERSION, "database": db_name, "schema": schema, "current_schema": live_schema, "reset_at": reset_at, "old_collection_id": old_id or "unknown",
            "new_collection_id": new_id or "unknown", "actions": actions,
            "dead_outbox_untouched": [r["id"] for r in dead], "skipped_tables": skipped,
            "slugs_with_proofs_after_reset": after}
    # the markers go FIRST (see ORDER in the module docstring)
    plan["actions"].sort(key=lambda a: a["kind"] != "write_markers")
    plan["hash"] = plan_hash(plan)
    return plan


def plan_hash(plan: dict) -> str:
    body = {k: v for k, v in plan.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def render(plan: dict, title: str) -> str:
    out = [f"{title} · database: {plan['database']} · schema: {plan['schema']} (connection current_schema: {plan['current_schema']}) · reset at {human_when(plan['reset_at'])}", ""]
    out.append(f"{'action':<10}{'table':<44}{'rows':>6}  detail")
    for a in plan["actions"]:
        verb = {"clear_columns": "clear", "delete_rows": "remove", "supersede_outbox": "supersede",
                "write_markers": "mark"}[a["kind"]]
        detail = ""
        if a["kind"] == "clear_columns":
            detail = ", ".join(a["set"])
        elif a["kind"] == "supersede_outbox":
            counts: dict[str, int] = {}
            for r in a["rows"]:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
            detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) + f" -> superseded · {a['reason']}"
        elif a["kind"] == "write_markers":
            detail = "proof kind egeria_reset, one per resource: " + ", ".join(
                f"{m['slug']} ({m['earlier_proofs']} earlier)" for m in a["rows"][:40])
        elif a["table"] == "app_settings":
            detail = ", ".join(r["key"] for r in a["rows"][:20])
        out.append(f"{verb:<10}{a['table']:<44}{a['count']:>6}  {detail}")
    out.append("")
    if plan.get("skipped_tables"):
        out.append("SKIPPED (table does not exist yet): " + ", ".join(plan["skipped_tables"]))
    out.append(f"dead before the reset · untouched: {len(plan['dead_outbox_untouched'])} outbox rows "
               f"{plan['dead_outbox_untouched'][:20]}")
    if plan["slugs_with_proofs_after_reset"]:
        out.append("WARNING proofs newer than the reset time exist for: "
                   + ", ".join(plan["slugs_with_proofs_after_reset"]) + " (published after the reset? check --reset-at)")
    out.append("kept untouched: every proof row, verdicts, scope events, enrichment, journal, groups, "
               "native_survey_annotations, step_runs, survey history, 'done' outbox rows")
    out.append(f"plan hash: {plan['hash']}")
    return "\n".join(out)


# ── guards ───────────────────────────────────────────────────────────────────

def parse_cleared_by(token: str, now: datetime) -> tuple[str, str]:
    who, sep, when = (token or "").partition("/")
    if not sep or not who.strip() or not when:
        raise Refused("--cleared-by must be <who>/<UTC time>, e.g. dwolfson/2026-10-08T19:05Z")
    stamp = parse_utc(when, "--cleared-by time")
    at = datetime.fromisoformat(stamp)
    if at > now + timedelta(minutes=5) or at < now - timedelta(hours=24):
        raise Refused(f"--cleared-by time {stamp} is not within the last 24 hours (now {now:%Y-%m-%dT%H:%M:%S})")
    return who.strip(), stamp


def activity_guard(conn) -> list[str]:
    """Reasons the registry looks in use. Empty means nothing looks active."""
    from resource_explorer.run_reconciler import _is_alive, owner_of

    reasons = []
    for r in _rows(conn, "SELECT id, detail FROM activity_log WHERE status = 'running'"):
        owner = owner_of(r.get("detail"))
        alive = _is_alive(owner) if owner else None
        if alive is not False:
            reasons.append(f"activity_log row {r['id']} is running ("
                           + ("its owner process is alive" if alive else "its owner cannot be judged") + ")")
    for r in _rows(conn, "SELECT id, state FROM runs WHERE state IN ('claimed', 'running')"):
        reasons.append(f"runs row {r['id']} is {r['state']}")
    n = _rows(conn, "SELECT COUNT(*) AS n FROM egeria_outbox WHERE status = 'running'")[0]["n"]
    if n:
        reasons.append(f"{n} egeria_outbox row(s) are running")
    return reasons


# ── apply ────────────────────────────────────────────────────────────────────

def _group(conn, fn):
    try:
        fn()
        conn.commit()
    except Exception:
        conn.raw_conn.rollback()
        raise


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def apply_plan(conn, plan: dict, cleared_by: str, snapshot_path: Path, now: datetime) -> dict:
    """Write the snapshot, then change the registry one table group at a time. Returns what was done."""
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = snapshot_path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)            # a pre-existing .tmp keeps its old mode otherwise
    with os.fdopen(fd, "w") as f:
        json.dump({"snapshot_of": "rows changed by clear_egeria_pointers_after_reset", "plan": plan}, f,
                  indent=1, default=str, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, snapshot_path)
    _fsync_dir(snapshot_path.parent)

    from resource_explorer.run_reconciler import process_identity
    act_id = str(uuid.uuid4())
    ts = now.isoformat()
    detail = {"cleared_by": cleared_by, "plan_hash": plan["hash"], "snapshot": snapshot_path.name,
              "skipped_tables": plan.get("skipped_tables", []), "_runner": process_identity()}
    _group(conn, lambda: conn.execute(
        "INSERT INTO activity_log (id, ts, operation, intent, entity_type, entity_slug, entity_name, "
        "entity_location, status, summary, detail, items_json, annotations_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act_id, ts, "egeria_reset_cleanup", "maintenance", "registry", plan["database"], "", "", "running",
         f"Egeria reset cleanup started by {cleared_by}", json.dumps(detail), "[]", "[]")))
    done: dict[str, int] = {}
    group = ""
    try:
        for a in plan["actions"]:
            group = a["id"]
            n = [0]

            def run(a=a, n=n):
                if a["kind"] == "clear_columns":
                    sets = ", ".join(f"{c} = ?" for c in a["set"])
                    trig = " OR ".join(_nonempty(c) for c in a["trigger"])
                    extra = f" AND {a['extra']}" if a.get("extra") else ""
                    for r in a["rows"]:
                        where = " AND ".join(f"{k} = ?" for k in a["keys"])
                        cur = conn.execute(f"UPDATE {a['table']} SET {sets} WHERE {where} AND ({trig}){extra}",
                                           [a["set"][c] for c in a["set"]] + [r[k] for k in a["keys"]])
                        n[0] += cur.rowcount or 0
                elif a["kind"] == "delete_rows":
                    for r in a["rows"]:
                        where = " AND ".join(f"{k} = ?" for k in a["keys"])
                        cur = conn.execute(f"DELETE FROM {a['table']} WHERE {where}", [r[k] for k in a["keys"]])
                        n[0] += cur.rowcount or 0
                elif a["kind"] == "supersede_outbox":
                    marks = ", ".join("?" for _ in OUTBOX_TERMINAL)
                    for r in a["rows"]:
                        cur = conn.execute(
                            "UPDATE egeria_outbox SET status = 'superseded', claimed_at = '', next_attempt_at = '', "
                            f"last_error = ?, completed_at = ? WHERE id = ? AND status NOT IN ({marks})",
                            [a["reason"], ts, r["id"], *OUTBOX_TERMINAL])
                        n[0] += cur.rowcount or 0
                else:
                    for m in a["rows"]:
                        conn.execute(
                            "INSERT INTO catalogue_commit_proofs (database_slug, curation_id, node_kind, schema_name, "
                            "table_name, proof, element_guid, target_guid, qualified_name, outbox_id, detail_json, "
                            "recorded_by, read_at) VALUES (?, '', ?, '', '', ?, '', '', '', NULL, ?, ?, ?)",
                            (m["slug"], m["node_kind"], PROOF_KIND,
                             json.dumps({"text": m["text"], "old_collection_id": plan["old_collection_id"],
                                         "new_collection_id": plan["new_collection_id"],
                                         "earlier_proofs": m["earlier_proofs"], "plan_hash": plan["hash"],
                                         "cleared_by": cleared_by}),
                             cleared_by.split("/")[0], plan["reset_at"]))
                        n[0] += 1

            _group(conn, run)
            done[a["id"]] = n[0]
    except Exception as exc:
        _group(conn, lambda: conn.execute(
            "UPDATE activity_log SET status = 'error', summary = ? WHERE id = ?",
            (f"Egeria reset cleanup FAILED in group {group} after {len(done)} committed: {type(exc).__name__}",
             act_id)))
        raise ApplyFailed(group, list(done), exc) from None
    try:
        _group(conn, lambda: conn.execute(
            "UPDATE activity_log SET status = 'ok', summary = ? WHERE id = ?",
            (f"Egeria reset cleanup by {cleared_by}: " + ", ".join(f"{k} {v}" for k, v in done.items() if v), act_id)))
    except Exception as exc:
        raise ApplyFailed(FINALISE_GROUP, list(done), exc) from None
    return done


# ── main ─────────────────────────────────────────────────────────────────────

def run(argv: list[str], out=print, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset-at", required=True, help="when the owner reset Egeria, UTC, e.g. 2026-10-08T18:30Z")
    ap.add_argument("--old-collection-id", default="")
    ap.add_argument("--new-collection-id", default="")
    ap.add_argument("--out-dir", default="egeria-reset-plans", help="where the plan and snapshot files go")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan-file", default="")
    ap.add_argument("--plan-hash", default="")
    ap.add_argument("--database", default="")
    ap.add_argument("--skip-missing-table", action="append", default=[], metavar="TABLE",
                    help="skip a lazily-created table that does not exist yet (only: work_lists); recorded in the plan")
    ap.add_argument("--schema", default="", help="the schema parsed from REGISTRY_DATABASE_URL's search_path, "
                    "passed again on apply (SQLite: schema is 'main'; the flag is optional there, or must be 'main')")
    ap.add_argument("--cleared-by", default="")
    ap.add_argument("--max-plan-age-minutes", type=int, default=15)
    args = ap.parse_args(argv)
    now = now or utcnow()
    try:
        url = registry_url()
        name = database_name(url)
        reset_at = parse_utc(args.reset_at, "--reset-at")
        schema = target_schema(url)
        unknown = [t for t in args.skip_missing_table if t not in LAZY_TABLES]
        if unknown:
            raise Refused(f"--skip-missing-table accepts only {', '.join(LAZY_TABLES)}, not {', '.join(unknown)}")
        if args.apply:
            who, _ = parse_cleared_by(args.cleared_by, now)
            if args.database != name:
                raise Refused(f"--database {args.database!r} is not the registry database in REGISTRY_DATABASE_URL "
                              f"({name!r}); pass the name again to confirm the target")
            if not (args.schema == schema or (url.startswith("sqlite") and not args.schema)):
                raise Refused(f"--schema {args.schema!r} is not the schema in REGISTRY_DATABASE_URL ({schema!r}); "
                              "pass the schema name again to confirm the target")
            if not args.plan_file or not args.plan_hash:
                raise Refused("--apply needs --plan-file and --plan-hash from a dry run of this database")
        conn = connect(url)
        try:
            live = check_live_schema(conn, url, schema)
            skipped = check_schema(conn, tuple(args.skip_missing_table))
            plan = build_plan(conn, name, reset_at, args.old_collection_id, args.new_collection_id, schema, skipped,
                              live)
            if not args.apply:
                out_dir = Path(args.out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / f"egeria-reset-plan-{name}-{plan['hash'][:12]}.json"
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as pf:
                    pf.write(json.dumps({"generated_at": now.isoformat(), "plan": plan}, indent=1,
                                        default=str, sort_keys=True))
                os.chmod(path, 0o600)
                _fsync_dir(out_dir)
                out(render(plan, "DRY RUN (nothing written to the registry)"))
                out(f"plan file: {path}")
                extra = "".join(f" --skip-missing-table {t}" for t in plan["skipped_tables"])
                extra += f" --old-collection-id {args.old_collection_id}" if args.old_collection_id else ""
                extra += f" --new-collection-id {args.new_collection_id}" if args.new_collection_id else ""
                out(f"to apply: {RUN_PREFIX} --apply --reset-at {args.reset_at} --plan-file {path} --plan-hash {plan['hash']} "
                    f"--database {name} --schema {schema}{extra} --cleared-by <who>/<UTC>")
                return 0
            saved = json.loads(Path(args.plan_file).read_text())
            sp = saved.get("plan") or {}
            if sp.get("script_version") != SCRIPT_VERSION:
                raise Refused(f"this plan was made by a different version of the script "
                              f"({sp.get('script_version') or 'none recorded'}, this is {SCRIPT_VERSION}); "
                              "run the dry run again")
            if sp.get("schema") != schema:
                raise Refused(f"the plan file was made for schema {sp.get('schema')!r}, not {schema!r}")
            if sp.get("current_schema") != live:
                raise Refused(f"the plan file was made under current_schema {sp.get('current_schema')!r}, the "
                              f"connection now reports {live!r}")
            if sp.get("database") != name:
                raise Refused(f"the plan file was made for database {sp.get('database')!r}, not {name!r}")
            if plan_hash(sp) != sp.get("hash") or sp.get("hash") != args.plan_hash:
                raise Refused("the plan file's hash does not match --plan-hash (or the file was edited)")
            made = datetime.fromisoformat(saved.get("generated_at", "1970-01-01T00:00:00"))
            if now - made > timedelta(minutes=args.max_plan_age_minutes) or made > now + timedelta(minutes=1):
                raise Refused(f"the dry run is older than {args.max_plan_age_minutes} minutes; run it again")
            if plan["hash"] != args.plan_hash:
                raise Refused("the registry changed since the dry run; run the dry run again "
                              f"(the plan recomputed now has hash {plan['hash'][:12]}, the dry run's was "
                              f"{args.plan_hash[:12]}; --reset-at and the collection ids must also be the same)")
            reasons = activity_guard(conn)
            if reasons:
                raise Refused("a Resource Explorer process looks active: " + "; ".join(reasons))
            snap = Path(args.out_dir) / f"egeria-reset-snapshot-{name}-{plan['hash'][:12]}.json"
            done = apply_plan(conn, plan, args.cleared_by, snap, now)
            out(f"APPLIED to database {name} (cleared by {args.cleared_by})")
            for k, v in done.items():
                out(f"  {k:<46}{v:>6}")
            out(f"snapshot: {snap}")
            return 0
        finally:
            conn.close()
    except Refused as exc:
        out(f"REFUSED: {exc}")
        return 2
    except ApplyFailed as exc:
        if exc.group == FINALISE_GROUP:
            out(f"All groups committed ({', '.join(exc.committed) or 'none'}); the activity row could not be "
                f"finalised ({exc.exc_type}). The registry changes are done; the activity row still says 'running'.")
            return 3
        out(f"FAILED in group {exc.group} ({exc.exc_type}); groups already committed: "
            f"{', '.join(exc.committed) or 'none'}. Earlier groups stay applied, the failing group was rolled back. "
            "Run a fresh dry run, then apply again to finish (markers already written are skipped).")
        return 3
    except Exception as exc:
        out(f"FAILED ({type(exc).__name__}) before any change was made or while reading the registry; "
            "no traceback is printed. Run the dry run again to see the state.")
        return 3


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
