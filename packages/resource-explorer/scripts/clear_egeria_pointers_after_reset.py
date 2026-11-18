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

What it does (all its writes are in RE's registry; no DDL; no proof row is ever deleted or edited):

  KEEP a pointer that still resolves (owner's decision 2026-11-13, after the 11-11 recovery's dry run found three
        live pointers it would have blanked). Before planning to clear or remove a row, the script reads each of its
        Egeria GUIDs BY GUID, as RE's service account (the daemon identity the re-attach tool uses). Its only
        Egeria calls are those reads; it never writes to Egeria. Per row:
          - every GUID is GONE (Egeria says not found FOR RE'S SERVICE ACCOUNT) -> cleared or removed as below.
            "Gone" is what that account can see: since e9fd817f4c a zone without a control is private, so an
            element in a zone RE's account cannot see answers not found too and its pointer is cleared;
          - any GUID RESOLVES                           -> the row is KEPT: "keep: live, created <time>";
          - any GUID cannot be READ (refused, unreachable, an unexpected answer) -> the row is KEPT: "keep:
            unreadable (<why>): not treated as gone". An unread pointer is never taken for a dead one.
        The kept rows, their reason and what each GUID answered are in the plan, so in its hash: an answer that
        changes between the dry run and the apply refuses the apply like any other change. A resource with a
        kept publish pointer (its asset, sub-resource or report GUID) gets no egeria_reset marker. Checked: the
        pointer columns below, the app_settings GUID keys (egeria.github_source_control_library_guid,
        egeria_server_guid::*, egeria_asset_guid::*) and the cache rows that hold a GUID (the materialized
        architecture rows, survey_definition_cache). Not checked: egeria_linkage_status (it records that a GUID
        was found stale, it is not a pointer RE follows) and the claim/credential settings (their values are not
        GUIDs). If Egeria cannot be reached at all the run REFUSES rather than planning without the reads.

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
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from resource_explorer.catalogue_commit import PROJECT_UNBOUND as UNBOUND_STATUS, _known, _ts  # noqa: E402
# The guards are shared with resource_explorer.egeria_reattach (re-attach by qualifiedName) and live in one module;
# every name is imported here, so `S.<name>` and this script's behaviour are unchanged.
from resource_explorer.registry_guards import (  # noqa: E402,F401
    _TS, ApplyFailed, Refused, _fsync_dir, _group, _missing_table, _nonempty, _rows, activity_guard,
    check_live_schema, check_saved_plan, connect, database_name, parse_utc, parse_who_when, plan_hash,
    registry_url, target_schema, utcnow, write_0600_json,
)

#: Written into every plan file and into its hash. A plan made by another version of this script is refused with
#: its own sentence (not the registry-changed one). Bump it when the plan's shape or meaning changes.
SCRIPT_VERSION = "2026-11-13.2"
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

#: What a by-GUID read of one pointer answered. GONE is Egeria's own not-found; anything else that is not a
#: readable element is UNREADABLE, and both LIVE and UNREADABLE keep the row (absence-as-answer).
LIVE, GONE, UNREADABLE = "live", "gone", "unreadable"
#: app_settings keys whose VALUE is an Egeria GUID (checked like any pointer). The other removed keys hold claims,
#: credential slugs or confirmation flags, not GUIDs, and are removed as before.
SETTING_GUID_KEYS = SETTING_KEYS
SETTING_GUID_PREFIXES = ("egeria_server_guid::", "egeria_asset_guid::")
#: Cache rows that hold an Egeria GUID: a live one is kept like a pointer. egeria_linkage_status is deliberately
#: absent (it records that a GUID was found stale; it is not a pointer RE follows).
DELETE_POINTERS = {"architecture_materialized_blueprints": ["guid"], "architecture_materialized_components": ["guid"],
                   "architecture_materialized_ports": ["guid"], "survey_definition_cache": ["process_guid"]}
#: Tables whose kept row means the RESOURCE's publish is still in Egeria, so it gets no egeria_reset marker
#: (table -> the column naming the resource).
PUBLISH_POINTER_RESOURCE = {
    "projects": "slug", "databases": "slug", "file_systems": "slug", "sub_resources": "resource_slug",
    "database_surveys": "database_slug", "filesystem_surveys": "filesystem_slug",
    "project_egeria_surveys": "project_slug", "project_published_analyses": "project_slug",
    "project_published_annotation_types": "project_slug",
}

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
    "project_egeria_surveys": ["project_slug", "published_at", "egeria_report_guid"],
    "project_published_analyses": ["project_slug", "published_at"],
    "project_published_annotation_types": ["project_slug", "published_at"],
}
#: Publish-state tables that record WHEN a publish happened (database_surveys and filesystem_surveys keep only
#: surveyed_at, so they cannot say). A row published after --reset-at is evidence of a post-reset publish even
#: when no proof row exists: the survey-run publisher (surveyors/egeria_publisher.py, the path a scheduled
#: RepoRefreshSurvey takes) records the asset GUID and project_egeria_surveys but writes no
#: catalogue_commit_proofs row; only the Publish button's read-back path (repo_publish) does.
PUBLISHED_AT_TABLES = ("project_egeria_surveys", "project_published_analyses", "project_published_annotation_types")

NODE_DATABASE, NODE_REPO, NODE_OTHER = "database", "repo_report", "resource"


FINALISE_GROUP = "activity_log:finalise"


# ── schema check (no ProjectRegistry: opening one runs the schema ensure, and this script is DDL-free) ───────

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
    from resource_explorer.registry_guards import check_columns
    check_columns(conn, want)
    return skipped


# ── which pointers still resolve (by-GUID reads in Egeria, as RE's service account) ────────────────────────

class GuidLiveness:
    """What Egeria says about one GUID, read once per distinct GUID in a run. `read` is a by-GUID element read
    (pyegeria's `get_metadata_element_by_guid`). Each answer is a small JSON-able dict, so it goes into the plan
    and its hash: {"state": live|gone|unreadable, "type", "qualified_name", "created", "why"}."""

    def __init__(self, read):
        self._read = read
        self._seen: dict[str, dict] = {}

    def __call__(self, guid: str) -> dict:
        if guid not in self._seen:
            self._seen[guid] = self._answer(guid)
        return self._seen[guid]

    @property
    def answers(self) -> dict[str, dict]:
        return dict(self._seen)

    def _answer(self, guid: str) -> dict:
        from resource_explorer.egeria_absence import ABSENT, PRESENT, is_absent

        try:
            answer = self._read(guid)
        except Exception as exc:  # noqa: BLE001 - classified, never swallowed as a miss
            if is_absent(exc) == ABSENT:
                return {"state": GONE}
            return {"state": UNREADABLE, "why": f"by-GUID read raised {type(exc).__name__}"}
        state = is_absent(answer)
        if state == ABSENT:
            return {"state": GONE}
        if state != PRESENT or not isinstance(answer, dict):
            return {"state": UNREADABLE, "why": "by-GUID read gave an answer of an unexpected shape"}
        from resource_explorer.egeria_reattach import _qualified_name_of

        header = answer.get("elementHeader") if isinstance(answer.get("elementHeader"), dict) else answer
        typ = (header.get("type") or {}).get("typeName", "") if isinstance(header.get("type"), dict) else ""
        created = (header.get("versions") or {}).get("createTime", "") if isinstance(header.get("versions"), dict) else ""
        return {"state": LIVE, "type": str(typ or ""), "qualified_name": _qualified_name_of(answer),
                "created": str(created or "")}


def default_reads() -> GuidLiveness:
    """The reads as RE's own daemon identity (the identity the re-attach tool reads with): no person's session."""
    from resource_explorer.egeria_reattach import daemon_reads

    return GuidLiveness(daemon_reads().expert.get_metadata_element_by_guid)


def keep_reason(answers: dict[str, dict], reset_at: str) -> str:
    """The plan's one-line reason for a kept row, from what each of its GUIDs answered (`answers`: col -> answer).
    LIVE wins over UNREADABLE in the wording; both keep the row."""
    live = [a for a in answers.values() if a["state"] == LIVE]
    if live:
        created = sorted(a.get("created", "") for a in live)[0]
        if not created:
            return "keep: live, created at an unknown time"
        before = _known(created) and _ts(created) < _ts(reset_at)
        return f"keep: live, created {created}" + (" (BEFORE the reset: check --reset-at)" if before else "")
    why = next(a.get("why", "") for a in answers.values() if a["state"] == UNREADABLE)
    return f"keep: unreadable ({why}): not treated as gone"


class _Reads:
    """The liveness reads, opened on first use: a plan with no pointer to check never contacts Egeria."""

    def __init__(self, factory):
        self._factory = factory
        self._reads = None

    def __call__(self, guid: str) -> dict:
        if self._reads is None:
            try:
                self._reads = self._factory()
            except Exception as exc:  # noqa: BLE001 - named, and nothing is planned without the reads
                raise Refused("cannot read Egeria to check which pointers still resolve "
                              f"({type(exc).__name__}); nothing is planned, because the clear never treats an "
                              "unread pointer as gone") from None
        return self._reads(guid)


def split_live(rows: list[dict], cols: list[str], reads, reset_at: str) -> tuple[list[dict], list[dict]]:
    """(rows to change, rows kept). A row is kept when any of its non-empty GUIDs in `cols` is LIVE or UNREADABLE;
    a kept entry carries the row, what each GUID answered and the reason. A relationship GUID (doc_sources) goes
    with its row's element GUID: the relationship cannot outlive the element it links."""
    change, kept = [], []
    element_cols = [c for c in cols if "relationship" not in c]
    rel_cols = [c for c in cols if "relationship" in c]
    for r in rows:
        answers = {c: {"guid": str(r[c]), **reads(str(r[c]))} for c in element_cols if r.get(c) not in (None, "")}
        if not answers:
            # A relationship GUID is not an element: a by-GUID element read would say "not found" for a live
            # one. With no element GUID beside it to judge the row by, it is unread, so kept.
            answers = {c: {"guid": str(r[c]), "state": UNREADABLE,
                           "why": "a relationship GUID with no element GUID beside it: not read"}
                       for c in rel_cols if r.get(c) not in (None, "")}
        if any(a["state"] != GONE for a in answers.values()):
            kept.append({"row": r, "pointers": answers, "reason": keep_reason(answers, reset_at)})
        else:
            change.append(r)
    return change, kept


def _kept_resources(conn, action: dict) -> set[str]:
    """The resources (slugs) whose publish pointer this action keeps."""
    col = PUBLISH_POINTER_RESOURCE.get(action["table"])
    if not col or not action.get("kept"):
        return set()
    out = set()
    for k in action["kept"]:
        r = k["row"]
        if col in r:
            out.add(r[col])
            continue
        where = " AND ".join(f"{key} = ?" for key in action["keys"])
        got = _rows(conn, f"SELECT {col} FROM {action['table']} WHERE {where}", [r[key] for key in action["keys"]])
        out.update(g[col] for g in got)
    return out


# ── time ─────────────────────────────────────────────────────────────────────

def human_when(reset_at: str) -> str:
    return reset_at[:16].replace("T", " ") + " UTC"


# ── the plan ─────────────────────────────────────────────────────────────────

def build_plan(conn, db_name: str, reset_at: str, old_id: str, new_id: str, schema: str = "main",
               skipped: list | None = None, live_schema: str = "main", reads=None) -> dict:
    """`reads`: GUID -> what Egeria answers (GuidLiveness); default RE's service account, opened on first use."""
    reads = reads if reads is not None else _Reads(default_reads)
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
        rows, kept = split_live(rows, [c for c in setcols if "guid" in c], reads, reset_at)
        actions.append({"id": f"clear:{table}{suffix}", "kind": "clear_columns", "table": table, "keys": keys,
                        "set": setcols, "trigger": trig, "extra": extra, "rows": rows, "count": len(rows),
                        "kept": kept})
    for table, keys in DELETE_SPECS:
        # the whole row is the snapshot for a removed cache row (these tables hold no credentials)
        full = _rows(conn, f"SELECT * FROM {table} ORDER BY {', '.join(keys)}")
        full, kept = split_live(full, DELETE_POINTERS.get(table, []), reads, reset_at)
        actions.append({"id": f"delete:{table}", "kind": "delete_rows", "table": table, "keys": keys,
                        "rows": full, "count": len(full), "kept": kept})
    keyrows = _rows(conn, "SELECT key, value FROM app_settings ORDER BY key")
    hit = [r for r in keyrows if r["key"].startswith(SETTING_PREFIXES) or r["key"] in SETTING_KEYS]
    guid_keys = [r for r in hit if r["key"] in SETTING_GUID_KEYS or r["key"].startswith(SETTING_GUID_PREFIXES)]
    _gone, kept = split_live(guid_keys, ["value"], reads, reset_at)
    kept_keys = {k["row"]["key"] for k in kept}
    hit = [r for r in hit if r["key"] not in kept_keys]
    actions.append({"id": "delete:app_settings", "kind": "delete_rows", "table": "app_settings", "keys": ["key"],
                    "rows": hit, "count": len(hit), "kept": kept})
    kept_resources: dict[str, list[str]] = {}
    for a in actions:
        for slug in _kept_resources(conn, a):
            kept_resources.setdefault(slug, []).append(a["table"])

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
    # Publishes AFTER the reset, from the publish-state rows' own times: the proof rows alone miss a publish made
    # by the survey-run publisher (it writes none). Reported beside the proofs' list; the liveness reads above are
    # what actually keeps such a publish's pointers.
    published_after: dict[tuple[str, str], str] = {}
    for table in PUBLISHED_AT_TABLES:
        for r in _rows(conn, f"SELECT project_slug, published_at FROM {table} "
                             "WHERE published_at IS NOT NULL AND published_at <> ''"):
            if _known(r["published_at"]) and _ts(r["published_at"]) > _ts(reset_at):
                key = (r["project_slug"], table)
                if key not in published_after or _ts(r["published_at"]) > _ts(published_after[key]):
                    published_after[key] = r["published_at"]
    markers, after, marker_skipped = [], [], []
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
        if slug in kept_resources:
            marker_skipped.append({"slug": slug, "reason": "a publish pointer still resolves in Egeria (kept in "
                                   + ", ".join(sorted(set(kept_resources[slug]))) + "): its publish is not gone"})
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
            "slugs_with_proofs_after_reset": after, "markers_skipped": marker_skipped,
            "slugs_published_after_reset": [{"slug": slug, "table": table, "published_at": at}
                                            for (slug, table), at in sorted(published_after.items())],
            "liveness": _liveness_summary(actions)}
    # the markers go FIRST (see ORDER in the module docstring)
    plan["actions"].sort(key=lambda a: a["kind"] != "write_markers")
    plan["hash"] = plan_hash(plan)
    return plan


