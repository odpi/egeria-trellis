"""Catalogue scope for a database: what Curate decides gets catalogued.

Slice A of the catalogue-scope work (the designer's reply on catalogue scope,
page 18 of wireframes/CatalogueScope.dc.html). Everything up to
the commit button; NOTHING here writes to Egeria and nothing here compiles a
list for Egeria's cataloguer. That is slice B.

The one rule: the scope is a declared, signed, dated choice stored in RE. A
node (a schema or a table) is `catalogue`, `leave_out`, or absent, which is
undecided. Storage is two append-only registry tables
(`catalogue_scope_events`, `catalogue_scope_baselines`); the current choice of
a node is its newest event, and a cleared choice is itself an event so who
cleared it is never lost.

What may be PROPOSED (designer section 2, exactly): only a measured fact that
decides ALONE. Three rules, one function each, and nothing else may propose:

- `empty_schema`   a schema with 0 tables, measured (never "no access")  -> leave out
- `dormant`        0 writes in cumulative counters covering >= `DORMANCY_DAYS` -> leave out

(Slice A2, the designer's round-2 reply: the old two-survey `no_writes` rule and the
name-matching `data_lens_match` proposal are gone. A lens term found in table names is a
SUGGESTED RULE sentence, never a proposal; rules themselves are a later slice.)

PII data classes are a MARK, never a proposal. A staging-looking schema name
is a NOTE, never a proposal. No access is "not established", never a
proposal. A verdict never proposes (it gates; the commit's population rule is
slice B). `propose_for_node` is the only door a proposal goes through, and
`tests/test_catalogue_scope.py` fails on purpose if any of those four sneaks in.

Four observation states, no fifth: proposed, confirmed, overridden, and
"survey now disagrees". A person's own choice that no rule proposed is
`chosen`; no choice at all is `undecided`; a table following its schema is
`inherited`. Those three are not observation states, they are the choice
itself.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from resource_explorer.registry import STATS_NEVER_RESET

CATALOGUE = "catalogue"
LEAVE_OUT = "leave_out"
CHOICES = (CATALOGUE, LEAVE_OUT)

#: The four depths, in the order the depth line draws them.
#:
#: **Slice B (owner's decision, 2026-10-05, the schema-kind door): a depth is a
#: view of this tree, not an instruction to Egeria.** A schema-kind catalog target
#: makes Egeria's cataloguer create a schema's tables AND columns, and it has no
#: depth option yet (suggestion S2), so a shallower depth cannot be asked for. The
#: earlier wording ("an include-schema list, with an impossible table name...")
#: described a lever the commit no longer uses and would now be a false claim on
#: screen. `commit_honours` says which depth is what the commit does.
DEPTHS: tuple[dict, ...] = (
    {"id": "database_only", "label": "the database only", "commit_honours": False,
     "how": "tree view only: shows the database alone · the commit still publishes whole schemas"},
    {"id": "schemas", "label": "schemas", "commit_honours": False,
     "how": "tree view only: shows schemas · the commit still publishes their tables and columns"},
    {"id": "schemas_and_tables", "label": "schemas and tables", "commit_honours": False,
     "how": "tree view only: shows schemas and tables · the commit still publishes their columns"},
    {"id": "tables_and_columns", "label": "tables and columns", "commit_honours": True,
     "how": "everything: this is what the commit publishes for each chosen schema"},
)
DEPTH_IDS = tuple(d["id"] for d in DEPTHS)
DEFAULT_DEPTH = "schemas_and_tables"
DEPTH_HELP = ("Egeria adds whole schemas with their tables and columns, and a shallower depth can't be "
              "asked for until Egeria has a depth option (S2), so a depth here changes this tree only.")
#: Shown beside the depth line, and in the commit's manifest, when the chosen depth is not what the commit does.
DEPTH_NOT_HONOURED = ("The commit publishes tables and columns for every chosen schema whatever depth is "
                      "chosen: Egeria has no depth option yet (S2).")

SYSTEM_SENTENCE = "not published: system schemas are never offered"

#: Proposal rule ids. They are stored on the event as the confirmed `source`.
RULE_EMPTY_SCHEMA = "empty_schema"
RULE_DORMANT = "dormant"
PROPOSAL_RULES = (RULE_EMPTY_SCHEMA, RULE_DORMANT)

#: Dormancy threshold (designer round 2, 2026-10-05): zero writes counted in a window of
#: at least this many days of cumulative-counter evidence. Shown in the activity column
#: header. Per-scope settability is a later slice; the default is the designer's 90.
DORMANCY_DAYS = 90


class ScopeError(Exception):
    """A scope request that cannot be honoured. `status` is the HTTP code."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def md(iso: str | None) -> str:
    """`2026-10-02T09:00:00` -> `10-02`, the short date every row uses."""
    s = str(iso or "")
    return s[5:10] if len(s) >= 10 else ""


# ── provenance of a node, by depth ───────────────────────────────────────────

def depth_provenance(depth: str, kind: str) -> str:
    """The second line of a row: what this depth's VIEW leaves out. A view claim only:
    the commit still catalogues the level (see `DEPTHS`)."""
    if depth == "database_only":
        return ("schemas hidden in this view · the commit still publishes whole schemas"
                if kind == "schema" else
                "tables hidden in this view · the commit still publishes them")
    if depth == "schemas":
        return "tables hidden in this view · the commit still publishes them" if kind == "table" else ""
    if depth == "schemas_and_tables":
        return "columns hidden in this view · the commit still publishes them" if kind == "table" else ""
    return ""


# ── the three proposal rules ─────────────────────────────────────────────────

def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(name or "").lower()) if t}


def measure_fact(rule: str, facts: dict) -> dict | None:
    """The measurement a rule stands on, now. None when it cannot be taken.

    The same function backs the proposal AND the "survey now disagrees" check,
    so the two can never be reading different things.
    """
    if rule == RULE_EMPTY_SCHEMA:
        if facts["kind"] != "schema" or facts.get("classification") in ("no_access", "system"):
            return None
        if facts.get("table_count") is None:
            return None
        return {"table_count": int(facts["table_count"])}
    if rule == RULE_DORMANT:
        if facts.get("classification") in ("no_access", "system"):
            return None
        if facts["kind"] == "schema" and not facts.get("table_count"):
            return None
        dormant = facts.get("dormant")      # None = can't tell: nothing to stand on
        if dormant is None:
            return None
        return {"dormant": bool(dormant)}
    return None


def propose_for_node(facts: dict) -> dict | None:
    """The single door a proposal goes through.

    `facts` is what was MEASURED about one node; nothing else is consulted, so
    a data class, a staging-looking name, a missing credential and a verdict
    cannot propose because none of them is in `facts` as a rule input. When two
    rules decide in opposite directions neither decides alone, so nothing is
    proposed and the row says the measurements disagree.
    """
    found: list[dict] = []
    m = measure_fact(RULE_EMPTY_SCHEMA, facts)
    if m is not None and m["table_count"] == 0:
        found.append({
            "rule": RULE_EMPTY_SCHEMA, "choice": LEAVE_OUT, "measured": m,
            "reason": ("0 tables, measured " + md(facts.get("measured_at"))).strip(),
        })
    m = measure_fact(RULE_DORMANT, facts)
    if m is not None and m["dormant"] is True:
        found.append({
            "rule": RULE_DORMANT, "choice": LEAVE_OUT, "measured": m,
            "reason": f"dormant, 0 writes in {facts.get('window_days')} days",
        })
    if not found:
        return None
    if len({p["choice"] for p in found}) > 1:
        return {"conflicting": [p["rule"] for p in found]}
    p = found[0]
    p["measured_at"] = facts.get("measured_at") or ""
    return p


def _disagreement(rule: str, stored: dict, current: dict | None, survey_at: str) -> dict | None:
    """Both values and the survey date when the measurement under a confirmed
    or overridden choice changed. None when unchanged or not measurable now
    (an unmeasurable now is not a disagreement, it is an absence)."""
    if current is None or not rule or current == stored:
        return None
    if rule == RULE_EMPTY_SCHEMA:
        was, now = stored.get("table_count"), current.get("table_count")
        words = f"it had {was} tables; it now has {now}"
    else:
        words = ("it was dormant; writes now show" if stored.get("dormant")
                 else "it was not dormant; it now is")
    return {"was": stored, "now": current, "survey_at": survey_at, "words": words}


