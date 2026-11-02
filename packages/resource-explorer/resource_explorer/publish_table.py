"""Curate's publish table: a row per declared measure, Publish | Keep in RE per row, carried by the page's one press
(DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md §5, the owner's answer 3; slice 1, PR 2).

Every row comes from an analysis's declaration (`trellis_analytics.declaration.Declaration`, carried on the
catalogue entry) and from the fact layer's read of that analysis; nothing here is written per analysis. A row says
the analysis, the measure, the value with its unit, the as-of, the confidence form with its basis, the scope, and
what it writes as: the declared Egeria type, or "generic measure · no native type declared" (owner's answer 1). Rows
of kind `proposal` are not here: a proposal has the verdict selector on Structure, and a verdict is a fact path,
not a publish of a measure.

**The choice** is a signed, dated, append-only row in `resource_scope_events` (kind `measure`, locator
`<analysis>/<measure>`), the selection record Curate's other choices already use; the newest row wins. No row is
"not chosen", which keeps the measure in RE.

**The press** is the database's one press, the Catalogue commit on Contents. Its step 3 publishes RE's own survey
report; the chosen measures ride inside it (`annotations_for`, appended after the stored verdicts), are read back
by name with the rest of the report, and each leaves a proof row (`record_proofs`): `measure_read_back` when the
report read shows the annotation, `measure_not_read_back` when it does not. The row's published word comes from
those rows only, never from the press.

**The two routes** (the walking skeleton's interim, now "the declared generic measure route, labelled", §8):
- generic: a ResourceMeasureAnnotation whose `resourceProperties` hold the value under the measure's name and whose
  `additionalProperties` say `declaredRoute: generic` beside the unit, the as-of and the confidence basis;
- native: the declared type, with the declared property map. ResourceMeasureAnnotation and SchemaAnalysisAnnotation
  are written natively (`annotation_for`); any other declared type is shown as what it writes as and offers Keep in
  RE only, saying why.

**The owner's answers, 2026-10-28** (`DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md` §4a):
1. a number already in Egeria another way says "already in Egeria via <route>" and offers no Publish
   (`published_elsewhere`), so a number is never written twice;
2. SchemaAnalysisAnnotation is the next native type; ResourcePhysicalStatusAnnotation follows in a later slice;
3. a file system's chosen measures wait for a file-system survey report of RE's own (built by Files steps 7-8, #905;
   the wait is lifted, 2026-10-29: they ride in that report, `EgeriaFileSystemSurveyor._publish_step_annotations`);
4. a choice lasts across surveys, sent by every commit until someone chooses Keep in RE, and the row says so.

**After slice 2** (`DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md` §4b): a row's confidence is its measure's own
(`Declaration.confidence_of`); an analysis declared but not built says "Planned, not built yet: not measured." and
cannot publish; a library step's result is unwrapped as the Chat compile unwraps it (`declared_context.library_rows`); a
member-scoped measure with no resource-level value says it has one value per member rather than "found nothing", and a
resource-scoped step's measure is read from the key the host stored it under (`declared_context.measure_records`).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

log = logging.getLogger(__name__)

#: The row's "writes as" when a measure declares no annotation (owner's answer 1, verbatim from the design note).
GENERIC_WORDS = "generic measure · no native type declared"
#: What the generic route writes as in Egeria.
GENERIC_TYPE = "ResourceMeasureAnnotation"
#: Declared types this publisher can write from a declaration alone (the owner's answer 2, 2026-10-28:
#: SchemaAnalysisAnnotation first, ResourcePhysicalStatusAnnotation in a later slice). Every other type needs its own
#: construction (a profile's value counts, a classification's name), which no declaration's property map carries yet.
NATIVE_WRITABLE = ("ResourceMeasureAnnotation", "SchemaAnalysisAnnotation")

#: The selection record's kind and choices (`registry.append_resource_scope_events`).
CHOICE_KIND = "measure"
PUBLISH, KEEP = "publish", "keep"
CHOICES = (PUBLISH, KEEP)

#: Proof rows (`catalogue_commit_proofs`, node_kind `measure`, schema_name = analysis id, table_name = measure).
NODE_MEASURE = "measure"
#: A file system's measure proofs. `catalogue_commit_proofs` is keyed by slug alone, so a file system's rows carry their
#: own node kind: a database and a file system that share a slug never read each other's proofs.
NODE_FS_MEASURE = "fs_measure"
P_MEASURE_READ_BACK = "measure_read_back"
P_MEASURE_NOT_READ_BACK = "measure_not_read_back"

#: What the table says about where the press is.
PRESS_WORDS = "chosen measures go with the page's one Publish: the commit on Contents"
#: Where a file system's chosen measures go: Resource Explorer's own file-system survey report (the owner's answer 3),
#: sent by the folder-survey and walk-extensions Publish (`web/routes/filesystems._publish_walk`), not the root-folder
#: Publish, which attaches the root folder only.
FS_PRESS_WORDS = ("chosen measures go inside Resource Explorer's own file-system survey report: the folder survey's "
                  "Publish sends them, each read back by name")
#: Any other kind has no survey report to carry measures.
NOT_FOR_KIND_WORDS = "this kind of resource has no survey report of Resource Explorer's own to carry measures"
#: A measure the step ran for and could not establish: said as such, never as a zero.
NOT_MEASURED_WORDS = "not measured this run"
PRESS_KINDS = ("database", "filesystem")
#: A choice of Publish lasts across surveys (the owner's answer 4): every commit sends the measure until Keep in RE is chosen.
STANDING_WORDS = "sent by every commit until Keep in RE"

#: Measures RE's own survey report already writes under annotations of its own (the owner's answer 1): the survey's database
#: counts (`DatabaseSurveyor._create_schema_annotations`: schemas, tables, columns), its database size
#: (`_create_statistics_annotations`) and its views (`_create_views_annotations`), which the catalogue commit's step 3
#: publishes whatever this table says. A fixed fact of RE's own survey, not of any analysis a person adds.
IN_SURVEY_REPORT = frozenset({("schema_inventory", "relations"), ("schema_inventory", "columns"),
                              ("row_count_snapshot", "total_size"), ("sql_analysis", "views")})
#: The engine host's guard for a step that ran and published its report (`step_outcome.SURVEY_GUARDS`).
_ENGINE_COMPLETED = "survey-completed"
#: The fact states that carry a value (facts.Fact.is_known).
_KNOWN = ("measured", "nothing_found", "partial", "measured_within_credential_scope")
#: The fact layer's state -> the declared absence that says it (Declaration.absence keys).
_ABSENCE_OF = {"never_run": "not_measured", "not_established": "not_measured", "no_reader": "not_measured",
               "unreadable": "not_measured", "nothing_found": "measured_nothing", "not_applicable": "not_applicable",
               "not_permitted": "not_permitted"}


def locator(analysis_id: str, measure: str) -> str:
    return f"{analysis_id}/{measure}"


def node_kind(entity_type: str) -> str:
    return NODE_FS_MEASURE if entity_type == "filesystem" else NODE_MEASURE


def check_name(analysis_id: str, measure: str) -> str:
    """The annotation's identity inside the report (`survey_report.annotation_qualified_name`)."""
    return f"declared_measure.{analysis_id}.{measure}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── reading a declared path ─────────────────────────────────────────────────────────────────────────────────────