def _liveness_summary(actions: list[dict]) -> dict:
    """How many distinct GUIDs were read and what they answered; who read them."""
    seen: dict[str, str] = {}
    for a in actions:
        for k in a.get("kept", []):
            for p in k["pointers"].values():
                seen[p["guid"]] = p["state"]
    kept = sum(len(a.get("kept", [])) for a in actions)
    return {"read_as": "RE's service account (daemon identity)", "rows_kept": kept,
            "guids_live": sum(1 for v in seen.values() if v == LIVE),
            "guids_unreadable": sum(1 for v in seen.values() if v == UNREADABLE)}


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
        if a.get("kept"):
            out.append(f"{'keep':<10}{a['table']:<44}{len(a['kept']):>6}  still resolves or unreadable in Egeria: "
                       "left as it is (listed below)")
    out.append("")
    kept = [(a["table"], k) for a in plan["actions"] for k in a.get("kept", [])]
    live = plan.get("liveness", {})
    out.append(f"pointers checked in Egeria by GUID, as {live.get('read_as', 'RE')}: {len(kept)} rows KEPT "
               f"({live.get('guids_live', 0)} GUIDs live, {live.get('guids_unreadable', 0)} unreadable); "
               "every other GUID listed above answered not found for RE's service account "
               "(an element in a zone it cannot see reads the same way)")
    for table, k in kept[:60]:
        ident = ", ".join(f"{key}={k['row'][key]}" for key in sorted(k["row"]) if "guid" not in key and key != "value"
                          and key in ("slug", "id", "key", "entity_slug", "entity_type", "user_id", "technology_type"))
        what = "; ".join(f"{c} {p['guid']}" + (f" = {p.get('type', '')} {p.get('qualified_name', '')}".rstrip()
                                                 if p["state"] == LIVE else f" = {p['state']}")
                         for c, p in k["pointers"].items())
        out.append(f"  KEPT {table} [{ident}] {k['reason']} · {what}")
    for m in plan.get("markers_skipped", []):
        out.append(f"  NO MARKER for {m['slug']}: {m['reason']}")
    if plan.get("skipped_tables"):
        out.append("SKIPPED (table does not exist yet): " + ", ".join(plan["skipped_tables"]))
    out.append(f"dead before the reset · untouched: {len(plan['dead_outbox_untouched'])} outbox rows "
               f"{plan['dead_outbox_untouched'][:20]}")
    if plan["slugs_with_proofs_after_reset"]:
        out.append("WARNING proofs newer than the reset time exist for: "
                   + ", ".join(plan["slugs_with_proofs_after_reset"]) + " (published after the reset? check --reset-at)")
    if plan.get("slugs_published_after_reset"):
        out.append("WARNING publish rows newer than the reset time exist (a publish with no proof row, e.g. a scheduled "
                   "survey's): " + ", ".join(f"{p['slug']} ({p['table']} {p['published_at']})"
                                             for p in plan["slugs_published_after_reset"][:20])
                   + "; their pointers are kept only if they still resolve (KEPT above)")
    out.append("kept untouched: every proof row, verdicts, scope events, enrichment, journal, groups, "
               "native_survey_annotations, step_runs, survey history, 'done' outbox rows, and every pointer "
               "that still resolves (KEPT above)")
    out.append(f"plan hash: {plan['hash']}")
    return "\n".join(out)


