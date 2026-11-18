"""Re-attach Resource Explorer's Egeria pointers by qualifiedName, after an Egeria metadata clear (or drift).

Why (brief RE-REATTACH-BY-QN, 2026-11-12): after an Egeria DB clear, RE's stored GUIDs point at nothing (140
`entity_egeria_project_context` rows answered 404 on 2026-11-12). Several tables keep the element's qualifiedName
beside the GUID, and Egeria re-seeds many of those names (the Coco projects, RE's own survey definitions). Looking
each name up again recovers the link without creating anything.

    resource-explorer registry reattach-egeria-by-name            # DRY RUN: writes only a plan file
    resource-explorer registry reattach-egeria-by-name --show reattach
    resource-explorer registry reattach-egeria-by-name --apply --plan-file F --plan-hash H \\
        --database D --schema S --reattached-by <who>/<UTC>

RULES
  * Registry writes only. Nothing is created, updated or deleted in Egeria. Egeria is READ, as RE's own daemon
    identity (the resync job), through the exact unique-name read `egeria_lookup.guid_by_qualified_name` (typed)
    and the element-by-GUID read. No fuzzy or prefix search.
  * A failed read is never a miss: an error, a timeout, a 401/403 or an answer of an unexpected shape is
    `unreadable`, and the row is left alone.
  * The guards are the clear script's, from `registry_guards` (shared, not copied): dry run by default, a 0600 plan
    file with a hash, the apply refused unless every guard holds, a 0600 snapshot before the first change, one
    transaction per table, failures named with no traceback.

RUN ORDER after a DB clear (the architect's ruling, 2026-11-12): stop RE -> `registry record-egeria-reset` -> the
clear script (dry run, apply, empty second dry run) -> THIS (dry run, apply, second dry run shows 0 to re-attach) ->
start RE -> Alignment. The clear script blanks EVERY pointer, so running it AFTER this undoes it. Both input states
are accepted: (a) a stored GUID that no longer resolves; (b) an empty GUID beside a stored qualifiedName (what the
clear script leaves).

VERDICTS, one per row; one Egeria read per distinct GUID and per distinct (qualifiedName, type), so copied rows
always get the same answer:
  ok          the stored GUID resolves and holds the stored qualifiedName            no write
  mismatch    the stored GUID resolves to a DIFFERENT qualifiedName                   no write, needs a person
  reattach    GUID empty or not found; the name resolves to exactly one element       the GUID is written
  missing     GUID empty or not found; Egeria answers "No elements found"             no write, needs republish
  ambiguous   two or more elements hold the name                                      no write, needs a person
  wrong type  an element of another type holds the name                              no write, needs a person
  unreadable  any read that did not give one of the answers above                    no write
  no stored name   a GUID with no qualifiedName beside it: out of this tool's reach   no write

STATUS: on a re-attached row, `entity_egeria_project_context.status` and `investigations.egeria_project_status`
move `unbound` -> `linked` and ONLY that; any other value is kept (and listed as "status <x> kept").
`catalogue_commit.derive_commit_state` reads proof rows, never these statuses, so no `egeria_reattach` proof row is
needed: a re-attached `linked` context reads as a bound project wherever `repo_publish.project_words` shows it.

This tool never clears anything: a dead GUID it cannot re-attach stays as it is, for the clear script or a
republish.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from resource_explorer.catalogue_commit import PROJECT_LINKED, PROJECT_UNBOUND
from resource_explorer.egeria_absence import ABSENT, PRESENT, is_absent
from resource_explorer.registry_guards import (
    ApplyFailed,
    Refused,
    _group,
    _missing_table,
    _nonempty,
    _rows,
    activity_guard,
    check_columns,
    check_live_schema,
    check_saved_plan,
    connect,
    database_name,
    parse_who_when,
    plan_hash,
    registry_url,
    target_schema,
    utcnow,
    write_0600_json,
)

#: Written into every plan file and its hash. Bump it when the plan's shape or meaning changes.
SCRIPT_VERSION = "2026-11-12.1"
RUN_PREFIX = "resource-explorer registry reattach-egeria-by-name"
OPERATION = "egeria_reattach_by_name"
FINALISE_GROUP = "activity_log:finalise"

OK, MISMATCH, REATTACH, MISSING = "ok", "mismatch", "reattach", "missing"
AMBIGUOUS, WRONG_TYPE, UNREADABLE, NO_NAME = "ambiguous", "wrong_type", "unreadable", "no_stored_name"
#: the verdicts, in the order the dry run prints them
VERDICTS = (REATTACH, OK, MISSING, AMBIGUOUS, WRONG_TYPE, MISMATCH, UNREADABLE, NO_NAME)
WORDS = {
    OK: "ok", MISMATCH: "mismatch · needs a person", REATTACH: "reattach", MISSING: "missing · needs republish",
    AMBIGUOUS: "ambiguous · needs a person", WRONG_TYPE: "wrong type · needs a person", UNREADABLE: "unreadable",
    NO_NAME: "no stored name",
}


@dataclass(frozen=True)
class Spec:
    table: str
    keys: tuple
    guid_col: str
    qn_col: str
    type_name: str
    status_col: str = ""


#: Where a qualifiedName is stored beside an Egeria GUID (registry.py, checked 2026-11-12).
SPECS = (
    Spec("entity_egeria_project_context", ("entity_type", "entity_slug", "user_id"), "egeria_project_guid",
         "egeria_project_qualified_name", "Project", "status"),
    Spec("investigations", ("slug",), "egeria_project_guid", "egeria_project_qualified_name", "Project",
         "egeria_project_status"),
    # Collection: a subtype (DigitalProduct, ...) also passes, the type check reads superTypeNames
    Spec("working_sets", ("slug",), "egeria_collection_guid", "egeria_collection_qualified_name", "Collection"),
    Spec("notification_subscriptions", ("id",), "egeria_notification_type_guid",
         "egeria_notification_type_qualified_name", "NotificationType"),
    # the clear script REMOVES these rows (a cache that refills); this covers state (a), a run without it
    Spec("survey_definition_cache", ("entity_type", "entity_slug", "technology_type"), "process_guid",
         "process_qualified_name", "GovernanceActionProcess"),
)

LEFT_NO_NAME = "no stored name · left to the clear script / republish"
LEFT_CACHE = "cache · left to the clear script"
#: Pointer columns with no stored qualifiedName (or a cache): counted in the dry run, never touched. Names are not
#: derived here: a derived name is a guess about the writer (brief §2).
OUT_OF_SCOPE = (
    ("projects", ("egeria_asset_guid",), LEFT_NO_NAME),
    ("databases", ("egeria_asset_guid",), LEFT_NO_NAME),
    ("file_systems", ("egeria_asset_guid",), LEFT_NO_NAME),
    ("sub_resources", ("egeria_guid",), LEFT_NO_NAME),
    ("work_lists", ("egeria_guid",), LEFT_NO_NAME),
    ("doc_sources", ("egeria_external_ref_guid", "egeria_link_relationship_guid"), LEFT_NO_NAME),
    ("rfa_actions", ("egeria_todo_guid", "egeria_notelog_guid"), LEFT_NO_NAME),
    ("database_surveys", ("egeria_report_guid",), LEFT_NO_NAME),
    ("filesystem_surveys", ("egeria_report_guid",), LEFT_NO_NAME),
    ("project_egeria_surveys", ("egeria_report_guid",), LEFT_NO_NAME),
    ("project_published_analyses", ("egeria_report_guid",), LEFT_NO_NAME),
    ("project_published_annotation_types", ("egeria_report_guid",), LEFT_NO_NAME),
    ("architecture_materialized_blueprints", ("guid",), LEFT_CACHE),
    ("architecture_materialized_components", ("guid",), LEFT_CACHE),
    ("architecture_materialized_ports", ("guid",), LEFT_CACHE),
)
#: tables created lazily by their own module: absent means "not created yet", reported, never refused
LAZY_TABLES = ("work_lists",)


# ── the Egeria reads ──────────────────────────────────────────────────────────────────────────────────────────

def _qualified_name_of(answer: dict) -> str:
    """The qualifiedName in a by-GUID answer (LIVE 6.2: elementProperties.propertiesAsStrings / propertyValueMap;
    `properties` is the asset-graph shape)."""
    props = answer.get("elementProperties") or {}
    qn = (props.get("propertiesAsStrings") or {}).get("qualifiedName")
    if not qn:
        pv = (props.get("propertyValueMap") or {}).get("qualifiedName")
        qn = pv.get("primitiveValue") if isinstance(pv, dict) else None
    if not qn:
        qn = (answer.get("properties") or {}).get("qualifiedName")
    return str(qn or "").strip()


class EgeriaReads:
    """The two reads, each made once per distinct argument in a run (copies get the same answer)."""

    def __init__(self, expert):
        self.expert = expert
        self._by_guid: dict = {}
        self._by_name: dict = {}

    @property
    def distinct_names(self) -> int:
        return len(self._by_name)

    def element(self, guid: str) -> tuple[str, str]:
        """(PRESENT, its qualifiedName) | (ABSENT, "") | (UNREADABLE, why)."""
        if guid not in self._by_guid:
            self._by_guid[guid] = self._element(guid)
        return self._by_guid[guid]

    def _element(self, guid: str) -> tuple[str, str]:
        try:
            answer = self.expert.get_metadata_element_by_guid(guid)
        except Exception as exc:  # noqa: BLE001 - classified, never swallowed as a miss
            state = is_absent(exc)
            return (ABSENT, "") if state == ABSENT else ("unreadable", f"by-GUID read raised {type(exc).__name__}")
        state = is_absent(answer)
        if state == ABSENT:
            return ABSENT, ""
        if state != PRESENT or not isinstance(answer, dict):
            return "unreadable", "by-GUID read gave an answer of an unexpected shape"
        qn = _qualified_name_of(answer)
        if not qn:
            return "unreadable", "by-GUID answer carries no qualifiedName"
        return PRESENT, qn

    def by_name(self, qn: str, type_name: str) -> tuple[str, str, str]:
        """(verdict, new GUID, detail) for the exact unique-name read of `qn`, typed."""
        key = (qn, type_name)
        if key not in self._by_name:
            self._by_name[key] = self._by_name_read(qn, type_name)
        return self._by_name[key]

    def _by_name_read(self, qn: str, type_name: str) -> tuple[str, str, str]:
        from resource_explorer.egeria_lookup import (
            QualifiedNameAmbiguous,
            QualifiedNameTypeMismatch,
            guid_by_qualified_name,
        )

        try:
            guid = guid_by_qualified_name(self.expert, qn, type_name=type_name)
        except QualifiedNameAmbiguous:
            return AMBIGUOUS, "", "more than one element holds the name"
        except QualifiedNameTypeMismatch:
            return WRONG_TYPE, "", f"the name is held by an element that is not a {type_name}"
        except Exception as exc:  # noqa: BLE001 - a failed read is never a miss
            return UNREADABLE, "", f"unique-name read raised {type(exc).__name__}"
        if guid is None:
            return MISSING, "", ""
        return REATTACH, guid, ""


def daemon_reads() -> EgeriaReads:
    """The reads as RE's own daemon identity (the resync job): a CLI run needs no person's session."""
    from pyegeria import MetadataExpert

    from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

    clients = egeria_client(Daemon(DaemonReason.RESYNC), purpose="egeria re-attach by name", shared=False)
    return EgeriaReads(clients.of(MetadataExpert))


# ── the plan ──────────────────────────────────────────────────────────────────────────────────────────────────

def verdict_for(row: dict, spec: Spec, reads: EgeriaReads) -> dict:
    qn = str(row.get(spec.qn_col) or "").strip()
    old = str(row.get(spec.guid_col) or "").strip()
    status = str(row.get(spec.status_col) or "") if spec.status_col else ""
    out = {"key": {k: row[k] for k in spec.keys}, "qn": qn, "old_guid": old, "verdict": "", "new_guid": "",
           "detail": ""}
    if spec.status_col:
        out["status"] = status
    if not qn:
        out["verdict"] = NO_NAME
        return out
    if old:
        state, info = reads.element(old)
        if state == PRESENT:
            out["verdict"] = OK if info == qn else MISMATCH
            if info != qn:
                out["detail"] = f"the GUID now holds {info!r}"
            return out
        if state != ABSENT:
            out["verdict"], out["detail"] = UNREADABLE, info
            return out
    verdict, new, detail = reads.by_name(qn, spec.type_name)
    out["verdict"], out["new_guid"], out["detail"] = verdict, new, detail
    if verdict == REATTACH and spec.status_col:
        out["status_after"] = PROJECT_LINKED if status == PROJECT_UNBOUND else status
        if status != PROJECT_UNBOUND:
            out["detail"] = f"status {status or '(empty)'} kept"
    return out


def check_schema(conn) -> list[str]:
    """Every column the tool reads or writes must exist; returns the lazy tables that are not created yet."""
    lazy_missing = [t for t in LAZY_TABLES if _missing_table(conn, t)]
    want: dict[str, set] = {}
    for s in SPECS:
        want.setdefault(s.table, set()).update(s.keys, (s.guid_col, s.qn_col), (s.status_col,) if s.status_col else ())
    for table, cols, _ in OUT_OF_SCOPE:
        if table not in lazy_missing:
            want.setdefault(table, set()).update(cols)
    want.setdefault("activity_log", set()).update(("id", "status", "detail"))
    want.setdefault("runs", set()).update(("id", "state"))
    want.setdefault("egeria_outbox", set()).update(("status",))
    check_columns(conn, want)
    return lazy_missing


def build_plan(conn, reads: EgeriaReads, db_name: str, schema: str = "main", live_schema: str = "main",
               lazy_missing: list | None = None) -> dict:
    tables = []
    for s in SPECS:
        cols = list(dict.fromkeys(list(s.keys) + [s.guid_col, s.qn_col] + ([s.status_col] if s.status_col else [])))
        rows = _rows(conn, f"SELECT {', '.join(cols)} FROM {s.table} "
                           f"WHERE {_nonempty(s.guid_col)} OR {_nonempty(s.qn_col)} ORDER BY {', '.join(s.keys)}")
        verdicts = [verdict_for(r, s, reads) for r in rows]
        counts = {v: 0 for v in VERDICTS}
        for v in verdicts:
            counts[v["verdict"]] += 1
        tables.append({"table": s.table, "keys": list(s.keys), "guid_col": s.guid_col, "qn_col": s.qn_col,
                       "type": s.type_name, "status_col": s.status_col, "rows": verdicts, "counts": counts,
                       "distinct_names": len({v["qn"] for v in verdicts if v["qn"]})})
    lazy_missing = sorted(lazy_missing or [])
    left = []
    for table, cols, words in OUT_OF_SCOPE:
        if table in lazy_missing:
            left.append({"table": table, "columns": list(cols), "count": None, "words": "table not created yet"})
            continue
        n = _rows(conn, f"SELECT COUNT(*) AS n FROM {table} WHERE " + " OR ".join(_nonempty(c) for c in cols))[0]["n"]
        left.append({"table": table, "columns": list(cols), "count": int(n), "words": words})
    plan = {"script_version": SCRIPT_VERSION, "database": db_name, "schema": schema, "current_schema": live_schema,
            "tables": tables, "left_alone": left, "distinct_names_looked_up": reads.distinct_names}
    plan["hash"] = plan_hash(plan)
    return plan


def to_reattach(plan: dict) -> int:
    return sum(t["counts"][REATTACH] for t in plan["tables"])


def counts_summary(plan: dict) -> dict:
    return {t["table"]: {v: n for v, n in t["counts"].items() if n} for t in plan["tables"]}


def cannot_reattach_by_name(plan: dict) -> tuple[int, int]:
    """(rows in SPECS tables with a GUID and no stored qualifiedName, non-empty GUIDs in OUT_OF_SCOPE columns)."""
    in_scope = sum(t["counts"][NO_NAME] for t in plan["tables"])
    elsewhere = sum(x["count"] or 0 for x in plan["left_alone"])
    return in_scope, elsewhere


def render(plan: dict, title: str, show: tuple = ()) -> str:
    out = [f"{title} · database: {plan['database']} · schema: {plan['schema']} "
           f"(connection current_schema: {plan['current_schema']})", ""]
    head = f"{'table':<32}{'rows':>6}{'names':>7}  " + "  ".join(f"{WORDS[v]}" for v in VERDICTS)
    out.append(head)
    for t in plan["tables"]:
        cells = "  ".join(f"{t['counts'][v]:>{len(WORDS[v])}}" for v in VERDICTS)
        out.append(f"{t['table']:<32}{len(t['rows']):>6}{t['distinct_names']:>7}  {cells}")
        kept = [r for r in t["rows"] if r["verdict"] == REATTACH and t["status_col"]
                and r.get("status") != PROJECT_UNBOUND]
        if kept:
            by: dict[str, int] = {}
            for r in kept:
                by[r.get("status") or "(empty)"] = by.get(r.get("status") or "(empty)", 0) + 1
            out.append("    " + ", ".join(f"status {s} kept: {n}" for s, n in sorted(by.items())))
    out.append("")
    out.append("left alone (this tool never touches them):")
    for x in plan["left_alone"]:
        n = "-" if x["count"] is None else x["count"]
        out.append(f"  {x['table'] + '.' + '/'.join(x['columns']):<62}{n:>6}  {x['words']}")
    out.append("")
    out.append(f"distinct names looked up: {plan['distinct_names_looked_up']}")
    in_scope, elsewhere = cannot_reattach_by_name(plan)
    # Its own line, never folded into missing/unreadable: a row with no stored name was not looked up at all.
    out.append(f"cannot re-attach by name (a GUID with no stored qualifiedName): {in_scope} in the tables above, "
               f"{elsewhere} in columns that keep no name (left alone)")
    for v in show:
        out.append("")
        out.append(f"rows with verdict {WORDS[v]}:")
        out.append(f"  {'table':<30} {'key':<44} {'qualifiedName':<60} {'old GUID':<37} new GUID  detail")
        for t in plan["tables"]:
            for r in t["rows"]:
                if r["verdict"] != v:
                    continue
                key = "/".join(str(x) for x in r["key"].values())
                out.append(f"  {t['table']:<30} {key:<44} {r['qn']:<60} {r['old_guid'] or '-':<37} "
                           f"{r['new_guid'] or '-'}  {r['detail']}")
    if any(t["counts"][MISSING] for t in plan["tables"]):
        out.append("")
        out.append("missing · needs republish: Egeria holds no element with that name. After a DB clear that is the "
                   "honest answer for every name Egeria did not re-seed, not a fault.")
    out.append(f"plan hash: {plan['hash']}")
    return "\n".join(out)


# ── apply ─────────────────────────────────────────────────────────────────────────────────────────────────────

def changes(plan: dict) -> list[tuple[dict, dict]]:
    return [(t, r) for t in plan["tables"] for r in t["rows"] if r["verdict"] == REATTACH]


def apply_plan(conn, plan: dict, by: str, snapshot_path: Path, now: datetime) -> dict:
    """Snapshot, then one transaction per table. Each UPDATE is conditioned on the row being exactly as planned
    (same qualifiedName, same old GUID, same status), so a row changed since the plan is not overwritten."""
    write_0600_json(snapshot_path, {"snapshot_of": "rows changed by egeria re-attach by name",
                                    "rows_before": [{"table": t["table"], **r} for t, r in changes(plan)],
                                    "plan": plan})
    from resource_explorer.run_reconciler import process_identity

    act_id = str(uuid.uuid4())
    ts = now.isoformat()
    detail = {"reattached_by": by, "plan_hash": plan["hash"], "snapshot": snapshot_path.name,
              "counts": counts_summary(plan), "_runner": process_identity()}
    _group(conn, lambda: conn.execute(
        "INSERT INTO activity_log (id, ts, operation, intent, entity_type, entity_slug, entity_name, "
        "entity_location, status, summary, detail, items_json, annotations_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (act_id, ts, OPERATION, "maintenance", "registry", plan["database"], "", "", "running",
         f"Egeria re-attach by name started by {by}", json.dumps(detail), "[]", "[]")))
    done: dict[str, int] = {}
    group = ""
    try:
        for t in plan["tables"]:
            rows = [r for r in t["rows"] if r["verdict"] == REATTACH]
            if not rows:
                continue
            group = f"reattach:{t['table']}"
            n = [0]

            def run(t=t, rows=rows, n=n):
                for r in rows:
                    sets, params = [f"{t['guid_col']} = ?"], [r["new_guid"]]
                    if t["status_col"] and r.get("status_after") != r.get("status"):
                        sets.append(f"{t['status_col']} = ?")
                        params.append(r["status_after"])
                    where = [f"{k} = ?" for k in t["keys"]] + [f"coalesce({t['qn_col']}, '') = ?",
                                                                 f"coalesce({t['guid_col']}, '') = ?"]
                    params += [r["key"][k] for k in t["keys"]] + [r["qn"], r["old_guid"]]
                    if t["status_col"]:
                        where.append(f"coalesce({t['status_col']}, '') = ?")
                        params.append(r.get("status") or "")
                    cur = conn.execute(f"UPDATE {t['table']} SET {', '.join(sets)} WHERE {' AND '.join(where)}",
                                       params)
                    n[0] += cur.rowcount or 0

            _group(conn, run)
            done[t["table"]] = n[0]
    except Exception as exc:
        failed = f"Egeria re-attach by name FAILED in group {group} after {len(done)} committed: {type(exc).__name__}"
        _group(conn, lambda: conn.execute(
            "UPDATE activity_log SET status = 'error', summary = ? WHERE id = ?", (failed, act_id)))
        raise ApplyFailed(group, list(done), exc) from None
    try:
        _group(conn, lambda: conn.execute(
            "UPDATE activity_log SET status = 'ok', summary = ? WHERE id = ?",
            (f"Egeria re-attach by name by {by}: "
             + (", ".join(f"{k} {v}" for k, v in done.items() if v) or "nothing re-attached"), act_id)))
    except Exception as exc:
        raise ApplyFailed(FINALISE_GROUP, list(done), exc) from None
    return done


# ── the Admin → Egeria Alignment step ─────────────────────────────────────────────────────────────────────────

def snapshot_dir() -> Path:
    """Where the Alignment apply writes its snapshot: never inside the serving checkout."""
    return Path(os.environ.get("RE_REATTACH_SNAPSHOT_DIR", "~/.resource-explorer/egeria-reattach")).expanduser()


def alignment_plan(registry, reads: EgeriaReads) -> dict:
    """The Alignment scan's plan: the same `build_plan` the CLI applies, over RE's own registry connection."""
    with registry._conn() as conn:
        lazy = check_schema(conn)
        return build_plan(conn, reads, "registry", "", "", lazy)


def alignment_apply(registry, reads: EgeriaReads, scanned_hash: str, by: str, now: datetime | None = None) -> dict:
    """Recompute the plan, refuse if it is not the one the scan showed, refuse while another RE writer is active,
    then apply exactly as the CLI does. Returns the step's result for the page."""
    now = now or utcnow()
    if not scanned_hash:
        return {"reattached": 0, "refused": "no plan hash from the scan; scan again"}
    with registry._conn() as conn:
        lazy = check_schema(conn)
        plan = build_plan(conn, reads, "registry", "", "", lazy)
        if plan["hash"] != scanned_hash:
            return {"reattached": 0, "refused": "something changed since the scan; scan again"}
        reasons = activity_guard(conn)
        if reasons:
            return {"reattached": 0, "refused": "another Resource Explorer writer is active: " + "; ".join(reasons)}
        snap = snapshot_dir() / f"egeria-reattach-snapshot-{now:%Y%m%dT%H%M%S}-{plan['hash'][:12]}.json"
        try:
            done = apply_plan(conn, plan, by, snap, now)
        except ApplyFailed as exc:
            return {"reattached": 0, "failed": 1,
                    "errors": [{"slug": exc.group, "error": f"{exc.exc_type}; committed: {exc.committed or 'none'}"}]}
    return {"reattached": sum(done.values()), "by_table": done, "snapshot": str(snap), "plan_hash": plan["hash"]}