_MISSING = object()

#: A member- or field-scoped measure with no resource-level value: its values are its extents' rows, one per member.
PER_MEMBER = "per_member"
#: A step ran and stored this measure's record, but the record does not hold the declared field (a version of the step
#: that did not write it yet). Neither "measured nothing" nor "never run": the run has to be repeated to measure it.
NOT_RECORDED = "not_recorded"


def sibling_state(value: dict, path: str) -> tuple[str, str]:
    """(state, explanation) the result's own check records beside a missing `value_field`, or ("", ""). A check
    object such as `checks.tables_without_comment` carries its `count` next to its `state`: a count that is absent
    while its state says "not_measured" is an unverified check, not a measured nothing (F42)."""
    parent, _, _leaf = str(path or "").strip().rpartition(".")
    if not parent or parent.startswith("run.") or not isinstance(value, dict):
        return "", ""
    got = value
    for part in parent.split("."):
        if not isinstance(got, dict) or part not in got:
            return "", ""
        got = got[part]
    if not isinstance(got, dict):
        return "", ""
    return str(got.get("state") or ""), str(got.get("explanation") or "")


def resolve(value: dict, path: str, run: dict) -> object:
    """A declared `value_field` or `as_of` path against a result (`MeasureDeclaration`'s rules): `run.<field>` reads
    the run that produced the result, a dotted path a nested field, a trailing `[]` the length of a list. A key that
    is itself the whole path (a library step's "Writes Per Day") is read first. `_MISSING` when nothing is there."""
    path = str(path or "").strip()
    if not path:
        return _MISSING
    if path.startswith("run."):
        got = (run or {}).get(path[4:])
        return _MISSING if got in (None, "") else got
    count = path.endswith("[]")
    if count:
        path = path[:-2]
    if isinstance(value, dict) and path in value:
        got = value[path]
    else:
        got = value
        for part in path.split("."):
            if not isinstance(got, dict) or part not in got:
                return _MISSING
            got = got[part]
    if count:
        return len(got) if isinstance(got, (list, tuple)) else _MISSING
    return _MISSING if got is None else got


