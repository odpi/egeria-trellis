"""Republish the proposal of RE's ClassificationAnnotations that Egeria 6.2 dropped, in place.

Until 2026-10-15 RE sent a ClassificationAnnotation's proposal as `candidateClassifications`, a field Egeria 6.2's
`ClassificationAnnotationProperties` does not have, so every one of them was stored with neither `classificationName`
nor `properties` (dev Egeria, 2026-10-15: 857, 854 from repository surveys and 3 from db_derived). PR #704 fixed the
serializer (`annotation_props.classification_fields`). This repairs the ones already written. The project owner said
yes to republishing them.

**What RE meant to publish is read locally, never guessed.** The source is the outbox (`egeria_outbox`), whose
`annotation` rows hold the exact body sent to Egeria (`payload_json`), keyed on the damage itself: the row's recorded
`egeria_guid` first, then the annotation's own `qualifiedName`. The proposal is rebuilt from that body by
`annotation_props.upgrade_classification_payload`, i.e. by `classification_fields`, the function the publisher and
the outbox drain use, so what this writes is what a publish today would have written. An annotation with no outbox
row is reported, not written: the database surveyor publishes directly (no outbox) and its db_derived results are
recomputed on read, not stored, so a recomputation today is not what was published then.

**The write updates the existing element in place**: `DataDiscovery`'s `annotations/{guid}/update` endpoint with an
`UpdateElementRequestBody` (`mergeUpdate: true`) holding a `ClassificationAnnotationProperties` with only
`classificationName`, `properties` and the element's own current primitive values. Egeria's builder
(OpenMetadataElementBuilder.getElementProperties) writes every primitive of the bean on a merge, so a primitive left
out would be reset to 0: `confidence` would be lost. They are carried from the element as read. No annotation is
created or deleted. It calls pyegeria's `DataDiscovery.update_annotation`, which sends its body from 6.2.7 on
(ISSUE-152; on 6.1.15 to 6.2.6 it passed the body into the wrong slot and sent nothing, so this script went
through the private `_async_update_element_body_request` helper until then).

Idempotent: each element's current `classificationName`/`properties` is compared to the planned ones first, so a
re-run after a partial apply skips what landed. One failure does not stop the batch; failures are listed at the end
and the exit code is 1. Every planned write and its outcome (guid, qualifiedName, before, after) is appended to a
JSONL log (`--log`, default under ~/.re-gate/). After each write the element is read back; a write is counted
`written` only when the read-back shows the planned fields and the confidence unchanged.

Usage (dry-run is the default; nothing is written without --apply):
    uv run --all-packages --extra dev python packages/resource-explorer/scripts/backfill_classification_annotations.py
    uv run --all-packages --extra dev python packages/resource-explorer/scripts/backfill_classification_annotations.py --apply --limit 3
    uv run --all-packages --extra dev python packages/resource-explorer/scripts/backfill_classification_annotations.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from resource_explorer.surveyors.annotation_props import (
    EGERIA_PROPERTIES_CLASSES,
    upgrade_classification_payload,
)
from resource_explorer.surveyors.survey_report import AnnotationType

#: Every RE annotation's qualifiedName starts here (repo `Annotation::{slug}::…`, database `Annotation::PostgreSQL::…`,
#: file system `Annotation::FileSystem::…`).
RE_PREFIX = "Annotation::"
TYPE_NAME = "ClassificationAnnotation"
PROPERTIES_CLASS = EGERIA_PROPERTIES_CLASSES[AnnotationType.CLASSIFICATION]
#: The fields this repair owns. Nothing else on the element is changed.
FIELDS = ("classificationName", "properties")
#: Primitive bean fields Egeria writes on every merge update (0 when absent from the body): carried as read.
CARRIED_PRIMITIVES = ("confidence", "sampleSize", "samplePercent", "absoluteUncertainty", "relativeUncertainty",
                      "matchingValues", "nonMatchingValues")

# Outcomes. Only REPAIR writes.
REPAIR = "repair"
ALREADY_CURRENT = "already_current"     # Egeria already holds what RE meant (a re-run, or never damaged)
NO_PROPOSAL = "no_proposal"             # RE's body proposed nothing: there is nothing to restore
DIFFERS = "differs"                     # Egeria holds a different classificationName: left alone, reported
NO_LOCAL_SOURCE = "no_local_source"     # no outbox row for this guid or qualifiedName: reported, never written
AMBIGUOUS_SOURCE = "ambiguous_source"   # rows that disagree, or a guid row naming another qualifiedName: reported
PRIVATE_OWNER = "private_owner"         # the resource is in a private investigation: only its owner writes there
REPORTED = (DIFFERS, NO_LOCAL_SOURCE, AMBIGUOUS_SOURCE, PRIVATE_OWNER)


@dataclass
class Element:
    guid: str
    qualified_name: str
    analysis_step: str
    current: dict           # the FIELDS Egeria holds now
    primitives: dict        # CARRIED_PRIMITIVES as read


@dataclass
class PlanItem:
    element: Element
    outcome: str
    source: str = ""        # "outbox:egeria_guid" | "outbox:qualified_name" | ""
    outbox_ids: list = field(default_factory=list)
    after: dict = field(default_factory=dict)
    body: dict | None = None
    reason: str = ""
    entities: list = field(default_factory=list)   # (entity_type, slug) of the outbox rows behind a repair


# ── reading Egeria ───────────────────────────────────────────────────────────

def element_from(raw) -> Element | None:
    """An RE ClassificationAnnotation from one raw element, or None for anything else."""
    if not isinstance(raw, dict):
        return None
    header = raw.get("elementHeader") or {}
    props = raw.get("properties") or {}
    type_name = ((header.get("type") or {}).get("typeName")) or ""
    qn = props.get("qualifiedName") or ""
    if type_name != TYPE_NAME or not qn.startswith(RE_PREFIX) or not header.get("guid"):
        return None
    return Element(
        guid=header["guid"], qualified_name=qn, analysis_step=props.get("analysisStep") or "",
        current={k: props[k] for k in FIELDS if props.get(k) not in (None, "", {})},
        primitives={k: props[k] for k in CARRIED_PRIMITIVES if props.get(k) is not None},
    )


def _enumerate_once(discovery, page_size: int) -> dict[str, Element]:
    """One full unordered pass over the annotation search. Raises when a page cannot be read."""
    found: dict[str, Element] = {}
    seen: set[str] = set()
    start = 0
    while True:
        # Do NOT pass sequencing_order. Measured 2026-10-15: the same query paged with
        # sequencing_order="CREATION_DATE_OLDEST" returned 398 distinct annotations, without it 598 (all 398
        # among them), so the ordered search silently drops elements.
        page = discovery.find_annotations(
            search_string=RE_PREFIX, starts_with=True, ignore_case=False, start_from=start, page_size=page_size,
            output_format="JSON", graph_query_depth=0, metadata_element_type=TYPE_NAME)
        if not isinstance(page, list):
            if start == 0 and not (isinstance(page, str) and "no elements" in page.lower()):
                raise RuntimeError(f"Egeria's annotation search did not answer with a list: {str(page)[:200]}")
            break
        guids = {((raw.get("elementHeader") or {}).get("guid") or "") for raw in page if isinstance(raw, dict)}
        if len(page) >= page_size and not (guids - seen):
            raise RuntimeError(f"Egeria's annotation search returned a full page at offset {start} with no new "
                               "element: the paging is not advancing, so the enumeration cannot be trusted")
        seen |= guids
        for raw in page:
            el = element_from(raw)
            if el is not None:
                found[el.guid] = el
        if len(page) < page_size:
            break
        start += page_size
    return found


def enumerate_annotations(discovery, page_size: int = 200, warn=None) -> list[Element]:
    """Every RE ClassificationAnnotation in Egeria: two full unordered passes, the union of both. Unordered offset
    paging is not guaranteed stable, so a second pass is the cross-check; when the passes differ, `warn` is told
    (the union is still the answer, and the script is idempotent, so a re-run catches anything else). Raises when
    a page cannot be read: a partial enumeration must not pass for a complete one."""
    first = _enumerate_once(discovery, page_size)
    second = _enumerate_once(discovery, page_size)
    if set(first) != set(second) and warn is not None:
        warn(f"WARNING: the two enumeration passes disagree: pass 1 found {len(first)}, pass 2 found "
             f"{len(second)}, {len(set(first) ^ set(second))} differ; using the union ({len(set(first) | set(second))})")
    found = {**first, **second}
    return sorted(found.values(), key=lambda e: e.qualified_name)


def read_element(discovery, guid: str) -> Element | None:
    raw = discovery.get_annotation_by_guid(guid, output_format="JSON", graph_query_depth=0)
    return element_from(raw)


# ── the local source ─────────────────────────────────────────────────────────

def intended_fields(payload: dict) -> dict:
    """The proposal RE meant, in Egeria's fields, from the body it sent: built by `classification_fields` (through
    `upgrade_classification_payload`), the publisher's own function. {} when it proposed nothing."""
    props = (upgrade_classification_payload(payload) or {}).get("properties") or {}
    if props.get("class") != PROPERTIES_CLASS:
        raise ValueError(f"outbox body is not a {PROPERTIES_CLASS}: {props.get('class')!r}")
    return {k: props[k] for k in FIELDS if props.get(k) not in (None, "", {})}