# ── guards ───────────────────────────────────────────────────────────────────

def parse_cleared_by(token: str, now: datetime) -> tuple[str, str]:
    return parse_who_when(token, now, "--cleared-by")


# ── apply ────────────────────────────────────────────────────────────────────

def apply_plan(conn, plan: dict, cleared_by: str, snapshot_path: Path, now: datetime) -> dict:
    """Write the snapshot, then change the registry one table group at a time. Returns what was done."""
    write_0600_json(snapshot_path, {"snapshot_of": "rows changed by clear_egeria_pointers_after_reset", "plan": plan})

    from resource_explorer.run_reconciler import process_identity
    act_id = str(uuid.uuid4())
    ts = now.isoformat()
    detail = {"cleared_by": cleared_by, "plan_hash": plan["hash"], "snapshot": snapshot_path.name,
              "skipped_tables": plan.get("skipped_tables", []), "_runner": process_identity(),
              "kept_live": plan.get("liveness", {}).get("rows_kept", 0),
              "markers_skipped": [m["slug"] for m in plan.get("markers_skipped", [])]}
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

def run(argv: list[str], out=print, now: datetime | None = None, reads_factory=None) -> int:
    """`reads_factory`: builds the GUID liveness reads (default: RE's service account); a test passes a fake."""
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
                              live, reads=_Reads(reads_factory or default_reads))
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
            check_saved_plan(saved, script_version=SCRIPT_VERSION, schema=schema, live=live, name=name,
                             plan_hash_arg=args.plan_hash, now=now, max_age_minutes=args.max_plan_age_minutes)
            if plan["hash"] != args.plan_hash:
                raise Refused("the registry changed since the dry run; run the dry run again "
                              f"(the plan recomputed now has hash {plan['hash'][:12]}, the dry run's was "
                              f"{args.plan_hash[:12]}; --reset-at and the collection ids must also be the same, "
                              "and so must what Egeria answered for each pointer)")
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
