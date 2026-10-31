"""The catalogue commit for a database, by schema-kind targets (slice B).

`BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md`. Slice A (`catalogue_scope.py`)
keeps the scope: a declared, signed, dated choice per schema and table. This
module compiles that record into Egeria's lever every time it is pressed, and
shows what Egeria actually holds afterwards.

**The door is the schema-kind catalog target** (owner's decision, 2026-10-05).
One DeployedDatabaseSchema per chosen schema, created from the template and
attached to the JDBC cataloguer with no configuration lists. Never the database
element and never the server: a database-kind target catalogues every schema's
tables directly under the database whatever its lists say.

Three mechanisms run, in this order, and the manifest names all three:

1. RE publishes the server and database elements (description and version
   supplied, the server's own description, so no `~{...}~` placeholder remains),
   then Owner from Context. **RE writes no ZoneMembership unless the deployment
   configured `EXPLORER_PUBLISH_ZONES`** (the rehearsal of 2026-10-05 found that a
   zone written first locks the service identity, Egeria's survey engine and the
   cataloguer out of the database element, and RE cannot undo it). With the setting
   configured the zone is the LAST write, after the targets, the survey submission,
   the refresh and the owner, is read back, and a refusal is reported in Egeria's
   words and never retried. Without it the element's zones are Egeria's defaults,
   shown as a read-back fact: "zones: <list> · set by Egeria".
2. Egeria's cataloguer creates the schemas' tables and columns on its next
   refresh. RE attaches one schema-kind target per chosen schema (reading the
   targets first, never attaching twice).
3. Egeria's survey measures, limited to the chosen schemas by the request
   parameter `includeSchemaNames` (never the connection configuration, which
   the service ignores; a schema whose name contains a comma cannot be scoped).

**Every state word is derived from a persisted proof row.** The tree's per-node
state comes from `catalogue_commit_proofs` (what a read-back saw) and from the
`egeria_outbox` rows this commit queued (queued, failed), never from the branch
the code took (the status-words rule). `derive_commit_state` is the one
function that does it, so the route, the header marker and the tests cannot
disagree.

Leaving a schema out has two forms, chosen per schema from a relationships read
and said in the preview before the press: soft-delete when nothing hangs off
it, archive (the delete endpoint with deleteMethod ARCHIVE; the `/archive`
endpoint answers 500) when something does. A failed read is "couldn't check
what hangs off it", never nothing. Every delete RE performs is per element,
leaf first, `forLineage` true, never a cascade.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from resource_explorer.catalogue_gateway import (
    ARCHIVE, CATALOG_SCHEMA_ACTION_TYPE, FAILED_ACTION_STATUSES, RUNNING_ACTION_STATUSES, SOFT_DELETE, CatalogueGateway, GatewayError, like_matches,
    schema_qualified_name, server_name_for)
from resource_explorer import catalogue_gateway as gw
from resource_explorer import endpoint_identity
from resource_explorer.catalogue_scope import CATALOGUE, LEAVE_OUT, current_schema_choice, md
from resource_explorer.curate_plan import NEEDS_READ_BACK

log = logging.getLogger(__name__)

#: A seam for tests: how the attach waits for Egeria's own action to put the target in the list.
_sleep = time.sleep
CATALOG_POLLS = 4
CATALOG_POLL_SECONDS = 2

# ── words the screen uses (one place, so tests and the UI pin the same text) ──

# S19 (owner ruling 2026-10-14, replacing "can't be re-included until Egeria restores archived elements"): Egeria
# renames what it archives to `<qn>_archivedOn_<date>`, so the original name is free and a re-include CREATES A NEW
# element. RE looks for that archived sibling before every create that would re-include it, and creates only on an
# explicit confirmation that names the sibling's GUID.
# The words and the decision live in catalogue_gateway (the survey path uses them too); repeated names here.
REINCLUDE_TAIL = gw.REINCLUDE_TAIL
S19_SENTENCE = REINCLUDE_TAIL
SIBLING_UNREADABLE = gw.SIBLING_UNREADABLE
SIBLING_CHANGED = gw.SIBLING_CHANGED
NEEDS_CONFIRMATION = gw.NEEDS_CONFIRMATION
reinclude_words = gw.reinclude_words
WHOLE_SCHEMAS_LINE = "Egeria adds whole schemas · table choices are kept for when it can"
SURVEY_LINE = "Egeria's survey is limited to your chosen schemas"
#: Each commit starts an Egeria survey (an engine action) whose own report is separate from RE's survey report, where RE's
#: measures go. Said wherever the survey is named, so a "0 annotations" report at the top of Egeria's list is not read as RE's.
ENGINE_REPORT_WORDS = "its own report is separate from RE's survey report, where RE's measures went, and fills when Egeria's survey finishes"
LINGERING_LINE = ("Egeria's cataloger still lists this schema until its connector restarts "
                  "· nothing is recreated")
# The archive-cascade block (ISSUE-117) lives in catalogue_gateway (`issue_117_blocked(slug, scope=)`, `ISSUE_117_WORDS`).
# Since 2026-10-23 it is OFF for the leave-out ARCHIVE (`gw.LEAVE_OUT_ARCHIVE`) unless RE_ISSUE_117_BLOCK re-arms it, and
# ON for a leave-out's soft delete; RE_ISSUE_117_BLOCK_OFF names a fresh override FOR ONE DATABASE SLUG. Read at call
# time so one switch governs the preview, the outbox handler and the real gateway. Every guard passes the slug it acts
# for, and only a guard on an ARCHIVE of a leave-out passes the scope.
CATALOGED_STATES = ("catalogued", "attached_waiting", "queued", "sent", "failed", "restored")
CANT_CHECK = "couldn't check what hangs off it"
IN_USE = "in use by a running survey · wait or cancel"
WAIT = "wait or cancel"
#: An engine action's `activityStatus` that does NOT hold a schema: it is over (terminal). Anything
#: else, a running status, a value never seen, or NO status at all (the live ActionTarget states none
#: for about 1.5 s while an action runs), holds it: a leave-out never deletes under an unstated action.
FINISHED_ACTIVITY = frozenset({"COMPLETED", "FAILED", "CANCELLED", "INVALID"})
WORKER_WORDS = "waiting for a worker"
OWNER_REFUSED = "owner set by Egeria's source · can't change from RE"
NO_SCOPE_SENTENCE = "no scope declared · nothing published"
NOT_COMMITTED_HEADER = "Saved in Resource Explorer · not yet published to Egeria"

# ── proof kinds (rows in catalogue_commit_proofs) ────────────────────────────

P_DATABASE = "database_published"
P_ZONES = "zones_set"                 # RE wrote a zone (only when EXPLORER_PUBLISH_ZONES is configured)
P_ZONES_READ = "zones_read"           # what Egeria says the element's zones are, read back
P_ATTACH_REQUESTED = "attach_requested"   # Egeria's attach action was initiated (its engine action guid is kept)
P_SURVEY_RESULT = "survey_result"     # a settled native survey: a report with annotations, or a failed action
P_OWNER = "owner_result"
P_REPORT = "report_published"
P_TARGET = "target_attached"
P_ELEMENTS = "elements_read_back"
P_DETACHED = "target_detached"
P_REMOVED = "removed"
P_ARCHIVED = "archived"
#: A restore script (never this module) appends this row by KIND NAME through append_catalogue_commit_proof when an
#: archived element has been restored in Egeria: `element_guid` is the restored element, `detail["from_guid"]` the
#: guid of the element it replaced, `detail["note"]` any free text (all in detail_json: no DDL).
P_RESTORED = "restored"
#: One row per destructive call RE sends (archive, soft delete, detach), written AFTER the call returns, is
#: refused by the archive-cascade block, or raises. A record of a call, never of a state: NOT in STATE_PROOFS.
P_DELETE_CALL = "delete_call"
#: Architect ruling 2026-10-25 (1): one row per archive call Egeria ACCEPTED, written the moment the call returns and
#: BEFORE any read-back, filed under its schema (`element_guid`, `qualified_name` and `detail["type"]` say which
#: element; `detail["planned"]` is the size of the walk). The read-back appends a newer row for the same element and
#: outbox row; an element's state is its newest row's `detail["read_back"]`: pending | confirmed | failed (with
#: `detail["reason"]`). A record of an element, never of the schema's state: NOT in STATE_PROOFS.
P_ELEMENT_ARCHIVED = "element_archived"
RB_PENDING, RB_CONFIRMED, RB_FAILED = "pending", "confirmed", "failed"
#: Ruling (2): the words after an accepted destructive write whose read-back did not confirm it.
DO_NOT_PRESS = "do not press again · Refresh re-reads"
P_LEAVE_OUT_BLOCKED = "leave_out_blocked"   # a leave-out that was refused (ISSUE-117): kept, not sent
KEPT_NOT_SENT = "kept, not sent · archive-cascade block"
#: Architect ruling 2026-10-23: a schema whose state is "restored" passes NO lifted scope (restored trees are unproven).
RESTORED_BLOCKED = ("archive blocked · this schema was restored after the 2026-10-06 incident; archiving restored "
                    "trees is not proven")
#: Architect ruling (3) 2026-10-23: the preview's GUID set rides in the payload; the apply walks again and refuses on
#: any difference (either direction), and on a payload with no set. A refusal of a request, never a state.
P_LEAVE_OUT_PLAN_CHANGED = "leave_out_plan_changed"
PLAN_CHANGED = "the plan changed · preview again"
#: Architect ruling 2026-10-23 (7): a re-used annotation is anchored to the report that first created it, so archiving
#: that report would take what later reports still link. Refused in `_send_destructive` whatever scope or override.
SURVEY_REPORT_TYPE = "SurveyReport"
SURVEY_REPORT_REFUSED = ("RE never archives or deletes an RE SurveyReport; a retirement, if ever, detaches "
                         "ReportedAnnotation links and leaves the report")
#: Brief Z2: a schema this commit did NOT write because the person may not change it (Egeria's zones, emulated by
#: `workflows.curate.curation_access`). A record of a refusal, never a state: NOT in STATE_PROOFS.
P_NOT_PERMITTED = "not_permitted"
NOT_PERMITTED_WORDS = "not permitted"
P_SURVEY = "survey_started"
P_CONNECTOR = "connector_read"
P_READ_FAILED = "read_failed"
#: A schema element RE found after Egeria answered a create error, and verified by its GUID (its
#: DataSetContent link to the database AND its ResourceConnection). Context, never a state: the
#: page's state words still come from the target / elements rows; this row only changes the
#: first sentence, "adopted after a create error · <Egeria's sentence>". No DDL: the proof column is free text.
P_ADOPTED = "create_error_adopted"
#: One marker per resource, appended by scripts/clear_egeria_pointers_after_reset.py after the owner reset
#: Egeria's metadata database. Its `read_at` is the reset time. Every proof row read BEFORE it is history of
#: what was once in Egeria and proves nothing about now; the screen says so, from this row, and counts 0 in
#: Egeria until a later proof says otherwise. `detail` carries `text` ("Egeria reset <when> · old → new").
P_EGERIA_RESET = "egeria_reset"
#: D1-a: what Egeria holds for this database's endpoint (every family) and the re-anchor plan; a read, never a write.
P_ENDPOINT = endpoint_identity.PROOF_KIND
#: D1-b: the database-level press (design §13); lists every schema's outcome and every relationship it added.
P_REANCHOR = endpoint_identity.APPLIED_KIND


_READ_BACK_AT = re.compile(r"read back (\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?")


def _press_summary(row: dict | None) -> dict | None:
    """The newest D1-b press, as Contents shows it on the plan's database row: its words, time, who, its counts and
    the schemas it could not link."""
    if row is None:
        return None
    d = row.get("detail") or {}
    # the press stored "read back YYYY-MM-DD HH:MM"; the page has one time form, `_stamp`'s "MM-DD HH:MM" (W25)
    words = _READ_BACK_AT.sub(lambda m: f"read back {_stamp(m.group(1))}", d.get("words", ""))
    return {"at": row["read_at"], "by": row.get("recorded_by") or "", "words": words,
            "counts": d.get("counts") or {}, "members_after": d.get("members_after"),
            "failed": [{"schema": o.get("schema", ""), "error": o.get("error", "")}
                       for o in d.get("outcomes") or [] if o.get("outcome") == endpoint_identity.FAILED],
            "undo": d.get("undo", "")}


#: The status value an Egeria project context (entity_egeria_project_context.status, investigations.
#: egeria_project_status) takes after a reset, when the Egeria project it named is gone. A status VALUE, never
#: the words: the words are display text only. It is "not answered" exactly as `unset` is, so a publish is
#: gated and offers the same two choices (bind a project, or publish without one).
PROJECT_UNBOUND = "unbound"
PROJECT_UNBOUND_WORDS = "unbound by reset \u00b7 rebind to recreate"
PROJECT_UNANSWERED = ("unset", PROJECT_UNBOUND)
RESET_WORDS = "published earlier · Egeria was reset · not in Egeria now"


def pre_reset_words(reset_at) -> str:
    """The words for a proof row older than the latest Egeria reset (architect ruling (b), 2026-10-23): "published
    before the <date> reset · not in Egeria now", the ◌ lane, never ✓. RESET_WORDS when the reset time cannot be
    read (the claim stays, without a date)."""
    day = _ts(reset_at)[:10]
    return f"published before the {day} reset · not in Egeria now" if day else RESET_WORDS


def pre_reset_sent_words(reset_at) -> str:
    """The words for a row SENT to Egeria and never read back before the latest reset (fix round 1): it was never
    published, so never "published before": "sent before the <date> reset · never confirmed"."""
    day = _ts(reset_at)[:10]
    return f"sent before the {day} reset · never confirmed" if day else "sent before Egeria was reset · never confirmed"


def latest_recorded_reset(registry) -> str:
    """The latest row of `egeria_resets` ('' when none, or for a registry without the reader: a test double).

    A read that FAILS is logged and raised, like `get_egeria_reset_at` (fix round 1): swallowing it would bring
    ✓ back silently for every proof row older than a real reset."""
    fn = getattr(registry, "latest_egeria_reset_at", None)
    if fn is None:
        return ""
    try:
        return fn() or ""
    except Exception:
        log.exception("could not read the record of Egeria resets (egeria_resets)")
        raise


def merge_recorded_reset(registry, marker: dict | None, rows: list[dict]) -> dict | None:
    """The reset a derivation splits its proof rows at: the newer of the resource's own `egeria_reset` marker and
    the latest recorded reset (`egeria_resets`), as a marker-shaped dict. The recorded reset is used only when some
    row in `rows` was read before it: a resource with nothing before the reset reads exactly as it did (no reset
    claim for something that was never in Egeria)."""
    rec = latest_recorded_reset(registry)
    if not _known(rec) or (marker is not None and _ts(marker.get("read_at")) >= _ts(rec)):
        return marker
    if not any(_known(r.get("read_at")) and _ts(r.get("read_at")) < _ts(rec) for r in rows):
        return marker
    return {"id": 0, "proof": P_EGERIA_RESET, "read_at": rec, "node_kind": "", "element_guid": "",
            "detail": {"text": f"Egeria reset {_ts(rec)[:10]} · recorded"}}
#: The schema states that mean "Egeria holds it right now" (read back or attached), for the header's count.
IN_EGERIA_STATES = ("catalogued", "attached_waiting", "restored")

#: Proofs that decide a schema's state in Egeria. Anything else is context.
READ_SUCCESS_PROOFS = (P_ZONES_READ, P_ELEMENTS, P_CONNECTOR, P_DATABASE, P_RESTORED)

STATE_PROOFS = (P_ATTACH_REQUESTED, P_TARGET, P_ELEMENTS, P_REMOVED, P_ARCHIVED, P_RESTORED)

#: The curation record's steps, in the manifest's order.
#: `zone_membership` is LAST among the writes (and absent unless zones are configured): a zone
#: written earlier locks the identity out of the element for every step that follows.
STEPS_DB = ("publish_elements", "owner", "schema_targets", "leave_outs", "survey_report",
            "survey", "refresh", "zone_membership", "read_back")

KIND_ATTACH = "catalogue_schema_attach"
KIND_LEAVE_OUT = "catalogue_schema_leave_out"

#: Relationships that are the cataloguer's own structure, not something that
#: hangs off a schema. Everything NOT in this set counts as hanging off it, so a
#: relationship type this list has never heard of errs towards ARCHIVE (which
#: keeps it) and never towards a soft delete (which removes it). The owner's gate
#: confirms the list against a live schema with a term assignment.
STRUCTURAL_RELATIONSHIPS = frozenset({
    "CatalogTarget", "DataSetContent", "Schema", "AttributeForSchema",
    "NestedSchemaAttribute", "SchemaTypeOption", "LinkedType", "Anchors",
    "ConnectionToAsset", "CapabilityAssetUse", "AssetConnection", "ReportSubject",
    "TemplateSource", "SourcedFrom", "ResourceList",
    # An engine action that targets a schema is Egeria's own machinery (the survey), never
    # something a person attached (architect, 2026-10-05). It never makes a leave-out an archive.
    "ActionTarget",
    # Machinery RE's own template creates for the element and anchors to it (the connection graph
    # of every template-made schema, rehearsal 2 step 4), and the cataloguer's own link from a schema
    # to its schema type (`RelationalDatabaseCataloguer.java:444-448`: `Schema`, parent at end 1).
    # `DataFlow` is deliberately NOT here: lineage someone asserted hangs off the schema (archive).
    "ConnectToEndpoint", "ConnectionConnectorType", "EmbeddedConnection", "ResourceConnection", "Schema",
})

HANGS_OFF_WORDS = {
    "SemanticAssignment": ("term assignment", "term assignments"),
    "LineageMapping": ("lineage mapping", "lineage mappings"),
    "DataClassAssignment": ("data class assignment", "data class assignments"),
    "DataClassComposition": ("data class composition", "data class compositions"),
    "DataFlow": ("lineage link", "lineage links"),
    "ImplementedBy": ("implementation link", "implementation links"),
}


class CommitBlocked(Exception):
    """The commit cannot be pressed. `status` is the HTTP code, `reasons` say why."""

    def __init__(self, status: int, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.reasons = reasons or []


class SchemaRefused(Exception):
    """A schema that cannot be (re-)included. Carries the sentence the row reads."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _ts(iso) -> str:
    """THE one timestamp normaliser (the cleanup script imports it; there is no other). A comparable string:
    UTC, 'T'-separated, to the second, from a PARSED value, so 'Z', '+00:00' or another offset, a space
    separator and 3- or 6-digit fractions all order correctly against each other. A naive value is read as
    UTC (every writer here uses datetime.utcnow(); the browser reads naive stamps the same way). One that
    cannot be parsed is UNKNOWN: '' (it sorts before every real time, and `_known()` says so), never a
    string that could sort after one and win."""
    text = str(iso or "").strip()
    if not text:
        return ""
    t = text.replace(" ", "T", 1)
    if t[-1] in "Zz":
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return ""
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d.isoformat(timespec="seconds")


def _known(iso) -> bool:
    """True when the value parses to a time. An unknown time never makes an earlier-than or since claim."""
    return bool(_ts(iso))


def _hm(iso: str) -> str:
    s = str(iso or "")
    return s[11:16] if len(s) >= 16 else ""


def _stamp(iso: str) -> str:
    """`2026-10-05T09:12:44` -> `10-05 09:12`."""
    return f"{md(iso)} {_hm(iso)}".strip()


# ── names ────────────────────────────────────────────────────────────────────

def names_for(db_entity) -> dict:
    server = server_name_for(db_entity)
    return {"server": server, "database": db_entity.database_name}


def schema_qn(db_entity, schema: str) -> str:
    return schema_qualified_name(server_name_for(db_entity), db_entity.database_name, schema)


def target_name(db_entity, schema: str) -> str:
    """The catalog target's name, as the scratch runs named theirs."""
    return f"{db_entity.database_name}_schema_{schema}"


# ── the `_` and `%` collision check ──────────────────────────────────────────

def wildcard_collisions(view: dict, schema_names: list[str]) -> list[dict]:
    """Where Egeria's listing for a chosen name would also return another name.

    The cataloguer hands real names to JDBC's metadata calls as patterns, so a
    schema `a_b` also returns `aXb`'s tables, and a table `x_y` also returns
    `xZy`'s columns (the scratch runs confirmed both, behind a schema-kind
    target too). RE sees every name in its inventory, so it says so BEFORE
    attaching, and the commit stays disabled while one stands. The only way out
    is to leave the chosen schema out, because Egeria catalogues whole schemas.

    Compared against the offered (non-system) schemas and, for tables, the other
    tables of the same schema; the system schemas' names are not in the tree.
    """
    chosen = set(schema_names)
    all_schemas = [s["name"] for s in view.get("schemas") or []]
    out: list[dict] = []
    for s in view.get("schemas") or []:
        if s["name"] not in chosen:
            continue
        name = s["name"]
        if "_" in name or "%" in name:
            for other in all_schemas:
                if other != name and like_matches(name, other):
                    out.append({"kind": "schema", "schema": name, "name": name, "other": other,
                                "text": f"⚠ Egeria's listing for {name} would also return {other}"})
        tnames = [t["name"] for t in s.get("tables") or []]
        for t in tnames:
            if "_" in t or "%" in t:
                for other in tnames:
                    if other != t and like_matches(t, other):
                        out.append({"kind": "table", "schema": name, "name": t, "other": other,
                                    "text": f"⚠ Egeria's listing for {t} in {name} would also return {other}"})
    return out


# ── what hangs off a schema ──────────────────────────────────────────────────

#: Egeria's OpenLineage cataloguer names the component it makes for each OpenLineage job
#: `DeployedSoftwareComponent::<namespace>::<name>` (connector source line 4665; docs/open-lineage-cataloguing.md:78),
#: and Egeria's own governance actions (the surveys, the attach action) emit their events in the namespace
#: `GovernanceActions` (the READBACK evidence note: `DeployedSoftwareComponent::GovernanceActions::
#: PostgreSQLSurvey::survey-postgres-database`, the far end of the database's DataFlow). A DataFlow to such a
#: component records "Egeria's own machinery touched this" and goes with the element.
MACHINERY_JOB_PREFIX = "DeployedSoftwareComponent::GovernanceActions::"


def is_machinery_dataflow(r) -> bool:
    """A DataFlow is machinery ONLY when its far end is Egeria's own governance-action job component. A DataFlow
    to anything else (another asset, a person's process, a schema, a governance ACTION PROCESS, or an end that could
    not be read) is lineage someone could rely on: it hangs off, whoever asserted it."""
    return (r.type_name == "DataFlow" and r.other_type == "DeployedSoftwareComponent"
            and str(r.other_qualified_name or "").startswith(MACHINERY_JOB_PREFIX))


def classify_hangs_off(rels_per_element: list[list]) -> dict:
    """Group the non-structural relationships found on a schema's elements."""
    by_type: dict[str, int] = {}
    lineage_to: list[str] = []
    for rels in rels_per_element:
        for r in rels:
            if r.type_name == "DataFlow":
                if is_machinery_dataflow(r):
                    continue
                lineage_to.append(r.other_name or r.other_qualified_name or "an element Resource Explorer could not name")
            if r.type_name and r.type_name not in STRUCTURAL_RELATIONSHIPS:
                by_type[r.type_name] = by_type.get(r.type_name, 0) + 1
    total = sum(by_type.values())
    parts = []
    for t, n in sorted(by_type.items()):
        one, many = HANGS_OFF_WORDS.get(t, (t, t))
        parts.append(f"{n} {one if n == 1 else many}")
    return {"total": total, "by_type": by_type, "words": " · ".join(parts), "lineage_to": lineage_to}


def _walk_schema(gateway: CatalogueGateway, schema_guid: str, rels_of) -> dict:
    """Walk the schema's content as the cataloguer wired it (live read 2026-10-06): tables, columns and the schema type are
    ANCHORED TO THE DATABASE, not to the schema, and the schema's own relationships are exactly two (DataSetContent to the
    database, `Schema` to its schema type). A table is reached ONLY by schema --Schema--> schema type --AttributeForSchema-->
    table --NestedSchemaAttribute--> column. Returns `{"schema_types", "tables", "columns"}` as `(guid, type, qualifiedName)`."""
    out = {"schema_types": [], "tables": [], "columns": []}
    seen: set[str] = set()
    for r in rels_of(schema_guid):
        if r.type_name == "Schema" and r.other_guid and r.other_guid not in seen:
            seen.add(r.other_guid)
            out["schema_types"].append((r.other_guid, r.other_type or "RelationalDBSchemaType", r.other_qualified_name))
    for st_guid, _, _ in list(out["schema_types"]):
        for r in rels_of(st_guid):
            if r.type_name == "AttributeForSchema" and r.other_guid and r.other_guid not in seen:
                seen.add(r.other_guid)
                out["tables"].append((r.other_guid, r.other_type or "RelationalTable", r.other_qualified_name))
    for t_guid, _, _ in list(out["tables"]):
        for r in rels_of(t_guid):
            if r.type_name == "NestedSchemaAttribute" and r.other_guid and r.other_guid not in seen \
                    and (r.other_type in ("RelationalColumn", "")):
                seen.add(r.other_guid)
                out["columns"].append((r.other_guid, r.other_type or "RelationalColumn", r.other_qualified_name))
    return out