# ── reading the world ────────────────────────────────────────────────────────

def _load_tree(registry, slug: str) -> dict:
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        schema_inventory_tree,
    )
    return schema_inventory_tree(registry, slug) or {"schemas": []}


def _tree_survey_at(registry, slug: str) -> str:
    """When the rows the tree is built from were measured (newest run)."""
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT MAX(surveyed_at) AS latest FROM database_tables WHERE database_slug = ?",
                (registry._normalize_slug(slug),),
            ).fetchone()
        return (dict(row).get("latest") if row else "") or ""
    except Exception:  # an unreadable table is an absence, not a date
        return ""


def _activity_counters(registry, slug: str) -> dict:
    """Cumulative write counters per table from the newest stored local snapshot:
    `{(schema, table): {"writes", "reset", "at", "source"}}`. `writes` is None when
    no counter was measured. Stored rows only. The evidence is the cumulative
    counters since the last statistics reset, NEVER the difference of two surveys."""
    out: dict = {}
    try:
        rows = registry.query_detail_rows("database_table_activity", slug) or []
    except Exception:
        return out
    for r in rows:
        vals = [r.get(k) for k in ("rows_inserted", "rows_updated", "rows_deleted")]
        known = [int(v) for v in vals if v is not None]
        out[(r.get("schema_name"), r.get("table_name"))] = {
            "writes": sum(known) if known else None, "reset": str(r.get("stats_reset") or ""),
            "at": str(r.get("surveyed_at") or ""), "source": SOURCE_LOCAL}
    return out


def _row_sources(registry, slug: str) -> dict:
    """Stored row counts per table from each source's own newest snapshot:
    `{(schema, table): [{"source", "rows", "state", "at"}]}`. Two sources that
    disagree are shown as disagreeing, never silently reconciled."""
    out: dict = {}
    for src in (SOURCE_LOCAL, SOURCE_EGERIA):
        try:
            rows = registry.query_detail_rows("database_tables", slug, None, src) or []
        except Exception:
            continue
        for r in rows:
            if r.get("row_count") is None:
                continue
            out.setdefault((r.get("schema_name"), r.get("table_name")), []).append(
                {"source": src, "rows": int(r["row_count"]), "state": r.get("state") or "",
                 "at": str(r.get("surveyed_at") or "")})
    return out


def data_lens_for(registry, slug: str) -> dict | None:
    """The data lens of an investigation this database is in, if one exists.

    None on this build, and it says so rather than guessing: no stored,
    investigation-level DataLens exists (`step_preconditions._needs_human_input`
    and `batch_io.NO_LENS` say the same). The lens-match proposal rule is
    fully implemented and tested through the `lens` argument; it fires in
    the product the day a lens is stored and this function returns it.
    """
    return None


def data_class_facts(registry, slug: str) -> dict:
    """Data classes found per table: `{(schema, table): {"classes": [...], "pii_columns": n}}`.

    Empty on this build, honestly: `data_class_match` results have no local
    store (Backlog, "Database per-column match results have no local store"),
    so the tree says "not established" rather than "none found". The PII mark
    is wired and tested through the `data_classes` argument.
    """
    return {}


# ── the measured node set: which survey the tree is read from ────────────────
#
# Slice A2. The tree used to be RE's own local survey only, which is credential
# scoped and can be older than Egeria's native survey. It is now the FULLEST and
# NEWEST measured set stored, native survey rows included, and every node
# carries its own source and as-of. Stored rows only: nothing here queries
# Postgres or Egeria.

SOURCE_EGERIA = "egeria"
SOURCE_LOCAL = "local"
SOURCE_WORDS = {SOURCE_EGERIA: "Egeria survey", SOURCE_LOCAL: "RE local survey"}

ANN_DATABASE = "Capture Database Measurements"
ANN_SCHEMA = "Capture Database Schema Measurements"
ANN_TABLE = "Capture Database Table Measurements"
_STAGING_MARKERS = ("stg", "staging", "tmp", "temp", "scratch", "sandbox")


def source_text(src: dict | None) -> str:
    """`from Egeria survey 10-04`: the muted line under every row."""
    if not src or not src.get("kind"):
        return ""
    return f"from {SOURCE_WORDS.get(src['kind'], src['kind'])} {md(src.get('as_of'))}".strip()


def _src(kind: str, as_of: str) -> dict:
    return {"kind": kind, "as_of": as_of or "", "text": source_text({"kind": kind, "as_of": as_of})}


def _tree_source_kind(registry, slug: str) -> str:
    """Which source wrote the newest `database_tables` rows the tree is read
    from. A row stamped `egeria` is Egeria's, whatever table it landed in."""
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT source FROM database_tables WHERE database_slug = ? "
                "ORDER BY surveyed_at DESC LIMIT 1", (registry._normalize_slug(slug),)).fetchone()
        kind = (dict(row).get("source") if row else "") or ""
    except Exception:
        kind = ""
    return SOURCE_EGERIA if kind == "egeria" else SOURCE_LOCAL