#: A field of the result named inside a declared unit: "files not modified for more than {Cold Days} days (cold)".
_UNIT_FIELD = re.compile(r"\{([^{}]+)\}")


def filled_unit(unit: str, *sources) -> str:
    """The declared unit with each "{Field}" read from the first result that holds it ("... more than 365 days"), so a
    parameter the step ran with is said as its value (F40). Where no result holds it (nothing measured yet) it reads
    "a set number of", never the field's name ("Cold Days days") and never a guessed value."""
    def fill(match):
        for src in sources:
            got = resolve(src, match.group(1), {}) if isinstance(src, dict) else _MISSING
            if got is not _MISSING and not isinstance(got, (dict, list)):
                return f"{got:,}" if isinstance(got, int) and not isinstance(got, bool) else str(got)
        return "a set number of"

    return _UNIT_FIELD.sub(fill, str(unit or ""))


def held_unmeasured(value: dict, path: str) -> tuple[bool, str]:
    """(True, why) when a known result HOLDS the declared field and holds None there: the reader says it did not measure
    it (absence is never 0), which is not "measured nothing". `why` is the reader's own reason under `_not_measured`
    (keyed by the field), or ''. (False, '') for a field the result does not hold at all, or a `run.` path."""
    path = str(path or "").strip()
    if not path or path.startswith("run.") or path.endswith("[]") or not isinstance(value, dict):
        return False, ""
    if path in value:
        held = value[path] is None
    else:
        got = value
        for part in path.split("."):
            if not isinstance(got, dict) or part not in got:
                return False, ""
            got = got[part]
        held = got is None
    return held, str(((value.get("_not_measured") or {}) if held else {}).get(path) or "")


def run_fields(fact: dict) -> dict:
    """The run beside the payload: what the reader carried under `_run` (a zero-fetch derivation's `read_snapshot`
    and compute time, `survey_definition_adapter._db_derived_field_reader`), else the fact layer's own run time."""
    carried = dict(((fact.get("value") or {}).get("_run")) or {})
    # A derivation (it carries `read_snapshot`) has no measurement time of its own: its recompute clock is
    # `computed_at`, and the fact's run time is not a stand-in (F18).
    if "read_snapshot" not in carried and not carried.get("surveyed_at") and fact.get("last_run_at"):
        carried["surveyed_at"] = fact["last_run_at"]
    return carried


# ── writes as ───────────────────────────────────────────────────────────────────────────────────────────────────

def writes_as(measure, confidence_form: str = "") -> dict:
    """What a measure writes as, and whether this publisher can write it. `route` is generic or native."""
    if confidence_form == "generated":
        return {"route": "none", "type": "", "words": "writes as nothing · a generated answer is not published",
                "writable": False, "why": "a generated answer is not published"}
    ann = getattr(measure, "annotation", None)
    if ann is None:
        return {"route": "generic", "type": GENERIC_TYPE, "words": GENERIC_WORDS, "writable": True, "why": ""}
    writable = ann.type in NATIVE_WRITABLE
    return {"route": "native", "type": ann.type, "words": ann.type, "writable": writable,
            "properties": dict(ann.properties or {}),
            "why": "" if writable else f"Resource Explorer does not write {ann.type} from a declaration yet"}


def confidence_words(conf) -> dict:
    if conf is None:
        return {"form": "", "words": "confidence not declared"}
    basis = conf.method or conf.basis   # the words; the field path only if a declaration has none (the validator forbids that)
    words = conf.form + (f" · {basis}" if basis else "")
    return {"form": conf.form, "basis": conf.basis, "method": conf.method, "score": conf.score,
            "calibrated": conf.calibrated, "words": words}


# ── the rows ────────────────────────────────────────────────────────────────────────────────────────────────────

def _entries(entity_type: str) -> list:
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    return [e for e in _load().get(entity_type, []) if e.declaration is not None]


def _fact(layer, slug: str, analysis_id: str) -> dict:
    try:
        return layer.fact(slug, analysis_id).as_dict()
    except Exception as exc:  # noqa: BLE001 -- a reader that raises is a state on its row, not a failed table
        log.debug("publish table: %s on %s could not be read: %s", analysis_id, slug, exc)
        return {"analysis_id": analysis_id, "state": "unreadable", "value": {}, "note": str(exc)}


def _scalar(v) -> bool:
    return isinstance(v, (int, float, str, bool)) and not (isinstance(v, str) and not v.strip())


#: The words a lower bound publishes with, and the annotation property that says it (the declared `capped_field` was true).
CAPPED_WORDS = "at least"
CAPPED_PROPERTY = "capped"