def read_hangs_off(gateway: CatalogueGateway, db_entity, schema: str) -> dict:
    """What hangs off one catalogued schema, from a relationships read of everything under it.

    The scan WALKS the graph (`_walk_schema`) and unions it with what the qualifiedName prefix finds (the connection
    graph the template made, and tables the walk could not reach), reading each element's relationships once.
    Returns `{"state": "absent"}` when Egeria holds no such schema element, `{"state": "read", "form", "hangs_off",
    "element_guid", "checked", "delete_order", "content"}` when the read completed, and `{"state": "cannot_check",
    "error"}` when any read failed. A failed read is never reported as "nothing hangs off it".

    A schema with CATALOGED CONTENT (tables read back) always archives: deleting the schema element never cascades its
    tables, columns or schema type (they are anchored to the database), so the form is not decided by relationships alone."""
    qn = schema_qn(db_entity, schema)
    cache: dict[str, list] = {}

    def rels_of(guid: str) -> list:
        if guid not in cache:
            cache[guid] = gateway.relationships(guid)
        return cache[guid]
    try:
        el = gateway.read_element(qn)
        if el is None:
            return {"state": "absent"}
        walked = _walk_schema(gateway, el.guid, rels_of)
        under = gateway.elements_under(qn + "::")
        members: dict[str, tuple] = {}
        for kind in ("columns", "tables", "schema_types"):
            for g, t, n in walked[kind]:
                members[g] = (g, t, n)
        for u in under:
            members.setdefault(u.guid, (u.guid, u.type_name, u.qualified_name))
        for g in members:
            rels_of(g)
        rels = [rels_of(el.guid)] + [cache[g] for g in members]
    except GatewayError as exc:
        return {"state": "cannot_check", "error": str(exc)}
    h = classify_hangs_off(rels)
    tables = {g for g, t, _ in members.values() if t == "RelationalTable"}
    columns = {g for g, t, _ in members.values() if t == "RelationalColumn"}
    content = {"tables": len(tables), "columns": len(columns)}
    if content["tables"]:
        h["words"] = " · ".join(([h["words"]] if h["words"] else [])
                                + [f"{content['tables']} published table{'s' if content['tables'] != 1 else ''}"])
        h["total"] += content["tables"]
    # leaf first, each by GUID: columns, tables, anything else under the name (the template's connection graph),
    # the schema type, then the schema (the caller deletes the schema last)
    rank = {"RelationalColumn": 0, "RelationalTable": 1, "RelationalDBSchemaType": 3}
    order = sorted(members.values(), key=lambda m: (rank.get(m[1], 2), -(m[2] or "").count("::")))
    return {"state": "read", "form": ARCHIVE if h["total"] else SOFT_DELETE, "hangs_off": h,
            "element_guid": el.guid, "checked": 1 + len(members), "in_use": running_actions(rels),
            "delete_order": order, "content": content}


def leave_out_walk(read: dict, qualified_name: str) -> list[tuple[str, str, str]]:
    """THE list a leave-out sends, in send order: `read_hangs_off`'s `delete_order` (leaf first), then the schema.
    `apply_leave_out` walks it and the preview counts it (`archive_plan`): one source, so what the person is shown
    before pressing is what the press would take."""
    return list(read["delete_order"]) + [(read["element_guid"], "DeployedDatabaseSchema", qualified_name)]


def plan_guids(walk: list[tuple[str, str, str]]) -> list[str]:
    """The GUID set the preview counted, in send order: it rides in the leave-out payload (`apply_leave_out`)."""
    return [g for g, _t, _qn in walk]


def is_survey_report(typ: str | None) -> bool:
    """A SurveyReport or any subtype of it (a subtype's name ends with "SurveyReport")."""
    return (typ or "").endswith(SURVEY_REPORT_TYPE)


#: A walk member whose type name is empty or missing: RE cannot tell it is not a SurveyReport, so it sends nothing.
UNKNOWN_TYPE_REFUSED = "couldn't tell what an element is · nothing archived"


def untyped_in(walk: list[tuple[str, str, str]]) -> list[str]:
    """GUIDs of walk members with an empty or missing type name."""
    return [g for g, t, _qn in walk if not (t or "").strip()]


def survey_reports_in(walk: list[tuple[str, str, str]]) -> list[str]:
    """GUIDs of any SurveyReport in a walk. The walk reads columns, tables, schema types and whatever sits under the
    schema's qualifiedName prefix, so a report named under it would be in the list: such a leave-out is refused
    whole (preview and apply), and `_send_destructive` refuses the report again behind that."""
    return [g for g, t, _qn in walk if is_survey_report(t)]


def archive_plan(walk: list[tuple[str, str, str]]) -> dict:
    """`{"total": N, "by_type": {type: n}}` from `leave_out_walk`'s list, types in send order."""
    by_type: dict[str, int] = {}
    for _g, typ, _qn in walk:
        by_type[typ or "unknown"] = by_type.get(typ or "unknown", 0) + 1
    return {"total": len(walk), "by_type": by_type}


def archive_plan_words(plan: dict, schema: str) -> str:
    """"will archive N elements anchored to <schema>: <counts by type>" (architect ruling 2026-10-23)."""
    counts = " · ".join(f"{n} {t}" for t, n in plan["by_type"].items())
    return f"will archive {plan['total']} element{'s' if plan['total'] != 1 else ''} anchored to {schema}: {counts}"


def running_actions(rels_per_element: list[list]) -> list[dict]:
    """Engine actions still holding the schema, from their `ActionTarget` relationships' own
    `activityStatus`. `ActionTarget` stays structural for archive-versus-delete; this is the other
    question: deleting under a running action is what ISSUE-90 loops on."""
    out = []
    for rels in rels_per_element:
        for r in rels:
            if r.type_name != "ActionTarget" or (r.activity_status or "") in FINISHED_ACTIVITY:
                continue
            out.append({"status": r.activity_status or "no status stated", "action_guid": r.other_guid,
                        "completion_time": r.completion_time, "message": r.completion_message,
                        "kind": r.action_kind})
    return out