def _prop_dict(value: Any) -> dict:
    """A property bag that may be a dict, a JSON string, empty or malformed."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            out = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return out if isinstance(out, dict) else {}
    return {}


def _annotation_props(a: dict) -> tuple[dict, bool]:
    """(the measured properties of one stored annotation, readable?).

    `detail.json_properties` is a JSON STRING (`tableName`, `qualifiedTableName`,
    `tableSize`, the counters); `resource_properties` and `subtype_data` carry
    the same bag as dicts on other paths. All three are read defensively and an
    unreadable one is an absence (`readable` False), never an exception."""
    d = a.get("detail") if isinstance(a.get("detail"), dict) else {}
    props: dict = {}
    props.update(_prop_dict((d.get("subtype_data") or {}).get("resourceProperties")
                            if isinstance(d.get("subtype_data"), dict) else None))
    props.update(_prop_dict(d.get("resource_properties")))
    props.update(_prop_dict(d.get("json_properties")))
    return props, bool(props)


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _is_system(name: str) -> bool:
    from resource_explorer.surveyors.database.connection import POSTGRES_CONTAINMENT
    return POSTGRES_CONTAINMENT.is_system_container(name)


def _newest_complete_native_run(registry, slug: str, before: str = "") -> tuple[dict, list[dict]] | None:
    """The newest native survey whose stored annotations are all there: the
    run's `report_annotation_count` equals the rows actually stored. A run that
    never read its report, or lost rows, is not a measurement to build from.
    With `before`, only runs taken at or before that moment (what was known then)."""
    try:
        runs = registry.list_native_survey_runs("database", slug) or []
    except Exception:
        return None
    for run in runs:
        guid = run.get("survey_report_guid") or ""
        want = run.get("report_annotation_count")
        if not guid or not want:
            continue
        if before and str(run.get("survey_report_at") or run.get("surveyed_at") or "")[:19] > before[:19]:
            continue
        try:
            if registry.count_native_survey_annotations(guid) != int(want):
                continue
            return run, registry.query_native_survey_annotations(guid)
        except Exception:
            continue
    return None


def _native_node_set(registry, slug: str, before: str = "") -> dict | None:
    """The tree-shaped node set of the newest complete native survey, or None.

    `{"schemas": [...], "as_of", "report_guid", "stats_reset", "unreadable"}`.
    Schemas are named by schema annotations and also by table annotations, so a
    schema whose own annotation is missing still lists; a table lists whenever
    its name is readable, with any unreadable fact left None ("not established")."""
    from resource_explorer.surveyors.result_materializer import (
        M_DB_COLUMN_COUNT, M_DB_TABLE_COUNT, M_LAST_STATS_RESET, M_ROWS_DELETED,
        M_ROWS_INSERTED, M_ROWS_UPDATED, M_SCHEMA_NAME, M_SCHEMA_TOTAL_TABLE_SIZE,
        M_TABLE_NAME, M_TABLE_QNAME, M_TABLE_SIZE, M_TABLE_TYPE, _split_qualified, native_props,
    )
    found = _newest_complete_native_run(registry, slug, before)
    if not found:
        return None
    run, anns = found
    as_of = (run.get("survey_report_at") or next((a.get("report_at") for a in anns if a.get("report_at")), "")
             or run.get("surveyed_at") or "")
    schemas: dict[str, dict] = {}
    tables: dict[str, dict[str, dict]] = {}
    unreadable, stats_reset = 0, ""
    for a in anns:
        kind = a.get("annotation_type") or ""
        if kind not in (ANN_DATABASE, ANN_SCHEMA, ANN_TABLE):
            continue
        props, ok = _annotation_props(a)
        if not ok:
            unreadable += 1
            continue
        props = native_props(props)
        if kind == ANN_DATABASE:
            stats_reset = stats_reset or str(props.get(M_LAST_STATS_RESET) or "")
        elif kind == ANN_SCHEMA:
            name = str(props.get(M_SCHEMA_NAME) or "")
            if name:
                schemas[name] = props
        else:
            tname = str(props.get(M_TABLE_NAME) or "")
            if not tname:
                unreadable += 1
                continue
            qn = str(props.get(M_TABLE_QNAME) or props.get("qualifiedTableName") or "")
            parts = _split_qualified(qn, tname, 2)
            sname = str(props.get(M_SCHEMA_NAME) or (parts[0] if len(parts) == 2 else ""))
            if not sname:
                unreadable += 1
                continue
            tables.setdefault(sname, {})[tname] = props
    out_schemas: list[dict] = []
    system = 0
    src = _src(SOURCE_EGERIA, as_of)
    for name in sorted(set(schemas) | set(tables)):
        if _is_system(name):
            system += 1
            continue
        sp, tps = schemas.get(name, {}), tables.get(name, {})
        t_nodes = []
        for tname in sorted(tps):
            p = tps[tname]
            t_nodes.append({
                "name": tname, "table_type": str(p.get(M_TABLE_TYPE) or ""),
                "row_count": None, "row_count_state": "", "size_bytes": _int(p.get(M_TABLE_SIZE)),
                "column_count": _int(p.get(M_DB_COLUMN_COUNT)), "columns": [], "source": src,
                "facts_from": {"size": src} if _int(p.get(M_TABLE_SIZE)) is not None else {},
                "counters": {k: _int(p.get(c)) for k, c in (
                    ("inserted", M_ROWS_INSERTED), ("updated", M_ROWS_UPDATED),
                    ("deleted", M_ROWS_DELETED))},
                "counters_at": as_of,
            })
        count = len(t_nodes) if t_nodes else _int(sp.get(M_DB_TABLE_COUNT))
        cls = "staging" if any(m in name.lower() for m in _STAGING_MARKERS) else "measured"
        bytes_total = _int(sp.get(M_SCHEMA_TOTAL_TABLE_SIZE))
        out_schemas.append({
            "schema": name, "classification": cls, "reason": "", "table_count": count,
            "row_total": None, "bytes_total": bytes_total, "is_estimate": False,
            "tables": t_nodes, "source": src,
            "facts_from": {"size": src} if bytes_total is not None else {},
        })
    out_schemas.sort(key=lambda s: (-(s["table_count"] or 0), s["schema"]))
    if system:
        out_schemas.append({"schema": None, "table_count": None, "row_total": None, "bytes_total": None,
                            "is_estimate": False, "reason": "", "classification": "system",
                            "system_count": system})
    return {"schemas": out_schemas, "as_of": as_of, "report_guid": run.get("survey_report_guid") or "",
            "stats_reset": stats_reset, "unreadable": unreadable}


def _tag_local(tree: dict, as_of: str, kind: str) -> list[dict]:
    """The local tree's schemas, each node tagged with its own source and as-of."""
    src = _src(kind, as_of)
    out = []
    for s in (tree or {}).get("schemas") or []:
        if s.get("classification") == "system" or not s.get("schema"):
            out.append(dict(s))
            continue
        tabs = [{**t, "source": src,
                 "facts_from": {k: src for k, v in (("rows", t.get("row_count")), ("size", t.get("size_bytes")))
                                if v is not None}} for t in s.get("tables") or []]
        out.append({**s, "tables": tabs, "source": src,
                    "facts_from": {k: src for k, v in (("rows", s.get("row_total")), ("size", s.get("bytes_total")))
                                   if v is not None}})
    return out


def _counts(schemas: list[dict]) -> tuple[int, int]:
    real = [s for s in schemas if s.get("schema") and s.get("classification") != "system"]
    return len(real), sum(len(s.get("tables") or []) for s in real)


def _fill_from(primary: dict, other: dict) -> dict:
    """`primary`'s node with any fact it lacks taken from `other`, the fact's
    source recorded so the row can say where a number came from."""
    out = {**primary, "facts_from": dict(primary.get("facts_from") or {})}
    pairs = (("rows", "row_total", "row_total"), ("size", "bytes_total", "bytes_total"))
    for fact, field, ofield in pairs:
        if out.get(field) is None and other.get(ofield) is not None:
            out[field] = other[ofield]
            out["facts_from"][fact] = (other.get("facts_from") or {}).get(fact) or other.get("source")
            if field == "row_total":
                out["is_estimate"] = bool(other.get("is_estimate"))
    if out.get("table_count") is None and other.get("table_count") is not None:
        out["table_count"] = other["table_count"]
    return out


def _fill_table(primary: dict, other: dict) -> dict:
    out = {**primary, "facts_from": dict(primary.get("facts_from") or {})}
    for fact, field in (("rows", "row_count"), ("size", "size_bytes")):
        if out.get(field) is None and other.get(field) is not None:
            out[field] = other[field]
            out["facts_from"][fact] = (other.get("facts_from") or {}).get(fact) or other.get("source")
            if field == "row_count":
                out["row_count_state"] = other.get("row_count_state") or ""
    if not out.get("table_type") and other.get("table_type"):
        out["table_type"] = other["table_type"]
    if not out.get("counters") and other.get("counters"):
        out["counters"], out["counters_at"] = other["counters"], other.get("counters_at") or ""
    if not out.get("columns") and other.get("columns"):
        out["columns"] = other["columns"]
    if out.get("column_count") is None and other.get("column_count") is not None:
        out["column_count"] = other["column_count"]
    return out


def _merge_sets(primary: list[dict], secondary: list[dict], *, secondary_newer: bool) -> list[dict]:
    """The primary's nodes, facts filled from the secondary node by node. A node
    only the secondary has is added when the secondary is the NEWER measurement
    (it appeared after the primary was taken); an older measurement's extra node
    may be a schema since dropped, so it is not listed."""
    sec = {s["schema"]: s for s in secondary if s.get("schema") and s.get("classification") != "system"}
    out: list[dict] = []
    sys_row = next((s for s in primary if s.get("classification") == "system"), None)
    for ps in primary:
        if ps.get("classification") == "system" or not ps.get("schema"):
            continue
        ss = sec.pop(ps["schema"], None)
        if ss is None:
            out.append(ps)
            continue
        merged = _fill_from(ps, ss)
        stab = {t["name"]: t for t in ss.get("tables") or []}
        tabs = []
        for pt in ps.get("tables") or []:
            ot = stab.pop(pt["name"], None)
            tabs.append(_fill_table(pt, ot) if ot else pt)
        if secondary_newer and stab:
            tabs.extend(stab.values())
            merged["table_count"] = len(tabs)
        merged["tables"] = tabs
        out.append(merged)
    if secondary_newer:
        out.extend(sec.values())
    sys_row = sys_row or next((s for s in secondary if s.get("classification") == "system"), None)
    if sys_row:
        out.append(sys_row)
    return out


def _local_header(registry, slug: str, schemas: list[dict], as_of: str, kind: str) -> dict:
    n_s, n_t = _counts(schemas)
    real = [s for s in schemas if s.get("schema") and s.get("classification") != "system"]
    blind = sum(1 for s in real if s.get("classification") == "no_access")
    surveyed_as, read_error = "", ""
    try:
        for r in registry.get_database_surveys(slug) or []:
            if (r.get("source") or "") == SOURCE_LOCAL and r.get("surveyed_as"):
                surveyed_as = r["surveyed_as"]
                break
    except Exception as exc:  # observable: a failed read is not "not measured"
        read_error = f"{type(exc).__name__}: {exc}"[:300]
    state = "measured" if n_s else ("unreadable" if read_error else "not_measured")
    return {"state": state, "read_error": read_error, "kind": kind, "schema_count": n_s,
            "table_count": n_t, "surveyed_at": as_of if n_s else "", "surveyed_as": surveyed_as,
            "sees": (f"sees {n_s - blind} of {n_s}" if blind else "")}