def index_outbox(rows: list[dict]) -> tuple[dict, dict]:
    by_guid: dict[str, list[dict]] = {}
    by_qn: dict[str, list[dict]] = {}
    for r in rows:
        if (r.get("egeria_guid") or "").strip():
            by_guid.setdefault(r["egeria_guid"].strip(), []).append(r)
        by_qn.setdefault(r.get("qualified_name") or "", []).append(r)
    return by_guid, by_qn


def update_body(element: Element, fields: dict) -> dict:
    """The exact body sent: a merge of the repaired fields, the element's primitives carried so none is reset."""
    props = {"class": PROPERTIES_CLASS, **element.primitives, **fields}
    return {"class": "UpdateElementRequestBody", "mergeUpdate": True, "properties": props}


def plan_one(element: Element, by_guid: dict, by_qn: dict) -> PlanItem:
    rows, source = by_guid.get(element.guid), "outbox:egeria_guid"
    if not rows:
        rows, source = by_qn.get(element.qualified_name), "outbox:qualified_name"
    if not rows:
        return PlanItem(element, NO_LOCAL_SOURCE, reason="no outbox row has this guid or qualifiedName")
    ids = [r["id"] for r in rows]
    if source == "outbox:egeria_guid":
        other = sorted({r.get("qualified_name") or "" for r in rows} - {element.qualified_name})
        if other:
            # A row's recorded GUID can name a peer element (an adopted lookup hit): not this annotation's body.
            return PlanItem(element, AMBIGUOUS_SOURCE, source, ids,
                            reason=f"outbox row with this guid names another qualifiedName: {other[0]}")
    try:
        candidates = {json.dumps(intended_fields(json.loads(r["payload_json"])), sort_keys=True) for r in rows}
    except (TypeError, ValueError) as exc:
        return PlanItem(element, AMBIGUOUS_SOURCE, source, ids, reason=f"outbox body unreadable: {exc}")
    if len(candidates) > 1:
        return PlanItem(element, AMBIGUOUS_SOURCE, source, ids, reason=f"{len(rows)} outbox rows disagree")
    after = json.loads(candidates.pop())
    if not after:
        return PlanItem(element, NO_PROPOSAL, source, ids)
    if all(element.current.get(k) == v for k, v in after.items()):
        return PlanItem(element, ALREADY_CURRENT, source, ids, after=after)
    held = element.current.get("classificationName")
    if held and held != after.get("classificationName"):
        return PlanItem(element, DIFFERS, source, ids, after=after,
                        reason=f"Egeria holds classificationName {held!r}")
    item = PlanItem(element, REPAIR, source, ids, after=after, body=update_body(element, after))
    item.entities = sorted({(r.get("entity_type") or "", r.get("entity_slug") or "") for r in rows})
    return item