def kind_route(dec) -> dict | None:
    """Where a finding or a proposal goes in Egeria, from its declared block (brief 2026-10-30, Part B), or None for any
    other kind. A finding that declares an `action_field` is a RequestForActionAnnotation; a proposal is a
    ClassificationAnnotation in DRAFT, per the 2026-10-15 rule, and never a fact: only Curate's verdict makes it one.
    A route says where the result would go; RE writes it through the step's own annotation, not from this table."""
    if dec.finding and dec.finding.action_field:
        return {"type": "RequestForActionAnnotation", "status": "", "words": "a Request For Action",
                "action_field": dec.finding.action_field}
    if dec.proposal:
        return {"type": "ClassificationAnnotation", "status": "DRAFT",
                "words": "a ClassificationAnnotation in DRAFT, never a fact until Curate confirms it",
                "decided_by": dec.proposal.decided_by}
    return None


def _declared_value_words(measure, dec, source: dict, run: dict) -> str:
    """The value in the declared words (basis, cap, share) for a numeric measure, or "" where the plain words stand. A lower
    bound's derived share is withheld by `declared_words`, so a capped row never shows one."""
    from resource_explorer import declared_words

    if not (getattr(measure, "basis", None) or getattr(measure, "capped_field", "")):
        return ""
    w = declared_words.measure_words(measure, source, run, allow_of=declared_words.allows_of(dec))
    if w is None or w["value"] is None or isinstance(w["value"], str):
        return ""
    return w["text"] + (f" ({w['share']})" if w["share"] else "")


def _is_capped(measure, source: dict, run: dict) -> bool:
    """Does the result say this measure's value is a lower bound (its declared `capped_field` is true)?"""
    field = getattr(measure, "capped_field", "")
    if not field:
        return False
    got = resolve(source, field, run)
    if isinstance(got, str):
        return got.strip().lower() in ("true", "yes", "1")
    return got is not _MISSING and bool(got)


def _value_words(v, unit: str) -> str:
    if isinstance(v, bool):
        return f"{'yes' if v else 'no'}"
    if isinstance(v, float):
        v = round(v, 4)
    return f"{v} {unit}".strip()


def current_choices(registry, entity_type: str, slug: str) -> dict[str, dict]:
    """locator -> the newest choice row for it (who, when, which)."""
    out: dict[str, dict] = {}
    for ev in registry.list_resource_scope_events(entity_type, slug):
        if ev.get("kind") == CHOICE_KIND:
            out[ev["locator"]] = ev
    return out


def published_states(registry, slug: str, entity_type: str = "database") -> dict[str, dict]:
    """locator -> the newest measure proof row, for a database or a file system."""
    out: dict[str, dict] = {}
    kind = node_kind(entity_type)
    for p in registry.list_catalogue_commit_proofs(slug):
        if p.get("node_kind") == kind and p.get("proof") in (P_MEASURE_READ_BACK, P_MEASURE_NOT_READ_BACK):
            out[locator(p["schema_name"], p["table_name"])] = p
    return out


def _published_word(proof: dict | None, value, as_of) -> dict:
    if proof is None:
        return {"state": "local", "words": "local · not published"}
    d = proof.get("detail") or {}
    when = str(proof.get("read_at") or "")[:16].replace("T", " ")
    if proof["proof"] == P_MEASURE_NOT_READ_BACK:
        return {"state": "not_read_back", "words": f"sent · not read back {when}".rstrip(),
                "why": d.get("why", ""), "read_at": proof.get("read_at", "")}
    # A value this run does not hold is not a different value: nothing was compared (F43).
    remeasured = value is not None
    changed = remeasured and (str(d.get("value")) != str(value) or str(d.get("as_of") or "") != str(as_of or ""))
    out = {"state": "published", "words": f"published · read back {when}".rstrip(), "read_at": proof.get("read_at", ""),
           "guid": proof.get("element_guid", ""), "type": d.get("type", ""), "route": d.get("route", ""),
           "value": d.get("value"), "as_of": d.get("as_of", "")}
    if changed:
        out.update(state="changed", words=f"changed since published · read back {when}".rstrip())
    elif not remeasured:
        out.update(words=f"published {when} · not re-measured since".replace("published  ·", "published ·"),
                   not_remeasured=True)
    return out


def engine_host_reports(registry, slug: str) -> dict[str, dict]:
    """step key -> the newest engine-host run that completed a step on this database with a survey report
    (`engine_host.run_engine_action` stores its outcome on the `runs` row). Read-only; a registry that cannot list
    runs gives nothing, which leaves the rows publishable rather than guessing they are in Egeria."""
    out: dict[str, dict] = {}
    try:
        runs = registry.list_runs(kind="engine_action", limit=500)
    except Exception as exc:  # noqa: BLE001
        log.debug("publish table: engine-host runs could not be read: %s", exc)
        return out
    for r in runs:                                   # newest first (`list_runs` orders by enqueued_at desc)
        try:
            t = json.loads(r.get("target") or "{}")
        except (TypeError, ValueError):
            continue
        o = t.get("outcome") or {}
        key = t.get("step_key") or ""
        if (t.get("slug") == slug and key and key not in out and o.get("guard") == _ENGINE_COMPLETED
                and o.get("report_guid")):
            out[key] = {"report_guid": o["report_guid"], "at": o.get("decided_at") or r.get("finished_at") or "",
                        "run_id": str(r.get("id") or "")}
    return out