def resolve_node_set(registry, slug: str, tree: dict | None = None) -> dict:
    """The one place the scope tree's nodes come from.

    Compares the newest complete native survey with the local tree by node
    count (completeness) and as-of: the FULLER one is primary (ties go to the
    newer), the other fills facts the primary lacks and, if newer, adds nodes
    the primary never saw. `{"schemas", "chosen", "egeria", "local", "disagree"}`;
    `chosen` names the primary and says whether another source merged in."""
    local_tree = tree if tree is not None else _load_tree(registry, slug)
    local_at = _tree_survey_at(registry, slug)
    local_kind = _tree_source_kind(registry, slug)
    local = _tag_local(local_tree, local_at, local_kind)
    native = _native_node_set(registry, slug)

    l_s, l_t = _counts(local)
    local_info = _local_header(registry, slug, local, local_at, local_kind)
    if native is None:
        egeria_info = _legacy_egeria_header(registry, slug)
        chosen = {"kind": local_kind, "as_of": local_at, "schemas": l_s, "tables": l_t, "merged": False}
        return {"schemas": _normalise_measures(local), "chosen": chosen, "egeria": egeria_info, "local": local_info,
                "disagree": False, "unreadable": 0, "stats_reset": ""}
    n_s, n_t = _counts(native["schemas"])
    egeria_info = {"state": "measured", "schema_count": n_s, "table_count": n_t,
                   "surveyed_at": native["as_of"], "report_guid": native["report_guid"]}
    native_first = (n_s + n_t, native["as_of"][:19]) >= (l_s + l_t, local_at[:19])
    if not (l_s + l_t):
        native_first = True
    if native_first:
        merged = _merge_sets(native["schemas"], local, secondary_newer=local_at[:19] > native["as_of"][:19])
        kind, as_of = SOURCE_EGERIA, native["as_of"]
        used_other = bool(l_s)
    else:
        merged = _merge_sets(local, native["schemas"], secondary_newer=native["as_of"][:19] > local_at[:19])
        kind, as_of = local_kind, local_at
        used_other = True
    merged = _normalise_measures(merged)
    m_s, m_t = _counts(merged)
    chosen = {"kind": kind, "as_of": as_of, "schemas": m_s, "tables": m_t, "merged": used_other}
    disagree = bool(l_s) and (l_s != n_s or l_t != n_t)
    return {"schemas": merged, "chosen": chosen, "egeria": egeria_info, "local": local_info,
            "disagree": disagree, "unreadable": native["unreadable"], "stats_reset": native["stats_reset"]}


def _has_row_evidence(t: dict) -> bool:
    """Did anything count rows in this table? A stored row count above zero, or
    inserted/updated counters above zero (a table that took writes has rows)."""
    if (t.get("row_count") or 0) > 0:
        return True
    c = t.get("counters") or {}
    return any((c.get(k) or 0) > 0 for k in ("inserted", "updated"))


def measured_size_rule(t: dict) -> tuple[int | None, int | None, str]:
    """`(size_bytes, column_count, why)` for one table, with a recorded zero that
    is not a measurement turned back into "not measured" (None).

    The native survey stores `tableSize: 0` and `columnCount: 0` for tables it did
    not measure, even when its other counters are non-zero: a "not measured" written
    as a zero. The rule, in order:
      - a column count of 0 is never a measurement (a table has columns): it becomes
        the number of columns listed, or None when none are listed;
      - a size of 0 with a column count of 0 is not a measurement;
      - a size of 0 where rows were counted above zero (or the table took writes)
        is not a measurement;
      - a size of 0 stays "0 B" only when rows were counted as 0 by a measured
        (not estimated) source and the column count is not 0: a plausible empty table.
        A size of 0 with no row evidence either way is not established, so None."""
    size, cols = t.get("size_bytes"), t.get("column_count")
    listed = len(t.get("columns") or [])
    why = ""
    if cols == 0:
        cols = listed or None
        why = "column count 0 is not a measurement"
    if size == 0:
        zero_cols = t.get("column_count") == 0
        measured_empty = (t.get("row_count") == 0 and t.get("row_count_state") != "catalog_estimate")
        if zero_cols or _has_row_evidence(t) or not measured_empty:
            size, why = None, why or "size 0 with no evidence that the table is empty"
    return size, cols, why


def _normalise_measures(schemas: list[dict]) -> list[dict]:
    """Apply `measured_size_rule` to every table of a resolved node set, and a
    schema's recorded zero total with it (a zero total stays only when a table is a
    measured empty one)."""
    out = []
    for s in schemas:
        if s.get("classification") == "system" or not s.get("schema"):
            out.append(s)
            continue
        tabs = []
        for t in s.get("tables") or []:
            size, cols, _ = measured_size_rule(t)
            if size == t.get("size_bytes") and cols == t.get("column_count"):
                tabs.append(t)
                continue
            nt = {**t, "size_bytes": size, "column_count": cols}
            if size is None:
                nt["facts_from"] = {k: v for k, v in (t.get("facts_from") or {}).items() if k != "size"}
            tabs.append(nt)
        ns = {**s, "tables": tabs}
        if s.get("bytes_total") == 0 and not any(t.get("size_bytes") == 0 for t in tabs):
            ns["bytes_total"] = None
            ns["facts_from"] = {k: v for k, v in (s.get("facts_from") or {}).items() if k != "size"}
        out.append(ns)
    return out


def sources_view(resolved: dict) -> dict:
    """The header facts of a resolved node set, shared by the Curate scope view and
    the Schema Inventory route so both name the same surveys the same way."""
    return {"chosen": resolved["chosen"], "egeria": resolved["egeria"], "local": resolved["local"],
            "disagree": resolved["disagree"], "unreadable": resolved["unreadable"]}


def _legacy_egeria_header(registry, slug: str) -> dict:
    """Egeria's native survey summarised by an older `database_surveys` row
    (`source = egeria`), used only when no native survey rows are stored."""
    from resource_explorer.registry import SOURCE_EGERIA as DB_SOURCE_EGERIA
    try:
        rows = registry.get_database_surveys(slug) or []
    except Exception:
        rows = []
    for r in rows:
        if (r.get("source") or "") != DB_SOURCE_EGERIA:
            continue
        if r.get("schema_count") is None or r.get("table_count") is None:
            continue
        return {"state": "measured", "schema_count": int(r["schema_count"]),
                "table_count": int(r["table_count"]), "surveyed_at": r.get("surveyed_at") or "",
                "report_guid": r.get("egeria_report_guid") or ""}
    return {"state": "not_measured", "schema_count": None, "table_count": None,
            "surveyed_at": "", "report_guid": ""}


def _known_before(registry, slug: str, declared_at: str) -> tuple[set, set, str]:
    """Schema names and `schema.table` keys that a COMPLETE survey measured at or
    before `declared_at`: the newest native survey of that time, and the newest
    local snapshot of that time. A node in here existed when the scope was
    declared, whatever the baseline's own (possibly thin) source could see.

    The third value is a read error ('' when the reads worked). A failed read is
    NOT "nothing was known before": the caller must branch on it."""
    schemas: set[str] = set()
    tables: set[str] = set()
    native = _native_node_set(registry, slug, before=declared_at)
    if native:
        for s in native["schemas"]:
            if s.get("schema") and s.get("classification") != "system":
                schemas.add(s["schema"])
                tables.update(f"{s['schema']}.{t['name']}" for t in s.get("tables") or [])
    try:
        with registry._conn() as conn:
            row = conn.execute(
                "SELECT MAX(surveyed_at) AS at FROM database_tables WHERE database_slug = ? "
                "AND surveyed_at <= ?", (registry._normalize_slug(slug), declared_at[:19] + "z")).fetchone()
        at = (dict(row).get("at") if row else "") or ""
        if at:
            for r in registry.query_detail_rows("database_tables", slug, at):
                if r.get("schema_name") and not _is_system(r["schema_name"]):
                    schemas.add(r["schema_name"])
                    tables.add(f"{r['schema_name']}.{r.get('table_name')}")
    except Exception as exc:
        return schemas, tables, f"{type(exc).__name__}: {exc}"[:300]
    return schemas, tables, ""