def make_plan(elements: list[Element], outbox_rows: list[dict], owner_of=None) -> list[PlanItem]:
    """`owner_of(entity_type, slug)` is private_publish.resolve_private_owner bound to the registry: a repair on a
    resource with a private owner is reported, not written (RE's rule: only that owner writes there). A lookup that
    fails is treated as private (fail closed)."""
    by_guid, by_qn = index_outbox(outbox_rows)
    plan = [plan_one(e, by_guid, by_qn) for e in elements]
    if owner_of is None:
        return plan
    for item in plan:
        if item.outcome != REPAIR:
            continue
        for entity_type, slug in item.entities:
            try:
                owner = owner_of(entity_type, slug)
            except Exception as exc:  # noqa: BLE001 -- cannot tell is not "not private"
                owner, why = "?", f"private owner of {entity_type}/{slug} could not be read: {exc}"
            else:
                why = f"{entity_type}/{slug} is in a private investigation of {owner}"
            if owner:
                item.outcome, item.body, item.reason = PRIVATE_OWNER, None, why
                break
    return plan


# ── the write ────────────────────────────────────────────────────────────────

def apply_plan(discovery, plan: list[PlanItem], log, limit: int | None = None) -> tuple[int, list[tuple[PlanItem, str]]]:
    """Write every REPAIR item (the first `limit` of them, for a canary); returns (written, failures). One failure
    never stops the batch."""
    written, failures = 0, []
    repairs = [i for i in plan if i.outcome == REPAIR]
    for item in repairs[:limit] if limit is not None else repairs:
        el = item.element
        entry = {"guid": el.guid, "qualifiedName": el.qualified_name, "analysisStep": el.analysis_step,
                 "source": item.source, "outbox_ids": item.outbox_ids, "before": el.current, "after": item.after,
                 "confidence": el.primitives.get("confidence")}
        try:
            # The primitives are read again just before the write: Egeria's merge writes every one of them, so the
            # body must carry what the element holds now, not what it held when the plan was made.
            fresh = read_element(discovery, el.guid)
            if fresh is None:
                raise RuntimeError("no RE ClassificationAnnotation at this guid just before the write")
            entry["confidence"] = fresh.primitives.get("confidence")
            discovery.update_annotation(el.guid, update_body(fresh, item.after))
            back = read_element(discovery, el.guid)
            if back is None:
                raise RuntimeError("read-back found no RE ClassificationAnnotation at this guid")
            wrong = {k: back.current.get(k) for k, v in item.after.items() if back.current.get(k) != v}
            wrong.update({k: back.primitives.get(k) for k in CARRIED_PRIMITIVES
                          if back.primitives.get(k) != fresh.primitives.get(k)})
            if wrong:
                raise RuntimeError(f"read-back differs from the plan: {wrong}")
        except Exception as exc:  # noqa: BLE001 -- one failure is recorded and the batch goes on
            words = f"{type(exc).__name__}: {str(exc)[:500]}"
            failures.append((item, words))
            log({**entry, "outcome": "failed", "error": words})
            continue
        written += 1
        log({**entry, "outcome": "written", "read_back": back.current})
    return written, failures