def published_elsewhere(entry, measure: str, engine_reports: dict[str, dict]) -> dict | None:
    """How this measure is already in Egeria without the table (the owner's answer 1), or None. Three routes: an analysis Egeria
    itself runs (its own survey report), a measure RE's survey report writes under its own annotation, and a step
    the engine host ran and published for Egeria."""
    if getattr(entry, "source", "") == "egeria":
        return {"route": "egeria_survey", "words": "already in Egeria via Egeria's own survey report"}
    if (entry.id, measure) in IN_SURVEY_REPORT:
        return {"route": "survey_report",
                "words": "already in Egeria via Resource Explorer's survey report (the commit on Contents)"}
    step = (getattr(entry, "provided_by", None) or {}).get("step") or entry.id
    ran = engine_reports.get(step)
    if ran:
        when = str(ran.get("at") or "")[:16].replace("T", " ")
        # F16 (2026-10-29): the record this reads is the engine host's own run row (`runs`, kind `engine_action`, whose
        # outcome holds the report it published), not a step run; the words name it so it can be found.
        which = f" · engine action run {ran['run_id']}" if ran.get("run_id") else ""
        return {"route": "engine_host", "words": f"already in Egeria via the engine host · report {when}{which}".rstrip(),
                "report_guid": ran["report_guid"], "at": ran.get("at", ""), "run_id": ran.get("run_id", "")}
    return None


def read_back_from_egeria(value: dict) -> dict | None:
    """A result RE read back from Egeria's own survey report (`detail.source` "egeria", e.g. an Egeria-led Folder
    Survey's counts): the number is already in Egeria, so it is never written a second time (the owner's answer 1)."""
    detail = (value or {}).get("detail") if isinstance(value, dict) else None
    if not isinstance(detail, dict) or detail.get("source") != "egeria":
        return None
    who = detail.get("surveyor") or "Egeria's own survey"
    guid = str(detail.get("report_guid") or "")
    return {"route": "egeria_survey", "report_guid": guid,
            "words": f"surveyed by {who} · already in Egeria via its own survey report"
                     + (f" {guid[:8]}" if guid else "")}