# ── the CLI ───────────────────────────────────────────────────────────────────────────────────────────────────

def run(argv: list[str], out=print, now: datetime | None = None, reads_factory=None) -> int:
    ap = argparse.ArgumentParser(prog=RUN_PREFIX, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="egeria-reattach-plans", help="where the plan and snapshot files go")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan-file", default="")
    ap.add_argument("--plan-hash", default="")
    ap.add_argument("--database", default="")
    ap.add_argument("--schema", default="")
    ap.add_argument("--reattached-by", default="")
    ap.add_argument("--max-plan-age-minutes", type=int, default=15)
    ap.add_argument("--show", action="append", default=[], choices=list(VERDICTS), metavar="VERDICT",
                    help=f"list the rows with this verdict ({', '.join(VERDICTS)}); repeatable")
    args = ap.parse_args(argv)
    now = now or utcnow()
    try:
        url = registry_url()
        name = database_name(url)
        schema = target_schema(url)
        if args.apply:
            parse_who_when(args.reattached_by, now, "--reattached-by")
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
            lazy = check_schema(conn)
            try:
                reads = (reads_factory or daemon_reads)()
            except Exception as exc:  # noqa: BLE001 - named, no traceback
                raise Refused(f"could not open an Egeria client as RE's daemon ({type(exc).__name__}); nothing "
                              "was read or written") from None
            plan = build_plan(conn, reads, name, schema, live, lazy)
            if not args.apply:
                path = Path(args.out_dir) / f"egeria-reattach-plan-{name}-{plan['hash'][:12]}.json"
                write_0600_json(path, {"generated_at": now.isoformat(), "plan": plan})
                out(render(plan, "DRY RUN (nothing written to the registry, nothing written to Egeria)",
                           tuple(args.show)))
                out(f"plan file: {path}")
                out(f"to re-attach: {to_reattach(plan)}")
                if to_reattach(plan):
                    out(f"to apply: {RUN_PREFIX} --apply --plan-file {path} --plan-hash {plan['hash']} "
                        f"--database {name} --schema {schema} --reattached-by <who>/<UTC>")
                return 0
            saved = json.loads(Path(args.plan_file).read_text())
            check_saved_plan(saved, script_version=SCRIPT_VERSION, schema=schema, live=live, name=name,
                             plan_hash_arg=args.plan_hash, now=now, max_age_minutes=args.max_plan_age_minutes,
                             what="re-attach tool")
            if plan["hash"] != args.plan_hash:
                raise Refused("the registry or Egeria's answers changed since the dry run; run the dry run again "
                              f"(the plan recomputed now has hash {plan['hash'][:12]}, the dry run's was "
                              f"{args.plan_hash[:12]})")
            reasons = activity_guard(conn)
            if reasons:
                raise Refused("a Resource Explorer process looks active: " + "; ".join(reasons))
            snap = Path(args.out_dir) / f"egeria-reattach-snapshot-{name}-{plan['hash'][:12]}.json"
            done = apply_plan(conn, plan, args.reattached_by, snap, now)
            out(f"APPLIED to database {name} (re-attached by {args.reattached_by}); nothing was written to Egeria")
            for k, v in done.items():
                out(f"  {k:<46}{v:>6}")
            out(f"snapshot: {snap}")
            out("next: a second dry run must show 'to re-attach: 0'")
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
            "Run a fresh dry run, then apply again to finish.")
        return 3
    except Exception as exc:  # noqa: BLE001
        out(f"FAILED ({type(exc).__name__}) before any change was made or while reading the registry; "
            "no traceback is printed. Run the dry run again to see the state.")
        return 3