def _ms_stamp(ms: str) -> str:
    """Epoch milliseconds (a string, as the raw relationship carries it) as `10-05 17:00`."""
    try:
        return _stamp(datetime.fromtimestamp(int(ms) / 1000, timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"))
    except (TypeError, ValueError, OverflowError, OSError):
        return str(ms)


def _in_use_phrase(first: dict) -> str:
    """"in use by a running survey" only when the running action IS a survey (or its kind is not stated);
    otherwise "in use by <action type>"."""
    kind = (first.get("kind") or "").strip()
    return "in use by a running survey" if (not kind or "survey" in kind) else f"in use by {kind}"


def in_use_text(schema: str, in_use: list[dict]) -> str:
    first = in_use[0]
    bits = [f"{schema}: {_in_use_phrase(first)} · {WAIT}", " / ".join(sorted({a["status"] for a in in_use}))]
    if first.get("completion_time"):
        bits.append(f"completionTime {_ms_stamp(first['completion_time'])}")
    if first.get("message"):
        bits.append(egeria_first_sentence(first["message"])[0])
    return " · ".join(bits)


# ── deriving every state word from proof rows ────────────────────────────────

#: The node kinds a database's commit state is built from (schema/table/column elements, the database
#: element itself). Anything else on the slug belongs to another kind of resource that shares the slug.
DATABASE_NODE_KINDS = frozenset({"database", "schema", "table", "column"})


def _by_node(proofs: list[dict]) -> dict:
    out: dict[tuple, list[dict]] = {}
    for p in proofs:
        out.setdefault((p["node_kind"], p["schema_name"], p["table_name"]), []).append(p)
    return out


def _latest(rows: list[dict], kinds: tuple[str, ...]) -> dict | None:
    for p in reversed(rows):
        if p["proof"] in kinds:
            return p
    return None


def _outbox_by_schema(outbox: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in outbox:                       # oldest first, so the last one wins
        s = (r.get("payload") or {}).get("schema")
        if s is not None:
            out[s] = r
    return out


def _egeria_word(text: str) -> str:
    """The error text without the Python class name the outbox prefixes it with."""
    return re.sub(r"^\w+(Error|Exception): ", "", str(text or "")).strip() or "no reason recorded"


def egeria_first_sentence(text: str) -> tuple[str, str]:
    """(the first sentence of Egeria's message, the rest).

    Egeria's errors arrive as a sentence followed by a `* Context: * class name=...` block (and
    sometimes a leading `=>`). The screen shows the sentence and folds the rest under "details".
    A message with no second part returns `(message, "")`."""
    t = " ".join(_egeria_word(text).split())
    t = re.sub(r"^(=>\s*)+", "", t)
    bullet = t.find(" * ")
    m = re.search(r"(?<=[.!?])\s+", t)
    if m and (bullet < 0 or m.start() <= bullet):
        cut, skip = m.start(), m.end()
    elif bullet >= 0:
        cut, skip = bullet, bullet + 1
    else:
        return t, ""
    return t[:cut].strip(), t[skip:].strip()


def _failed_state(ob: dict, second: str) -> dict:
    first, rest = egeria_first_sentence(ob.get("last_error"))
    out = {"state": "failed", "words": f"failed · {first}", "second": second,
           "proof": {"kind": "outbox", "outbox_id": ob["id"], "status": ob.get("status")}}
    if rest:
        out["details"] = rest
    return out


def _schema_state(rows: list[dict], ob: dict | None, effective: str | None, connector: dict | None) -> dict:
    """One schema's state: the NEWEST fact by time, whether it is a proof row (read back from Egeria) or
    the schema's newest outbox row. A failed or queued row older than a proof does not outrank it (the
    header and every row come from this one derivation). Ladder: queued, sent, attached, cataloged."""
    step = ""
    last = _latest(rows, STATE_PROOFS)
    if ob is not None:
        step = "leave out" if ob["element_kind"] == KIND_LEAVE_OUT else "attach"
        status = ob.get("status")
        row_at = str(ob.get("created_at") or "")
        proof_newer = last is not None and str(last["read_at"] or "") >= row_at
        if status in ("pending", "running") and not proof_newer:
            return {"state": "queued", "words": f"queued · outbox #{ob['id']}",
                    "second": f"step: {step}",
                    "proof": {"kind": "outbox", "outbox_id": ob["id"], "status": status}}
        attempt = (archive_attempt(rows, str((ob.get("payload") or {}).get("schema") or ""), ob["id"])
                   if step == "leave out" and status in ("failed", "dead") and not proof_newer else None)
        if attempt is not None:
            # Egeria accepted archive calls for this leave-out (ruling 2, 2026-10-25): the words are its rows', never
            # "failed · not sent". All accepted is archived; a walk Egeria stopped part-way stays failed.
            return {"state": "archived" if attempt["accepted"] == attempt["planned"] else "failed",
                    "words": _archive_words(attempt, ob.get("last_error") or ""),
                    "second": f"step: {step} · outbox #{ob['id']} · each accepted archive has its proof row",
                    "proof": {"kind": P_ELEMENT_ARCHIVED, "outbox_id": ob["id"], "status": status}}
        if status == "failed" and not proof_newer:
            return _failed_state(ob, f"step: {step} · outbox #{ob['id']} · {WORKER_WORDS}")
        if status == "dead" and not proof_newer:
            return _failed_state(ob, f"step: {step} · outbox #{ob['id']} · gave up after {ob.get('attempts')} attempts")
    had_elements = _latest(rows, (P_ELEMENTS,)) is not None
    detached = _latest(rows, (P_DETACHED,)) is not None
    if last is None:
        if effective == LEAVE_OUT:
            return {"state": "left_out", "words": "left out", "second": "scope record · never published",
                    "proof": None}
        if effective == CATALOGUE:
            return {"state": "uncommitted", "words": "not committed yet",
                    "second": "chosen in the scope · nothing read back from Egeria", "proof": None}
        return {"state": "none", "words": "", "second": "", "proof": None}
    proof = {"kind": last["proof"], "at": last["read_at"], "element_guid": last["element_guid"],
             "outbox_id": last.get("outbox_id")}
    kind = last["proof"]
    if kind == P_ATTACH_REQUESTED:
        guid = str(last["detail"].get("engine_action") or "")
        second = "scope says leave out · still being attached" if effective == LEAVE_OUT else \
            "Egeria's attach action was started; its target is not in the cataloger's list yet"
        return {"state": "sent", "words": f"sent to Egeria · attach action {guid[:8]} · waiting for the target",
                "second": second, "proof": proof}
    adopted = _latest([r for r in rows if r["element_guid"] == last["element_guid"]], (P_ADOPTED,))
    adopted_words = (f"adopted after a create error · {adopted['detail'].get('egeria_said', '')}"
                     if adopted is not None and kind in (P_TARGET, P_ELEMENTS) else "")
    if kind == P_ELEMENTS:
        d = last["detail"]
        n = len(d.get("tables") or [])
        words = f"published · {n} table{'s' if n != 1 else ''} · read back {_stamp(last['read_at'])}"
        if adopted_words:
            words = f"{adopted_words} · {words}"
        second = "element · read back from Egeria"
        if effective == LEAVE_OUT:
            second = "scope says leave out · still in Egeria until the next commit"
        return {"state": "catalogued", "words": words, "second": second, "proof": proof}
    if kind == P_TARGET:
        ct = (connector or {}).get("last_refresh_time") or last["detail"].get("connector_last_refresh") or ""
        second = (f"connector's last refresh {_hm(ct)} (the connector's, not this schema's)"
                  if ct else "last cycle not reported")
        if effective == LEAVE_OUT:
            second = "scope says leave out · still attached until the next commit"
        return {"state": "attached_waiting",
                "words": adopted_words or "attached · waiting for Egeria's next refresh",
                "second": second, "proof": proof}
    if kind == P_REMOVED:
        # "removed" is RESERVED for "Remove from Resource Explorer", which only touches RE's record; an Egeria
        # soft delete is the state `deleted`, in words "deleted in Egeria". The stored proof kind keeps its
        # old name `removed` (`P_REMOVED`): existing proof rows carry it and a rename would orphan them.
        was = "was published" if had_elements else ""
        second = LINGERING_LINE if detached else ""
        if effective == CATALOGUE:
            second = "scope says publish · the next commit re-creates it from the template"
        second = " · ".join(x for x in (was, second) if x)
        return {"state": "deleted", "words": f"deleted in Egeria · {_stamp(last['read_at'])}",
                "second": second, "proof": proof}
    if kind == P_RESTORED:
        d = last["detail"]
        was = str(d.get("from_guid") or "")
        words = f"restored · {str(last['element_guid'] or '')[:8]} · from {was[:8] or 'unknown'} · {_stamp(last['read_at'])}"
        return {"state": "restored", "words": words, "second": str(d.get("note") or ""), "proof": proof}
    if kind == P_ARCHIVED:
        second = S19_SENTENCE if effective == CATALOGUE else (LINGERING_LINE if detached else "")
        return {"state": "archived", "words": f"archived in Egeria · {_stamp(last['read_at'])}",
                "second": second, "proof": proof}
    return {"state": "none", "words": "", "second": "", "proof": proof}


def schema_state_of(registry, slug: str, schema: str, effective: str | None = None) -> dict:
    """One schema's derived state, straight from the rows (no scope view needed)."""
    by = _by_node(registry.list_catalogue_commit_proofs(slug))
    ob = _outbox_by_schema(registry.list_catalogue_outbox_rows(slug)).get(schema)
    return _schema_state(by.get(("schema", schema, ""), []), ob, effective, None)


def published_element_of(registry, slug: str) -> dict | None:
    """The database element Egeria holds NOW, by the same rows and the same reset cut the Curate band reads:
    `{"guid", "read_at"}`, or None when no publish/restore proof is live (never published, or only before a
    reset). The header's "published" word reads this when the registry's cached GUID is empty, so the header
    and the band cannot disagree about whether the element exists."""
    proofs_all = [p for p in registry.list_catalogue_commit_proofs(slug)
                  if p["node_kind"] in DATABASE_NODE_KINDS or p["proof"] == P_EGERIA_RESET]
    reset = merge_recorded_reset(registry, _latest(proofs_all, (P_EGERIA_RESET,)),
                                 [p for p in proofs_all if p["proof"] != P_EGERIA_RESET])
    cutoff = _ts(reset["read_at"]) if reset else ""
    live = [p for p in proofs_all if p["proof"] != P_EGERIA_RESET and (reset is None or _ts(p["read_at"]) >= cutoff)]
    found = _latest(_by_node(live).get(("database", "", ""), []), (P_DATABASE, P_RESTORED))
    if found is None or not found.get("element_guid"):
        return None
    return {"guid": found["element_guid"], "read_at": found["read_at"]}


def derive_commit_state(registry, slug: str, view: dict, now: datetime | None = None) -> dict:
    """Every state word on the scope tree, header included, from persisted rows.

    The one function the route and the tests share. Returns
    `{"schemas": {name: state}, "tables": {"s.t": state}, "header": {...},
    "database": {...}, "collisions": [...]}`. Reads only the registry; Egeria is
    never contacted here, so opening Curate stays free."""
    # Proof rows are keyed by slug alone, and a repository and a database can share one slug. Only the rows
    # about a database's own nodes describe its commit state: a repository's blueprint-shape, report,
    # architecture-publish or sub-resource rows must never make a database read "committed" or move a state.
    # The reset marker is the exception: one per slug, whatever node kind the script gave it.
    proofs_all = [p for p in registry.list_catalogue_commit_proofs(slug)
                  if p["node_kind"] in DATABASE_NODE_KINDS or p["proof"] == P_EGERIA_RESET]
    # A reset marker splits the history: rows read before it are what Egeria once held, not what it holds.
    # Ordered by read_at (the reset time), never by id, so a proof recorded after the reset but before the
    # marker was written still counts.
    reset = _latest(proofs_all, (P_EGERIA_RESET,))
    # The recorded reset (`egeria_resets`) splits the history the same way when it is newer than the marker.
    reset = merge_recorded_reset(registry, reset, [p for p in proofs_all if p["proof"] != P_EGERIA_RESET])
    cutoff = _ts(reset["read_at"]) if reset else ""
    # ONE tie rule, shared with repo_publish.publish_state: a proof read AT or after the reset time is live.
    # A proof whose time cannot be read is neither live nor earlier (no claim either way).
    proofs = [p for p in proofs_all if p["proof"] != P_EGERIA_RESET and (reset is None or _ts(p["read_at"]) >= cutoff)]
    earlier = [p for p in proofs_all if reset is not None and p["proof"] != P_EGERIA_RESET
               and _known(p["read_at"]) and _ts(p["read_at"]) < cutoff]
    earlier_by = _by_node(earlier)
    by = _by_node(proofs)
    ob_by_schema = _outbox_by_schema(registry.list_catalogue_outbox_rows(slug))
    db_rows = by.get(("database", "", ""), [])
    # The element the header names is the newest of the two rows that NAME a database element: a publish, or a
    # restore (whose element_guid is the restored element; a roll-forward's earlier publish names a retired one).
    published = _latest(db_rows, (P_DATABASE, P_RESTORED))
    connector = _latest(db_rows, (P_CONNECTOR,))
    conn_d = ({"last_refresh_time": connector["detail"].get("last_refresh_time", ""),
               "read_at": connector["read_at"]} if connector else None)
    failed_read = _latest(db_rows, (P_READ_FAILED,))
    # A failed read is healed by any LATER proof that Egeria answered a read: zones_read, elements_read_back,
    # connector_read (all three are written only from a successful read), or a newer database_published/restored
    # (both name an element Egeria returned). Order is the proof list's own (oldest first), never a guess.
    if failed_read is not None:
        after = proofs[proofs.index(failed_read) + 1:] if failed_read in proofs else []
        if any(p["proof"] in READ_SUCCESS_PROOFS for p in after):
            failed_read = None
    zones_text = _zones_text(_latest(db_rows, (P_ZONES,)), _latest(db_rows, (P_ZONES_READ,)))

    schemas: dict[str, dict] = {}
    tables: dict[str, dict] = {}
    for s in view.get("schemas") or []:
        name = s["name"]
        st = _schema_state(by.get(("schema", name, ""), []), ob_by_schema.get(name), s.get("effective"), conn_d)
        if reset is not None and st["state"] in ("none", "uncommitted") and \
                _latest(earlier_by.get(("schema", name, ""), []), STATE_PROOFS) is not None:
            st = {"state": "reset", "words": pre_reset_words(reset["read_at"]),
                  "second": str((reset["detail"] or {}).get("text") or "Egeria was reset"),
                  "proof": {"kind": P_EGERIA_RESET, "at": reset["read_at"], "element_guid": "", "outbox_id": None}}
        schemas[name] = st
        found = None
        el = _latest(by.get(("schema", name, ""), []), (P_ELEMENTS,))
        if el is not None and st["state"] == "catalogued":
            found = set(el["detail"].get("tables") or [])
        for t in s.get("tables") or []:
            key = f"{name}.{t['name']}"
            if st["state"] == "catalogued":
                if t["name"] in found:
                    ts = {"state": "catalogued", "words": f"published · read back {_stamp(el['read_at'])}",
                          "second": "element · read back from Egeria"}
                else:
                    ts = {"state": "not_read_back", "words": "in the survey · not in Egeria's listing",
                          "second": f"schema read back {_stamp(el['read_at'])}; this table was not under it"}
                if t.get("effective") == LEAVE_OUT:
                    ts["second"] = WHOLE_SCHEMAS_LINE
            elif st["state"] in ("attached_waiting", "queued", "sent", "failed", "deleted", "archived", "restored", "reset"):
                ts = {"state": "follows_schema", "words": f"as its schema: {'deleted in Egeria' if st['state'] == 'deleted' else pre_reset_words(reset["read_at"]) if st['state'] == 'reset' else st['state'].replace('_', ' ')}",
                      "second": WHOLE_SCHEMAS_LINE if t.get("effective") == LEAVE_OUT else ""}
            else:
                ts = {"state": "none", "words": "", "second": ""}
            tables[key] = ts

    # What the chosen schemas would be, for the collision check shown on the tree.
    chosen = [n for n, s in ((x["name"], x) for x in view.get("schemas") or [])
              if s.get("effective") == CATALOGUE
              or (s.get("effective") is None and schemas[n]["state"] in ("catalogued", "attached_waiting", "queued", "sent"))]
    collisions = wildcard_collisions(view, chosen)

    # A leave-out that was refused (ISSUE-117) is a ROW: the schema says "kept, not sent" and the header counts
    # it, so "0 archived" is derived from rows, not from the absence of one.
    blocked_names = []
    for s in view.get("schemas") or []:
        rows = by.get(("schema", s["name"], ""), [])
        bl = _latest(rows, (P_LEAVE_OUT_BLOCKED, P_LEAVE_OUT_PLAN_CHANGED))
        if bl is not None and bl["proof"] == P_LEAVE_OUT_PLAN_CHANGED:
            # Ruling (3): the same schema cue, its own words; not counted as an archive-cascade refusal.
            if s.get("effective") == LEAVE_OUT and schemas[s["name"]]["state"] not in ("archived", "deleted"):
                schemas[s["name"]] = {**schemas[s["name"]], "plan_changed": True,
                                      "second": f"{PLAN_CHANGED} · {_stamp(bl['read_at'])}"}
            continue
        if bl is not None and s.get("effective") == LEAVE_OUT and schemas[s["name"]]["state"] not in ("archived", "deleted"):
            blocked_names.append(s["name"])
            schemas[s["name"]] = {**schemas[s["name"]], "blocked_117": True,
                                  "second": f"{KEPT_NOT_SENT} · {_stamp(bl['read_at'])}"}
    # D1-a: the endpoint's other families and Egeria's duplicate links, from the newest `endpoint_resolved` row (a
    # read-back's). Carried BESIDE RE's own state (`endpoint`), never in place of it: RE's state machine, the header
    # counts and the press read RE's own rows only, and the IN EGERIA cell shows both.
    ep_row = _latest(db_rows, (P_ENDPOINT,))
    resolution = ((ep_row or {}).get("detail") or {}).get("resolution")
    for s in view.get("schemas") or []:
        over = endpoint_identity.schema_state_overlay(resolution, s["name"])
        if over is not None:
            schemas[s["name"]] = {**schemas[s["name"]], "endpoint": over}
    counts: dict[str, int] = {}
    for s in view.get("schemas") or []:
        if s.get("effective") == CATALOGUE:
            k = schemas[s["name"]]["state"]
            counts[k] = counts.get(k, 0) + 1
    n_gone = sum(1 for st in schemas.values() if st.get("state") in ("archived", "deleted"))
    # An `endpoint_resolved` row is a read of what OTHERS hold at the endpoint: it never makes RE's commit exist.
    anything = bool([p for p in proofs_all if p["proof"] != P_ENDPOINT] or ob_by_schema)
    header = _header(published, counts, conn_d, failed_read, view, anything, zones_text, blocked_names, n_gone,
                     reset=reset, published_earlier=_latest(earlier, (P_DATABASE, P_RESTORED)))
    zr = _latest(db_rows, (P_ZONES_READ,))
    chosen_states = {s["name"]: schemas[s["name"]] for s in view.get("schemas") or [] if s.get("effective") == CATALOGUE}
    last_read = max((str(p["read_at"] or "") for p in proofs if p["proof"] in _READ_BACK_ROWS), default="")
    # No publish/restore row and no proof row for a chosen schema (both after any reset): nothing was read back,
    # so there are no counts to show, never three measured zeros (fix round 2). The header says "not read back".
    any_schema_proof = any(by.get(("schema", n, "")) for n in chosen_states)
    # Rows from before the reset prove nothing about now; they make the chips ◌ (never ✓) until a post-reset proof.
    pre = None
    if reset is not None:
        pre_att = {n for n in chosen_states
                   if _latest(earlier_by.get(("schema", n, ""), []), (P_TARGET, P_ELEMENTS)) is not None}
        pre_built = {n for n in chosen_states if _latest(earlier_by.get(("schema", n, ""), []), (P_ELEMENTS,)) is not None}
        pre_pub = _latest(earlier, (P_DATABASE, P_RESTORED)) is not None
        if pre_pub or pre_att:
            pre = {"reset_at": reset["read_at"], "published": pre_pub, "attached": pre_att, "built": pre_built}
    read_back = (read_back_chips(published, chosen_states, last_read, now, pre_reset=pre)
                 if published is not None or any_schema_proof or pre is not None else {"chips": []})
    endpoint = None
    if ep_row is not None:
        d = ep_row.get("detail") or {}
        res = d.get("resolution") or {}
        endpoint = {"at": ep_row["read_at"], "error": res.get("error", ""), "endpoint": res.get("endpoint", ""),
                    "families": res.get("families") or [], "plan": d.get("plan"),
                    "neither": (endpoint_identity.neither_line(res, [x["name"] for x in view.get("schemas") or []])
                                if d.get("plan") else None),
                    "press": _press_summary(_latest(db_rows, (P_REANCHOR,))),
                    "words": (f"{endpoint_identity.NOT_READ} · {res.get('error')}" if res.get("error")
                              else (d.get("plan") or {}).get("words") or "this endpoint: only RE's elements")}
    return {"schemas": schemas, "tables": tables, "header": header, "collisions": collisions,
            "read_back": read_back, "endpoint": endpoint,
            "database": ({"guid": published["element_guid"], "short": published["element_guid"][:8],
                          "at": published["read_at"], "owner": (_latest(db_rows, (P_OWNER,)) or {}).get("detail"),
                          "zones": list((zr or {}).get("detail", {}).get("zones") or []),
                          "zones_text": zones_text}
                         if published else None)}


def _zones_text(written: dict | None, read: dict | None) -> str:
    """The element's zones as a read-back FACT. No ZoneMembership on the element reads "zones: none ·
    everyone visible" (what the 2026-10-05 read-back found on every element it created); a zone
    reads "zones: a, b · set by Egeria", or "set by RE" only when RE wrote a zone AND what Egeria
    reports back is exactly what RE wrote. No read at all is "" (the header says "not read back")."""
    if not read:
        return ""
    zones = list((read.get("detail") or {}).get("zones") or [])
    if not zones:
        return "zones: none · everyone visible"
    mine = sorted((written or {}).get("detail", {}).get("zones") or [])
    by = "RE (EXPLORER_PUBLISH_ZONES)" if mine and mine == sorted(zones) else "Egeria"
    return f"zones: {', '.join(zones)} · set by {by}"


def _header(published, counts, conn_d, failed_read, view, anything: bool, zones_text: str = "",
            blocked: list | None = None, n_gone: int = 0, reset: dict | None = None,
            published_earlier: dict | None = None) -> dict:
    """The state-derived marker line: never a constant (the owner's gate, item 6)."""
    if not anything:
        return {"state": "not_committed", "text": NOT_COMMITTED_HEADER}
    n_cat = sum(1 for s in view.get("schemas") or [] if s.get("effective") == CATALOGUE)
    parts = []
    if published:
        if published.get("proof") == P_RESTORED:
            was = str((published.get("detail") or {}).get("from_guid") or "")
            parts.append(f"Database element {published['element_guid'][:8]} in Egeria · restored "
                         f"{_stamp(published['read_at'])}" + (f" (from {was[:8]})" if was else ""))
        else:
            parts.append(f"Database element {published['element_guid'][:8]} in Egeria · published {_stamp(published['read_at'])}")
    elif reset is not None and published_earlier is not None:
        parts.append(f"Database element {published_earlier['element_guid'][:8]} {pre_reset_words(reset['read_at'])}")
    else:
        parts.append("Database element not read back from Egeria")
    if published:
        parts.append(zones_text or "zones: not read back")
    if n_cat:
        # Attached and catalogued carry no state word here: the read-back chips below show it (architect,
        # 2026-10-23). The header counts them once, as attached: "N schemas attached", "no schemas attached".
        order = (("sent", "sent"), ("queued", "queued"), ("failed", "failed"), ("restored", "restored"), ("uncommitted", "not committed yet"),
                 ("deleted", "deleted in Egeria"), ("archived", "archived in Egeria"),
                 ("reset", f"published before the {_ts((reset or {}).get('read_at'))[:10]} reset"))
        bits = [f"{counts[k]} {w}" for k, w in order if counts.get(k)]
        parts.append(f"{n_cat} schema{'s' if n_cat != 1 else ''} chosen" + (": " + ", ".join(bits) if bits else ""))
        n_att = counts.get("attached_waiting", 0) + counts.get("catalogued", 0)
        parts.append(f"{n_att} schema{'s' if n_att != 1 else ''} attached" if n_att else "no schemas attached")
        # No "N in Egeria" sentence here: the read-back chips below are the one statement of it (the sentence
        # disagreed with the chip's "not checked since", audit 2026-10-28).
    if blocked:
        parts.append(f"{len(blocked)} left out, {KEPT_NOT_SENT} · {n_gone} archived or deleted in Egeria")
    if conn_d and conn_d.get("last_refresh_time"):
        parts.append(f"cataloger connector's last refresh {_stamp(conn_d['last_refresh_time'])} (the connector's, not a schema's)")
    if failed_read:
        parts.append(f"last read of Egeria failed {_stamp(failed_read['read_at'])}: {failed_read['detail'].get('error', '')}")
    return {"state": "committed", "text": " · ".join(parts)}


# ── the read-back counts: three chips in a fixed order (architect ruling 2026-10-23) ──────────────────────

#: A read-back whose newest proof row is older than this says "not checked since <read_at>": 2 x the read-back
#: loop's 60 s interval, the same bound as native_survey_run.NOT_CHECKED_AFTER_S.
READ_BACK_NOT_CHECKED_AFTER_S = 120
#: The rows that are a successful read of Egeria for the catalogue: the newest one says when RE last looked.
_READ_BACK_ROWS = (P_CONNECTOR, P_ELEMENTS, P_TARGET)


def _age_s(iso: str, now: datetime | None) -> float | None:
    t = _ts(iso)
    if not t:
        return None
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    return (ref - datetime.fromisoformat(t)).total_seconds()


def _schemas_unit(n: int) -> str:
    """The unit word a schema count names on screen: "schema" for 1, "schemas" otherwise (0 included)."""
    return "schema" if n == 1 else "schemas"


def read_back_chips(published: dict | None, schema_states: dict[str, dict], last_read_at: str = "",
                    now: datetime | None = None, pre_reset: dict | None = None) -> dict:
    """The three counts under the catalogue section, from proof rows only, never from the branch a commit took.

    1. "published by RE N": the server and database elements in the newest `database_published` row (a restore
       names the database only). ✓ once that row exists: it is written from the element Egeria returned.
    2. "attached N": chosen schemas whose newest proof says a CatalogTarget exists (`target_attached`, or
       `elements_read_back`, which is read through that target). ✓.
    3. "cataloged by Egeria N": chosen schemas whose newest proof is `elements_read_back` (Egeria's cataloguer
       built the tables under them). ✓ when every attached schema is catalogued; ◌ pending while some attached
       schema has no tree yet. A pending count whose newest read-back row is older than 120 s also carries
       "not checked since <read_at>".

    `schema_states` is {schema: _schema_state(...)} for the chosen schemas. Returns {"chips": [3 dicts]} with
    each chip {key, word, count, unit, cue: 'read_back' | 'pending' | 'none', read_at, hover, not_checked_since}.
    `unit` is the word after the count on screen ("attached 2 schemas", "cataloged by Egeria 1 schema"); the
    published count has none ("published by RE 2", architect fix round 1).

    `pre_reset` ({reset_at, published, attached, built}, from `derive_commit_state`) names what proof rows from
    BEFORE the latest Egeria reset showed. Those rows are never counted (the counts are post-reset proof only) and a
    chip they cover that no post-reset proof has caught up with is ◌ (`pending`), never ✓, with `pre_reset_at` and the
    words "published before the <date> reset · not in Egeria now" on hover (architect rulings (b) and (c),
    2026-10-23): "cataloged by Egeria 0 ◌" until the cataloguer rebuilds."""
    pub_n, pub_at = 0, ""
    if published is not None:
        pub_n = (1 if (published.get("detail") or {}).get("server_guid") else 0) + (1 if published.get("element_guid") else 0)
        pub_at = str(published.get("read_at") or "")
    attached = {n: st for n, st in schema_states.items() if st.get("state") in ("attached_waiting", "catalogued")}
    built = {n: st for n, st in attached.items() if st.get("state") == "catalogued"}
    at = lambda sts: max((str((st.get("proof") or {}).get("at") or "") for st in sts.values()), default="")  # noqa: E731
    att_at, built_at = at(attached), at(built)
    read_at = last_read_at or max(att_at, built_at)

    if not attached:
        cat_cue = "none"
    elif len(built) == len(attached):
        cat_cue = "read_back"
    else:
        cat_cue = "pending"
    stale = ""
    if cat_cue == "pending" and read_at:
        age = _age_s(read_at, now)
        stale = read_at if age is not None and age > READ_BACK_NOT_CHECKED_AFTER_S else ""
    waiting = sorted(set(attached) - set(built))
    pre = pre_reset or {}
    pre_words = pre_reset_words(pre.get("reset_at")) if pre else ""
    pub_behind = bool(pre.get("published")) and not pub_n
    att_behind = sorted(set(pre.get("attached") or ()) - set(attached))
    built_behind = sorted(set(pre.get("built") or ()) - set(built))
    if built_behind:
        cat_cue = "pending"
    chips = [
        {"key": "published", "word": "published by RE", "count": pub_n, "unit": "",
         "cue": "read_back" if pub_n else ("pending" if pub_behind else "none"),
         "read_at": pub_at, "not_checked_since": "",
         "hover": (f"the server and database elements RE published · read {_stamp(pub_at)}" if pub_n
                   else f"the database element {pre_words}" if pub_behind
                   else "nothing RE published has been read back")},
        {"key": "attached", "word": "attached", "count": len(attached), "unit": _schemas_unit(len(attached)),
         "cue": "pending" if att_behind else ("read_back" if attached else "none"),
         "read_at": att_at, "not_checked_since": "",
         "hover": ((f"chosen schemas attached to Egeria's cataloger as targets · read {_stamp(att_at)}" if attached
                    else "no chosen schema is attached to Egeria's cataloger")
                   + (f" · {pre_words}: {', '.join(att_behind)}" if att_behind else ""))},
        {"key": "catalogued", "word": "cataloged by Egeria", "count": len(built), "unit": _schemas_unit(len(built)),
         "cue": cat_cue,
         "read_at": built_at or read_at, "not_checked_since": stale,
         "hover": ("chosen schemas whose tables Egeria's cataloger built"
                   + (f" · read {_stamp(built_at)}" if built_at else "")
                   + (f" · no tree yet: {', '.join(waiting)}" if waiting else "")
                   + (f" · last read {_stamp(read_at)}" if read_at and read_at != built_at else "")
                   + (f" · {pre_words}: {', '.join(built_behind)}" if built_behind else ""))},
    ]
    if pre:
        for c, behind in zip(chips, (pub_behind, att_behind, built_behind)):
            if behind:
                c["pre_reset_at"] = pre.get("reset_at") or ""
    return {"chips": chips}


def read_back_chips_for(registry, slug: str, schemas: list[str], now: datetime | None = None) -> dict:
    """The same three counts for a commit's own schemas (the read-back step's text). ONE derivation with the
    on-screen chips (fix round 2): `derive_commit_state` over those schemas, so the Egeria-reset cutoff applies."""
    view = {"schemas": [{"name": s, "effective": CATALOGUE, "tables": []} for s in schemas]}
    return derive_commit_state(registry, slug, view, now)["read_back"]


def read_back_words(chips: dict) -> str:
    """The chips as one plain line (the step's text): fixed order, no "waiting"; the cue is the state."""
    glyph = {"read_back": " ✓", "pending": " ◌", "none": ""}
    if not chips.get("chips"):
        return "nothing read back from Egeria"
    # F37: a count not checked lately is not a count: no number, one cue.
    return " · ".join(f"{c['word']} ◌ not checked since {_stamp(c['not_checked_since'])}" if c.get("not_checked_since")
                      else f"{c['word']} {c['count']}{(' ' + c['unit']) if c.get('unit') else ''}{glyph[c['cue']]}"
                      for c in chips["chips"])


# ── the preview and its manifest ─────────────────────────────────────────────

def _chosen_and_kept(view: dict, states: dict) -> tuple[list[str], list[str]]:
    """Schemas to be targets: chosen ones, plus undecided ones already in Egeria.

    The scope's own rule: an undecided schema keeps what is in Egeria now; only
    an explicit leave out removes anything."""
    chosen = [s["name"] for s in view["schemas"] if s.get("effective") == CATALOGUE]
    kept = [s["name"] for s in view["schemas"]
            if s.get("effective") is None and states.get(s["name"], {}).get("state") in
            ("catalogued", "attached_waiting", "queued", "sent")]
    return chosen, kept


def survey_schema_list(names: list[str]) -> tuple[list[str], list[str]]:
    """(names the request parameter can scope, names with a comma it cannot).

    The parameter is split on commas with no trimming, so a comma inside a name
    makes it `cm` and `ma`, neither of which exists: the survey would report
    success with nothing in it. Such a name is left out of the list and said so."""
    ok = [n for n in names if "," not in n]
    bad = [n for n in names if "," in n]
    return ok, bad


# ── who may change each part (Brief Z2) ───────────────────────────────────────
#
# The database's own element is checked by the route before anything here runs (`authorize_resource_write`,
# publish). Each SCHEMA element that is already in Egeria and that this commit would write is checked on its own,
# with the operations its write performs, by the same decision every other write uses
# (`workflows.curate.curation_access`, emulating `OpenMetadataAccessSecurityConnector.validateZoneAccess`):
#   attach    ATTACH                (the CatalogTarget link on the schema: validateUserForElementAttach :1582-1620)
#   leave out DETACH + DELETE       (the target removal :1639-1694, then the archive / soft delete :2540-2566), and
#             DELETE on every table, column and schema-type element the delete walks (`read_hangs_off`'s
#             delete_order) when the archive-cascade block is off for the leave-out ARCHIVE (the default since
#             2026-10-23). With the block on (re-armed) nothing is deleted, and the check runs IN FRONT of the block
#             only, so a refusal is said before the block would be.
# RE writes no table element on an attach: Egeria's cataloguer creates the tables (whole schemas). A part that is
# not permitted is skipped with its own word ("not permitted · <reason>") and the rest proceed, as Publish does.
# The element reads are batched (`zone_access.read_elements`: lanes, Daemon(ACCESS_CHECK)); no per-element client.

ATTACH_OPS = ("ATTACH",)
LEAVE_OUT_OPS = ("DETACH", "DELETE")
MEMBER_DELETE_OPS = ("DELETE",)


def _caller_signed_in() -> bool:
    from resource_explorer.workflows.curate import _caller_identity

    who = _caller_identity()
    return bool(who is not None and who.auth_source != "anonymous" and who.user_id)


def _caller_overrides() -> bool:
    """A Portal curator/admin (`curation_access` rule 2): carried into the run, as Publish carries it."""
    from resource_explorer.workflows.curate import CURATOR_ROLES, _caller_identity

    who = _caller_identity()
    return bool(who is not None and who.auth_source != "anonymous" and who.user_id
                and (who.role or "").lower() in CURATOR_ROLES)


def known_schema_guid(state: dict) -> str:
    """The schema element RE's own rows say is in Egeria now ('' when none: never cataloged, deleted, reset, or
    only an outbox row so far). The run resolves the element by qualified name instead (`parts_access_by_name`)."""
    if (state or {}).get("state") not in ("catalogued", "attached_waiting", "restored", "sent"):
        return ""
    return str(((state or {}).get("proof") or {}).get("element_guid") or "")


def parts_access(registry, slug: str, parts: list[tuple[str, list[str], tuple[str, ...]]], *,
                 reader=None, new_ops: tuple[str, ...] = (), anchor_guid: str = "") -> dict[str, Any]:
    """{schema: AccessDecision} for each (schema, [element guids], operations): the schema is permitted only when
    every one of its elements is. All the elements are read together, once (`EgeriaAccessReader.check`), and
    nothing is read for a caller the decision settles first (anonymous, Portal curator/admin).

    `new_ops` (round 3): a part with NO element yet is one the write will CREATE; with `new_ops` it is judged for
    those operations as a new member of `anchor_guid` (the database, when it is in Egeria: Egeria's
    validateUserForAnchorMemberCreate :1965-1990), else on RE's configured zones (`curation_access`)."""
    from resource_explorer.workflows.curate import AccessDecision, _reads_zones, curation_access
    from resource_explorer.zone_access import EgeriaAccessReader

    reader = reader or EgeriaAccessReader(cache_elements=True)
    wanted = [g for _, guids, _ in parts for g in guids if g] if _reads_zones() else []
    if wanted is not None and _reads_zones() and new_ops and anchor_guid and any(not g for _, g, _ in parts):
        wanted.append(anchor_guid)
    out: dict[str, Any] = {}
    with reader.check(wanted):
        for name, guids, ops in parts:
            decision = AccessDecision(True)
            if not [g for g in guids if g] and new_ops:
                out[name] = curation_access(registry, "database", slug, "", operations=new_ops, reader=reader,
                                            element_guid="", anchor_guid=anchor_guid)
                continue
            for guid in [g for g in guids if g]:
                d = curation_access(registry, "database", slug, "", operations=ops, reader=reader, element_guid=guid)
                if not d.allowed:
                    decision = d
                    break
                decision = d
            out[name] = decision
    return out


def not_permitted_row(schema: str, reason: str) -> dict:
    return {"schema": schema, "reason": reason, "words": f"{NOT_PERMITTED_WORDS} · {reason}",
            "text": f"{schema}: {NOT_PERMITTED_WORDS} · {reason}"}


#: Schema states whose proof rows say Egeria holds the element. With no gateway they are attached on the rows' word
#: (the run re-checks); every other schema is held, since nothing can say whether its create re-includes an archive.
NO_CREATE_STATES = ("catalogued", "attached_waiting", "restored")


class PreviewReads:
    """The gateway as one preview sees it: each Egeria READ made once, its answer kept for the rest of the build.

    A preview writes nothing, so within one build an answer cannot go stale by our own hand, and the build reads the
    same things more than once (a leave-out's walk is read for the access check and again for its form; each schema
    element by name for the access check and again by the walk). Measured on the fake 2026-10-30: two catalogued
    leave-outs and one attach read 58 times, 27 of them a repeat. A read that FAILED is not kept: the next asker reads
    again and meets its own failure. Anything that is not a read goes straight to the gateway."""

    _READS = ("read_element", "elements_under", "relationships", "schema_elements", "archived_siblings")

    def __init__(self, gateway):
        self._gateway = gateway
        self._kept: dict[tuple, Any] = {}
        #: Egeria reads actually made, by method (F31: the phase-timing line names them).
        self.made: dict[str, int] = {}

    def knows(self, qualified_name: str, element) -> None:
        """An element by name, already read some other way in this build (the paged find of every schema element):
        a later `read_element(qualified_name)` is answered from it. Only a FOUND element is kept: a name the find
        did not return is still read by name, so a miss is never assumed."""
        if element is not None:
            self._kept.setdefault(("read_element", (qualified_name,), ()), element)

    def __getattr__(self, name):
        fn = getattr(self._gateway, name)
        if name not in self._READS:
            return fn

        def once(*args, **kwargs):
            key = (name, args, tuple(sorted(kwargs.items())))
            if key not in self._kept:
                self.made[name] = self.made.get(name, 0) + 1
                self._kept[key] = fn(*args, **kwargs)
            return self._kept[key]
        return once


def build_preview(registry, slug: str, view: dict, gateway: CatalogueGateway | None, *,
                  db_entity=None, derived: dict | None = None, confirms: list[dict] | None = None) -> dict:
    """What pressing Catalogue would do, with the Egeria reads it needs.

    `gateway` None means Egeria could not be reached: every leave out that needs
    a read is then "couldn't check what hangs off it" and blocked, which is the
    honest answer, never "nothing".

    Brief Z2: each schema element already in Egeria that the commit would write is checked for the signed-in
    caller (`parts_access`); one not permitted is held with "not permitted · <reason>" and the rest proceed. The
    press (`start_commit`) builds this same preview, so what the panel shows is what the commit does. An anonymous
    viewer is not checked part by part: no commit can start without a sign-in (`start_commit` refuses first)."""
    db_entity = db_entity or registry.get_database(slug, allow_unreadable=True)
    if gateway is not None and not isinstance(gateway, PreviewReads):
        gateway = PreviewReads(gateway)
    phase: dict[str, float] = {}
    mark = [time.perf_counter()]

    def lap(name: str) -> None:
        """F31: each phase's milliseconds, logged once at the end so the next read of the log measures it."""
        now = time.perf_counter()
        phase[name] = phase.get(name, 0.0) + (now - mark[0]) * 1000
        mark[0] = now
    derived = derived or derive_commit_state(registry, slug, view)
    states = derived["schemas"]
    chosen, kept = _chosen_and_kept(view, states)
    refused, attach, leave, blockers = [], [], [], []
    not_permitted: list[dict] = []

    # S19 (owner ruling 2026-10-14): a schema whose create would re-include an archived element is NOT attached until
    # the person confirms, by its archived GUID. `reinclude_check` is the routine the run's attach calls too.
    needs_confirmation: list[dict] = []
    changed: list[dict] = []
    reincluded: dict[str, dict] = {}
    # A name collision stops the press before anything else (409) and no Egeria read is made for it (round 3), so
    # the archived-sibling reads are skipped too: the press cannot go ahead, and the press re-reads when it can.
    colliding = bool(wildcard_collisions(view, chosen + kept))
    # Every schema the press would attach is read, whatever its proof rows say: Egeria may have archived (and renamed)
    # it since. `reinclude_check` reads the live element first, so one that is there costs one read and asks nothing.
    lap("derive")
    checks = reinclude_checks(gateway, db_entity, [] if colliding or gateway is None else chosen + kept, confirms,
                              held=states)
    lap("reinclude")
    for name in chosen + kept:
        st = states.get(name, {})
        if colliding or (gateway is None and st.get("state") in NO_CREATE_STATES):
            attach.append(name)
            continue
        if gateway is None:
            refused.append({"schema": name, "text": f"{name}: {SIBLING_UNREADABLE}", "state": "cannot_check"})
            continue
        chk = checks[name]
        if chk["state"] in ("exists", "clear"):
            attach.append(name)
        elif chk["state"] == "confirmed":
            attach.append(name)
            reincluded[name] = reinclude_row("schema", name, chk)
        elif chk["state"] == NEEDS_CONFIRMATION:
            needs_confirmation.append(reinclude_row("schema", name, chk))
        elif chk["state"] == "changed":
            changed.append({"node": "schema", "name": name, "text": f"{name}: {SIBLING_CHANGED}"})
            refused.append({"schema": name, "text": f"{name}: {SIBLING_CHANGED}", "state": "changed"})
        else:
            refused.append({"schema": name, "text": f"{name}: {SIBLING_UNREADABLE}", "state": "cannot_check"})
    # The database element: a publish under its name re-includes an archived database the same way.
    db_check = None
    if gateway is not None and not colliding:
        db_check = reinclude_check(gateway, gw.database_qualified_name(gw.server_name_for(db_entity),
                                                                      db_entity.database_name),
                                   confirmed_guid_for(confirms, "database"))
        if db_check["state"] == NEEDS_CONFIRMATION:
            needs_confirmation.insert(0, reinclude_row("database", "", db_check))
        elif db_check["state"] == "changed":
            changed.insert(0, {"node": "database", "name": "", "text": f"the database: {SIBLING_CHANGED}"})
        elif db_check["state"] == "confirmed":
            reincluded[""] = reinclude_row("database", "", db_check)

    lap("database")
    leave_out_names = [s["name"] for s in view["schemas"] if s.get("effective") == LEAVE_OUT]
    checked = _caller_signed_in()
    # Round 3: a name collision stops the commit before anything else (409), so no access read is made for it:
    # nothing is read from Egeria for a press that cannot go ahead (the press's own "no Egeria call" rule).
    if checked and wildcard_collisions(view, attach):
        checked = False
    leave_refused: dict = {}
    reader = None
    if checked:
        from resource_explorer.zone_access import EgeriaAccessReader

        reader = EgeriaAccessReader(cache_elements=True)
        leave_cands = [n for n in leave_out_names if states.get(n, {}).get("state") in CATALOGED_STATES]
        if gateway is not None:
            # Round 2 (§5b): the SAME function the run uses, resolving each schema by qualified name through the
            # gateway exactly as the write will, so the preview's count is the run's. Round 3: a new schema is
            # judged as a new member of the database element (found by its qualified name, as the run's step 0
            # finds it), and a leave-out walks its members exactly as the run does when the block lets it through.
            try:
                db_el = gateway.read_element(gw.database_qualified_name(gw.server_name_for(db_entity),
                                                                       db_entity.database_name))
                anchor = db_el.guid if db_el is not None else ""
            except GatewayError:
                anchor = ""               # the run reads it again; a part read below says if Egeria is unreadable
            attach, np_attach = parts_check_by_name(registry, gateway, db_entity, slug, attach, ATTACH_OPS,
                                                    override=False, reader=reader, anchor_guid=anchor)
            _ok, np_leave = parts_check_by_name(registry, gateway, db_entity, slug, leave_cands, LEAVE_OUT_OPS,
                                                override=False, reader=reader,
                                                members=not gw.issue_117_blocked(slug, scope=gw.LEAVE_OUT_ARCHIVE))
        else:
            # No gateway: Egeria cannot be reached, and the commit cannot run either. RE's own rows name the
            # elements it knows; the rest wait for the run.
            got = parts_access(registry, slug,
                               [(("attach", n), [known_schema_guid(states.get(n, {}))], ATTACH_OPS) for n in attach
                                if known_schema_guid(states.get(n, {}))]
                               + [(("leave", n), [known_schema_guid(states.get(n, {}))], LEAVE_OUT_OPS)
                                  for n in leave_cands if known_schema_guid(states.get(n, {}))], reader=reader)
            np_attach = [not_permitted_row(n, d.reason) for (w, n), d in got.items() if w == "attach" and not d.allowed]
            np_leave = [not_permitted_row(n, d.reason) for (w, n), d in got.items() if w == "leave" and not d.allowed]
            attach = [n for n in attach if n not in {r["schema"] for r in np_attach}]
        not_permitted += [{**r, "write": "attach"} for r in np_attach]
        leave_refused = {r["schema"]: r for r in np_leave}

    lap("access")
    for name in leave_out_names:
        st = states.get(name, {})
        node = next(s for s in view["schemas"] if s["name"] == name)
        tcount = node.get("table_count")
        row = {"schema": name, "tables": tcount}
        if st.get("state") in ("deleted", "archived", "left_out", "none", "uncommitted"):
            row.update(form="none", blocked=False,
                       text=f"{name}: nothing to remove" + (" · already " + ("deleted in Egeria" if st.get("state") == "deleted" else "archived in Egeria")
                                                                  if st.get("state") in ("deleted", "archived") else " · never published"))
            leave.append(row)
            continue
        # Brief Z2: the access check stands IN FRONT of the archive-cascade block (which is unchanged): a person who may
        # not change the schema is told so, before the block would be.
        np_row = leave_refused.get(name)
        if np_row is not None:
            row.update(form="not_permitted", blocked=True, reason=np_row["words"], text=np_row["text"])
            not_permitted.append({**np_row, "write": "leave out"})
            leave.append(row)
            continue
        # A restored schema passes NO lifted scope (architect, 2026-10-23): only a slug override opens it.
        if st.get("state") == "restored" and gw.issue_117_blocked(slug):
            row.update(form="issue_117", blocked=True, reason=RESTORED_BLOCKED, text=f"{name}: {RESTORED_BLOCKED}")
            leave.append(row)
            continue
        # The leave-out ARCHIVE is lifted (2026-10-23) unless re-armed; its soft delete keeps the block, so a schema
        # the archive path may take is read first and held only if its form is the soft delete.
        if gw.issue_117_blocked(slug, scope=gw.LEAVE_OUT_ARCHIVE) and st.get("state") in CATALOGED_STATES:
            row.update(form="issue_117", blocked=True, reason=gw.ISSUE_117_WORDS, text=f"{name}: {gw.ISSUE_117_WORDS}")
            leave.append(row)
            continue
        if gateway is None:
            row.update(form="cannot_check", blocked=True, text=f"{name}: {CANT_CHECK}",
                       error="Egeria could not be reached")
            leave.append(row)
            continue
        read = read_hangs_off(gateway, db_entity, name)
        if read["state"] == "absent":
            row.update(form="none", blocked=False, text=f"{name}: not in Egeria any more · nothing to remove")
        elif read["state"] == "cannot_check":
            row.update(form="cannot_check", blocked=True, text=f"{name}: {CANT_CHECK}", error=read["error"])
        elif read.get("in_use"):
            row.update(form="in_use", blocked=True, reason=f"{_in_use_phrase(read['in_use'][0])} · {WAIT}",
                       in_use=read["in_use"],
                       text=in_use_text(name, read["in_use"]))
        elif survey_reports_in(leave_out_walk(read, schema_qn(db_entity, name))):
            row.update(form="survey_report", blocked=True, reason=SURVEY_REPORT_REFUSED,
                       text=f"{name}: {SURVEY_REPORT_REFUSED}")
        elif untyped_in(leave_out_walk(read, schema_qn(db_entity, name))):
            row.update(form="unknown_type", blocked=True, reason=UNKNOWN_TYPE_REFUSED,
                       text=f"{name}: {UNKNOWN_TYPE_REFUSED}")
        elif read["form"] == SOFT_DELETE and gw.issue_117_blocked(slug) and st.get("state") in CATALOGED_STATES:
            row.update(form="issue_117", blocked=True, reason=gw.ISSUE_117_WORDS, text=f"{name}: {gw.ISSUE_117_WORDS}")
        elif read["form"] == SOFT_DELETE:
            row.update(form=SOFT_DELETE, blocked=False, hangs_off=read["hangs_off"], checked=read["checked"],
                       plan_guids=plan_guids(leave_out_walk(read, schema_qn(db_entity, name))),
                       text=f"{name}: nothing hangs off it · will delete in Egeria"
                            + (f" with its {tcount} tables" if tcount else "") + " · delete · nothing depends on it")
        else:
            walk = leave_out_walk(read, schema_qn(db_entity, name))
            plan = archive_plan(walk)
            row.update(form=ARCHIVE, blocked=False, hangs_off=read["hangs_off"], checked=read["checked"], plan=plan,
                       plan_guids=plan_guids(walk),
                       text=f"{name}: {read['hangs_off']['words']} hang off it · "
                            f"{archive_plan_words(plan, name)} · archive in Egeria, "
                            f"not delete · re-including it later creates a new element; the archived record stays"
                            + "".join(f" · archive · lineage to {n} would be lost"
                                      for n in read["hangs_off"].get("lineage_to", [])))
        leave.append(row)

    lap("leave_out_walk")
    blocked_names = {r["schema"] for r in leave if r.get("blocked")}
    collisions = wildcard_collisions(view, attach)
    if collisions:
        blockers.append(f"{len(collisions)} name collision{'s' if len(collisions) != 1 else ''} Egeria's listing "
                        "can't tell apart: leave the schema out, or rename it in the database")
    # A leave out that cannot be checked is blocked FOR THAT SCHEMA: it is left exactly
    # where it is and the commit proceeds for the rest (the brief: "disabled for that
    # schema"). It is never read as "nothing hangs off it".

    ok_names, comma_names = survey_schema_list(attach)
    ctx = registry.get_context("database", slug) or {}
    owner = ((ctx.get("enrichment") or {}).get("owner") or {}).get("value") or ""
    from resource_explorer.egeria_identity import configured_publish_zones
    zones = configured_publish_zones()
    removes = [r for r in leave if r["form"] == SOFT_DELETE]
    archives = [r for r in leave if r["form"] == ARCHIVE]
    n_new = sum(1 for n in attach if states.get(n, {}).get("state") not in ("catalogued", "attached_waiting", "queued", "sent"))
    lines = [
        {"id": "re_publishes", "mechanism": 1,
         "text": ("RE publishes the server and database assets and RE's own survey report, supplying the database "
                  "description and version and the server's own description and version. "
                  + (f"The deployment configured publish zones ({', '.join(zones)}): joining them is RE's LAST write, "
                     "after the targets, the survey and the owner, read back, and a refusal is reported in Egeria's "
                     "words, not retried (unverified for a second commit). "
                     if zones else
                     "RE writes no ZoneMembership: zones left to Egeria (EXPLORER_PUBLISH_ZONES is not configured). ")
                  + (f"Owner from Context: {owner}." if owner else "Not carried: owner, not declared on Context."))},
        {"id": "cataloguer_creates", "mechanism": 2,
         "text": (f"Egeria's cataloger creates tables and columns for {len(attach)} schema target"
                  f"{'s' if len(attach) != 1 else ''} ({n_new} to attach now). Each is a schema-kind target, "
                  "never the database or the server. The elements arrive on the daemon's next refresh, not now.")},
        {"id": "survey_measures", "mechanism": 3,
         "text": (f"{SURVEY_LINE}: " + (", ".join(ok_names) if ok_names else "no schema chosen, so the survey is not started")
                  + (f". The survey's report is separate from RE's survey report, where RE's measures go, and fills when "
                     f"Egeria's survey finishes" if ok_names else "")
                  + (f". Not scopable, a comma in the name: {', '.join(comma_names)}" if comma_names else ""))},
    ]
    # What this commit will NOT do, named: "1 schema not committed: s_x · couldn't check what
    # hangs off it". Both per-schema holds appear here; the rest of the commit proceeds.
    held: dict[str, list[str]] = {}
    for r in leave:
        if r.get("blocked"):
            held.setdefault(r.get("reason", CANT_CHECK), []).append(r["schema"])
    for r in refused:
        held.setdefault(r["text"][len(r["schema"]) + 2:], []).append(r["schema"])
    for r in needs_confirmation:
        if r["node"] == "schema":
            held.setdefault(f"archived in Egeria · {REINCLUDE_TAIL} · confirm Re-include", []).append(r["name"])
    for r in not_permitted:
        if r["write"] == "attach":
            held.setdefault(r["words"], []).append(r["schema"])
    not_committed = [{"reason": why, "schemas": names} for why, names in held.items()]
    for nc in not_committed:
        n = len(nc["schemas"])
        nc["text"] = f"{n} schema{'s' if n != 1 else ''} not committed: {', '.join(nc['schemas'])} · {nc['reason']}"
        lines.append({"id": "not_committed", "mechanism": 0, "text": nc["text"]})
    # The leave outs are drawn once, as their own rows (`leave_out` below); a joined copy here
    # repeated every one of them in the manifest (2026-10-06).
    lines.append({"id": "whole_schemas", "mechanism": 0, "text": WHOLE_SCHEMAS_LINE})
    # Two counts, two sources: what RE's own survey could read with its credential, and what Egeria's
    # survey counted (the header's number). The sentence names both (live finding 2026-10-06: "all 8
    # schemas" under a header that said 29).
    try:
        own = registry.latest_measured_database_survey(slug)
    except Exception:
        own = None
    n_egeria = len(view["schemas"])
    if own:
        m_own = own.get("schema_count")
        report_sentence = (f"RE's survey report is published whole; it describes the {m_own} schemas RE's own "
                           f"{md(own.get('surveyed_at', ''))} survey could read (Egeria's survey counts {n_egeria}); "
                           f"elements are created for the {len(attach)} you chose.")
    else:
        report_sentence = (f"RE has no survey of its own to publish (Egeria's survey counts {n_egeria} schemas); "
                           f"elements are created for the {len(attach)} you chose.")
    lines.append({"id": "survey_report_whole", "mechanism": 0, "text": report_sentence})
    # Curate's publish table: the measures chosen Publish on Describe ride in that report (the SAME function the press
    # uses, `publish_table.chosen_rows`, so the count here is what goes out). None chosen: no line.
    declared_n = None
    lap("manifest")
    try:
        from resource_explorer.publish_table import chosen_rows
        from resource_explorer.surveyors.database.db_derived import one_read

        # F38: one read of the table recomputes the database's derivations once, not once per derived measure
        # (17 times on coco_pharma, 36-48 s of a 37-50 s preview, 2026-10-30).
        with one_read():
            declared_n = len(chosen_rows(registry, "database", slug))
    except Exception as exc:  # noqa: BLE001 -- unread, said as unread, never as "none chosen"
        log.warning("commit preview: the publish table of %s could not be read: %s", slug, exc)
        lines.append({"id": "declared_measures", "mechanism": 0,
                      "text": "the declared measures you chose could not be read; the press will try again"})
    lap("declared_measures")
    if declared_n:
        word = "measure" if declared_n == 1 else "measures"
        lines.append({"id": "declared_measures", "mechanism": 0, "count": declared_n, "text": (
            f"{declared_n} declared {word} you chose on Describe go inside RE's survey report, each read back by name"
            if own else f"{declared_n} declared {word} you chose on Describe are not sent: RE has no survey report "
                        "of its own to carry them · run a survey first")})

    # S19: the database itself is archived and unconfirmed (or a confirmation went stale, or it could not be checked):
    # everything hangs under it, so nothing is pressed until the person confirms (Re-include on its row).
    if db_check is not None and db_check["state"] == NEEDS_CONFIRMATION:
        blockers.append(f"the database is {reinclude_words(db_check['sibling'].date)} · confirm Re-include")
    elif db_check is not None and db_check["state"] in ("changed", "cannot_check"):
        blockers.append(f"the database: {db_check['text']}")
    something = bool(attach or [r for r in leave if r["form"] in (SOFT_DELETE, ARCHIVE)])
    all_refused = bool(not something and not_permitted)
    # Round 2 (LOW 7): what ELSE holds this commit, each as a short word with its reason on demand, so a refusal
    # never hides another blocker (a name collision, a leave-out the archive-cascade block keeps).
    also_blocked = [{"word": "name collision", "reason": b} for b in blockers]
    also_blocked += [{"word": "archive-cascade block", "reason": r["text"]} for r in leave if r.get("form") == "issue_117"]
    if all_refused:
        # Nothing the person may change is left: refused whole, with the first reason (Publish's rule), and the
        # other blockers kept beside it.
        blockers.insert(0, not_permitted[0]["words"])
    if not something and not blockers:
        blockers.append("nothing to commit: choose at least one schema to publish")
    label = f"Publish · {len(attach)} schema{'s' if len(attach) != 1 else ''}"
    lap("manifest")
    made = gateway.made if isinstance(gateway, PreviewReads) else {}
    log.info("preview_timing slug=%s attach=%d leave_out=%d total_ms=%.0f %s egeria_reads=%s", slug, len(attach),
             len(leave_out_names), sum(phase.values()), " ".join(f"{k}_ms={v:.0f}" for k, v in phase.items()),
             ",".join(f"{k}:{v}" for k, v in sorted(made.items())) or "none")
    if removes:
        label += f" · deletes {len(removes)} from Egeria"
    if archives:
        label += f" · archives {len(archives)}"
    return {
        "can_commit": not blockers,
        "button": label,
        "blockers": blockers,
        "blocked_schemas": sorted(blocked_names),
        "blocked_notes": [r["text"] for r in leave if r.get("blocked")],
        "attach": attach,
        "refused": refused,
        # S19: rows the person confirms with Re-include (each names the archived GUID it re-submits), the
        # confirmations that no longer match the newest archived copy, and the confirmed ones this press re-includes.
        "needs_confirmation": needs_confirmation,
        "held_elsewhere": held_elsewhere_rows(states, chosen + kept),
        "confirmation_changed": changed,
        "reinclude": list(reincluded.values()),
        "not_permitted": not_permitted,
        "all_refused": all_refused,
        "also_blocked": also_blocked,
        "access_checked": checked,
        "leave_out": leave,
        "collisions": collisions,
        "survey": {"schemas": ok_names, "not_scopable": comma_names, "line": SURVEY_LINE},
        "manifest": {"lines": lines, "not_committed": not_committed, "schema_targets": len(attach), "new_targets": n_new,
                     "survey_schemas": ok_names, "zones": zones, "zones_written": bool(zones), "owner": owner,
                     "not_permitted": [{"schema": r["schema"], "write": r["write"], "reason": r["reason"]}
                                       for r in not_permitted],
                     "whole_schemas_line": WHOLE_SCHEMAS_LINE},
        "tables_line": WHOLE_SCHEMAS_LINE,
    }


# ── S19: the archived-sibling guard, the ONE routine every re-including create calls ─────────────────

#: A record of a confirmed re-include: the new element and the archived sibling it stands beside. A record of a
#: create, never a state: NOT in STATE_PROOFS. Its detail carries `archived_sibling_guid` and `archived_on` (no DDL).
P_REINCLUDED = "reincluded"
#: A re-include the run refused (no confirmation, or the archived copy changed since it): a record, NOT a state.
P_REINCLUDE_HELD = "reinclude_held"


def reinclude_check(gateway: CatalogueGateway, qn: str, confirmed_guid: str = "") -> dict:
    """What a create under `qn` may do, read from Egeria now. The preview, the press, the schema attach and the
    database publish all call this, so what the panel shows is what the run does (§5b rule 1).

    `state`:
      exists              a live element holds the name: no create (it is adopted).
      clear               no live element and no archived copy: a normal create.
      needs_confirmation  an archived copy exists and nobody confirmed it: NO create.
      confirmed           the newest archived copy is the one `confirmed_guid` names: create, and record it.
      changed             a confirmation names a copy that is no longer the newest one: NO create.
      cannot_check        a read could not be made: NO create.
    `live` is the live element (exists), `sibling` the newest archived copy, `text` the sentence for the row."""
    try:
        live = gateway.read_element(qn)
        sibs = [] if live is not None else gateway.archived_siblings(qn)
    except GatewayError as exc:
        return _cannot_check(exc)
    return gw.decide_reinclude(live, sibs, confirmed_guid)


def _cannot_check(exc: Exception) -> dict:
    return {"state": "cannot_check", "live": None, "sibling": None, "text": SIBLING_UNREADABLE,
            "error": egeria_first_sentence(str(exc))[0]}


#: D1-a: a schema another family holds at this endpoint. The check reads both families: it says so, and a create still
#: makes RE's own element beside it until adoption at first publish (D1-c) is built; nothing of the pack's is changed.
HELD_ELSEWHERE_WORDS = "in Egeria under '{family}' · a commit adds RE's own element beside it · the pack's is not changed"


def held_elsewhere_rows(states: dict, names: list[str]) -> list[dict]:
    """The preview's rows for chosen schemas another family already holds at this endpoint (from the derived states,
    which come from the newest `endpoint_resolved` row: no extra Egeria read)."""
    return [{"schema": n, "family": states[n]["endpoint"].get("family", ""),
             "element_guid": states[n]["endpoint"].get("element_guid", ""),
             "words": HELD_ELSEWHERE_WORDS.format(family=states[n]["endpoint"].get("family", ""))}
            for n in names if ((states.get(n) or {}).get("endpoint") or {}).get("state") == "in_egeria_other"]


def reinclude_checks(gateway: CatalogueGateway, db_entity, names: list[str], confirms: list[dict] | None,
                     held: dict | None = None) -> dict:
    """`reinclude_check` for many schemas of one database from ONE paged find (review round 1): every
    DeployedDatabaseSchema under `<db schema prefix>`, live and archived, read once; each schema's answer is built
    from it with the same decision (`gw.decide_reinclude`). A failed read makes every answer `cannot_check`."""
    if not names:
        return {}
    prefix = schema_qn(db_entity, "")
    try:
        els = gateway.schema_elements(prefix)
    except GatewayError as exc:
        return {n: _cannot_check(exc) for n in names}
    out = {}
    for n in names:
        qn = schema_qn(db_entity, n)
        live = next((e for e in els if e.qualified_name == qn and not e.archived), None)
        if isinstance(gateway, PreviewReads):
            gateway.knows(qn, live)          # the access check and the walk read this schema by name next (F32)
        sibs = gw._newest_first([gw.ArchivedSibling(e.guid, e.qualified_name, *gw.archived_on_of(e.qualified_name, qn),
                                                    archived_at=e.archived_at, archived=e.archived)
                                 for e in els if e.qualified_name.startswith(qn + gw.ARCHIVED_ON)])
        out[n] = gw.decide_reinclude(live, sibs, confirmed_guid_for(confirms, "schema", n))
        other = ((held or {}).get(n) or {}).get("endpoint") or {}
        if other.get("state") == "in_egeria_other":
            out[n]["held_by"] = {"family": other.get("family", ""), "element_guid": other.get("element_guid", ""),
                                 "words": HELD_ELSEWHERE_WORDS.format(family=other.get("family", ""))}
    return out


def reinclude_row(node: str, name: str, check: dict) -> dict:
    """One row of the preview's `needs_confirmation` list: what the Re-include control re-submits."""
    sib = check["sibling"]
    label = name or "the database"
    return {"node": node, "name": name, "archived_guid": sib.guid, "archived_qualified_name": sib.qualified_name,
            "archived_on": sib.archived_on, "archived_at": sib.archived_at, "date": sib.date,
            "words": f"archived · {sib.date}", "text": f"{label}: {check['text']}"}


def reinclude_detail(check: dict, by: str = "") -> dict:
    sib = check["sibling"]
    return {"archived_sibling_guid": sib.guid, "archived_qualified_name": sib.qualified_name,
            "archived_on": sib.archived_on, "archived_at": sib.archived_at,
            "other_archived_guids": check.get("others") or [], "confirmed_by": by,
            "note": REINCLUDE_TAIL}


def confirmed_guid_for(confirms: list[dict] | None, node: str, name: str = "") -> str:
    """The archived GUID a confirmation names for one node ('' = none)."""
    for c in confirms or []:
        if (c.get("node") or "schema") == node and (c.get("name") or "") == (name or ""):
            return str(c.get("archived_guid") or "")
    return ""


# ── what an outbox row does ──────────────────────────────────────────────────

def _proof(registry, slug: str, proof: str, *, schema: str = "", node_kind: str = "schema",
           table: str = "", **kw) -> int:
    return registry.append_catalogue_commit_proof(
        slug, proof=proof, node_kind=node_kind, schema_name=schema, table_name=table, **kw)


_NODE_KIND_OF_TYPE = {"RelationalTable": "table", "RelationalColumn": "column"}
DELETE_OK, DELETE_REFUSED, DELETE_FAILED = "ok", "refused-by-block", "failed"


def _send_destructive(registry, gateway, op: str, *, slug: str, schema: str, element_guid: str, typ: str = "",
                      form: str = "", relationship_guid: str = "", qualified_name: str = "",
                      curation_id: str = "", outbox_id: int | None = None, by: str = "", scope: str = "") -> None:
    """Send ONE destructive call (ARCHIVE / SOFT_DELETE through `delete_element`, or `remove_catalog_target`)
    and record it: an INFO line before, a line after, and one `delete_call` proof row written after the call
    returns, is refused by the archive-cascade block, or raises (the row is written, then the exception propagates).
    The cascade of 2026-10-06 was invisible in RE's own record because none of this existed.

    `scope` comes from the caller and defaults to "" (fail closed: the block applies); only the leave-out ARCHIVE of
    a schema that is not restored passes `gw.LEAVE_OUT_ARCHIVE`. An archive or delete of a SurveyReport is refused
    with `SURVEY_REPORT_REFUSED` whatever scope or override is set (architect ruling 2026-10-23)."""
    method = "remove_catalog_target" if op == "remove_catalog_target" else ("ARCHIVE" if form == ARCHIVE else "SOFT_DELETE")
    flags = ({"forLineage": True, "forDuplicateProcessing": True} if method == "remove_catalog_target" else
             {"deleteMethod": method, "forLineage": True, "forDuplicateProcessing": True, "cascade_delete": False})
    who = f"curation={curation_id or '-'} outbox={outbox_id if outbox_id is not None else '-'}"
    what = (f"{method} element={element_guid} type={typ or 'unknown'} schema={schema}"
            + (f" relationship={relationship_guid}" if relationship_guid else "") + f" flags={flags} {who}")
    outcome, error = DELETE_OK, ""
    report = method != "remove_catalog_target" and is_survey_report(typ)
    blocked = method != "remove_catalog_target" and (report or gw.issue_117_blocked(slug, scope=scope))
    if report:
        outcome, error = DELETE_REFUSED, SURVEY_REPORT_REFUSED
        log.warning("destructive call refused: a SurveyReport is never archived or deleted (slug=%r): %s", slug, what)
    elif blocked:
        outcome, error = DELETE_REFUSED, gw.ISSUE_117_WORDS
        log.warning("destructive call refused by archive-cascade block (slug=%r scope=%r): %s", slug, scope, what)
    else:
        log.info("destructive call BEFORE: %s", what)
    raised: BaseException | None = None
    if not blocked:
        try:
            if method == "remove_catalog_target":
                gateway.remove_catalog_target(relationship_guid)
            else:
                gateway.delete_element(element_guid, form, slug=slug, scope=scope)
        except BaseException as exc:                      # the row is written first, then this propagates
            raised = exc
            error = " ".join(str(exc).split())[:gw.MAX_EGERIA_TEXT] or type(exc).__name__
            outcome = DELETE_REFUSED if error == gw.ISSUE_117_WORDS else DELETE_FAILED
        if raised is None:
            log.info("destructive call AFTER: %s outcome=ok", what)
        else:
            log.warning("destructive call AFTER: %s outcome=%s: %s", what, outcome, error)
    detail = {"operation": method, "flags": flags, "outcome": outcome, "type": typ}
    if relationship_guid:
        detail["relationship_guid"] = relationship_guid
    if error:
        detail["error"] = error
    try:
        _proof(registry, slug, P_DELETE_CALL, schema=schema,
               node_kind="schema" if method == "remove_catalog_target" else _NODE_KIND_OF_TYPE.get(typ, "schema"),
               element_guid=element_guid, qualified_name=qualified_name, curation_id=curation_id,
               outbox_id=outbox_id, recorded_by=by, detail=detail)
    except Exception:
        if raised is None and outcome == DELETE_OK:
            raise                                          # the call went out and left no record: say so loudly
        log.exception("could not record the delete_call row for %s", what)
    if blocked:
        raise GatewayError(error)
    if raised is not None:
        raise raised


def _entity(registry, slug: str):
    e = registry.get_database(slug, allow_unreadable=True)
    if e is None:
        raise GatewayError(f"database {slug!r} is no longer registered")
    return e


def gw_schema_placeholders(db_entity, schema: str) -> dict:
    from resource_explorer.catalogue_gateway import schema_placeholders
    return schema_placeholders(db_entity, schema)


#: A COMPLETED attach action whose target has still not appeared after this long is not "lag": the
#: target is gone (someone removed it), and a new attach may be started.
LAG_GIVE_UP_SECONDS = 600


def _targets_for(targets: list, guid: str, db_entity, schema: str) -> list:
    """The targets that ARE this schema: by element GUID, or by the name Egeria's own attach gives it
    (`<db>.<schema>`, the schema's displayName) or RE's fallback name."""
    names = {f"{db_entity.database_name}.{schema}", target_name(db_entity, schema)}
    return [t for t in targets if t.element_guid == guid or (t.name and t.name in names)]


def _unsettled_request(registry, slug: str, schema: str) -> dict | None:
    """The newest attach request for this schema that nothing has since detached, removed or archived."""
    rows = [p for p in registry.list_catalogue_commit_proofs(slug)
            if p["node_kind"] == "schema" and p["schema_name"] == schema]
    req = _latest(rows, (P_ATTACH_REQUESTED,))
    if req is None:
        return None
    gone = _latest(rows, (P_DETACHED, P_REMOVED, P_ARCHIVED))
    return None if (gone is not None and str(gone["read_at"]) >= str(req["read_at"])) else req


def _older_than(iso: str, seconds: int) -> bool:
    try:
        return (datetime.utcnow() - datetime.fromisoformat(str(iso))).total_seconds() > seconds
    except ValueError:
        return False


def _wait_for_target(gateway: CatalogueGateway, guid: str, db_entity=None, schema: str = "") -> list:
    """Egeria's action attaches on its own side: look for the target a few times before concluding."""
    for i in range(CATALOG_POLLS):
        found = gateway.list_catalog_targets()
        mine = _targets_for(found, guid, db_entity, schema) if db_entity is not None else \
            [t for t in found if t.element_guid == guid]
        if mine:
            return mine
        if i + 1 < CATALOG_POLLS:
            _sleep(CATALOG_POLL_SECONDS)
    return []


def _own_catalog_targets(gateway: CatalogueGateway, guid: str) -> list:
    """The CatalogTarget relationships on THIS element, read through a relationship read by its GUID."""
    return [r for r in gateway.relationships(guid) if r.type_name == "CatalogTarget"]


def _verify_adopted(gateway: CatalogueGateway, guid: str, database_guid: str, egeria_said: str) -> None:
    """After a create error, an element that exists is reused only if it is whole: it has its
    DataSetContent link to the database AND its ResourceConnection, read by its GUID. Otherwise the
    create is reported as what it was, with Egeria's own sentence."""
    rels = gateway.relationships(guid)
    linked = any(r.type_name == "DataSetContent" and (not database_guid or r.other_guid == database_guid) for r in rels)
    connected = any(r.type_name == "ResourceConnection" for r in rels)
    if not (linked and connected):
        raise GatewayError(f"schema created without its connection · {egeria_said}")


def apply_attach(registry, gateway: CatalogueGateway, payload: dict, *, outbox_id: int | None = None) -> str:
    """Create the schema element and attach it as a schema-kind target. Idempotent.

    Reads first, always: the element by qualified name (adopting an existing one),
    the cataloguer's targets (never attaching the same schema twice), and after
    the attach the targets again, because the proof row is the read-back and not
    the call's return. Returns the schema element's GUID."""
    slug, schema = payload["slug"], payload["schema"]
    e = _entity(registry, slug)
    qn = schema_qn(e, schema)
    if current_schema_choice(registry, slug, schema) == LEAVE_OUT:
        # The person left it out after this was queued. Attaching it now would
        # undo their choice; nothing is written and the row is simply done.
        log.info("catalog commit: %s/%s was left out after its attach was queued; not attaching", slug, schema)
        return ""
    # S19 (owner ruling 2026-10-14): Egeria renames an archived element to `<qn>_archivedOn_<date>`, so the name is
    # free and a create here would re-include it as a NEW element. Created only when the press confirmed exactly the
    # newest archived copy (`archived_sibling_guid`); otherwise refused, and nothing is created.
    chk = reinclude_check(gateway, qn, str(payload.get("archived_sibling_guid") or ""))
    if chk["state"] == "cannot_check":           # a read that could not be made: Egeria unreadable, nothing created
        raise GatewayError(f"{schema}: {chk['text']}" + (f" ({chk['error']})" if chk.get("error") else ""))
    if chk["state"] in (NEEDS_CONFIRMATION, "changed"):
        # A record of the refusal (not a state): the run keeps this schema out of the survey's scope too.
        _proof(registry, slug, P_REINCLUDE_HELD, schema=schema, qualified_name=qn,
               element_guid=chk["sibling"].guid, curation_id=payload.get("curation_id", ""), outbox_id=outbox_id,
               recorded_by=payload.get("by", ""), detail={"state": chk["state"], "text": chk["text"],
                                                          "confirmed": str(payload.get("archived_sibling_guid") or "")})
        raise SchemaRefused(f"{schema}: {chk['text']}")
    el = chk["live"]
    create_note, requester_note = "", ""
    if el is None:
        try:
            guid = gateway.create_schema_element(e, schema, payload.get("database_guid", ""))
        except GatewayError as exc:
            # Egeria answered an error but the element may exist (rehearsal 2: a 500 on the parent link
            # still created it). Read before believing the error: an element that is there is adopted.
            again = gateway.read_element(qn)
            if again is None:
                raise
            # Egeria's WHOLE sentence is kept (never cut to a first sentence): it is the evidence.
            guid, create_note = again.guid, " ".join(_egeria_word(str(exc)).split())
            _verify_adopted(gateway, guid, payload.get("database_guid", ""), create_note)
            _proof(registry, slug, P_ADOPTED, schema=schema, element_guid=guid, qualified_name=qn,
                   curation_id=payload.get("curation_id", ""), outbox_id=outbox_id, recorded_by=payload.get("by", ""),
                   detail={"egeria_said": create_note,
                           "verified": "DataSetContent link to the database and ResourceConnection, read by the element's GUID"})
        if not guid:
            raise GatewayError(f"Egeria created no schema element for {schema}")
        if chk["state"] == "confirmed":
            _proof(registry, slug, P_REINCLUDED, schema=schema, element_guid=guid, qualified_name=qn,
                   curation_id=payload.get("curation_id", ""), outbox_id=outbox_id, recorded_by=payload.get("by", ""),
                   detail=reinclude_detail(chk, payload.get("by", "")))
        if not create_note:
            # Freshly created on a person's behalf (Brief I): `requestedBy` in its provenance and
            # Ownership = the requester, from the one helper. A drain-loop retry runs as
            # Daemon(OUTBOX, requested_by=<the payload's author>), so it stamps the author too;
            # a row with no author stamps nothing (never the service account).
            from resource_explorer.egeria_clients import current_principal
            from resource_explorer.egeria_identity import on_behalf_of

            behalf = on_behalf_of(current_principal())
            requester_note = gateway.mark_on_behalf(guid, behalf.requester, behalf.owner)
            if requester_note:
                log.warning("catalog: schema %s (%s): %s", schema, guid, requester_note)
    else:
        guid = el.guid
        requester_note = ""
    # THE GUARD: read the targets FIRST. A target for this schema (by element or by name) means it is
    # attached: no initiation, no add_catalog_target. Egeria creates ANOTHER CatalogTarget on every
    # initiation (9 targets for 3 schemas in rehearsal 2), so nothing below runs when one is there.
    mine = _targets_for(gateway.list_catalog_targets(), guid, e, schema)
    mechanism, action_guid, fallback = "already_attached", "", ""
    if not mine:
        req = _unsettled_request(registry, slug, schema)
        if req is not None and req["element_guid"] != guid:
            req = None          # that request was for a schema element Egeria no longer has (a reset): it settles nothing
        if req is not None:
            # An attach was started earlier and its target is not readable yet (the read-back lags).
            # Never initiate again while that action is not over-and-failed.
            action_guid = str(req["detail"].get("engine_action") or "")
            st = gateway.engine_action_status(action_guid)
            if st.status in FAILED_ACTION_STATUSES or (st.status == "COMPLETED" and _older_than(req["read_at"], LAG_GIVE_UP_SECONDS)):
                action_guid, req = "", None
            else:
                mechanism = "action_type"
                mine = _wait_for_target(gateway, guid, e, schema)
                if not mine:
                    raise GatewayError(
                        f"Egeria's attach action {action_guid[:8]} is {st.status or 'status not stated'} but the target for "
                        f"{schema} is not in the cataloger's list yet (still waiting; not attaching a second time)")
        if not mine and req is None:
            # Prefer Egeria's own attach: its GovernanceActionType takes the EXISTING schema element as
            # action target `newAsset`. Fall back to add_catalog_target only when that is refused or
            # errors. Never both: a still-running action is an error to retry, not a reason to attach.
            try:
                action_guid = gateway.initiate_catalog_action(guid, gw_schema_placeholders(e, schema))
            except GatewayError as exc:
                fallback = egeria_first_sentence(str(exc))[0]
                action_guid = ""
            else:
                _proof(registry, slug, P_ATTACH_REQUESTED, schema=schema, element_guid=guid, qualified_name=qn,
                       curation_id=payload.get("curation_id", ""), outbox_id=outbox_id, recorded_by=payload.get("by", ""),
                       detail={"engine_action": action_guid, "action_type": CATALOG_SCHEMA_ACTION_TYPE})
                mechanism = "action_type"
                mine = _wait_for_target(gateway, guid, e, schema)
                if not mine:
                    st = gateway.engine_action_status(action_guid)
                    if st.status in FAILED_ACTION_STATUSES:
                        fallback = egeria_first_sentence(st.message or f"the action ended {st.status}")[0]
                    else:
                        raise GatewayError(
                            f"Egeria's attach action {action_guid[:8]} is {st.status or 'status not stated'} but the target for "
                            f"{schema} is not in the cataloger's list yet (still waiting; not attaching a second time)")
            if not mine and fallback:
                mechanism = "add_catalog_target"
                gateway.add_catalog_target(guid, target_name(e, schema))
                mine = _targets_for(gateway.list_catalog_targets(), guid, e, schema)
        if not mine:
            raise GatewayError(f"the target for {schema} was added but is not in the cataloger's list on read-back")
    # A proof row proves ONE element: the target must be readable as a relationship ON that element's GUID.
    # (The cataloguer's target list hides a target whose element is archived, and matches by name too.)
    own = _own_catalog_targets(gateway, guid)
    if not own:
        raise GatewayError(f"the target for {schema} is in the cataloger's list but the element {guid[:8]} "
                           f"shows no CatalogTarget relationship when read by its own GUID")
    status = None
    try:
        status = gateway.connector_status()
    except GatewayError:
        pass
    _proof(registry, slug, P_TARGET, schema=schema, element_guid=guid, target_guid=own[0].guid or mine[0].relationship_guid,
           qualified_name=qn, curation_id=payload.get("curation_id", ""), outbox_id=outbox_id,
           recorded_by=payload.get("by", ""),
           detail={"targets_for_schema": len(mine), "mechanism": mechanism,
                   "action_type": CATALOG_SCHEMA_ACTION_TYPE if action_guid else "",
                   "engine_action": action_guid, "fallback_reason": fallback, "create_error_adopted": create_note,
                   "connector_last_refresh": status.last_refresh_time if status else "",
                   "connector_note": "the connector's last refresh, not this target's",
                   # Brief I: '' = the requester is on the element (or nobody asked); else partial.
                   "requester": f"partial · {requester_note}" if requester_note else ""})
    return guid


def _schema_restored(registry, slug: str, schema: str) -> bool:
    """True when the schema's newest STATE proof is `restored` (the same row its "restored" state word derives from)."""
    rows = _by_node(registry.list_catalogue_commit_proofs(slug)).get(("schema", schema, ""), [])
    last = _latest(rows, STATE_PROOFS)
    return last is not None and last["proof"] == P_RESTORED


def element_archived_words(detail: dict) -> str:
    """An archived element's words, from its newest `element_archived` row (ruling 1, 2026-10-25)."""
    rb = (detail or {}).get("read_back")
    if rb == RB_CONFIRMED:
        return "archived · confirmed"
    if rb == RB_FAILED:
        return f"archived · read-back failed: {(detail or {}).get('reason') or 'reason not stated'}"
    return "archived · read-back pending"


def member_name(qualified_name: str, schema_qualified_name: str, schema: str) -> str:
    """A walk member as the screen names it: `widgets::made_on` under the schema, the schema's own name for itself."""
    if qualified_name == schema_qualified_name:
        return schema
    prefix = schema_qualified_name + "::"
    return qualified_name[len(prefix):] if qualified_name.startswith(prefix) else qualified_name


class ArchiveReadBackFailed(GatewayError):
    """Egeria accepted the archive calls (each has its `element_archived` row) but the read-back did not confirm
    every one. The write happened: it is never "not sent", and it is never sent again."""


def _element_archived(registry, slug: str, schema: str, guid: str, typ: str, qualified_name: str, read_back: str, *,
                      name: str, planned: int, curation_id: str, outbox_id: int | None, by: str,
                      reason: str = "") -> None:
    detail = {"read_back": read_back, "type": typ, "name": name, "planned": planned,
              **({"reason": reason} if reason else {})}
    _proof(registry, slug, P_ELEMENT_ARCHIVED, schema=schema, element_guid=guid, qualified_name=qualified_name,
           curation_id=curation_id, outbox_id=outbox_id, recorded_by=by,
           detail={**detail, "words": element_archived_words(detail)})


def _archive_seen(gateway: CatalogueGateway, qualified_name: str, guid: str, typ: str) -> str:
    """'' when the read-back shows THIS element archived (its own name free, its Memento copy found by the archived
    name, typed with the element's own type, and matched by GUID); otherwise why not. A read that cannot be made
    raises: that is not an answer, and the element's row stays pending."""
    if not qualified_name:
        return "no qualified name to read it back by"
    if gateway.read_element(qualified_name) is not None:
        return "still readable under its own name"
    if _archived_copy(gateway, qualified_name, guid, typ) is None:
        return "no archived copy under its archived name"
    return ""


def archive_attempt(proofs: list[dict], schema: str, outbox_id: int | None) -> dict | None:
    """One leave-out's archive, from its `element_archived` rows (one outbox row's): None when Egeria accepted no
    call for it. `accepted` counts the elements with a row, `planned` the walk; `elements` is each element's newest
    row, in the order the calls were accepted."""
    rows = [p for p in proofs if p["proof"] == P_ELEMENT_ARCHIVED and p["schema_name"] == schema
            and p.get("outbox_id") == outbox_id]
    if not rows:
        return None
    newest: dict[str, dict] = {}
    for r in rows:                                   # oldest first: the last row per element is its state
        newest[r["element_guid"]] = r
    els = list(newest.values())
    planned = max(int((r["detail"] or {}).get("planned") or 0) for r in rows)
    by_state = {k: [r for r in els if (r["detail"] or {}).get("read_back") == k] for k in (RB_PENDING, RB_CONFIRMED, RB_FAILED)}
    return {"accepted": len(els), "planned": planned or len(els), "elements": els, **by_state}


def apply_leave_out(registry, gateway: CatalogueGateway, payload: dict, *, outbox_id: int | None = None) -> str:
    """Detach the schema's target and remove or archive its element. Idempotent.

    The form is re-derived from a fresh walk here, BEFORE anything is sent, and the
    SAFER one wins: if the preview said soft delete and something now hangs off the
    schema it is archived; a failed read raises (nothing sent). The walk must hold
    exactly the GUID set the preview counted (`payload["plan_guids"]`): a GUID the
    preview did not count, one it counted that the walk no longer holds, or no set
    at all is refused with "the plan changed · preview again" and nothing is sent.
    A restored schema passes no lifted scope; a walk holding a SurveyReport is
    refused whole. Both forms send one call per element, leaf first (columns,
    tables, the connection graph, the schema type, then the schema), `forLineage`
    true, never a cascade: an archive archives each element leaf-first."""
    slug, schema = payload["slug"], payload["schema"]
    e = _entity(registry, slug)
    qn = schema_qn(e, schema)
    cid = payload.get("curation_id", "")
    if current_schema_choice(registry, slug, schema) == CATALOGUE:
        log.info("catalog commit: %s/%s was chosen again after its leave-out was queued; not removing", slug, schema)
        return ""
    el = gateway.read_element(qn)
    guid = el.guid if el else ""
    by = payload.get("by", "")
    restored = _schema_restored(registry, slug, schema)

    def refuse_117(words: str = gw.ISSUE_117_WORDS) -> None:
        # Defence behind the preview: whatever queued this, nothing is sent for a schema Egeria holds,
        # and the refusal is a ROW, so the header derives "0 archived" from it rather than from silence.
        _proof(registry, slug, P_LEAVE_OUT_BLOCKED, schema=schema, element_guid=guid, qualified_name=qn, curation_id=cid,
               outbox_id=outbox_id, recorded_by=by, detail={"note": KEPT_NOT_SENT, "form": payload.get("form", ""),
                                                            **({"restored": True} if restored else {})})
        raise GatewayError(f"{schema}: {words}")

    if el is not None and restored and gw.issue_117_blocked(slug):
        refuse_117(RESTORED_BLOCKED)                      # a restored schema passes no lifted scope
    if gw.issue_117_blocked(slug, scope=gw.LEAVE_OUT_ARCHIVE) and el is not None:
        refuse_117()                                      # re-armed (RE_ISSUE_117_BLOCK) and no override for the slug
    read: dict = {}
    walk: list[tuple[str, str, str]] = []
    form, scope = SOFT_DELETE, ""
    if el is not None:
        # Walk again BEFORE anything is sent (the target detach included): the form, the block and the plan are all
        # decided from this one read. The CatalogTarget is structural, so the detach does not change the walk.
        read = read_hangs_off(gateway, e, schema)
        if read["state"] == "cannot_check":
            raise GatewayError(f"{schema}: {CANT_CHECK}: {read['error']}")
        if read.get("in_use"):
            raise GatewayError(in_use_text(schema, read["in_use"]))
        walk = leave_out_walk(read, qn) if read["state"] == "read" else []
        # The SAFER form wins: if the preview said soft delete and something now hangs off the schema, it archives.
        form = ARCHIVE if ARCHIVE in (payload.get("form"), read.get("form")) else SOFT_DELETE
        scope = gw.LEAVE_OUT_ARCHIVE if form == ARCHIVE and not restored else ""
        if gw.issue_117_blocked(slug, scope=scope):
            refuse_117()                                  # the soft delete keeps the block; nothing is detached
        reports = survey_reports_in(walk)
        if reports:
            # Refused whole, nothing sent. No leave_out_blocked row: its words are the archive-cascade block's, and
            # this is not that; the outbox row's failure carries the sentence.
            log.warning("leave-out %s/%s refused: the walk holds SurveyReport %s", slug, schema, ", ".join(reports))
            raise GatewayError(f"{schema}: {SURVEY_REPORT_REFUSED}")
        if untyped_in(walk):
            log.warning("leave-out %s/%s refused: a walk member has no type name", slug, schema)
            raise GatewayError(f"{schema}: {UNKNOWN_TYPE_REFUSED}")
        counted = payload.get("plan_guids")
        now_held = set(plan_guids(walk))
        if counted is None or read["state"] != "read" or now_held != set(counted):
            extra = sorted(now_held - set(counted or []))
            gone = sorted(set(counted or []) - now_held)
            _proof(registry, slug, P_LEAVE_OUT_PLAN_CHANGED, schema=schema, element_guid=guid, qualified_name=qn,
                   curation_id=cid, outbox_id=outbox_id, recorded_by=by,
                   detail={"note": PLAN_CHANGED, "form": form, "not_counted": extra, "no_longer_held": gone,
                           "preview_set": "none in the payload" if counted is None else f"{len(counted)} counted"})
            raise GatewayError(f"{schema}: {PLAN_CHANGED}")
    if guid:
        for t in [t for t in gateway.list_catalog_targets() if t.element_guid == guid]:
            _send_destructive(registry, gateway, "remove_catalog_target", slug=slug, schema=schema, element_guid=guid,
                              typ="CatalogTarget", relationship_guid=t.relationship_guid, qualified_name=qn,
                              curation_id=cid, outbox_id=outbox_id, by=payload.get("by", ""))
        if any(t.element_guid == guid for t in gateway.list_catalog_targets()):
            raise GatewayError(f"the target for {schema} is still in the cataloger's list after removal")
        _proof(registry, slug, P_DETACHED, schema=schema, element_guid=guid, qualified_name=qn,
               curation_id=cid, outbox_id=outbox_id, recorded_by=payload.get("by", ""),
               detail={"note": "target gone from the cataloguer's list on read-back"})
    if el is None:
        # An archived schema is no longer under its own name (Egeria renames it `<qn>_archivedOn_<date>`): it is
        # found by that name. A lookup that cannot be made raises, never "absent".
        archived = gateway.archived_siblings(qn)
        if archived:
            _proof(registry, slug, P_ARCHIVED, schema=schema, element_guid=archived[0].guid, qualified_name=qn,
                   curation_id=cid, outbox_id=outbox_id,
                   detail={"note": "already archived when read", "archived_qualified_name": archived[0].qualified_name,
                           "archived_on": archived[0].archived_on})
            return archived[0].guid
        _proof(registry, slug, P_REMOVED, schema=schema, qualified_name=qn, curation_id=cid, outbox_id=outbox_id,
               detail={"form": "absent", "note": "no schema element in Egeria when read"})
        return ""
    # Every element by GUID, leaf first (columns, tables, the template's connection graph, the schema type), then the
    # schema: tables, columns and the schema type are anchored to the database, so deleting the schema never cascades
    # them. Each is read back; the schema's own read-back is the proof row. `walk` is the list read above, before the
    # detach, and equal to the GUID set the preview counted.
    order = walk[:-1]
    send = dict(slug=slug, schema=schema, form=form, curation_id=cid, outbox_id=outbox_id, by=by, scope=scope)
    rb = dict(planned=len(walk), curation_id=cid, outbox_id=outbox_id, by=by)
    for g, typ, mqn in walk:
        _send_destructive(registry, gateway, "delete", element_guid=g, typ=typ, qualified_name=mqn or "", **send)
        if form == ARCHIVE:
            # Ruling (1) 2026-10-25: the row the moment Egeria accepts the call, before any read-back.
            _element_archived(registry, slug, schema, g, typ, mqn or "", RB_PENDING,
                              name=member_name(mqn or g, qn, schema), **rb)
    if form == ARCHIVE:
        return _read_back_archive(registry, gateway, slug, schema, qn, guid, walk, rb,
                                  detail={"form": ARCHIVE, "hangs_off": read["hangs_off"]["by_type"],
                                          "content": read["content"]})
    for g, typ, mqn in order:
        if mqn:
            _prove_gone(gateway, mqn, form, schema, g, typ)
    now = gateway.read_element(qn)
    if now is not None or gateway.elements_under(qn + "::"):
        raise GatewayError(f"{schema}: the delete was sent but the read-back still finds elements")
    _proof(registry, slug, P_REMOVED, schema=schema, element_guid=guid, qualified_name=qn, curation_id=cid,
           outbox_id=outbox_id, recorded_by=payload.get("by", ""),
           detail={"form": SOFT_DELETE, "elements_deleted": len(order) + 1})
    return guid


def _read_back_archive(registry, gateway: CatalogueGateway, slug: str, schema: str, qn: str, guid: str,
                       walk: list[tuple[str, str, str]], rb: dict, *, detail: dict) -> str:
    """Read back every element whose archive Egeria accepted, in walk order, and move its row: confirmed, or
    "read-back failed: <reason>". A read that raises leaves its row and every later one pending. All confirmed
    writes the schema's `archived` row; anything else raises `ArchiveReadBackFailed` naming the first element
    not confirmed (the outbox keeps the "not retried: destructive write" guard)."""
    failed: tuple[str, str] | None = None
    for g, typ, mqn in walk:
        name = member_name(mqn or g, qn, schema)
        try:
            reason = _archive_seen(gateway, mqn or "", g, typ)
        except Exception as exc:                          # not an answer: this row and the rest stay pending
            said = egeria_first_sentence(" ".join(str(exc).split()) or type(exc).__name__)[0]
            raise ArchiveReadBackFailed(f"{schema}: read-back failed on {name}: {said}") from exc
        _element_archived(registry, slug, schema, g, typ, mqn or "", RB_FAILED if reason else RB_CONFIRMED,
                          name=name, reason=reason, **rb)
        if reason and failed is None:
            failed = (name, reason)
    if failed is not None:
        raise ArchiveReadBackFailed(f"{schema}: read-back failed on {failed[0]}: {failed[1]}")
    _proof(registry, slug, P_ARCHIVED, schema=schema, element_guid=guid, qualified_name=qn,
           curation_id=rb["curation_id"], outbox_id=rb["outbox_id"], recorded_by=rb["by"],
           detail={**detail, "elements_archived": len(walk)})
    return guid


def _archived_copy(gateway: CatalogueGateway, qualified_name: str, guid: str, type_name: str = ""):
    """The archived copy of THIS element (by its GUID) under the name Egeria gives it on archive, or None. The find
    is typed with `type_name`, the element's own type, when the caller knows it: a guess from the name's prefix
    types every element under a schema as the schema (`CatalogueGateway.archived_siblings`)."""
    return next((x for x in gateway.archived_siblings(qualified_name, type_name=type_name)
                 if x.guid == guid and x.archived), None)


def _prove_gone(gateway: CatalogueGateway, qualified_name: str, form: str, schema: str, guid: str = "",
                type_name: str = "") -> None:
    """The read-back that proves one element gone (soft delete) or archived: a Memento, renamed by Egeria to
    `<qn>_archivedOn_<date>` and visible with `forLineage`, found by that name and matched by its GUID."""
    if gateway.read_element(qualified_name) is not None:
        raise GatewayError(f"{schema}: {qualified_name} is still readable after its {form}")
    if form == ARCHIVE and _archived_copy(gateway, qualified_name, guid, type_name) is None:
        raise GatewayError(f"{schema}: {qualified_name} was archived but the read-back does not show it")


def _guid_to_read(registry, slug: str, schema: str, by_name) -> str:
    """The GUID whose own links the read-back walks: the element RE created or adopted (its newest
    proof row that names one, unless a removal came after), else the one the name resolves to. The
    name only ever says WHICH element; every fact then comes from that GUID's relationships."""
    rows = _by_node(registry.list_catalogue_commit_proofs(slug)).get(("schema", schema, ""), [])
    last = _latest(rows, (P_ATTACH_REQUESTED, P_TARGET, P_ELEMENTS, P_ADOPTED, P_RESTORED, P_REMOVED, P_ARCHIVED))
    if last is not None and last["proof"] not in (P_REMOVED, P_ARCHIVED) and last["element_guid"]:
        return last["element_guid"]
    return by_name.guid


def _read_schema_by_guid(gateway: CatalogueGateway, guid: str, qn: str):
    """schema -> its own `Schema` link -> schema type -> `AttributeForSchema` tables ->
    `NestedSchemaAttribute` columns, every hop a relationship read by GUID. Returns
    (guid, the schema's own CatalogTarget relationships, table names, column count, element count)."""
    rels = gateway.relationships(guid)
    own = [r for r in rels if r.type_name == "CatalogTarget"]
    tables: list[str] = []
    cols = 0
    n = 0
    for st in (r for r in rels if r.type_name == "Schema" and r.other_guid):
        n += 1
        for t in (r for r in gateway.relationships(st.other_guid) if r.type_name == "AttributeForSchema"):
            n += 1
            tq = t.other_qualified_name or ""
            tables.append(tq[len(qn) + 2:] if tq.startswith(qn + "::") else (tq.rsplit("::", 1)[-1] or t.other_name))
            for c in gateway.relationships(t.other_guid):
                if c.type_name == "NestedSchemaAttribute":
                    cols += 1
                    n += 1
    return guid, own, sorted(tables), cols, n


def read_back(registry, gateway: CatalogueGateway, slug: str, schemas: list[str], *,
              curation_id: str = "", by: str = "", reread: bool = False) -> dict:
    """Read Egeria and write what it says as proof rows. The one place a schema
    becomes `catalogued`: elements found under it by qualified name.

    A failed read writes a `read_failed` row and changes no state, so "Egeria
    could not be read just now" never poses as a state of any schema."""
    e = _entity(registry, slug)
    summary = {"catalogued": 0, "attached_waiting": 0, "read_failed": 0, "schemas": {}, "errors": []}
    started = time.perf_counter()
    # F29 (2026-10-30): this used to list EVERY target of the JDBC cataloguer first (73 on the coco box: 21.8 s, then
    # a 30 s timeout twice), and the list was never used: each schema's own CatalogTarget is read from its own
    # relationships below. Only the connector's status is read here.
    try:
        status = gateway.connector_status()
    except GatewayError as exc:
        _proof(registry, slug, P_READ_FAILED, node_kind="database", curation_id=curation_id,
               detail={"error": str(exc), "what": "the cataloguer's connector"})
        summary["read_failed"] += 1
        summary["errors"].append(f"the cataloger's connector: {exc}")
        return summary
    if status is not None:
        _proof(registry, slug, P_CONNECTOR, node_kind="database", curation_id=curation_id,
               detail={"last_refresh_time": status.last_refresh_time, "status": status.status,
                       "note": "the connector's own time; no per-target time exists"})
    for schema in schemas:
        qn = schema_qn(e, schema)
        try:
            el = gateway.read_element(qn)
            found = _read_schema_by_guid(gateway, _guid_to_read(registry, slug, schema, el), qn) if el else None
        except GatewayError as exc:
            _proof(registry, slug, P_READ_FAILED, schema=schema, curation_id=curation_id,
                   detail={"error": str(exc)})
            summary["read_failed"] += 1
            summary["errors"].append(f"{schema}: {exc}")
            continue
        if el is None or found is None:
            continue
        guid, own, tables, cols, n_elements = found
        # Only tables and columns count as cataloged content: the template's own connection graph
        # (4 elements, rehearsal 2) is under every schema from the moment it is created.
        if tables or cols:
            _proof(registry, slug, P_ELEMENTS, schema=schema, element_guid=guid, qualified_name=qn,
                   curation_id=curation_id, recorded_by=by, target_guid=own[0].guid if own else "",
                   detail={"tables": tables, "columns": cols, "elements": n_elements})
            summary["catalogued"] += 1
            summary["schemas"][schema] = "catalogued"
        elif own:
            _proof(registry, slug, P_TARGET, schema=schema, element_guid=guid, target_guid=own[0].guid,
                   qualified_name=qn, curation_id=curation_id, recorded_by=by,
                   detail={"connector_last_refresh": status.last_refresh_time if status else "",
                           "connector_note": "the connector's last refresh, not this target's"})
            summary["attached_waiting"] += 1
            summary["schemas"][schema] = "attached_waiting"
    if reread:
        # Refresh only (the route): a commit's own read-back step leaves its leave-out's rows as the apply left them.
        reread_archives(registry, gateway, slug, summary, by=by)
    _read_database_facts(registry, gateway, slug, curation_id, summary)
    if hasattr(gateway, "find_endpoints"):
        # D1-a: every family Egeria holds at this endpoint, and the re-anchor plan (logged). Its own row; a failed
        # endpoint read is said on that row and never makes a schema's own read-back fail.
        ep = endpoint_identity.record_resolution(registry, gateway, e, curation_id=curation_id, by=by)
        summary["endpoint"] = {"error": (ep["resolution"] or {}).get("error", ""),
                               "families": (ep["resolution"] or {}).get("families") or [],
                               "pairs": (ep["plan"] or {}).get("pairs")}
    log.info("read_back_timing slug=%s schemas=%d total_ms=%.0f cataloged=%d attached_waiting=%d read_failed=%d",
             slug, len(schemas), (time.perf_counter() - started) * 1000, summary["catalogued"],
             summary["attached_waiting"], summary["read_failed"])
    return summary


def read_back_line(summary: dict, chips_words: str) -> str:
    """F28: what a read-back says on the button's status line. A read that failed says it could not read, with
    Egeria's words, and that nothing changed: never the counts first, because "cataloged by Egeria 0" after a failed
    read is a failure shown as a number. Counts lead only when something was read."""
    n = summary.get("read_failed") or 0
    errors = summary.get("errors") or []
    if n and not (summary.get("catalogued") or summary.get("attached_waiting")):
        first = errors[0] if errors else "no reason given"
        more = f" · {n - 1} more read{'' if n - 1 == 1 else 's'} failed" if n > 1 else ""
        return f"could not read Egeria · {first}{more} · nothing changed: the counts are from the last read that worked"
    tail = f" · {n} read{'' if n == 1 else 's'} failed: {'; '.join(errors)}" if n else ""
    return f"read back: {chips_words or 'nothing read back from Egeria'}{tail}"


def reread_archives(registry, gateway: CatalogueGateway, slug: str, summary: dict, *, by: str = "") -> None:
    """Refresh re-reads (ruling 2, 2026-10-25): every leave-out whose archive calls Egeria all accepted but whose
    read-back did not confirm them is read again, element by element, and each answer is a new `element_archived`
    row. When every element is confirmed the schema gets its `archived` row, and the commit's leave-outs step and
    state are worded again from the rows. A read that raises leaves the rows as they are and counts a failed read.
    Nothing is sent to Egeria but reads."""
    from resource_explorer.curate_plan import Curations

    proofs = registry.list_catalogue_commit_proofs(slug)
    leave_rows = [r for r in registry.list_catalogue_outbox_rows(slug) if r["element_kind"] == KIND_LEAVE_OUT]
    touched: set[str] = set()
    for r in leave_rows:
        payload = r.get("payload") or {}
        schema = str(payload.get("schema") or "")
        a = archive_attempt(proofs, schema, r["id"])
        if a is None or a["accepted"] != a["planned"] or len(a[RB_CONFIRMED]) == a["accepted"]:
            continue
        cid = str(payload.get("curation_id") or "")
        rb = dict(planned=a["planned"], curation_id=cid, outbox_id=r["id"], by=by)
        all_seen = True
        for el in a["elements"]:
            d = el["detail"] or {}
            if d.get("read_back") == RB_CONFIRMED:
                continue
            try:
                reason = _archive_seen(gateway, el["qualified_name"] or "", el["element_guid"], d.get("type") or "")
            except Exception as exc:
                _proof(registry, slug, P_READ_FAILED, schema=schema, curation_id=cid,
                       detail={"error": " ".join(str(exc).split())[:gw.MAX_EGERIA_TEXT] or type(exc).__name__,
                               "what": f"the archive read-back of {d.get('name') or el['element_guid']}"})
                summary["read_failed"] += 1
                all_seen = False
                break
            _element_archived(registry, slug, schema, el["element_guid"], d.get("type") or "", el["qualified_name"] or "",
                              RB_FAILED if reason else RB_CONFIRMED, name=d.get("name") or "", reason=reason, **rb)
            all_seen = all_seen and not reason
        if all_seen:
            own = next((el for el in a["elements"] if (el["detail"] or {}).get("type") == "DeployedDatabaseSchema"), None)
            _proof(registry, slug, P_ARCHIVED, schema=schema, element_guid=own["element_guid"] if own else "",
                   qualified_name=own["qualified_name"] if own else "", curation_id=cid, outbox_id=r["id"],
                   recorded_by=by, detail={"form": ARCHIVE, "elements_archived": a["planned"],
                                           "note": "every accepted archive confirmed on a later read-back"})
            summary.setdefault("schemas", {})[schema] = "archived"
        if cid:
            touched.add(cid)
    cur = Curations(registry)
    for cid in touched:
        rec = cur.get(cid) or {}
        rows = [x for x in leave_rows if x.get("run_id") == cid]
        if rec and rows:
            _step_from_outbox(cur, cid, "leave_outs", rows, "remove", registry, settle=True)
            if rec.get("state") == NEEDS_READ_BACK:
                cur.finish(cid)


def _read_database_facts(registry, gateway: CatalogueGateway, slug: str, curation_id: str, summary: dict) -> None:
    """The database element's zones (a fact read back, whoever set them) and how its survey ended."""
    published = _latest(_by_node(registry.list_catalogue_commit_proofs(slug)).get(("database", "", ""), []),
                        (P_DATABASE,))
    if published is None:
        return
    try:
        zones: list[str] | None = list(gateway.read_zones(published["element_guid"]))
    except GatewayError as exc:
        _proof(registry, slug, P_READ_FAILED, node_kind="database", curation_id=curation_id,
               detail={"error": str(exc), "what": "the database element's zones"})
        summary["read_failed"] += 1
        zones = None
    if zones is not None:   # a read that failed is None; `[]` is a read that found no ZoneMembership
        _proof(registry, slug, P_ZONES_READ, node_kind="database", element_guid=published["element_guid"],
               curation_id=curation_id, detail={"zones": zones})
    try:
        summary["survey"] = settle_survey(registry, gateway, slug)
    except GatewayError as exc:
        _proof(registry, slug, P_READ_FAILED, node_kind="database", curation_id=curation_id,
               detail={"error": str(exc), "what": "how the survey ended"})
        summary["read_failed"] += 1




def _engine_report_words(re_report_guid: str = "") -> str:
    """Which report holds RE's measures, and that the engine survey's own report is another one."""
    return (f"RE's measures are in report {re_report_guid[:8]}; " if re_report_guid else "") + ENGINE_REPORT_WORDS


def _re_report_guid(db_rows: list) -> str:
    """The report RE's own measures were published into (newest report proof), or ""."""
    row = _latest(db_rows, (P_REPORT,))
    return str((row or {}).get("element_guid") or "")


def _submitted_words(submitted_at: str, names: list[str], bad: list[str], guid: str, re_report: str = "") -> str:
    return (f"submitted · {_stamp(submitted_at)} · {SURVEY_LINE}: {', '.join(names)} · engine action {guid[:8]}"
            + (f" · not scopable (comma): {', '.join(bad)}" if bad else "")
            + f" · {_engine_report_words(re_report)}")


def settle_survey(registry, gateway: CatalogueGateway, slug: str) -> str:
    """Read how the newest native survey turned out and say so on its curation step.

    "done" only when the read-back shows the survey report WITH annotations; a failed engine
    action reads Egeria's word; anything else stays "submitted" (with what was seen). Initiation
    is never completion. Returns "" (no survey), "settled" (already final), "done", "failed" or
    "submitted". Writes a proof row only for a final outcome, so a pending survey is read again."""
    from resource_explorer.curate_plan import Curations
    rows = registry.list_catalogue_commit_proofs(slug)
    db_rows = _by_node(rows).get(("database", "", ""), [])
    started = _latest(db_rows, (P_SURVEY,))
    published = _latest(db_rows, (P_DATABASE,))
    if started is None or published is None:
        return ""
    d = started["detail"]
    action = d.get("engine_action") or started["element_guid"]
    for r in db_rows:
        if r["proof"] == P_SURVEY_RESULT and r["detail"].get("engine_action") == action:
            return "settled"
    cur = Curations(registry)
    cid = started.get("curation_id") or ""
    names = list(d.get("includeSchemaNames") or [])
    bad = list(d.get("not_scopable") or [])
    submitted_at = d.get("submitted_at") or started["read_at"]
    re_rep = _re_report_guid(db_rows)
    out = gateway.survey_outcome(published["element_guid"], action, submitted_at)
    status = (out.action_status or "").upper()
    if status in FAILED_ACTION_STATUSES:
        first, rest = egeria_first_sentence(out.message or f"the engine action ended {out.action_status}")
        _proof(registry, slug, P_SURVEY_RESULT, node_kind="database", element_guid=out.report_guid, curation_id=cid,
               detail={"outcome": "failed", "engine_action": action, "action_status": out.action_status,
                       "message": out.message, "annotations": out.annotations})
        if cid:
            cur.settle_step(cid, "survey", "failed", f"Egeria's survey failed: {first}", more=rest)
        return "failed"
    if status == "COMPLETED" and out.annotations:
        _proof(registry, slug, P_SURVEY_RESULT, node_kind="database", element_guid=out.report_guid, curation_id=cid,
               detail={"outcome": "annotated", "engine_action": action, "annotations": out.annotations,
                       "action_status": out.action_status})
        if cid:
            cur.settle_step(cid, "survey", "done",
                            f"done · report {out.report_guid[:8]} · {out.annotations} annotations · read back "
                            f"{_stamp(_now())} · {SURVEY_LINE}: {', '.join(names)} · "
                            f"the engine survey's report, separate from RE's survey report"
                            + (f" {re_rep[:8]}, where RE's measures went" if re_rep else ""))
        return "done"
    if status in RUNNING_ACTION_STATUSES or not status:
        # not over: "running in Egeria", with the annotations counted so far (never "done")
        so_far = (f" · {out.annotations} annotations so far" if out.annotations is not None else "")
        seen = f" · read back {_stamp(_now())}: running in Egeria · {out.action_status or 'status not stated yet'}{so_far}"
    elif status == "COMPLETED":
        seen = (f" · read back {_stamp(_now())}: the action COMPLETED but "
                + (f"the engine survey's report {out.report_guid[:8]} holds 0 annotations" if out.report_guid else "no survey report was seen"))
    else:
        seen = (f" · read back {_stamp(_now())}: status '{out.action_status}' · not one Resource Explorer knows"
                + (f" · {out.annotations} annotations so far" if out.annotations is not None else ""))
    if cid:
        cur.settle_step(cid, "survey", "submitted", _submitted_words(submitted_at, names, bad, action, re_rep) + seen)
    return "submitted"


# ── the commit itself ────────────────────────────────────────────────────────

def run_with_loop(fn: Callable[..., Any], *args, **kwargs):
    """Run `fn` on this thread with an event loop set: pyegeria's sync wrappers
    call `asyncio.get_event_loop()`, which a worker thread does not have."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return fn(*args, **kwargs)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def make_gateway(db_entity, identity=None) -> CatalogueGateway:
    """The real gateway. Tests replace this one name. `identity` None = `current_principal()`
    when the gateway first calls Egeria (the signed-in Caller on a Curate route)."""
    from resource_explorer.catalogue_gateway import PyegeriaCatalogueGateway
    return PyegeriaCatalogueGateway(db_entity, identity=identity)


def start_commit(registry, slug: str, author: str, *, refresh_now: bool = False,
                 gateway: CatalogueGateway | None = None, confirm_reinclude: list[dict] | None = None) -> dict:
    """Validate the press against a fresh preview and queue the run.

    The preview is recomputed here, with its Egeria reads: what the person saw
    is not trusted, only the rows are. Raises `CommitBlocked` (409) with the
    reasons when the commit is disabled.

    `confirm_reinclude` (S19): the person's Re-include confirmations, each `{"node": "schema"|"database", "name",
    "archived_guid"}`. The preview re-reads the archived copy here; a confirmation that names a copy which is no
    longer the newest one is refused whole (409), nothing queued."""
    from resource_explorer.catalogue_scope import build_scope_view
    from resource_explorer.curate_plan import Curations
    if not author:
        raise CommitBlocked(401, "Sign in to publish: the record needs an author.")
    from resource_explorer.egeria_clients import Caller

    # A live sign-in, checked before anything is written (Brief I): 401 otherwise. It is also who the run writes as
    # (2026-10-23: the person who pressed, from their session held in memory; see `_queue_as_presser`).
    pressed = Caller()
    db_entity = registry.get_database(slug, allow_unreadable=True)
    if db_entity is None:
        raise CommitBlocked(404, f"Database '{slug}' not found")
    if not registry.list_catalogue_scope_baselines(slug):
        # Nothing has ever been declared for this database. Catalogue-by-default is exactly
        # what the scope exists to prevent, so this stops before any Egeria call is made.
        raise CommitBlocked(409, NO_SCOPE_SENTENCE, [NO_SCOPE_SENTENCE])
    view = build_scope_view(registry, slug)
    derived = derive_commit_state(registry, slug, view)
    if gateway is None:
        try:
            gateway = make_gateway(db_entity)
        except Exception as exc:  # no client at all: every read-dependent row will say so
            log.warning("catalog commit: no Egeria gateway for %s: %s", slug, exc)
            gateway = None
    preview = build_preview(registry, slug, view, gateway, db_entity=db_entity, derived=derived,
                            confirms=confirm_reinclude)
    if preview.get("confirmation_changed"):
        raise CommitBlocked(409, SIBLING_CHANGED, [c["text"] for c in preview["confirmation_changed"]])
    if preview.get("all_refused"):
        # Brief Z2: every part the press would write is one the person may not change: refused whole (403).
        raise CommitBlocked(403, preview["not_permitted"][0]["reason"],
                            [r["text"] for r in preview["not_permitted"]]
                            + [f"{b['word']} · {b['reason']}" for b in preview.get("also_blocked") or []])
    if not preview["can_commit"]:
        raise CommitBlocked(409, "The commit is disabled: " + "; ".join(preview["blockers"]), preview["blockers"])
    selection = {
        "attach": preview["attach"],
        # Ruling (3): the GUID set the preview counted rides with each leave-out; the apply refuses on any drift.
        "leave_out": [{"schema": r["schema"], "form": r["form"], "plan_guids": list(r.get("plan_guids") or [])}
                      for r in preview["leave_out"]
                      if r["form"] in (SOFT_DELETE, ARCHIVE)],
        "blocked_117": [r["schema"] for r in preview["leave_out"] if r["form"] == "issue_117"],
        "survey_schemas": preview["survey"]["schemas"],
        "survey_not_scopable": preview["survey"]["not_scopable"],
        "refresh_now": bool(refresh_now),
        # S19: the confirmed re-includes, each naming the archived GUID the run re-checks before it creates.
        "reinclude": [{"node": r["node"], "name": r["name"], "archived_guid": r["archived_guid"],
                       "archived_on": r["archived_on"]} for r in preview.get("reinclude") or []],
        # Brief Z2: the parts refused at the press (said by the run as rows, never written), and a Portal
        # curator/admin override seen at the press, from the verified caller (the run re-checks every part as the
        # requester unless this is set, as Publish's run does).
        "not_permitted": [{"schema": r["schema"], "write": r["write"], "reason": r["reason"]}
                          for r in preview["not_permitted"]],
        "portal_role": _caller_overrides(),
    }
    from resource_explorer.activity_logger import log_survey
    activity_id = log_survey(
        registry, entity_type="database", entity_slug=slug, entity_name=db_entity.display_name,
        entity_location=f"{db_entity.host}:{db_entity.port}/{db_entity.database_name}",
        intent="curate", status="running",
        summary=f"Publishing {db_entity.display_name}: {len(selection['attach'])} schema targets…")
    rec = Curations(registry).create("database", slug, author=author, selection=selection,
                                     manifest=preview["manifest"], steps=list(STEPS_DB), activity_id=activity_id)
    # Queued, and written AS the person who pressed (owner rule "run as the initiating user", 2026-10-14; the #836
    # mechanism, `presser_hold`): Ownership and requestedBy = the author as before (Brief I).
    run_id = _queue_as_presser(registry, {"slug": slug, "curation_id": rec["id"]}, pressed,
                               result_ref=activity_id, requested_by=author)
    return {"curation": rec, "run_id": run_id, "activity_id": activity_id, "preview": preview}


def _queue_as_presser(registry, target: dict, pressed, *, result_ref: str, requested_by: str) -> str:
    """Enqueue a catalogue commit that writes as `pressed` (the request's signed-in Caller), the same way a pressed
    survey run publishes (#836, `presser_hold`): the run id is made first, the presser's session is held in THIS
    process's memory under it (only when this process claims `catalogue_commit` rows), and the row carries the flag,
    the person and a non-secret claim-affinity stamp. Never the token. A row claimed where nothing is held ends with
    the expired words and writes nothing (`refuse_presser_gone`), never as the service account."""
    import uuid

    from resource_explorer import presser_hold, run_queue

    run_id = str(uuid.uuid4())
    person = (getattr(pressed, "user_id", "") or "").strip()
    target = {**target, "publish_as_presser": bool(person), "presser": person}
    held = (bool(person) and run_queue.consumes_kind_here("catalogue_commit")
            and presser_hold.hold(run_id, person, getattr(pressed, "token", None)))
    try:
        held_by = presser_hold.held_by_for(run_id) if held else None
        if held_by:
            target["held_by"] = held_by
        elif held:
            presser_hold.release(run_id)         # no usable stamp: keep no session nobody is bound to
        return registry.enqueue_run("catalogue_commit", target, result_ref=result_ref, requested_by=requested_by,
                                    run_id=run_id)
    except BaseException:
        if held:
            presser_hold.release(run_id)
        raise


def refuse_presser_gone(registry, curation_id: str) -> dict:
    """A pressed commit whose presser's session is not held where the row was claimed (expired, released, another
    process, or a restart): nothing is written, as anyone, and every step says so with the expired words."""
    from resource_explorer.curate_plan import Curations
    from resource_explorer.egeria_clients import EXPIRED_SENTENCE

    cur = Curations(registry)
    cur.set_step(curation_id, "publish_elements", "failed", f"{EXPIRED_SENTENCE} · nothing written")
    for st in STEPS_DB[1:]:
        cur.set_step(curation_id, st, "skipped", f"nothing written: {EXPIRED_SENTENCE}")
    return cur.finish(curation_id)


_PAST = {"attach": "attached", "remove": "deleted in Egeria"}


#: A pressed commit's outbox row its own drain did not finish: the background drain leaves it (2026-10-23).
PRESS_AGAIN_WORDS = "not sent · press Publish again"


def _as_presser_row(row: dict) -> bool:
    """Whether an outbox row was enqueued inside a pressed run (its payload names `registry.AS_PRESSER_KEY`)."""
    from resource_explorer.registry import AS_PRESSER_KEY

    payload = row.get("payload")
    return isinstance(payload, dict) and AS_PRESSER_KEY in payload


def commit_written_as_line(registry, curation_id: str, since) -> str:
    """'written as <x>' for a commit, from the elements THIS commit wrote, as its proof rows record them: the first
    schema element it attached, then the first leave-out it archived (its archived copy). A proof row for a target
    that was already there (`already_attached`, "already archived when read") is not a write and is not read. Said
    only when the schema-targets or leave-outs step is done and the element was changed at or after `since` (the
    run's start); "written as: could not check" otherwise (`presser_hold.written_as_since`)."""
    from resource_explorer import presser_hold
    from resource_explorer.curate_plan import Curations

    rec = Curations(registry).get(curation_id) or {}
    steps = {s.get("name"): s.get("state") for s in rec.get("steps") or []}
    wrote = any(steps.get(st) == "done" for st in ("schema_targets", "leave_outs"))
    proofs = [p for p in registry.list_catalogue_commit_proofs(rec.get("entity_slug") or "")
              if p.get("curation_id") == curation_id and p.get("element_guid")] if rec else []
    attached = [p["element_guid"] for p in proofs if p["proof"] == P_TARGET
                and (p.get("detail") or {}).get("mechanism") != "already_attached"]
    archived = [p["element_guid"] for p in proofs if p["proof"] == P_ARCHIVED
                and (p.get("detail") or {}).get("note") != "already archived when read"]
    return presser_hold.written_as_since(attached[:1] + archived[:1], since, wrote=wrote)


def _archive_words(attempt: dict, last_error: str = "") -> str:
    """Ruling (2) 2026-10-25, from one leave-out's `element_archived` rows: "archived in Egeria (14 of 14 accepted) ·
    read-back failed on <element>: <reason> · do not press again · Refresh re-reads". The element is the first not
    confirmed; a pending one (its read raised) takes the reason the run recorded on its outbox row."""
    head = f"archived in Egeria ({attempt['accepted']} of {attempt['planned']} accepted)"
    if attempt["accepted"] < attempt["planned"]:
        # a call Egeria did not accept stopped the walk: what it said is the outbox row's (never "not sent")
        said = egeria_first_sentence(_unguarded(last_error))[0]
        return f"{head} · {said} · do not press again"
    first = next((r for r in attempt["elements"] if (r["detail"] or {}).get("read_back") != RB_CONFIRMED), None)
    if first is None:
        return f"{head} · confirmed"
    d = first["detail"] or {}
    if d.get("read_back") == RB_FAILED:
        name, reason = d.get("name") or first["element_guid"], d.get("reason") or "reason not stated"
    else:
        name, reason = _failed_on(last_error, first)
    return f"{head} · read-back failed on {name}: {reason} · {DO_NOT_PRESS}"


def _unguarded(last_error: str) -> str:
    """An outbox error without the "not retried: destructive write · " guard words and the schema prefix."""
    from resource_explorer.egeria_outbox import NOT_RETRIED
    text = " ".join(str(last_error or "").split())
    if text.startswith(NOT_RETRIED):
        text = text[len(NOT_RETRIED):].lstrip(" ·")
    return text


_FAILED_ON_RE = re.compile(r"read-back failed on (.+?): (.+?)(?: · Egeria said:|$)")


def _failed_on(last_error: str, row: dict) -> tuple[str, str]:
    """(element, reason) of a read that raised, as `_read_back_archive` worded it on the outbox row."""
    m = _FAILED_ON_RE.search(_unguarded(last_error))
    if m:
        return m.group(1), m.group(2)
    return (row["detail"] or {}).get("name") or row["element_guid"], "the read-back did not answer"


def _step_from_outbox(cur, cid: str, step: str, rows: list[dict], what: str, registry=None, *, settle: bool = False) -> None:
    """A step's state and words from its outbox rows and, for a leave-out, its proof rows. A leave-out whose archive
    calls Egeria accepted is judged by its `element_archived` rows, whatever the outbox row says: all confirmed is
    done; all accepted and some not confirmed is NEEDS_READ_BACK (ruling 2, 2026-10-25), never "failed" and never
    "not sent". `settle`: update a finished record without reopening it (a Refresh)."""
    set_step = cur.settle_step if settle else cur.set_step
    if not rows:
        set_step(cid, step, "skipped", f"no schema to {what}")
        return
    attempts: dict[int, dict] = {}
    if what == "remove" and registry is not None:
        slug = ((rows[0].get("payload") or {}).get("slug")) or ""
        proofs = registry.list_catalogue_commit_proofs(slug) if slug else []
        for r in rows:
            got = archive_attempt(proofs, (r.get("payload") or {}).get("schema") or "", r["id"])
            if got is not None:
                attempts[r["id"]] = got
    confirmed = {i for i, a in attempts.items() if a["accepted"] == a["planned"] and len(a[RB_CONFIRMED]) == a["accepted"]}
    needs = {i for i, a in attempts.items() if a["accepted"] == a["planned"] and i not in confirmed}
    done = [r for r in rows if r["status"] == "done" or r["id"] in confirmed]
    bad = [r for r in rows if r["status"] in ("failed", "dead") and r["id"] not in confirmed]
    wait = [r for r in rows if r["status"] in ("pending", "running")]
    verb = _PAST.get(what, what + "ed")
    parts = [f"{len(done)} of {len(rows)} {verb}, each with its proof row"]
    if not done and bad and all(r["id"] in attempts for r in bad):
        parts = []          # every failure is an accepted write: its own words say what happened, no "0 of n"
    if what == "attach" and registry is not None and done:
        # Two of three may already have been attached by an earlier commit: say which (the proof
        # row's mechanism is `already_attached` when the guard found the target already there).
        slug = ((rows[0].get("payload") or {}).get("slug")) or ""
        proofs = registry.list_catalogue_commit_proofs(slug) if slug else []
        already = 0
        for r in done:
            sch = (r.get("payload") or {}).get("schema")
            last = _latest([p for p in proofs if p["node_kind"] == "schema" and p["schema_name"] == sch], (P_TARGET,))
            if last and (last["detail"] or {}).get("mechanism") == "already_attached":
                already += 1
        if already:
            fresh = len(done) - already
            parts = [" · ".join(x for x in ((f"{fresh} attached" if fresh else ""), f"{already} already attached") if x)
                     + (f" (of {len(rows)} chosen)" if len(rows) != len(done) else "")]
    if what == "remove" and registry is not None and done:
        # what was actually done to each schema, from its proof row: an archive says "archived", never "removed"
        slug = ((rows[0].get("payload") or {}).get("slug")) or ""
        proofs = registry.list_catalogue_commit_proofs(slug) if slug else []
        kinds = []
        for r in done:
            sch = (r.get("payload") or {}).get("schema")
            last = _latest([p for p in proofs if p["node_kind"] == "schema" and p["schema_name"] == sch], (P_REMOVED, P_ARCHIVED))
            kinds.append("archived" if last and last["proof"] == P_ARCHIVED else "deleted")
        n_arch = kinds.count("archived")
        if n_arch == len(kinds):
            parts = [f"{len(done)} of {len(rows)} archived in Egeria, each with its proof row"]
        elif n_arch:
            parts = [f"{len(done)} of {len(rows)}: {len(kinds) - n_arch} deleted in Egeria, {n_arch} archived in Egeria, each with its proof row"]
    # 2026-10-23: a row of a pressed commit (`"as_presser"` in its payload) that its own inline drain did not finish is
    # never retried by the background drain (it would write as the service account): it waits for a press again.
    held = [r for r in wait if _as_presser_row(r)]
    wait = [r for r in wait if not _as_presser_row(r)]
    if wait:
        parts.append(f"{len(wait)} queued in the outbox (#{', #'.join(str(r['id']) for r in wait)})")
    for r in held:
        parts.append(f"{(r['payload'] or {}).get('schema')}: {PRESS_AGAIN_WORDS}")
    more: list[str] = []
    for r in bad:
        sch = (r['payload'] or {}).get('schema')
        if r["id"] in attempts:
            # Egeria accepted at least one archive call: never "not sent", never "press again" (ruling 2).
            parts.append(f"{sch}: {_archive_words(attempts[r['id']], r.get('last_error') or '')}")
            continue
        first, rest = egeria_first_sentence(r.get("last_error"))
        parts.append(f"{sch}: {first}"
                     + (f" · {PRESS_AGAIN_WORDS}" if _as_presser_row(r) else ""))
        if rest:
            more.append(f"{sch}: {rest}")
    hard = [r for r in bad if r["id"] not in needs]
    state = "done" if len(done) == len(rows) else "failed" if hard or wait or held else NEEDS_READ_BACK
    set_step(cid, step, state, " · ".join(parts), more="\n".join(more))


def fresh_report_shortfall(res: dict) -> str:
    """"k of n annotations published" when a NEW report holds fewer annotations in Egeria than RE built, else "".
    A reused report is worded by its own branch; no count from Egeria is "could not be counted", never a shortfall."""
    if res.get("report_reused") or res.get("report_error"):
        return ""
    k, n = res.get("annotations_in_egeria"), res.get("annotation_count")
    if isinstance(k, int) and isinstance(n, int) and k < n:
        return f"{k} of {n} annotations published"
    return ""


def report_step_words(res: dict, surveyed_at: str) -> tuple[str, str]:
    """The state and words of the "RE's own survey report" step, from what the publish returned.

    `annotation_count` is how many annotations RE BUILT from its survey. "published" is said only with
    a report id and no error. A create refused as a duplicate of the SAME survey run is reused (the
    surveyor looked the report up by its exact name): then the words give the count Egeria HOLDS,
    never RE's local count, and RE's own number only when the two differ. Any other failure fails the
    step with Egeria's first sentence."""
    n = res.get("annotation_count")
    when = md(surveyed_at) if surveyed_at else ""
    src = f" from the {when} survey" if when else ""
    guid = str(res.get("report_element_guid") or res.get("report_guid") or "")
    if res.get("report_error"):
        first, _ = egeria_first_sentence(str(res["report_error"]))
        if guid:
            return "failed", f"report {guid[:8]} was created, but its annotations were not: {first} · {n} annotations built"
        return "failed", f"report not published · {first} · {n} annotations built, none published ·{src}".rstrip(" ·")
    if res.get("report_reused"):
        k = res.get("annotations_in_egeria")
        held = f"{k} annotations in Egeria" if k is not None else "annotations in Egeria could not be counted"
        local = f" · built locally: {n}" if (n is not None and k is not None and n != k) else ""
        if res.get("annotation_error"):
            first, _ = egeria_first_sentence(str(res["annotation_error"]))
            return "failed", f"already in Egeria · report {guid[:8]} ·{src} · {held}{local} · the missing annotations were not published: {first}"
        return "done", f"already in Egeria · report {guid[:8]} ·{src} · {held}{local}"
    if guid:
        # A fresh report: the count is what Egeria returned a GUID for (`annotations_in_egeria`), never how many
        # were sent, and RE's own number only when the two differ. No count back is said, not assumed.
        k = res.get("annotations_in_egeria")
        if k is None:
            built = f" · {n} built" if n is not None else ""
            return "done", f"report {guid[:8]} · annotations in Egeria could not be counted{built} ·{src}"
        short = fresh_report_shortfall(res)
        if short:
            # Fewer landed than were built (0 of 76): not "done". The commit writes a failure row for it too.
            return "failed", f"report {guid[:8]} was created, but {short} ·{src}"
        local = f" · built locally: {n}" if (n is not None and n != k) else ""
        return "done", f"report {guid[:8]} · {k} annotations published{local} ·{src}"
    if n is None and not res:
        return "done", "report not found · the publish returned nothing"
    return "done", f"{n} annotations{src} · Egeria returned no report element id, so the report itself is not confirmed"


def _fail_step(cur, cid: str, step: str, exc: Exception | str, prefix: str = "") -> None:
    """A failed step reads Egeria's first sentence; the rest goes under "details"."""
    first, rest = egeria_first_sentence(str(exc) or type(exc).__name__)
    cur.set_step(cid, step, "failed", f"{prefix}{first}"[:400], more=rest)


def _is_timeout(exc: Exception) -> bool:
    text = " ".join(str(exc).split()).lower()
    return "408" in text or "timeout" in text or "timed out" in text


def _try_status(gateway):
    try:
        return gateway.connector_status()
    except Exception:
        return None


def parts_check_by_name(registry, gateway: CatalogueGateway, db, slug: str, names: list[str], ops: tuple[str, ...], *,
                        override: bool, members: bool = False, reader=None,
                        anchor_guid: str = "") -> tuple[list[str], list[dict]]:
    """The run's re-check (Brief Z2), as the requester (run_queue sets the caller), right before each schema's
    write is queued: (permitted names, not-permitted rows). The element checked is the one the write will use,
    found by its qualified name exactly as `apply_attach` / `apply_leave_out` find it, so an element adopted since
    the press is checked too. `members`: also DELETE on every element the leave-out's delete walks
    (`read_hangs_off`, the delete's own read). A failed read of the schema element denies that schema with the
    reason; a failed walk leaves the members to the delete, which then sends nothing."""
    from resource_explorer.workflows.curate import _reads_zones, curation_access

    if override or not names:
        return list(names), []
    if not _reads_zones():
        # Settled before any read (anonymous: denied; a Portal curator/admin: allowed): one decision for all.
        d = curation_access(registry, "database", slug, "", operations=ops, element_guid="")
        return (list(names), []) if d.allowed else ([], [not_permitted_row(n, d.reason) for n in names])
    parts, refused = [], []
    reads = _reads_zones()
    for name in names:
        guids: list[str] = []
        if reads:
            try:
                el = gateway.read_element(schema_qn(db, name))
                guids = [el.guid] if el is not None and el.guid else []
                if members and guids:
                    # A failed walk is not an access answer: the delete itself reads the same walk first and,
                    # failing, sends nothing ("couldn't check what hangs off it"). Only the schema is checked then.
                    read = read_hangs_off(gateway, db, name)
                    if read["state"] == "read":
                        guids += [g for g, _t, _q in read.get("delete_order") or [] if g]
            except GatewayError as exc:
                refused.append(not_permitted_row(name, f"could not check access in Egeria ({exc})"[:300]))
                continue
        parts.append((name, guids, ops))
    # One batched check: the schema itself with `ops`; with `members`, every element its delete walks with DELETE.
    checks = [((name, "self"), guids[:1] if members else guids, ops) for name, guids, _ in parts]
    if members:
        checks += [((name, "members"), guids[1:], MEMBER_DELETE_OPS) for name, guids, _ in parts if guids[1:]]
    # A schema not in Egeria yet is CREATED by an attach (then classified with Ownership, then attached): judged
    # as a new member of the database (round 3). A leave-out of one not in Egeria writes nothing.
    new_ops = (("CREATE", "CLASSIFY") + tuple(ops)) if not members and ops == ATTACH_OPS else ()
    got = parts_access(registry, slug, checks, reader=reader, new_ops=new_ops,
                       anchor_guid=anchor_guid) if checks else {}
    decided = {}
    for name, _g, _o in parts:
        head, rest = got[(name, "self")], got.get((name, "members"))
        decided[name] = rest if head.allowed and rest is not None and not rest.allowed else head
    ok = []
    for name, _g, _o in parts:
        d = decided[name]
        if d.allowed:
            ok.append(name)
        else:
            refused.append(not_permitted_row(name, d.reason))
    return ok, refused


def _record_not_permitted(registry, slug: str, curation_id: str, author: str, rows: list[dict], write: str) -> None:
    for r in rows:
        _proof(registry, slug, P_NOT_PERMITTED, schema=r["schema"], curation_id=curation_id, recorded_by=author,
               detail={"write": write, "reason": r["reason"], "words": r["words"]})


def _np_note(rows: list[dict]) -> str:
    return f"{NOT_PERMITTED_WORDS}: " + ", ".join(r["schema"] for r in rows) if rows else ""


def _add_step_note(cur, cid: str, step: str, note: str) -> None:
    if not note:
        return
    st = next((x for x in cur.get(cid)["steps"] if x["name"] == step), None)
    if st is not None:
        cur.set_step(cid, step, st["state"], " · ".join(x for x in (st.get("detail") or "", note) if x),
                     st.get("more") or "")


def _link_database_to_server(gateway, server_guid: str, database_guid: str, server_name: str):
    """The publish step's link to the server's DatabaseManager. Never raises: a gateway that fails outright is a
    `could not check` sentence."""
    from resource_explorer import database_server_link as dsl

    try:
        return gateway.link_database_to_server(server_guid, database_guid, server_name=server_name)
    except Exception as exc:  # noqa: BLE001 - the link never fails the publish step
        return dsl.ServerLink(dsl.COULD_NOT_CHECK, f"could not check ({egeria_first_sentence(str(exc))[0]}) · not linked")


def execute_commit(registry, curation_id: str, *, gateway: CatalogueGateway | None = None,
                   drain: Callable[..., Any] | None = None) -> dict:
    """Run the curation record's steps. Nothing raises past a step: a step that
    fails is a failed step on the record, and a step that depends on it is
    skipped with the reason, never run on a guess."""
    from resource_explorer.curate_plan import Curations
    cur = Curations(registry)
    rec = cur.get(curation_id)
    if not rec:
        raise KeyError(curation_id)
    slug = rec["entity_slug"]
    sel = rec.get("selection") or {}
    author = rec.get("author", "")
    try:
        db = registry.get_database(slug)       # credentials may be needed: not allow_unreadable
    except Exception as exc:                   # e.g. the stored password cannot be decrypted
        _fail_step(cur, curation_id, "publish_elements", exc)
        return cur.finish(curation_id)
    if db is None:
        cur.set_step(curation_id, "publish_elements", "failed", "database no longer registered")
        return cur.finish(curation_id)
    if gateway is None:
        gateway = make_gateway(db)
    # 2026-10-23: a pressed commit's outbox rows (attach, leave out) carry `"as_presser": true`, so only THIS run's
    # inline drain below, as the person who pressed, applies them. A background drain (the scheduler's, a retry after
    # a 5xx, one that claims between the enqueue and this drain) leaves them waiting; it never writes them as the
    # service account. A press again makes a new commit with its own rows.
    from resource_explorer import presser_hold
    from resource_explorer.registry import AS_PRESSER_KEY

    pressed_mark = {AS_PRESSER_KEY: True} if presser_hold.is_pressed() else {}
    if drain is None:
        from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

        def drain(run_id):  # noqa: E306
            # the ONE drain that may apply this run's marked rows, as the presser (`drain_as_presser`)
            return drain_outbox(registry, OutboxClients(catalogue_gateway=gateway, registry=registry),
                                lambda qn: "", run_id=run_id, drain_as_presser=bool(pressed_mark))

    # 0 ── Brief Z2: the run re-checks as the requester before anything is written (Publish's rule). A Portal
    # curator/admin override seen at the press is carried in the selection. The parts refused at the press are
    # rows of their own, never written.
    override = bool(sel.get("portal_role"))
    checked_db_guid: str | None = None          # the database element step 0 judged ('' = not in Egeria then)
    for r in sel.get("not_permitted") or []:
        _record_not_permitted(registry, slug, curation_id, author, [not_permitted_row(r["schema"], r["reason"])],
                              r.get("write") or "")
    if not override:
        from resource_explorer.workflows.curate import resource_access

        try:
            # Round 2: the database element the write will use, found by its qualified name exactly as step 1's
            # read finds it (an element adopted since the press, or never recorded by RE, is checked too).
            el = gateway.read_element(gw.database_qualified_name(gw.server_name_for(db), db.database_name))
            checked_db_guid = el.guid if el is not None else ""
            d = resource_access(registry, "database", slug, "publish", element_guid=checked_db_guid)
            why = "" if d.allowed else d.reason
        except GatewayError as exc:
            why = f"could not check access in Egeria ({exc})"[:300]
        except Exception as exc:  # noqa: BLE001 - a failure is a refusal with its reason, never a pass
            why = f"could not check access ({type(exc).__name__}: {exc})"[:300]
        if why:
            cur.set_step(curation_id, "publish_elements", "failed", f"{NOT_PERMITTED_WORDS} · {why}")
            for st in STEPS_DB[1:]:
                cur.set_step(curation_id, st, "skipped", f"{NOT_PERMITTED_WORDS}: nothing written")
            return cur.finish(curation_id)

    db_guid = ""
    # 1 ── the server and database elements
    cur.set_step(curation_id, "publish_elements", "running")
    try:
        # Brief I round 5: only elements THIS commit creates are stamped with the requester. A server
        # is shared by host:port (and may be someone else's, created outside RE); a database may
        # come from an earlier commit. Read before publishing: what already existed is never
        # re-stamped or re-owned. A read that fails counts as "existed" (the safe direction).
        existed, expected = {}, {
            "server": gw.server_qualified_name(gw.server_name_for(db)),
            "database": gw.database_qualified_name(gw.server_name_for(db), db.database_name)}
        found_db_guid = ""
        for which, qn_ in expected.items():
            try:
                el_ = gateway.read_element(qn_)
                existed[which] = el_ is not None
                if which == "database" and el_ is not None:
                    found_db_guid = el_.guid or ""
            except GatewayError:
                existed[which] = True

        def refuse_database(guid_: str, *, written: bool) -> str:
            """Round 3/4: judge a database element other than the one step 0 judged; "" = permitted. ANY
            failure of the check is a refusal (as step 0)."""
            if override or not guid_ or guid_ == checked_db_guid:
                return ""
            from resource_explorer.workflows.curate import resource_access

            try:
                d = resource_access(registry, "database", slug, "publish", element_guid=guid_)
                why_ = "" if d.allowed else d.reason
            except Exception as exc:  # noqa: BLE001 - a failure is a refusal with its reason, never a pass
                why_ = f"could not check access ({type(exc).__name__}: {exc})"[:300]
            if why_:
                cur.set_step(curation_id, "publish_elements", "failed",
                             f"{NOT_PERMITTED_WORDS} · {why_} (the database element "
                             f"{'Egeria returned' if written else 'found before the publish'}, {guid_[:8]})")
                for st in STEPS_DB[1:]:
                    cur.set_step(curation_id, st, "skipped", f"{NOT_PERMITTED_WORDS}: nothing more written")
            return why_

        # Round 4: the element the publish will update, found by its qualified name just now, is judged BEFORE
        # the write when it is not the one step 0 judged (a race since step 0).
        if refuse_database(found_db_guid, written=False):
            return cur.finish(curation_id)
        # S19: a publish under the database's name when Egeria holds none re-includes an archived database as a NEW
        # element. The same routine as the preview, re-read now: created only when the press confirmed exactly the
        # newest archived copy.
        db_reinclude = None
        if not existed["database"]:
            db_reinclude = reinclude_check(gateway, expected["database"],
                                           confirmed_guid_for(sel.get("reinclude"), "database"))
            if db_reinclude["state"] in (NEEDS_CONFIRMATION, "changed", "cannot_check"):
                why_ = (f"the database is {db_reinclude['text']} · confirm Re-include"
                        if db_reinclude["state"] == NEEDS_CONFIRMATION else f"the database: {db_reinclude['text']}")
                cur.set_step(curation_id, "publish_elements", "failed", f"{why_} · nothing created")
                for st in STEPS_DB[1:]:
                    cur.set_step(curation_id, st, "skipped", "needs the database element first: nothing created")
                return cur.finish(curation_id)
        before_pointer = getattr(registry.get_database(slug, allow_unreadable=True), "egeria_asset_guid", "") or ""
        pub = gateway.publish_database(
            db, db.db_user, db.db_password, registry=registry, submitted_by=author,
            reinclude_guid=(db_reinclude["sibling"].guid
                            if db_reinclude is not None and db_reinclude["state"] == "confirmed" else ""))
        returned = pub.database_guid
        if not returned:
            raise GatewayError("Egeria returned no database element")
        # Round 3/4: the surveyor resolves the database by its full qualifiedName (Brief L), but a create that
        # races another, or a lookup that lands elsewhere, can hand back an element neither check above judged.
        # It is judged before anything is attached, owned or surveyed against it, and held in a local until then.
        # The surveyor records its GUID on RE's row as it publishes (`set_database_egeria_guid`): on refusal that
        # pointer is put back, so RE never points at an element the person may not change.
        if returned != found_db_guid and refuse_database(returned, written=True):
            registry.set_database_egeria_guid(slug, before_pointer)
            return cur.finish(curation_id)
        db_guid = returned
        registry.set_database_egeria_guid(slug, db_guid)
        if db_reinclude is not None and db_reinclude["state"] == "confirmed":
            _proof(registry, slug, P_REINCLUDED, node_kind="database", element_guid=db_guid,
                   qualified_name=pub.database_qualified_name, curation_id=curation_id, recorded_by=author,
                   detail=reinclude_detail(db_reinclude, author))
        # Brief I: the server and database elements carry the requester like everything else a
        # queued person-action creates: `requestedBy` merged into their provenance, Ownership = the
        # requester. The database's Ownership is left to the owner step below when the Context
        # DECLARES an owner (that owner fact has its own ruling); otherwise it is the requester.
        from resource_explorer.egeria_clients import current_principal
        from resource_explorer.egeria_identity import on_behalf_of

        behalf = on_behalf_of(current_principal())
        declared = (((registry.get_context("database", slug) or {}).get("enrichment") or {})
                    .get("owner") or {}).get("value") or ""
        # Brief I round 6: SURE this commit created it = nothing under this host's name before AND
        # the returned GUID reads back with exactly that name. Should the publish hand back an element
        # under another name (a race, or a lookup that landed elsewhere), that one is never stamped,
        # and the step says so.
        notes = []
        for which, guid_, owner_ in (("server", pub.server_guid, behalf.owner),
                                     ("database", db_guid, "" if declared else behalf.owner)):
            if not guid_ or existed[which]:
                continue
            try:
                got = gateway.qualified_name_of(guid_)
            except GatewayError as exc:
                notes.append(f"{which} not read back ({egeria_first_sentence(str(exc))[0]}); requester not recorded")
                continue
            if got != expected[which]:
                notes.append(f"adopted an element for a different host ({got}); requester not recorded")
                continue
            note = gateway.mark_on_behalf(guid_, behalf.requester, owner_)
            if note:
                notes.append(note)
        # The database is related to its server's DatabaseManager (CapabilityAssetUse, OWNS), read first: the
        # outcome is a sentence on this step and on the database proof, never a failure of the step (a missing
        # link is recoverable on the next press or by the heal; a duplicate is the bug).
        server_link = _link_database_to_server(gateway, pub.server_guid, db_guid, pub.server_name)
        _proof(registry, slug, P_DATABASE, node_kind="database", element_guid=db_guid,
               qualified_name=pub.database_qualified_name, curation_id=curation_id, recorded_by=author,
               detail={"server_guid": pub.server_guid, "server_name": pub.server_name,
                       "requester": ("partial · " + "; ".join(notes)) if notes else "",
                       "server_link": {"outcome": server_link.outcome, "words": server_link.words,
                                       "capability_guid": server_link.capability_guid}})
        done = (f"server {pub.server_guid[:8]} · database {db_guid[:8]} · descriptions and versions supplied"
                f" · {server_link.words}")
        cur.set_step(curation_id, "publish_elements", "done",
                     done + (" · partial · " + "; ".join(notes) if notes else ""))
    except Exception as exc:
        _fail_step(cur, curation_id, "publish_elements", exc)

    # 1b ── owner, added after the database element is read back
    owner = (((registry.get_context("database", slug) or {}).get("enrichment") or {}).get("owner") or {}).get("value") or ""
    if not db_guid:
        cur.set_step(curation_id, "owner", "skipped", "no database element")
    elif not owner:
        cur.set_step(curation_id, "owner", "skipped", "not carried: owner, not declared on Context")
    else:
        failed_text = ""
        try:
            outcome, detail = gateway.set_owner(db_guid, owner)
        except Exception as exc:
            outcome, detail, failed_text = "failed", "", str(exc) or type(exc).__name__
        if outcome in ("set", "already", "refused"):
            _proof(registry, slug, P_OWNER, node_kind="database", element_guid=db_guid, curation_id=curation_id,
                   detail={"outcome": outcome, "owner": owner, "egeria_said": detail})
        if outcome == "failed":
            _fail_step(cur, curation_id, "owner", failed_text)
        else:
            cur.set_step(curation_id, "owner", "done", OWNER_REFUSED if outcome == "refused" else f"{detail}")

    # 2 ── one schema-kind target per schema, through the outbox
    attach = list(sel.get("attach") or [])
    pressed_np = {w: [not_permitted_row(r["schema"], r["reason"]) for r in sel.get("not_permitted") or []
                      if (r.get("write") or "") == w] for w in ("attach", "leave out")}
    np_attach: list[dict] = []
    if not attach:
        cur.set_step(curation_id, "schema_targets", "skipped",
                     _np_note(pressed_np["attach"]) or "no schema to attach")
    elif not db_guid:
        cur.set_step(curation_id, "schema_targets", "skipped", "needs the database element first: no database element")
    else:
        cur.set_step(curation_id, "schema_targets", "running")
        attach, np_attach = parts_check_by_name(registry, gateway, db, slug, attach, ATTACH_OPS, override=override,
                                                anchor_guid=db_guid)
        _record_not_permitted(registry, slug, curation_id, author, np_attach, "attach")
        for schema in attach:
            payload = {"slug": slug, "schema": schema, "database_guid": db_guid, "curation_id": curation_id, "by": author,
                       **pressed_mark}
            confirmed = confirmed_guid_for(sel.get("reinclude"), "schema", schema)
            if confirmed:
                payload["archived_sibling_guid"] = confirmed     # S19: the attach re-checks it before it creates
            registry.enqueue_outbox_element("database", slug, KIND_ATTACH, schema_qn(db, schema), payload,
                                            run_id=curation_id)
        drain(curation_id)
        rows = [r for r in registry.list_catalogue_outbox_rows(slug)
                if r["run_id"] == curation_id and r["element_kind"] == KIND_ATTACH]
        note = _np_note(pressed_np["attach"] + np_attach)
        if attach:
            _step_from_outbox(cur, curation_id, "schema_targets", rows, "attach", registry)
            _add_step_note(cur, curation_id, "schema_targets", note)
        else:
            cur.set_step(curation_id, "schema_targets", "skipped", note)

    # 2b ── leave outs
    leave = list(sel.get("leave_out") or [])
    blocked117 = list(sel.get("blocked_117") or [])
    # Brief Z2: the access check stands in front of the (unchanged) archive-cascade block, at run time as at the press.
    blocked117, np_blocked = parts_check_by_name(registry, gateway, db, slug, blocked117, LEAVE_OUT_OPS, override=override)
    _record_not_permitted(registry, slug, curation_id, author, np_blocked, "leave out")
    ok_leave, np_leave = parts_check_by_name(registry, gateway, db, slug, [i["schema"] for i in leave], LEAVE_OUT_OPS,
                                          override=override, members=True)
    _record_not_permitted(registry, slug, curation_id, author, np_leave, "leave out")
    leave = [i for i in leave if i["schema"] in ok_leave]
    np_note = _np_note(pressed_np["leave out"] + np_blocked + np_leave)
    for nm in blocked117:
        # nothing is sent; the refusal is recorded as a row so the header can say "0 archived" from it
        _proof(registry, slug, P_LEAVE_OUT_BLOCKED, schema=nm, qualified_name=schema_qn(db, nm), curation_id=curation_id,
               recorded_by=author, detail={"note": KEPT_NOT_SENT, "form": "issue_117"})
    if not leave:
        cur.set_step(curation_id, "leave_outs", "skipped",
                     " · ".join(x for x in (
                         (f"{len(blocked117)} left out, {KEPT_NOT_SENT}: {', '.join(blocked117)}" if blocked117
                          else "" if np_note else "no schema to delete or archive"), np_note) if x))
    else:
        cur.set_step(curation_id, "leave_outs", "running")
        for item in leave:
            registry.enqueue_outbox_element(
                "database", slug, KIND_LEAVE_OUT, schema_qn(db, item["schema"]) + "#leave_out",
                {"slug": slug, "schema": item["schema"], "form": item["form"], "curation_id": curation_id, "by": author,
                 "plan_guids": item.get("plan_guids"),       # None for a selection made before ruling (3): refused
                 **pressed_mark},
                run_id=curation_id)
        drain(curation_id)
        rows = [r for r in registry.list_catalogue_outbox_rows(slug)
                if r["run_id"] == curation_id and r["element_kind"] == KIND_LEAVE_OUT]
        _step_from_outbox(cur, curation_id, "leave_outs", rows, "remove", registry)
        _add_step_note(cur, curation_id, "leave_outs", np_note)

    # 3 ── RE's own survey report and annotations (no native survey started here)
    measured = registry.latest_measured_database_survey(slug)
    rep_guid = ""   # the report RE's measures went into, named on the survey step below
    if not db_guid:
        cur.set_step(curation_id, "survey_report", "skipped", "no database element")
    elif measured is None:
        cur.set_step(curation_id, "survey_report", "skipped", "no measured survey of RE's own to publish: run a survey first")
    else:
        try:
            res = gateway.publish_local_report(db, db.db_user, db.db_password, measured, registry=registry, submitted_by=author)
            rep_guid = res.get("report_element_guid", res.get("report_guid", "")) or ""
            failed_text = str(res.get("report_error") or res.get("annotation_error")
                              or fresh_report_shortfall(res) or "")
            if rep_guid and not failed_text:
                # A success row only for a report that has a GUID and no error: a created one, or a reuse
                # whose annotation count Egeria itself gave (`annotations_in_egeria`).
                _proof(registry, slug, P_REPORT, node_kind="database", element_guid=rep_guid,
                       curation_id=curation_id,
                       detail={"annotation_count": res.get("annotation_count"),
                               "surveyed_at": measured.get("surveyed_at", ""),
                               "report_error": "",
                               "outcome": "reused" if res.get("report_reused") else "published",
                               "note": (f"reused existing report (same survey run {measured.get('surveyed_at', '')})"
                                        if res.get("report_reused") else ""),
                               "annotations_in_egeria": res.get("annotations_in_egeria")})
            elif failed_text:
                # A failed publish (a 409, a 500, annotations that did not land) is a failed read of what was
                # asked for, never a success row, so no later word can derive "published" from it.
                _proof(registry, slug, P_READ_FAILED, node_kind="database", curation_id=curation_id,
                       element_guid=rep_guid,
                       detail={"error": failed_text, "what": "RE's own survey report",
                               "annotation_count": res.get("annotation_count"), "report_guid": rep_guid})
            state, words = report_step_words(res, measured.get("surveyed_at", ""))
            cur.set_step(curation_id, "survey_report", state, words)
            # Curate's publish table: one proof row per chosen measure, from the report read, never from the send.
            declared = res.get("declared_measures") or {}
            if declared.get("sent") or declared.get("not_built"):
                from resource_explorer import publish_table

                counts = publish_table.record_proofs(registry, slug, declared.get("sent") or [], report_guid=rep_guid,
                                                     curation_id=curation_id, recorded_by=author)
                _add_step_note(cur, curation_id, "survey_report",
                               publish_table.step_words(counts, declared.get("not_built")))
            elif declared.get("state") == "not_read":
                _add_step_note(cur, curation_id, "survey_report",
                               "declared measures not sent · the publish table could not be read")
        except Exception as exc:
            _fail_step(cur, curation_id, "survey_report", exc)

    # 4 ── Egeria's survey, limited to the chosen schemas by request parameter.
    # Initiation is not completion: the step reads "submitted · <time>" and becomes "done" only
    # when a read-back shows the survey report WITH annotations (`settle_survey`).
    # Round 2: a schema refused at run time is never surveyed either (the survey writes its report against it).
    refused_now = {r["schema"] for r in np_attach}
    # S19: a schema whose re-include the run refused (its archived copy changed since the press) is not surveyed.
    refused_now |= {p["schema_name"] for p in registry.list_catalogue_commit_proofs(slug)
                    if p["proof"] == P_REINCLUDE_HELD and p.get("curation_id") == curation_id}
    names = [n for n in sel.get("survey_schemas") or [] if n not in refused_now]
    bad = list(sel.get("survey_not_scopable") or [])
    if not db_guid:
        cur.set_step(curation_id, "survey", "skipped", "no database element")
    elif not names:
        cur.set_step(curation_id, "survey", "skipped",
                     "no schema the survey can be limited to" + (f" (a comma in: {', '.join(bad)})" if bad else "")
                     + ": not started, because an empty list would survey every schema")
    else:
        try:
            guid, process_qn = gateway.initiate_survey(db_guid, {"includeSchemaNames": ",".join(names)})
            submitted_at = _now()
            registry.record_native_survey_submission("database", slug, process_qn, submitted_at,
                                                     engine_action_guid=guid, submitted_by=author,
                                                     started_as="gov_action_type")
            _proof(registry, slug, P_SURVEY, node_kind="database", element_guid=guid, curation_id=curation_id,
                   detail={"includeSchemaNames": names, "not_scopable": bad, "process": process_qn,
                           "submitted_at": submitted_at, "engine_action": guid})
            cur.set_step(curation_id, "survey", "submitted", _submitted_words(submitted_at, names, bad, guid, rep_guid))
        except Exception as exc:
            registry.record_native_survey_submission("database", slug, "PostgreSQLSurvey::survey-postgres-database", _now(),
                                                     submit_error=f"{exc}"[:300], submitted_by=author)
            _fail_step(cur, curation_id, "survey", exc)

    # 5 ── optional forced refresh (never a restart). "refreshed" only when the connector's own
    # last-refresh time moved on a status read; otherwise "refresh requested".
    if not sel.get("refresh_now"):
        cur.set_step(curation_id, "refresh", "skipped", "not asked: the cataloger's own cycle will pick the targets up")
    elif not attach:
        cur.set_step(curation_id, "refresh", "skipped", "no target to refresh")
    else:
        before = _try_status(gateway)
        t_start = time.monotonic()
        log.info("catalog commit %s: asking the daemon to refresh %s (status %s)", curation_id,
                 getattr(before, "name", "") or "the cataloger connector", (before.status if before else "") or "unreadable")
        try:
            gateway.refresh_connector(120)
        except Exception as exc:
            took = time.monotonic() - t_start
            first, _ = egeria_first_sentence(str(exc) or type(exc).__name__)
            timed_out = _is_timeout(exc)
            now_status = _try_status(gateway)
            seen = [x.status.upper() for x in (before, now_status) if x is not None and x.status]
            if timed_out and "REFRESHING" in seen:
                log.info("catalog commit %s: refresh timed out after %.0f s while the connector was REFRESHING: not a failure", curation_id, took)
                cur.set_step(curation_id, "refresh", "skipped",
                             "the cataloger was already refreshing · elements arrive on its pass")
            else:
                log.info("catalog commit %s: refresh %s: %s", curation_id,
                         f"timed out after {took:.0f} s (connector status {', '.join(seen) or 'unreadable'})" if timed_out else "refused", first)
                _fail_step(cur, curation_id, "refresh", exc)
        else:
            log.info("catalog commit %s: refresh finished in %.0f s", curation_id, time.monotonic() - t_start)
            after = _try_status(gateway)
            t0 = (before.last_refresh_time if before else "") or ""
            t1 = (after.last_refresh_time if after else "") or ""
            if t0 and t1 and t1 != t0:
                cur.set_step(curation_id, "refresh", "done",
                             f"refreshed · connector time moved {_stamp(t0)} → {_stamp(t1)} "
                             "(the JDBC cataloger connector's own time, not a schema's)")
            else:
                why = ("the connector's status could not be read before and after, so a move cannot be shown"
                       if not (t0 and t1) else "the connector's last refresh time did not move on the status read")
                cur.set_step(curation_id, "refresh", "requested", f"refresh requested · {why}")

    # 6 ── the zone, LAST of the writes, and only when the deployment configured one
    from resource_explorer.egeria_identity import configured_publish_zones, zone_lockout_refusal
    zones = configured_publish_zones()
    steps_now = {s["name"]: s["state"] for s in cur.get(curation_id)["steps"]}
    # A zone that grants nobody once Egeria e9fd817f4c ships (or whose control could not be read) is never
    # written: the database element would be unreadable and unmovable for everyone, RE's daemon included.
    zone_refusal = zone_lockout_refusal(zones) if (db_guid and zones) else ""
    if not db_guid:
        cur.set_step(curation_id, "zone_membership", "skipped", "no database element: the publish step did not produce one")
    elif not zones:
        cur.set_step(curation_id, "zone_membership", "skipped",
                     "zones left to Egeria · RE writes no ZoneMembership (EXPLORER_PUBLISH_ZONES is not configured)")
    elif any(steps_now.get(n) == "failed" for n in ("owner", "schema_targets", "leave_outs", "survey", "refresh")):
        cur.set_step(curation_id, "zone_membership", "skipped",
                     "not written: an earlier step failed, and a zone written now would stop that step being retried")
    elif zone_refusal:
        cur.set_step(curation_id, "zone_membership", "failed", f"not written: {zone_refusal}")
    else:
        try:
            gateway.set_zone_membership(db_guid, zones)
        except Exception as exc:
            log.warning("catalog commit %s: zone write refused: %s", curation_id, exc)
            _fail_step(cur, curation_id, "zone_membership", exc, prefix="Egeria did not accept the ZoneMembership: ")
        else:
            try:
                back = list(gateway.read_zones(db_guid))
            except Exception:
                back = []
            if sorted(back) == sorted(zones):
                _proof(registry, slug, P_ZONES, node_kind="database", element_guid=db_guid,
                       curation_id=curation_id, detail={"zones": zones, "read_back": back})
                cur.set_step(curation_id, "zone_membership", "done",
                             f"ZoneMembership {', '.join(zones)} written last, read back from Egeria")
            else:
                cur.set_step(curation_id, "zone_membership", "failed",
                             "Egeria accepted the ZoneMembership but the read-back shows "
                             + (", ".join(back) if back else "no zones I could read")
                             + f" (asked for {', '.join(zones)})")

    # 7 ── read back: the proof every state word rests on
    cur.set_step(curation_id, "read_back", "running")
    try:
        schemas = sorted(set(attach))
        s = read_back(registry, gateway, slug, schemas, curation_id=curation_id, by=author)
        if s["read_failed"]:
            cur.set_step(curation_id, "read_back", "failed", f"{s['read_failed']} read(s) of Egeria failed; states are unchanged")
        else:
            # The words come from the proof rows the read-back just wrote, never from its own counters.
            cur.set_step(curation_id, "read_back", "done",
                         read_back_words(read_back_chips_for(registry, slug, schemas)))
    except Exception as exc:
        _fail_step(cur, curation_id, "read_back", exc)
    return cur.finish(curation_id)


# ── "published" badges that read the reset marker ────────────────────────────

PUBLISHED_EARLIER_SHORT = "published earlier"
PUBLISHED_EARLIER_WORDS = "published earlier · Egeria was reset"


def egeria_reset_at(registry, slug: str) -> str:
    """The latest Egeria reset for this resource (its `egeria_reset` marker or the recorded reset), '' when there is
    none. Tolerates a registry without the reader (a test double): no reset known means none claimed. A read that
    FAILS is logged and raised, never "no reset" (fix round 2): swallowing it would bring ✓ back silently."""
    fn = getattr(registry, "get_egeria_reset_at", None)
    if fn is None:
        return ""
    try:
        return fn(slug) or ""
    except Exception:
        log.exception("could not read the Egeria reset for %r", slug)
        raise


def egeria_reset_at_or_none(registry, slug: str) -> str | None:
    """`egeria_reset_at`, or None when the read fails (already logged). A caller passes None on to `publish_fields`,
    which then says "could not check" for a published row; it never reads None as "no reset"."""
    try:
        return egeria_reset_at(registry, slug)
    except Exception:  # noqa: BLE001 - logged by egeria_reset_at
        return None


RESET_UNREADABLE_WORDS = "publish not established · the record of Egeria resets could not be read"


def published_state(published_at: str, reset_at: str) -> str:
    """'' (never published), 'published' (at or after the reset, or no reset), or 'published_earlier' (the
    row predates the marker, so it proves nothing about what Egeria holds now). A badge made from the row's
    existence alone would say Published for a row Egeria no longer has any element for."""
    if not published_at:
        return ""
    if _known(reset_at) and _known(published_at) and _ts(published_at) < _ts(reset_at):
        return "published_earlier"
    return "published"


def publish_fields(registry, slug: str, published_at: str, reset_at: str | None = None) -> dict:
    """The fields every payload that carries `last_published_at` adds, so the screen can pick its words. The words
    for a publish older than the reset come from HERE (`published_earlier_words`, `pre_reset_words`: "published
    before the <date> reset · not in Egeria now"), never from a constant of the screen's (fix round 1)."""
    if reset_at is None:
        try:
            reset = egeria_reset_at(registry, slug)
        except Exception:  # noqa: BLE001 - logged; a publish with an unreadable reset is never ✓ (fix round 2)
            if not published_at:
                return {"published_state": "", "egeria_reset_at": "", "published_earlier_words": ""}
            return {"published_state": "published_earlier", "egeria_reset_at": "",
                    "published_earlier_words": RESET_UNREADABLE_WORDS}
    else:
        reset = reset_at
    state = published_state(published_at, reset)
    return {"published_state": state, "egeria_reset_at": reset,
            "published_earlier_words": pre_reset_words(reset) if state == "published_earlier" else ""}


def pre_reset_claim(reset_at, at) -> dict | None:
    """{at: <reset time>, words} when the proof time `at` is before `reset_at`; None otherwise, and None when either
    time cannot be read (no claim either way). The one test the file-system publish proofs use (fix round 1)."""
    if not (_known(reset_at) and _known(at)) or _ts(at) >= _ts(reset_at):
        return None
    return {"at": reset_at, "words": pre_reset_words(reset_at)}


def reset_since_read(read_at: str, reset_at: str) -> bool:
    """True when the reset marker postdates a read: what was read is a copy of an element Egeria no longer
    holds. No marker, or a marker at or before the read, is False (a read at or after the reset is live)."""
    return _known(read_at) and _known(reset_at) and _ts(reset_at) > _ts(read_at)