def build_rows(registry, entity_type: str, slug: str, *, layer=None, entries: list | None = None) -> dict:
    """The table for one resource. Read-only: nothing here calls Egeria. `entries` overrides the catalogue (tests,
    and a step declared but not yet in the generated catalogue)."""
    from resource_explorer import read_memo
    from resource_explorer.declared_context import (
        NOT_BUILT,
        NOT_BUILT_WORDS,
        library_rows,
        measure_records,
    )
    from resource_explorer.facts import FactLayer

    layer = layer or FactLayer(registry, resource_type=entity_type)
    entries = _entries(entity_type) if entries is None else entries
    choices = current_choices(registry, entity_type, slug)
    proofs = published_states(registry, slug, entity_type) if entity_type in PRESS_KINDS else {}
    engine_reports = engine_host_reports(registry, slug) if entity_type == "database" else {}
    rows = []
    with read_memo.scope():
        for entry in entries:
            dec = entry.declaration
            if dec is None or dec.result_kind == "proposal":
                continue
            fact = _fact(layer, slug, entry.id)
            run = run_fields(fact)
            # A library step's record as the step declared it, not RE's reader's wrapping (the Chat compile's rule).
            raw = fact.get("value") or {}
            value = library_rows(raw)
            # A resource-scoped library step stores each measure under its own key (empty for any other result).
            provided = getattr(entry, "provided_by", None) or {}
            step = (provided.get("step") or entry.id) if provided else ""
            records = measure_records(registry, entity_type, slug, step, dec, entry.id)
            step_known = fact.get("state") in _KNOWN and dec.built
            for m in dec.measures:
                loc = locator(entry.id, m.name)
                conf = confidence_words(dec.confidence_of(m))
                record = records.get(m.name)
                # A measure is read where ITS scope says (`Declaration.scope_of`): a resource-scoped summary of a
                # member-scoped step has its own record, whatever the step's rows say.
                scope = dec.scope_of(m) or ""
                per_member = scope in ("member", "field") and bool(dec.extents)
                known = (step_known or (bool(record) and dec.built))
                source = record["value"] if record else value
                v = resolve(source, m.value_field, run) if known else _MISSING
                as_of = resolve(source, m.as_of, run) if known else _MISSING
                as_of = "" if as_of is _MISSING else str(as_of)
                # F15: two dates, both named: when the statistic is as of, and when RE measured it.
                # A derived row's second date is the snapshot it was derived from, never the recompute clock (F18).
                measured_at = ((record or {}).get("measured_at")
                               or (run.get("read_snapshot") if "read_snapshot" in run else run.get("surveyed_at"))
                               or "")
                # Any record of the step can carry the parameter (an unmeasured access row still ran with Cold Days).
                unit = filled_unit(m.unit, source, *(r.get("value") for r in records.values()), raw)
                w = writes_as(m, conf["form"])
                row = {"capped": False, "kind_route": kind_route(dec), "analysis_id": entry.id, "analysis": entry.name, "measure": m.name, "locator": loc,
                       "unit": unit, "value_field": m.value_field, "as_of_field": m.as_of,
                       "state": fact.get("state", ""), "as_of": as_of, "confidence": conf,
                       "scope": scope, "writes_as": w, "result_kind": dec.result_kind,
                       "measured_at": str(measured_at) if known else "",
                       # A derivation is recomputed on read: its second date is the survey it read (F18/item 2).
                       "derived": bool(known and not (record or {}).get("measured_at") and "read_snapshot" in run)}
                if dec.table is not None and dec.table.is_result():
                    # A table result publishes its summary measures only (owner, 2026-11-02, "Summary only"): no row
                    # of the table leaves Resource Explorer, and the band says so (table_result.ROWS_STAY).
                    from resource_explorer.table_result import ROWS_STAY

                    row["rows_band"] = ROWS_STAY
                if not dec.built:
                    # Planned, not built: said first, whatever the fact layer holds (project owner, 2026-10-28).
                    row.update(value=None, value_words="—", absence=NOT_BUILT,
                               absence_words=f"{NOT_BUILT_WORDS} {dec.absence.sentence('not_measured')}")
                elif v is _MISSING and known and record and record["state"] not in ("", "measured"):
                    # The measure's own record says why it has no value: its state, and the step's reason.
                    absence = _ABSENCE_OF.get(record["state"], "not_measured")
                    reason = f" · {record['reason']}" if record["reason"] else ""
                    row.update(value=None, value_words="—", absence=absence,
                               absence_words=dec.absence.sentence(absence) + reason)
                elif v is _MISSING and known and record and record["state"] == "measured":
                    # The step stored this measure and the stored record has no such field: never "found nothing".
                    row.update(value=None, value_words="—", absence=NOT_RECORDED,
                               absence_words=f"The stored result of this run does not record '{m.value_field}' (a version "
                                             "of the step that did not write it stored it): run the step again to measure it.")
                elif v is _MISSING and known and per_member:
                    # One value per member, not one for the resource: it ran, and its values are its rows.
                    names = ", ".join(e.name for e in dec.extents)
                    row.update(value=None, value_words="—", absence=PER_MEMBER,
                               absence_words=f"one value per {scope}, not one for the resource · see {names}")
                elif v is _MISSING:
                    absence = _ABSENCE_OF.get(fact.get("state", ""), "not_measured") if not known else "measured_nothing"
                    reason = why_none = ""
                    if known:
                        # The result's own check says whether the missing count is a measured zero or was never
                        # measured; the row says the same (F42).
                        own, why_own = sibling_state(source, m.value_field)
                        held, held_why = held_unmeasured(source, m.value_field)
                        if own and own != "measured":
                            absence = _ABSENCE_OF.get(own, "not_measured")
                            reason = f" · {why_own}" if why_own else ""
                        elif held and not own:
                            # The reader holds the field as None: not measured by this surveyor, never "measured
                            # nothing" (an Egeria-led survey's count Egeria does not make, F39).
                            absence, why_none = "not_measured", held_why
                    words = f"Not measured: {why_none}." if why_none else dec.absence.sentence(absence) + reason
                    row.update(value=None, value_words="—", absence=absence, absence_words=words)
                else:
                    row.update(value=v, value_words=_value_words(v, unit) if _scalar(v) else f"a {type(v).__name__}")
                    words = _declared_value_words(m, dec, source, run) if _scalar(v) and not isinstance(v, bool) else None
                    if words:
                        # The same words as every other surface (declared_words): "226 of 266 tables (85%)", "at least ...".
                        row.update(value_words=words)
                    if _is_capped(m, source, run):
                        # A lower bound publishes as one: the flag travels with the number, the words say so.
                        row.update(capped=True)
                        if not words:
                            row.update(value_words=f"{CAPPED_WORDS} {row['value_words']}")
                elsewhere = published_elsewhere(entry, m.name, engine_reports) or read_back_from_egeria(raw)
                row["elsewhere"] = elsewhere
                why = ""
                if entity_type not in PRESS_KINDS:
                    why = NOT_FOR_KIND_WORDS
                elif elsewhere:
                    why = f"{elsewhere['words']}; publishing it here would write the same number twice"
                elif not dec.built:
                    why = f"nothing to publish · {NOT_BUILT_WORDS}"
                elif row.get("absence") == "not_measured" and v is _MISSING and known:
                    # The step ran and could not establish it: said as that, with the step's own reason.
                    why = f"{NOT_MEASURED_WORDS} · {row['absence_words']}"
                elif row.get("absence") == PER_MEMBER:
                    why = f"nothing to publish · {row['absence_words']}"
                elif not w["writable"]:
                    why = w["why"]
                elif v is _MISSING:
                    why = f"nothing to publish · {row['absence_words']}"
                elif not _scalar(v):
                    why = f"the value is a {type(v).__name__}, not one number or word"
                elif not as_of:
                    why = "no as-of: a number never travels without its as-of"
                row["publishable"], row["why_not"] = not why, why
                ch = choices.get(loc)
                row["choice"] = ({"choice": ch["choice"], "by": ch["author"], "at": ch["changed_at"]}
                                 if ch and ch.get("choice") else None)
                if row["choice"] and row["choice"]["choice"] == PUBLISH:
                    row["choice"]["standing"] = STANDING_WORDS
                row["published"] = _published_word(proofs.get(loc), row.get("value"), as_of)
                if (row["published"]["state"] == "changed" and row["publishable"]
                        and (row["choice"] or {}).get("choice") == PUBLISH):
                    row["published"]["words"] += " · the next commit sends the new value"
                rows.append(row)
    chosen = [r for r in rows if r["publishable"] and (r["choice"] or {}).get("choice") == PUBLISH]
    return {"entity_type": entity_type, "slug": slug, "rows": rows, "chosen": len(chosen),
            "press": (PRESS_WORDS if entity_type == "database"
                      else FS_PRESS_WORDS if entity_type == "filesystem" else NOT_FOR_KIND_WORDS)}