# ── reporting ────────────────────────────────────────────────────────────────

def summarize(plan: list[PlanItem]) -> dict:
    return {
        "total": len(plan),
        "by_outcome": dict(Counter(i.outcome for i in plan)),
        "by_source": dict(Counter(i.source or "(none)" for i in plan)),
        "repair_by_analysis_step": dict(Counter(i.element.analysis_step or "(none)" for i in plan
                                                if i.outcome == REPAIR)),
        "by_analysis_step_and_outcome": dict(Counter(f"{i.element.analysis_step or '(none)'} | {i.outcome}"
                                                     for i in plan)),
    }


def print_report(plan: list[PlanItem], out=print, samples: int = 5) -> None:
    s = summarize(plan)
    out(f"=== ClassificationAnnotations in Egeria with qualifiedName prefix {RE_PREFIX} (RE and others): "
        f"{s['total']} ===")
    for title, key in (("by outcome", "by_outcome"), ("by source", "by_source"),
                       ("by analysisStep and outcome", "by_analysis_step_and_outcome")):
        out(f"\n{title}:")
        for k, n in sorted(s[key].items(), key=lambda kv: (-kv[1], kv[0])):
            out(f"  {n:6d}  {k}")
    repairs = [i for i in plan if i.outcome == REPAIR]
    out(f"\nsamples ({min(samples, len(repairs))} of {len(repairs)} to repair):")
    for i in repairs[:samples]:
        out(f"  {i.element.guid}  {i.element.qualified_name}\n"
            f"    before: {json.dumps(i.element.current, sort_keys=True)}\n"
            f"    after:  {json.dumps(i.after, sort_keys=True)}  (confidence kept: "
            f"{i.element.primitives.get('confidence')})")
    reported = [i for i in plan if i.outcome in REPORTED]
    if reported:
        out(f"\nreported, not written ({len(reported)}):")
        for i in reported:
            out(f"  {i.outcome:17s} {i.element.guid}  {i.element.qualified_name}  {i.reason}")