def _parse_day(iso: str):
    try:
        return datetime.strptime(str(iso or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None


def activity_for(c: dict | None, *, threshold: int | None = None) -> dict:
    """The activity word of one table and the window it stands on.

    Evidence is the cumulative counters since `stats_reset`, window = reset -> the
    survey's date. active: writes counted. dormant: zero writes in a window of at
    least `DORMANCY_DAYS`. Everything else is "can't tell" with its reason (counters
    not measured, reset date not recorded, or too few days of evidence), and a
    can't-tell proposes nothing."""
    threshold = DORMANCY_DAYS if threshold is None else threshold
    base = {"state": "cant_tell", "writes": None, "reset": "", "window_days": None, "reason": ""}
    if not c or c.get("writes") is None:
        return {**base, "reason": "counters not measured",
                "text": "can't tell · counters not measured"}
    writes, reset, at = int(c["writes"]), str(c.get("reset") or ""), str(c.get("at") or "")
    d0, d1 = _parse_day(reset), _parse_day(at)
    days = (d1 - d0).days if d0 and d1 else None
    base.update(writes=writes, reset=reset, window_days=days)
    # Brief D 7f.3: "never" is Postgres saying the counters were never reset --
    # a different fact from a reset date that was not read. Its window start is
    # unknown (the counters run from server start), so it never proves dormancy.
    never = reset == STATS_NEVER_RESET
    if writes > 0:
        since = (f"since counters reset {md(reset)}" if md(reset)
                 else "(counters never reset)" if never else "(reset date not recorded)")
        return {**base, "state": "active", "text": f"active · {writes:,} writes {since}"}
    if never:
        return {**base, "reason": "counters never reset",
                "text": "can't tell · counters never reset · window start unknown"}
    if days is None or days < 0:
        return {**base, "reason": "reset date not recorded",
                "text": "can't tell · reset date not recorded"}
    if days >= threshold:
        return {**base, "state": "dormant",
                "text": f"dormant · 0 writes in {days} days (counters reset {reset[:10]})"}
    return {**base, "reason": f"{days} days of evidence",
            "text": f"can't tell · counters reset {md(reset)} · {days} day{'s' if days != 1 else ''} of evidence"}


def rollup_activity(acts: list[dict]) -> dict:
    """A schema's activity from its tables': any active -> active; all dormant ->
    dormant (the shortest window); otherwise can't tell, with a table's reason."""
    if not acts:
        return {"state": "cant_tell", "writes": None, "reset": "", "window_days": None,
                "reason": "counters not measured", "text": "can't tell · counters not measured"}
    active = [a for a in acts if a["state"] == "active"]
    if active:
        total = sum(a["writes"] for a in active)
        resets = sorted(a["reset"] for a in active if a["reset"])
        since = f"since counters reset {md(resets[0])}" if resets else "(reset date not recorded)"
        return {"state": "active", "writes": total, "reset": resets[0] if resets else "",
                "window_days": None, "reason": "", "text": f"active · {total:,} writes {since}"}
    if all(a["state"] == "dormant" for a in acts):
        days = min(a["window_days"] for a in acts)
        reset = max(a["reset"] for a in acts)
        return {"state": "dormant", "writes": 0, "reset": reset, "window_days": days, "reason": "",
                "text": f"dormant · 0 writes in {days} days (counters reset {reset[:10]})"}
    first = next(a for a in acts if a["state"] == "cant_tell")
    return {**first}


def _pick_counters(cands: list[dict]) -> dict | None:
    """The counters that give the LONGEST window (earliest known reset), then the
    newer survey. A candidate with no counter is not evidence."""
    cands = [c for c in cands if c and c.get("writes") is not None]
    if not cands:
        return None
    return sorted(cands, key=lambda c: (c.get("reset") or "9999", -len(c.get("at") or ""), c.get("at") or ""))[0]


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "not measured"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{int(v)} {unit}" if unit == "B" or v >= 10 else f"{v:.1f} {unit}"
        v /= 1024
    return f"{int(n)} B"


def _src_words(src: str) -> str:
    return SOURCE_WORDS.get(src, src)


def rows_cell(cands: list[dict], *, no_access: bool = False) -> dict:
    """One table's row-count cell. Preference: a measured count over an estimate, then
    the newer. "◐ sources disagree" (both values, each dated) when two sources differ
    by more than a factor of two; never a silent pick. Never a blank and never a
    zero for what was not measured."""
    if no_access:
        return {"state": "not_established", "value": None, "text": "? not established", "detail": ""}
    if not cands:
        return {"state": "not_measured", "value": None, "text": "not measured", "detail": ""}
    by_src: dict[str, dict] = {}
    for c in sorted(cands, key=lambda c: c.get("at") or ""):
        by_src[c["source"]] = c                      # newest per source
    vals = list(by_src.values())
    if len(vals) > 1:
        lo, hi = min(v["rows"] for v in vals), max(v["rows"] for v in vals)
        if (lo == 0 and hi > 0) or (lo > 0 and hi / lo > 2):
            detail = " · ".join(
                f"{_src_words(v['source'])} {md(v['at'])}: {'≈' if v['state'] == 'catalog_estimate' else ''}{v['rows']:,}"
                for v in vals)
            return {"state": "disagree", "value": None, "text": "◐ sources disagree", "detail": detail}
    best = max(vals, key=lambda v: (v["state"] != "catalog_estimate", v.get("at") or ""))
    est = best["state"] == "catalog_estimate"
    return {"state": "estimate" if est else "measured", "value": best["rows"], "estimate": est,
            "text": f"{'≈' if est else ''}{best['rows']:,}",
            "detail": f"{_src_words(best['source'])} {md(best['at'])}{' · estimate' if est else ' · scan'}"}


def rows_rollup(cells: list[dict], *, no_access: bool = False) -> dict:
    """A schema's rows: the sum of its tables that have a value, saying so when
    any table has none ("≥ 2.1 M rows · 3 tables not measured")."""
    if no_access:
        return {"state": "not_established", "value": None, "text": "? not established", "detail": ""}
    have = [c for c in cells if c["state"] in ("measured", "estimate")]
    miss = len(cells) - len(have)
    if not have:
        return {"state": "not_measured", "value": None, "text": "not measured", "detail": ""}
    total = sum(c["value"] for c in have)
    est = any(c.get("estimate") for c in have)
    if miss:
        return {"state": "partial", "value": total, "estimate": est,
                "text": f"≥ {total:,} · {miss} table{'s' if miss != 1 else ''} not measured", "detail": ""}
    return {"state": "estimate" if est else "measured", "value": total, "estimate": est,
            "text": f"{'≈' if est else ''}{total:,}", "detail": ""}


def size_cell(n: int | None, src: dict | None, *, partial_missing: int = 0) -> dict:
    if n is None:
        return {"state": "not_measured", "value": None, "text": "not measured", "detail": ""}
    detail = source_text(src).replace("from ", "", 1) if src else ""
    text = ("≥ " if partial_missing else "") + _fmt_bytes(n)
    return {"state": "partial" if partial_missing else "measured", "value": n, "text": text, "detail": detail}


def _tree_keys(resolved: dict) -> dict:
    """Baseline keys from a resolved node set: the non-system schemas and
    tables, plus which source they came from and its as-of (so a baseline taken
    from a thin source says so)."""
    schemas, tables = [], []
    for s in resolved.get("schemas") or []:
        if s.get("classification") == "system" or not s.get("schema"):
            continue
        schemas.append(s["schema"])
        for t in s.get("tables") or []:
            tables.append(f"{s['schema']}.{t['name']}")
    ch = resolved.get("chosen") or {}
    return {"schemas": sorted(schemas), "tables": sorted(tables),
            "source": ch.get("kind") or "", "as_of": ch.get("as_of") or ""}


def _latest_by_node(events: list[dict]) -> tuple[dict, dict | None]:
    """(node key -> newest event, newest depth event).

    A node whose newest event is a clear is absent from the first dict."""
    latest: dict[tuple, dict] = {}
    depth = None
    for e in events:  # oldest first, so the last write wins
        if e["node_kind"] == "depth":
            depth = e
            continue
        latest[(e["node_kind"], e["schema_name"], e["table_name"])] = e
    return {k: v for k, v in latest.items() if v["choice"]}, depth


def current_schema_choice(registry, slug: str, schema: str) -> str | None:
    """A schema's explicit choice right now (`catalogue`, `leave_out`) or None.

    Read from the events alone, so a queued write can check whether the person
    changed their mind after it was queued without building the whole view."""
    explicit, _depth = _latest_by_node(registry.list_catalogue_scope_events(slug))
    ev = explicit.get(("schema", schema, ""))
    return ev["choice"] if ev else None


# ── the merged view ──────────────────────────────────────────────────────────

def _dormant_fact(act: dict) -> bool | None:
    """True/False only when the counters say so; None (can't tell) otherwise, and a
    None never proposes."""
    return True if act["state"] == "dormant" else (False if act["state"] == "active" else None)


def build_scope_view(registry, slug: str, *, tree: dict | None = None,
                     data_classes: dict | None = None,
                     lens: dict | None = None) -> dict:
    """The scope merged with the Schema Inventory tree: one read for the screen."""
    resolved = resolve_node_set(registry, slug, tree)
    tree = {"schemas": resolved["schemas"]}
    events = registry.list_catalogue_scope_events(slug)
    baselines = registry.list_catalogue_scope_baselines(slug)
    explicit, depth_event = _latest_by_node(events)
    survey_at = resolved["chosen"]["as_of"]
    native_reset = resolved["stats_reset"]
    local_counters = _activity_counters(registry, slug)
    row_sources = _row_sources(registry, slug)
    classes = data_classes if data_classes is not None else data_class_facts(registry, slug)
    lens = lens if lens is not None else data_lens_for(registry, slug)
    lens_terms = list((lens or {}).get("subjectTerms") or [])

    baseline = baselines[-1] if baselines else None
    base_keys = (baseline or {}).get("baseline") or {}
    base_schemas = set(base_keys.get("schemas") or [])
    base_tables = set(base_keys.get("tables") or [])

    before: dict[str, tuple[set, set, str]] = {}

    def _existed_then(base: dict, key: str, is_table: bool) -> bool:
        """Was it in ANY complete survey taken before the declaration? Computed
        once, and only when something is absent from the baseline."""
        if "k" not in before:
            before["k"] = _known_before(registry, slug, str(base.get("declared_at") or ""))
        return key in before["k"][1 if is_table else 0]

    def _earlier_unreadable() -> bool:
        return bool(before.get("k") and before["k"][2])

    def _is_new(base: dict | None, known: bool, node_at: str, key: str = "", is_table: bool = False) -> bool:
        """A node is "new since your scope was declared" only when the baseline
        did not know it AND a measurement taken AFTER the declaration shows it.
        A baseline recorded from a thinner source (an 8-schema local survey,
        then a 29-schema Egeria one) must not call every node the thin source
        could not see "new": those were measured before the declaration, so
        they existed. Unknown date: not flagged (the safe reading)."""
        if not base or known:
            return False
        declared = str(base.get("declared_at") or "")[:19]
        if not (declared and node_at and str(node_at)[:19] > declared):
            return False
        existed = _existed_then(base, key, is_table)
        if _earlier_unreadable():
            return False          # never infer "nothing known before" from a failed read
        return not existed

    def _table_activity(sname: str, t: dict) -> dict:
        cands = [local_counters.get((sname, t["name"]))]
        c = t.get("counters") or {}
        known = [v for v in c.values() if v is not None]
        if known:
            cands.append({"writes": sum(known), "reset": native_reset,
                          "at": t.get("counters_at") or "", "source": SOURCE_EGERIA})
        return activity_for(_pick_counters(cands))

    def _table_rows(sname: str, t: dict, no_access: bool) -> dict:
        cands = list(row_sources.get((sname, t["name"]), []))
        if t.get("row_count") is not None:
            ff = (t.get("facts_from") or {}).get("rows") or t.get("source") or {}
            mine = {"source": ff.get("kind") or SOURCE_LOCAL, "rows": int(t["row_count"]),
                    "state": t.get("row_count_state") or "", "at": ff.get("as_of") or ""}
            if not any(c["source"] == mine["source"] and c["rows"] == mine["rows"] for c in cands):
                cands.append(mine)
        return rows_cell(cands, no_access=no_access)

    def _facts_from(ff: dict | None) -> dict:
        return {k: v for k, v in (ff or {}).items() if v}

    depth = (depth_event or {}).get("choice") or DEFAULT_DEPTH

    def event_view(e: dict | None) -> dict | None:
        if not e:
            return None
        return {"choice": e["choice"], "action": e["action"], "source": e["source"],
                "by": e["author"], "at": e["changed_at"], "reason": e["reason"],
                "proposal_rule": e["proposal_rule"], "proposal_choice": e["proposal_choice"],
                "measured_at": e["measured_at"], "measured": e["measured"]}

    def decide(kind: str, facts: dict, ev: dict | None) -> dict:
        """The observation state of one node from its facts and its newest event."""
        proposal = propose_for_node(facts)
        out: dict[str, Any] = {"proposal": None, "live_proposal": None, "overridden": None,
                               "disagrees": None, "notes": [], "marks": []}
        if proposal and "conflicting" in proposal:
            out["notes"].append("measurements disagree: nothing is proposed")
            proposal = None
        out["live_proposal"] = proposal
        if ev is None:
            out["state"] = "proposed" if proposal else "undecided"
            out["proposal"] = proposal
            return out
        rule = ev["proposal_rule"]
        if ev["action"] in ("confirm", "override") and rule:
            now = measure_fact(rule, facts)
            out["disagrees"] = _disagreement(rule, ev["measured"], now, survey_at)
        if ev["action"] == "override":
            out["overridden"] = {"rule": rule, "choice": ev["proposal_choice"],
                                 "reason": ev["reason"], "measured_at": ev["measured_at"]}
            out["state"] = "overridden"
        elif ev["action"] == "confirm":
            out["state"] = "confirmed"
        else:
            out["state"] = "chosen"
        if out["disagrees"]:
            out["state"] = "disagrees"
        return out

    schemas_out: list[dict] = []
    system_row = None
    name_to_tables: dict[str, list[dict]] = {}

    for s in tree.get("schemas") or []:
        if s.get("classification") == "system":
            system_row = s
            continue
        sname = s["schema"]
        cls = s.get("classification") or ""
        tabs = s.get("tables") or []
        t_acts = [_table_activity(sname, t) for t in tabs]
        s_act = rollup_activity(t_acts)
        no_acc = cls == "no_access"
        t_rows = [_table_rows(sname, t, no_acc) for t in tabs]
        s_rows = rows_rollup(t_rows, no_access=no_acc)
        sized = [t.get("size_bytes") for t in tabs if t.get("size_bytes") is not None]
        s_size_n = sum(sized) if sized else s.get("bytes_total")
        s_size = size_cell(s_size_n, (s.get("facts_from") or {}).get("size") or s.get("source"),
                           partial_missing=(len(tabs) - len(sized)) if sized and len(sized) < len(tabs) else 0)
        s_src = s.get("source") or _src(resolved["chosen"]["kind"], survey_at)
        s_at = s_src.get("as_of") or survey_at
        s_facts = {"kind": "schema", "name": sname, "classification": cls,
                   "table_count": s.get("table_count"),
                   "dormant": _dormant_fact(s_act), "window_days": s_act.get("window_days"),
                   "measured_at": s_at}
        s_ev = explicit.get(("schema", sname, ""))
        sv = decide("schema", s_facts, s_ev)
        s_choice = s_ev["choice"] if s_ev else None
        in_baseline = sname in base_schemas
        node: dict[str, Any] = {
            "kind": "schema", "name": sname, "key": sname, "classification": cls,
            "reason": s.get("reason") or "", "table_count": s.get("table_count"),
            "row_total": s.get("row_total"), "bytes_total": s.get("bytes_total"),
            "is_estimate": bool(s.get("is_estimate")),
            "source": s_src, "facts_from": _facts_from(s.get("facts_from")),
            "explicit": event_view(s_ev), "effective": s_choice,
            "effective_from": "self" if s_choice else None,
            "new_since": _is_new(baseline, in_baseline, s_at, sname),
            "access": "not_established" if cls == "no_access" else "established",
            "last_write": s_act, "rows_view": s_rows, "size_view": s_size,
            "data_classes": {"state": "not_established", "classes": [], "pii_columns": 0},
            "provenance": depth_provenance(depth, "schema"),
            "tables": [], **sv,
        }
        if cls == "staging":
            node["notes"].append("name suggests staging")
        if s_choice is None:
            node["undecided_words"] = "keeps what’s in Egeria now"
        pii_s = 0
        for ti, t in enumerate(tabs):
            tname = t["name"]
            dc = classes.get((sname, tname))
            pii = int((dc or {}).get("pii_columns") or 0)
            pii_s += pii
            t_act = t_acts[ti]
            t_src = t.get("source") or s_src
            t_at = t_src.get("as_of") or s_at
            t_facts = {"kind": "table", "schema": sname, "name": tname, "classification": cls,
                       "dormant": _dormant_fact(t_act), "window_days": t_act.get("window_days"),
                       "measured_at": t_at}
            t_ev = explicit.get(("table", sname, tname))
            tv = decide("table", t_facts, t_ev)
            own = t_ev["choice"] if t_ev else None
            if own:
                eff, eff_from = own, "self"
            elif s_choice:
                eff, eff_from = s_choice, "schema"
            else:
                eff, eff_from = None, None
            tnode = {
                "kind": "table", "name": tname, "schema": sname, "key": f"{sname}.{tname}",
                "table_type": t.get("table_type") or "", "row_count": t.get("row_count"),
                "row_count_state": t.get("row_count_state") or "", "size_bytes": t.get("size_bytes"),
                "column_count": t.get("column_count"),
                "source": t_src, "facts_from": _facts_from(t.get("facts_from")),
                "explicit": event_view(t_ev), "effective": eff, "effective_from": eff_from,
                "inherited": s_choice if not own else None,
                "differs_from_schema": bool(own and s_choice and own != s_choice),
                "new_since": _is_new(baseline, f"{sname}.{tname}" in base_tables, t_at, f"{sname}.{tname}", True),
                "access": "not_established" if cls == "no_access" else "established",
                "last_write": t_act, "rows_view": t_rows[ti],
                "size_view": size_cell(t.get("size_bytes"), (t.get("facts_from") or {}).get("size") or t_src),
                "data_classes": ({"state": "measured", "classes": list(dc.get("classes") or []),
                                  "pii_columns": pii} if dc
                                 else {"state": "not_established", "classes": [], "pii_columns": 0}),
                "provenance": depth_provenance(depth, "table"),
                **tv,
            }
            if pii:
                tnode["marks"].append(f"PII · {pii} column{'s' if pii != 1 else ''}")
            if depth == "tables_and_columns":
                tnode["columns"] = t.get("columns") or []
            node["tables"].append(tnode)
            name_to_tables.setdefault(tname, []).append(tnode)
        if pii_s:
            node["marks"].append(f"PII · {pii_s} column{'s' if pii_s != 1 else ''}")
            node["data_classes"] = {"state": "measured", "classes": [], "pii_columns": pii_s}
        schemas_out.append(node)

    # A lens term found in table NAMES is a suggested rule, never a proposal (designer
    # round 2: a name substring is not "a measured match"). Only the sentence is built;
    # rules themselves are a later slice, so there is no control behind it.
    suggested = []
    for term in lens_terms:
        n_hit = sum(1 for tl in name_to_tables for _ in name_to_tables[tl]
                    if str(term).lower() in tl.lower())
        if n_hit:
            suggested.append({"term": term, "count": n_hit,
                              "text": f"The lens names {term}: {n_hit} table names contain it · make that a scope rule?"})

    view = {
        "database": slug, "schemas": schemas_out,
        "system": ({"folded": system_row.get("system_count"), "text": SYSTEM_SENTENCE}
                   if system_row else None),
        "survey": resolved["egeria"],
        "sources": sources_view(resolved),
        "tree_surveyed_at": survey_at, "suggested_rules": suggested,
        "dormancy_days": DORMANCY_DAYS,
        "declared": ({"declared": True, "by": baseline["declared_by"], "at": baseline["declared_at"],
                      "kind": baseline["kind"], "baseline_survey_at": baseline["survey_at"],
                      "baseline_source": base_keys.get("source") or ""}
                     if baseline else {"declared": False, "by": "", "at": "", "kind": "",
                                       "baseline_survey_at": ""}),
        "depth": {"value": depth, "declared": depth_event is not None,
                  "by": (depth_event or {}).get("author", ""), "at": (depth_event or {}).get("changed_at", ""),
                  "options": [dict(d) for d in DEPTHS], "help": DEPTH_HELP,
                  "commit_note": ("" if depth == "tables_and_columns" else DEPTH_NOT_HONOURED)},
    }
    view["counts"] = {
        "schemas_offered": len(schemas_out),
        "schemas_catalogue": sum(1 for n in schemas_out if n["effective"] == CATALOGUE),
        "schemas_leave_out": sum(1 for n in schemas_out if n["effective"] == LEAVE_OUT),
        "schemas_undecided": sum(1 for n in schemas_out if n["effective"] is None),
    }
    view["earlier_read_error"] = before["k"][2] if before.get("k") else ""
    view["new_since"] = _new_since_from_view(view)
    return view


def _new_since_from_view(view: dict) -> dict:
    """What appeared in the latest survey that the declared scope never saw.

    Nodes that have since been given an explicit choice are in the scope now
    and are not counted. With no declaration there is nothing to be new since,
    and the answer says so rather than reporting zero."""
    if not view["declared"]["declared"]:
        return {"declared": False, "schemas": 0, "tables": 0, "tables_in_known_schemas": 0,
                "schema_names": [], "text": "", "since": ""}
    if view.get("earlier_read_error"):
        # A failed read of the earlier surveys is not "nothing was known before":
        # say we can't tell, and flag nothing as new.
        return {"declared": True, "can_tell": False, "schemas": 0, "tables": 0,
                "tables_in_known_schemas": 0, "schema_names": [],
                "error": view["earlier_read_error"], "since": view["declared"]["at"],
                "text": "can't tell: the earlier surveys could not be read"}
    new_schemas = [s for s in view["schemas"] if s["new_since"] and s["explicit"] is None]
    tables_in_new = sum(1 for s in new_schemas for t in s["tables"] if t["explicit"] is None)
    known = [t for s in view["schemas"] if not s["new_since"] for t in s["tables"]
             if t["new_since"] and t["explicit"] is None]
    n, m = len(new_schemas), tables_in_new
    text = ""
    if n:
        text = f"{n} new schema{'s' if n != 1 else ''} ({m} table{'s' if m != 1 else ''}) not in your scope"
    if known:
        extra = f"{len(known)} new table{'s' if len(known) != 1 else ''} in schemas you already know"
        text = f"{text} · {extra}" if text else extra
    return {"declared": True, "schemas": n, "tables": m, "tables_in_known_schemas": len(known),
            "schema_names": [s["name"] for s in new_schemas], "text": text,
            "since": view["declared"]["at"]}


def new_since_declared(registry, slug: str, *, view: dict | None = None, **kw) -> dict:
    """The count slice B's manifest quotes: "N new schemas (M tables) not in your scope"."""
    return (view or build_scope_view(registry, slug, **kw))["new_since"]


# ── writes (all append; none touch Egeria) ───────────────────────────────────

def _now() -> str:
    return datetime.utcnow().isoformat()


def _declare_if_needed(registry, slug: str, author: str, keys: dict, now: str) -> None:
    """First declaration: store the baseline (the node keys the latest survey
    knows right now). A no-op once a baseline exists."""
    if registry.list_catalogue_scope_baselines(slug):
        return
    registry.append_catalogue_scope_baseline(
        slug, baseline=keys, survey_at=keys.get("as_of") or _tree_survey_at(registry, slug),
        author=author, kind="first", declared_at=now)


def _find_node(view: dict, schema: str, table: str) -> dict:
    for s in view["schemas"]:
        if s["name"] == schema:
            if not table:
                return s
            for t in s["tables"]:
                if t["name"] == table:
                    return t
            raise ScopeError(404, f"table {schema}.{table} is not in the latest survey")
    raise ScopeError(404, f"schema {schema} is not offered: it is not in the latest survey, "
                          "or it is a system schema (system schemas are never offered)")


def set_node_choice(registry, slug: str, author: str, *, schema: str, table: str = "",
                    choice: str, reason: str = "", now: str | None = None, **view_kw) -> dict:
    """Set a node's choice. If a rule is proposing for this node right now, the
    event records it: the same choice is a confirmation (source = the rule id),
    the opposite is an override (source = person, the proposal kept beside it)."""
    if choice not in CHOICES:
        raise ScopeError(400, f"choice must be one of {', '.join(CHOICES)}")
    if not author:
        raise ScopeError(401, "a scope choice needs an author")
    now = now or _now()
    view = build_scope_view(registry, slug, **view_kw)
    node = _find_node(view, schema, table)
    prop = node.get("live_proposal")
    kw: dict[str, Any] = {"action": "set", "source": "person", "reason": reason}
    if prop:
        kw.update(proposal_rule=prop["rule"], proposal_choice=prop["choice"],
                  reason=prop["reason"], measured_at=prop.get("measured_at") or "",
                  measured=prop["measured"])
        if prop["choice"] == choice:
            kw.update(action="confirm", source=prop["rule"])
        else:
            kw.update(action="override", source="person")
    _declare_if_needed(registry, slug, author, _view_tree_keys(view), now)
    registry.append_catalogue_scope_event(
        slug, node_kind="table" if table else "schema", schema_name=schema, table_name=table,
        choice=choice, author=author, changed_at=now, **kw)
    return {"state": kw["action"], "node": f"{schema}.{table}" if table else schema}


def confirm_proposal(registry, slug: str, author: str, *, schema: str, table: str = "",
                     now: str | None = None, **view_kw) -> dict:
    node = _find_node(build_scope_view(registry, slug, **view_kw), schema, table)
    if not node.get("live_proposal"):
        raise ScopeError(409, "nothing is proposed for this row now")
    return set_node_choice(registry, slug, author, schema=schema, table=table,
                           choice=node["live_proposal"]["choice"], now=now, **view_kw)


def override_proposal(registry, slug: str, author: str, *, schema: str, table: str = "",
                      now: str | None = None, **view_kw) -> dict:
    node = _find_node(build_scope_view(registry, slug, **view_kw), schema, table)
    prop = node.get("live_proposal")
    if not prop:
        raise ScopeError(409, "nothing is proposed for this row now")
    other = LEAVE_OUT if prop["choice"] == CATALOGUE else CATALOGUE
    return set_node_choice(registry, slug, author, schema=schema, table=table,
                           choice=other, now=now, **view_kw)


def clear_node_choice(registry, slug: str, author: str, *, schema: str, table: str = "",
                      now: str | None = None, **view_kw) -> dict:
    if not author:
        raise ScopeError(401, "a scope choice needs an author")
    node = _find_node(build_scope_view(registry, slug, **view_kw), schema, table)
    if node["explicit"] is None:
        raise ScopeError(409, "this row has no choice to clear")
    registry.append_catalogue_scope_event(
        slug, node_kind="table" if table else "schema", schema_name=schema, table_name=table,
        choice="", action="clear", author=author, changed_at=now or _now())
    return {"state": "cleared", "node": f"{schema}.{table}" if table else schema}


def set_depth(registry, slug: str, author: str, *, depth: str, now: str | None = None,
              **view_kw) -> dict:
    if depth not in DEPTH_IDS:
        raise ScopeError(400, f"depth must be one of {', '.join(DEPTH_IDS)}")
    if not author:
        raise ScopeError(401, "a depth choice needs an author")
    now = now or _now()
    _declare_if_needed(registry, slug, author,
                       _tree_keys(resolve_node_set(registry, slug, view_kw.get("tree"))), now)
    registry.append_catalogue_scope_event(
        slug, node_kind="depth", choice=depth, action="set", author=author, changed_at=now)
    return {"state": "set", "depth": depth}


def redeclare(registry, slug: str, author: str, *, now: str | None = None, **view_kw) -> dict:
    """Declare the scope again: the baseline becomes what the latest survey
    knows now, so "new since your scope was declared" starts counting from here."""
    if not author:
        raise ScopeError(401, "a declaration needs an author")
    keys = _tree_keys(resolve_node_set(registry, slug, view_kw.get("tree")))
    # The implicit baseline a first choice makes is kind "first". The first EXPLICIT declaration is
    # "declare" (the rehearsal of 2026-10-05 saw it recorded as "redeclare" because that implicit
    # baseline already existed); only a declaration after an explicit one is a re-declaration.
    explicit = [b for b in registry.list_catalogue_scope_baselines(slug) if b.get("kind") in ("declare", "redeclare")]
    registry.append_catalogue_scope_baseline(
        slug, baseline=keys, survey_at=keys.get("as_of") or _tree_survey_at(registry, slug), author=author,
        kind="redeclare" if explicit else "declare", declared_at=now or _now())
    return {"state": "declared"}


def set_nodes_choice(registry, slug: str, author: str, *, nodes: list[dict] | None = None,
                     choice: str = "", all_schemas: bool = False, now: str | None = None,
                     **view_kw) -> dict:
    """Bulk choice: `catalogue`, `leave_out`, or `''` (clear) on many schemas at
    once (a node is `{"schema", "table"}`; `all_schemas` means every offered one).

    One event per node, each with the signed-in author: this is the same
    append the one-row door makes, so a proposal under a node is recorded as a
    confirmation or an override exactly as it is for a single row. A bulk choice
    never writes to a TABLE that has its own explicit choice: when it targets a
    schema, tables with their own differing choice are left as they are and
    returned in `differing_tables`, so the tree keeps showing "differs from its
    schema" rather than the bulk choice silently overwriting them.
    The whole request is checked before anything is written."""
    if choice not in (*CHOICES, ""):
        raise ScopeError(400, f"choice must be one of {', '.join(CHOICES)}, or empty to clear")
    if not author:
        raise ScopeError(401, "a scope choice needs an author")
    now = now or _now()
    view = build_scope_view(registry, slug, **view_kw)
    wanted = ([{"schema": s["name"], "table": ""} for s in view["schemas"]] if all_schemas
              else [{"schema": n.get("schema") or "", "table": n.get("table") or ""} for n in (nodes or [])])
    if not wanted:
        raise ScopeError(400, "no schemas were selected")
    targets = []
    for w in wanted:
        targets.append((w, _find_node(view, w["schema"], w["table"])))   # 404 before any write
    written, skipped, differing = [], [], []
    declared = False
    for w, node in targets:
        label = f"{w['schema']}.{w['table']}" if w["table"] else w["schema"]
        if choice == "":
            if node["explicit"] is None:
                skipped.append(label)
                continue
            registry.append_catalogue_scope_event(
                slug, node_kind="table" if w["table"] else "schema", schema_name=w["schema"],
                table_name=w["table"], choice="", action="clear", author=author, changed_at=now)
            written.append(label)
            continue
        kw: dict[str, Any] = {"action": "set", "source": "person", "reason": "bulk choice"}
        prop = node.get("live_proposal")
        if prop:
            kw.update(proposal_rule=prop["rule"], proposal_choice=prop["choice"], reason=prop["reason"],
                      measured_at=prop.get("measured_at") or "", measured=prop["measured"])
            if prop["choice"] == choice:
                kw.update(action="confirm", source=prop["rule"])
            else:
                kw.update(action="override", source="person")
        if not declared:
            _declare_if_needed(registry, slug, author, _view_tree_keys(view), now)
            declared = True
        registry.append_catalogue_scope_event(
            slug, node_kind="table" if w["table"] else "schema", schema_name=w["schema"],
            table_name=w["table"], choice=choice, author=author, changed_at=now, **kw)
        written.append(label)
        if not w["table"]:
            for t in node.get("tables") or []:
                if t["explicit"] and t["explicit"]["choice"] != choice:
                    differing.append({"schema": w["schema"], "table": t["name"],
                                      "choice": t["explicit"]["choice"]})
    return {"state": "bulk", "choice": choice, "written": written, "skipped": skipped,
            "differing_tables": differing}


def _view_tree_keys(view: dict) -> dict:
    """Baseline keys from a built view (its schemas are the non-system tree)."""
    ch = (view.get("sources") or {}).get("chosen") or {}
    return {"schemas": sorted(s["name"] for s in view["schemas"]),
            "tables": sorted(t["key"] for s in view["schemas"] for t in s["tables"]),
            "source": ch.get("kind") or "", "as_of": ch.get("as_of") or ""}