def chosen_rows(registry, entity_type: str, slug: str, **kw) -> list[dict]:
    """The rows the next press sends: publishable and chosen Publish. ONE source for the table's count and the
    press (`annotations_for`), so what the table says goes out is what goes out."""
    return [r for r in build_rows(registry, entity_type, slug, **kw)["rows"]
            if r["publishable"] and (r["choice"] or {}).get("choice") == PUBLISH]


# ── the choice ──────────────────────────────────────────────────────────────────────────────────────────────────

class ChoiceRefused(ValueError):
    pass


def save_choice(registry, entity_type: str, slug: str, *, analysis_id: str, measure: str, choice: str,
                author: str, entries: list | None = None) -> dict:
    """Record one signed choice. Refuses a measure no declaration names, a proposal's row, and Publish on a row
    that cannot publish (its reason is the refusal)."""
    if choice not in CHOICES:
        raise ChoiceRefused(f"choice '{choice}' is not one of {', '.join(CHOICES)}")
    table = build_rows(registry, entity_type, slug, entries=entries)
    row = next((r for r in table["rows"] if r["analysis_id"] == analysis_id and r["measure"] == measure), None)
    if row is None:
        raise ChoiceRefused(f"'{analysis_id}' declares no measure '{measure}' on this table")
    if choice == PUBLISH and not row["publishable"]:
        raise ChoiceRefused(f"{row['analysis']} · {measure} cannot publish: {row['why_not']}")
    registry.append_resource_scope_event(entity_type, slug, locator=row["locator"], kind=CHOICE_KIND,
                                         choice=choice, author=author)
    return next(r for r in build_rows(registry, entity_type, slug, entries=entries)["rows"]
                if r["locator"] == row["locator"])


# ── the press ───────────────────────────────────────────────────────────────────────────────────────────────────