def default_log_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path.home() / ".re-gate" / f"classification-annotation-backfill-{stamp}.jsonl"


def jsonl_logger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(entry: dict) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now(UTC).isoformat(), **entry}, sort_keys=True) + "\n")
    return log


def run(discovery, registry, *, apply: bool, log_path: Path, out=print, page_size: int = 200,
        limit: int | None = None) -> int:
    """Enumerate, plan, report, and (with apply) write. Returns the exit code."""
    elements = enumerate_annotations(discovery, page_size=page_size, warn=out)
    rows = registry.list_outbox_payloads("annotation", PROPERTIES_CLASS)
    from resource_explorer.private_publish import resolve_private_owner

    plan = make_plan(elements, rows, owner_of=lambda et, slug: resolve_private_owner(registry, et, slug))
    out(f"{'APPLY' if apply else 'DRY RUN (nothing is written; --apply to write)'}  "
        f"outbox annotation rows read: {len(rows)}")
    print_report(plan, out=out)
    log = jsonl_logger(log_path)
    for i in plan:
        if i.outcome != REPAIR:
            log({"mode": "apply" if apply else "dry-run", "guid": i.element.guid,
                 "qualifiedName": i.element.qualified_name, "outcome": i.outcome, "reason": i.reason,
                 "source": i.source, "before": i.element.current, "after": i.after})
    if not apply:
        if limit is not None:
            out(f"--limit {limit} is ignored in a dry run: the whole plan is shown")
        for i in plan:
            if i.outcome == REPAIR:
                log({"mode": "dry-run", "guid": i.element.guid, "qualifiedName": i.element.qualified_name,
                     "outcome": "planned", "source": i.source, "before": i.element.current, "after": i.after,
                     "body": i.body})
        out(f"\nlog: {log_path}")
        return 0
    written, failures = apply_plan(discovery, plan, lambda e: log({"mode": "apply", **e}), limit=limit)
    out(f"\nwritten (read back and verified): {written}   failed: {len(failures)}")
    for item, words in failures:
        out(f"  FAILED {item.element.guid}  {item.element.qualified_name}  {words}")
    out(f"log: {log_path}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the repairs (default: dry run).")
    parser.add_argument("--log", type=Path, default=None, help="JSONL audit log (default: ~/.re-gate/…jsonl).")
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=None,
                        help="With --apply, write only the first N repairs (a canary); a re-run continues.")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be 0 or more")
    if args.page_size < 1:
        parser.error("--page-size must be 1 or more")

    from pyegeria import DataDiscovery

    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as, egeria_client
    from resource_explorer.registry import ProjectRegistry

    with acting_as(Daemon(DaemonReason.RESYNC)) as identity:
        discovery = egeria_client(identity, purpose="classification annotation backfill").of(DataDiscovery)
        return run(discovery, ProjectRegistry(), apply=args.apply, log_path=args.log or default_log_path(),
                   page_size=args.page_size, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