def annotation_for(row: dict):
    """The annotation a chosen row writes as. Every route says the same things about itself (`declaredRoute`, the
    analysis and measure, unit, as-of, confidence, scope); where they go depends on what the type carries:

    - ResourceMeasureAnnotation: the value in `resourceProperties` under the mapped property or the measure's name,
      the rest in `additionalProperties`;
    - SchemaAnalysisAnnotation: its only typed fields are `schemaName` and `schemaType`, written when the declaration
      maps a field to them; the value and the rest go in `jsonProperties`, which every annotation carries.
    """
    from resource_explorer.surveyors.survey_report import (
        ResourceMeasureAnnotation,
        SchemaAnalysisAnnotation,
    )

    w = row["writes_as"]
    if not w["writable"]:
        raise ValueError(f"{row['locator']} writes as {w['words']}, which this publisher cannot write")
    props = w.get("properties") or {}
    prop = props.get(row["value_field"]) or row["measure"]
    conf = row["confidence"]
    extra = {"declaredRoute": w["route"], "analysis": row["analysis_id"], "measure": row["measure"],
             "unit": row["unit"], "asOf": row["as_of"], "valueField": row["value_field"],
             "confidenceForm": conf.get("form", ""), "confidenceBasis": conf.get("method") or conf.get("basis") or "",
             "scope": row["scope"], "resultState": "measured", "measuredAt": row["as_of"]}
    if w["route"] == "generic":
        extra["declaredRouteWords"] = GENERIC_WORDS
    extra = {k: str(v) for k, v in extra.items() if v not in (None, "")}
    if row.get("capped"):
        extra[CAPPED_PROPERTY] = "true"
    common = {
        "summary": f"{row['analysis']} · {row['measure']}: {row['value_words']} · as of {row['as_of']}",
        "analysis_step": row["analysis_id"], "annotation_type_name": row["measure"].replace("_", " ").title(),
        "explanation": f"Declared measure '{row['measure']}' of {row['analysis']}, published from Curate's table "
                       f"({w['words']}).",
        "check_name": check_name(row["analysis_id"], row["measure"])}
    if w["type"] == "SchemaAnalysisAnnotation":
        typed = {field: str(row["value"]) for src, field in props.items()
                 if field in ("schemaName", "schemaType") and src == row["value_field"]}
        return SchemaAnalysisAnnotation(
            **common, schema_name=typed.get("schemaName", ""), schema_type=typed.get("schemaType", ""),
            json_properties={prop: str(row["value"]), **extra})
    return ResourceMeasureAnnotation(
        **common, resource_properties={prop: str(row["value"]), "unit": row["unit"]}, additional_properties=extra)


def annotations_for(registry, slug: str, entity_type: str = "database") -> dict:
    """What the next press of a database or a file system adds to RE's survey report: `{"rows", "annotations"}`, index-aligned. A table
    that cannot be read sends nothing and says so (`state: not_read`), never an empty choice."""
    try:
        rows = chosen_rows(registry, entity_type, slug)
    except Exception as exc:  # noqa: BLE001
        log.warning("publish table: the chosen measures of %s could not be read: %s", slug, exc)
        return {"state": "not_read", "reason": str(exc), "rows": [], "annotations": []}
    built, anns, not_built = [], [], []
    for r in rows:
        try:
            anns.append(annotation_for(r))
            built.append(r)
        except Exception as exc:  # noqa: BLE001 -- one row that cannot be built never blocks the others; it is named
            log.warning("publish table: %s could not be built: %s", r["locator"], exc)
            not_built.append({"locator": r["locator"], "why": str(exc)})
    return {"state": "sent" if built else "none_chosen", "rows": built, "annotations": anns, "not_built": not_built}


def record_proofs(registry, slug: str, sent: list[dict], *, report_guid: str, curation_id: str = "",
                  recorded_by: str = "", entity_type: str = "database") -> dict:
    """One proof row per measure the press sent, from the report read (`sent[i]["guid"]`: the annotation's GUID the
    read found under the report, "" when it was not there). Returns the counts for the step's words."""
    read_back = 0
    for s in sent:
        found = bool(s.get("guid"))
        read_back += found
        registry.append_catalogue_commit_proof(
            slug, proof=P_MEASURE_READ_BACK if found else P_MEASURE_NOT_READ_BACK,
            node_kind=node_kind(entity_type),
            schema_name=s["analysis_id"], table_name=s["measure"], curation_id=curation_id,
            element_guid=s.get("guid") or "", target_guid=report_guid or "", qualified_name=s.get("qualified_name", ""),
            detail={"value": s.get("value"), "unit": s.get("unit", ""), "as_of": s.get("as_of", ""),
                    "type": s.get("type", ""), "route": s.get("route", ""),
                    "why": "" if found else (s.get("why") or "the report read did not show this annotation")},
            recorded_by=recorded_by, read_at=s.get("read_at") or _now())
    return {"sent": len(sent), "read_back": read_back}


def step_words(counts: dict, not_built: list | None = None) -> str:
    n, k = counts.get("sent", 0), counts.get("read_back", 0)
    word = "measure" if n == 1 else "measures"
    out = (f"{k} of {n} declared {word} read back" if k != n else f"{n} declared {word} read back") if n else ""
    if not_built:
        missed = f"{len(not_built)} chosen not sent · could not be built ({', '.join(b['locator'] for b in not_built)})"
        out = " · ".join(x for x in (out, missed) if x)
    return out
