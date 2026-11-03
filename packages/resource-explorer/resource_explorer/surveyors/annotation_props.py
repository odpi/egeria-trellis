"""One place that maps an Annotation dataclass to an Egeria properties body.

Extracted 2026-08-19 from three near-identical private copies — EgeriaPublisher
(repo), EgeriaDatabaseSurveyor, EgeriaFileSystemSurveyor — after the filesystem
copy was found to be silently broken and materially drifted from the other two.

The drift was not cosmetic. The filesystem copy:
  * read `ann.egeria_type_name`, an attribute that exists on no class in
    survey_report.py and nowhere else in the package, so it raised
    AttributeError on the FIRST annotation of every run;
  * emitted `valueProperties` where the others emit `resourceProperties`, and
    `confidenceLevel` where the others emit `confidence`;
  * handled six of the seven AnnotationTypes, omitting RELATIONSHIP from both
    its dispatch and its class map.

Because the failing call sits outside `_create_annotations`' inner try (which
guards only the create_annotation request), the error escaped the whole loop
rather than costing one annotation. On the Survey-Definition path
(publish_step_annotations) nothing caught it and the run failed outright; on the
catalog_and_survey path a broad except logged "Failed to publish SurveyReport",
which was doubly misleading — the report had been created, and it was the
annotations that died. Net effect: no filesystem analysis ever reached Egeria.

The test suite could not have caught it: the filesystem adapter test patches
`_create_annotations` out entirely, so the broken code never ran under test.

Repo and database publishing were unaffected, and this module is their existing
behaviour verbatim, so extracting it is a no-op for them and a fix for
filesystems. The three callers keep their thin `_build_annotation_props`/
`_to_string_map` methods as delegating wrappers, since tests and subclasses
reach for them by name.

2026-08-29: the CREATE loop itself (`_create_annotations`) was extracted the
same way, as `publish_annotations` below, for the same reason — three
near-identical copies, one of which had already drifted once. That loop also
gained an idempotency guard (design doc `outbox-publishing-design.md` D2):
before creating an annotation, look up its qualifiedName in Egeria and adopt
the existing GUID rather than creating a duplicate. Previously
`DataDiscovery.create_annotation` was called blind, so replaying a publish
(e.g. after a crash between Egeria recording the write and the caller
recording success) created a second annotation with the same qualifiedName.
The qualifiedName format/timestamp is unchanged — see the module docstring on
each caller's `_create_annotations` wrapper for why the run timestamp in it is
correct and must not be removed.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from resource_explorer.surveyors.survey_report import annotation_qualified_name, assert_unique_qualified_names, AnnotationType

log = logging.getLogger(__name__)


def to_string_map(d: dict) -> dict[str, str]:
    """Convert a dict to map<string, string> as required by Egeria typed map fields.

    Nested dicts/lists are JSON-serialised to a string value so no nested
    structures escape into the wire format.  All scalar values are str()-coerced.
    """
    result: dict[str, str] = {}
    for k, v in d.items():
        result[str(k)] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
    return result


def _annotation_type_name(ann, atype) -> str:
    """What Egeria's `annotationType` should say: WHICH result this is.

    It named the entity subtype until 2026-08-26 — "ClassificationAnnotation",
    duplicating the `class` field beside it. 14 repo analyses share that
    subtype, so the field distinguished nothing, and the names RE actually
    holds (chaoss_metrics, supply_chain, cve_scan) never reached the catalog.
    That is why publish attribution needed a local side-table: the question
    "which analysis produced this?" was unanswerable from Egeria.

    Order of preference:

    1. an explicit `annotation_type_name` on the annotation — for a step that
       emits more than one kind of result and wants to say which is which;
    2. the name derived from `analysis_step` via the step registry;
    3. the entity subtype name, as before.

    Three is wrong, and kept deliberately: database and filesystem surveyors
    are not in the repo step registry, so falling back preserves exactly their
    current behaviour rather than inventing a name for them. Wrong-as-before
    beats newly-fabricated.
    """
    explicit = (getattr(ann, "annotation_type_name", "") or "").strip()
    if explicit:
        return explicit
    try:
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            resolve_annotation_type,
        )
    except ImportError as exc:  # pragma: no cover - import guard
        log.warning("annotation type registry unavailable (%s) — annotations will "
                    "name their entity subtype instead of their result", exc)
        return atype.value
    return resolve_annotation_type(getattr(ann, "analysis_step", "")) or atype.value


# Map our AnnotationType enum to the Egeria Jackson subtype class name.
# Note: RequestForActionAnnotationProperties is NOT valid — use RequestForActionProperties.
#: The strict test fakes (tests/egeria_annotation_beans.py) check every class here against Egeria's beans.
EGERIA_PROPERTIES_CLASSES = {
    AnnotationType.RESOURCE_MEASURE:   "ResourceMeasureAnnotationProperties",
    AnnotationType.CLASSIFICATION:     "ClassificationAnnotationProperties",
    AnnotationType.QUALITY_SCORE:      "QualityAnnotationProperties",
    AnnotationType.DATA_CLASS:         "DataClassAnnotationProperties",
    AnnotationType.REQUEST_FOR_ACTION: "RequestForActionProperties",
    AnnotationType.SCHEMA_ANALYSIS:    "SchemaAnalysisAnnotationProperties",
    AnnotationType.RELATIONSHIP:       "RelationshipAdviceAnnotationProperties",
    AnnotationType.RESOURCE_PHYSICAL_STATUS: "ResourcePhysicalStatusAnnotationProperties",
    AnnotationType.DATA_GRAIN:         "DataGrainAnnotationProperties",
    AnnotationType.FINGERPRINT:        "FingerprintAnnotationProperties",
    AnnotationType.RESOURCE_PROFILE:   "ResourceProfileAnnotationProperties",
    AnnotationType.RESOURCE_PROFILE_LOG: "ResourceProfileLogAnnotationProperties",
}


#: The key, inside a ClassificationAnnotation's `properties`, that holds every classification it proposes (a JSON
#: list, first = `classificationName`) when it proposes more than one. Read back by egeria_reader.
CANDIDATES_KEY = "candidateClassifications"

#: The key, inside a QualityAnnotation's `jsonProperties`, that holds RE's {name: score} map. Egeria's bean has no
#: field for it (only an int `qualityScore`). Read back by egeria_reader into subtype_data under the same name.
QUALITY_SCORES_KEY = "qualityScores"


def classification_fields(ann) -> dict:
    """A ClassificationAnnotation's proposal in the only fields Egeria 6.2 keeps for it.

    `ClassificationAnnotationProperties` has `classificationName` (String) and `properties` (map<string,string>) of
    its own, and the typedef agrees (OpenMetadataTypesArchive1_2.getClassificationAnnotationEntity). The bean ignores
    unknown fields, so the `candidateClassifications` RE sent until 2026-10-15 was dropped on every annotation with
    no error: the proposal never reached Egeria.

    One annotation names one classification. When RE proposes several (a ranked list, or a set of labels), the
    first is `classificationName` and the whole list, in order, goes in `properties` under CANDIDATES_KEY. The
    alternatives are evidence for this one proposal, not proposals of their own, and splitting them would change the
    annotation count, the per-index qualifiedNames and every reader that counts by them. An explicit
    `classification_name` (an Analytic Library step) wins; its `classification_properties` ride in `properties`.
    A proposal of nothing sends neither field.
    """
    candidates = [str(c) for c in (getattr(ann, "candidate_classifications", None) or [])]
    name = getattr(ann, "classification_name", "") or (candidates[0] if candidates else "")
    if name and candidates and candidates[0] != name:
        # The list reads back with its first entry as the proposal: an explicit name leads it.
        candidates = [name] + [c for c in candidates if c != name]
    values = dict(getattr(ann, "classification_properties", None) or {})
    if candidates and candidates != [name]:
        values[CANDIDATES_KEY] = candidates
    out: dict = {}
    if name:
        out["classificationName"] = name
    if values:
        out["properties"] = to_string_map(values)
    return out


def upgrade_classification_payload(body: dict) -> dict:
    """An annotation body enqueued before 2026-10-15, rewritten into Egeria's fields for the drain.

    The outbox replays the body built at enqueue time, so a ClassificationAnnotation queued before
    `classification_fields` existed still carries `candidateClassifications`, which Egeria drops. That key is
    removed and the proposal rebuilt through `classification_fields`. Any other body is returned unchanged.
    """
    props = body.get("properties") if isinstance(body, dict) else None
    if (not isinstance(props, dict) or props.get("class") != EGERIA_PROPERTIES_CLASSES[AnnotationType.CLASSIFICATION]
            or CANDIDATES_KEY not in props or props.get("classificationName")):
        return body
    props = dict(props)
    candidates = props.pop(CANDIDATES_KEY) or []
    from types import SimpleNamespace

    old = props.get("properties") if isinstance(props.get("properties"), dict) else {}
    props.update(classification_fields(SimpleNamespace(candidate_classifications=list(candidates),
                                                       classification_name="", classification_properties=old)))
    return {**body, "properties": props}


def build_annotation_props(ann, qualified_name: str) -> dict:
    """Map an Annotation dataclass to the correct Egeria subtype properties body."""
    atype = ann.annotation_type

    egeria_class = EGERIA_PROPERTIES_CLASSES.get(atype, "AnnotationProperties")

    props: dict = {
        "class": egeria_class,
        "qualifiedName": qualified_name,
        "annotationType": _annotation_type_name(ann, atype),
        "summary": ann.summary,
        "analysisStep": ann.analysis_step,
        "confidence": ann.confidence,
    }
    if ann.explanation:
        props["explanation"] = ann.explanation
    if ann.expression:
        props["expression"] = ann.expression
    if ann.json_properties:
        props["jsonProperties"] = json.dumps(ann.json_properties)
    # Egeria's `contentStatus` — whether the CONTENT is complete, not whether
    # the entity persists (that is `ElementStatus`, a different field, set by a
    # different mechanism, and one pyegeria drops silently). A plain domain
    # property on `AuthoredReferenceableProperties`, so it goes inside
    # `properties` on the existing `NewElementRequestBody` with no other
    # change; verified live 2026-09-21 to round-trip as `DRAFT` — the
    # mechanism the project owner's 2026-09-21 decision settled on for
    # proposing governance elements that do not exist yet
    # (`docs/egeria-support-for-multi-resource.md` §3, corrected probe 4).
    #
    # Guarded on truthiness like `explanation`/`expression` above, so the wire
    # payload stays byte-identical for every caller that does not set it.
    content_status = getattr(ann, "content_status", "")
    if content_status:
        props["contentStatus"] = content_status

    # Subtype-specific fields — native typed fields for registered subtypes;
    # additionalProperties (map<string,string>) for unregistered types.
    if atype == AnnotationType.RESOURCE_MEASURE:
        rp = getattr(ann, "resource_properties", {})
        if rp:
            props["resourceProperties"] = to_string_map(rp)
        # The annotation envelope (resultState, measuredAt, producingRun, scope) rides here.
        # Guarded on truthiness: every measure that sets none is byte-identical on the wire.
        ap = getattr(ann, "additional_properties", {})
        if ap:
            props["additionalProperties"] = to_string_map(ap)

    elif atype == AnnotationType.CLASSIFICATION:
        props.update(classification_fields(ann))
        # The annotation envelope (resultState, measuredAt, producingRun, scope) of a library step's proposal rides
        # here, as on a ResourceMeasureAnnotation. Only an annotation that carries an envelope: every other
        # classification annotation stays byte-identical on the wire.
        ap = getattr(ann, "additional_properties", {}) or {}
        if "resultState" in ap:
            props["additionalProperties"] = to_string_map(ap)

    elif atype == AnnotationType.QUALITY_SCORE:
        # Egeria's QualityAnnotationProperties has an int `qualityScore`, not a map, and ignores unknown fields, so a
        # `qualityScores` field was stored as nothing. The map rides in jsonProperties instead, as numbers (readers
        # ignore numeric-looking strings); egeria_reader restores it to subtype_data["qualityScores"].
        qs = getattr(ann, "quality_scores", {})
        if qs:
            carried = dict(ann.json_properties or {})
            carried[QUALITY_SCORES_KEY] = qs
            props["jsonProperties"] = json.dumps(carried)
        # A QualityCheckAnnotation (one evaluated check) uses Egeria's own three fields, never jsonProperties:
        # QualityAnnotationProperties.java qualityDimension, qualityDescription, qualityScore. Each is sent only
        # when stated, so a check that has no score sends none (the bean's int would read an absent one as 0).
        dimension = getattr(ann, "quality_dimension", "")
        if dimension:
            props["qualityDimension"] = dimension
        description = getattr(ann, "quality_description", "")
        if description:
            props["qualityDescription"] = description
        score = getattr(ann, "quality_score", None)
        if score is not None:
            props["qualityScore"] = int(score)
        # AnnotationProperties.sampleSize (long): the records the check read, sent only where known (0 is a real
        # count of an empty table, and is sent; None is not known, and is not).
        sample = getattr(ann, "sample_size", None)
        if sample is not None:
            props["sampleSize"] = int(sample)
        ap = getattr(ann, "additional_properties", None) or {}
        if ap and (dimension or description or score is not None):
            props["additionalProperties"] = to_string_map(ap)

    elif atype == AnnotationType.DATA_CLASS:
        dc = getattr(ann, "candidate_data_class_names", [])
        if dc:
            props["candidateDataClassGUIDs"] = dc  # field name per Egeria type

    elif atype == AnnotationType.REQUEST_FOR_ACTION:
        action_req = getattr(ann, "action_requested", "")
        if action_req:
            props["actionRequested"] = action_req
        action_target = getattr(ann, "action_target_name", "")
        if action_target:
            props["actionProperties"] = {"actionTargetName": action_target}
        # The envelope (surveyor, walk limits, capped) rides here as on a measure. Guarded on truthiness: a request for
        # action that sets none is byte-identical on the wire.
        ap = getattr(ann, "additional_properties", None) or {}
        if ap:
            props["additionalProperties"] = to_string_map(ap)

    elif atype == AnnotationType.SCHEMA_ANALYSIS:
        sn = getattr(ann, "schema_name", "")
        st = getattr(ann, "schema_type", "")
        if sn:
            props["schemaName"] = sn
        if st:
            props["schemaType"] = st

    elif atype == AnnotationType.RELATIONSHIP:
        ren = getattr(ann, "related_entity_name", "")
        rtn = getattr(ann, "relationship_type_name", "")
        if ren:
            props["relatedEntityName"] = ren
        if rtn:
            props["relationshipTypeName"] = rtn

    elif atype == AnnotationType.RESOURCE_PHYSICAL_STATUS:
        # No native typed field is registered for this subtype's payload yet
        # (see ResourcePhysicalStatusAnnotation's docstring — its real fixed
        # fields describe a filesystem resource, not this finding), so the
        # bag travels as additionalProperties, same as any other
        # not-yet-natively-typed field on a registered subtype.
        pp = getattr(ann, "physical_properties", {})
        if pp:
            props["additionalProperties"] = to_string_map(pp)

    elif atype == AnnotationType.DATA_GRAIN:
        # Native typed fields — the names are known from the Egeria Java
        # source (quoted in `egeria-support-for-multi-resource.md` §3), so
        # these do NOT go through additionalProperties.
        gs = getattr(ann, "grain_statement", "")
        gb = getattr(ann, "granularity_basis", "")
        iv = getattr(ann, "interval", None)
        name = getattr(ann, "interval_name", "")
        cg = getattr(ann, "candidate_data_grain_guids", [])
        if gs:
            props["grainStatement"] = gs
        if gb:
            props["granularityBasis"] = gb
        # Egeria's `interval` is a Java `long` (DataGrainAnnotationProperties, OpenMetadataProperty.INTERVAL
        # DataType.LONG): anything but a whole number is a 400 for the whole annotation (live 2026-10-14:
        # "Cannot deserialize value of type `long` from String \"per-date\""). Refused here, loudly, rather
        # than sent; 0 is a real value and is sent. None means not known: nothing is sent.
        if iv is not None:
            if isinstance(iv, bool) or not isinstance(iv, int):
                raise TypeError(
                    f"DataGrainAnnotation.interval must be a whole number (Egeria's long) or None, got {iv!r}; "
                    "a period word belongs in interval_name")
            props["interval"] = iv
        if name:
            # No string field for a period word on Egeria's type: a named departure, in additionalProperties.
            props["additionalProperties"] = {"intervalName": str(name)}
        if cg:
            props["candidateDataGrainGUIDs"] = cg

    elif atype == AnnotationType.RESOURCE_PROFILE:
        # Egeria's own field: valueCount, Map<String,Integer>. Counts stay integers.
        vc = getattr(ann, "value_count", {})
        if vc:
            props["valueCount"] = {str(k): int(v) for k, v in vc.items()}
        ap = getattr(ann, "additional_properties", {})
        if ap:
            props["additionalProperties"] = to_string_map(ap)

    elif atype == AnnotationType.RESOURCE_PROFILE_LOG:
        # The CSV's name travels in additionalProperties: the native field name for the
        # log file was not verified against Egeria's Java source in this environment.
        ap = dict(getattr(ann, "additional_properties", {}) or {})
        lf = getattr(ann, "log_file", "")
        if lf:
            ap["logFile"] = lf
        if ap:
            props["additionalProperties"] = to_string_map(ap)

    elif atype == AnnotationType.FINGERPRINT:
        # The native field names for this subtype are NOT known in this
        # environment (see FingerprintAnnotation's docstring), so the payload
        # travels as additionalProperties rather than being guessed into
        # typed fields that would silently land nowhere.
        fp = getattr(ann, "fingerprint_properties", {})
        if fp:
            props["additionalProperties"] = to_string_map(fp)

    return props


def build_annotation_body(ann, qualified_name: str, report_guid: str) -> dict:
    """The exact body `DataDiscovery.create_annotation` expects.

    Extracted so the two paths that create an annotation — publishing it
    directly (`publish_annotations` below) and enqueueing it for the outbox
    (`egeria_outbox.enqueue_annotations`) — build the identical payload. If
    they diverged, a retried annotation would differ from the one the direct
    path would have written, which is exactly the class of drift the outbox
    exists to remove rather than introduce.
    """
    return {
        "class": "NewElementRequestBody",
        "parentGUID": report_guid,
        "parentRelationshipTypeName": "ReportedAnnotation",
        # ANCHORED to the report, which is what makes an annotation inherit the
        # report's governance zones. `parentGUID` alone does NOT do this:
        # measured 2026-09-08 against live annotations, every one came back
        # `anchorGUID: None` — its own anchor — and with **no ZoneMembership of
        # its own**. Which means that before this, a private investigation's
        # SurveyReport was zoned and its annotations, carrying the actual
        # findings, were world-readable.
        #
        # Inheritance is real and was measured, not inferred: a child with no
        # zones of its own, anchored to a zoned parent, is DENIED to a
        # non-owner, because `validateUserForAnchorMemberRead`'s else branch
        # evaluates `anchorEntity.getClassifications()` when the element itself
        # carries none. And it reads the LIVE anchor, not the copy cached on the
        # child's own `Anchors` classification — re-zoning the report moves
        # every annotation under it with no sweep.
        #
        # This is preferable to stamping each annotation: one property per
        # annotation, set at creation, instead of N classification calls that a
        # later code path can forget.
        "isOwnAnchor": False,
        "anchorGUID": report_guid,
        "properties": build_annotation_props(ann, qualified_name),
    }


def publish_annotations(
    discovery,
    find_element_guid,
    annotations: list,
    report_guid: str,
    qualified_name_prefix: str,
    *,
    skip: frozenset[int] | set[int] = frozenset(),
) -> list[str | None]:
    """Create one Annotation element per item in `annotations`, idempotently.

    `skip`: indices NOT to create (None in their place, nothing looked up). The profile publish reshape passes the
    value-profile measures it re-links to an existing annotation instead; the list is passed whole so every other
    annotation keeps its index, and with it its published name.

    Returns one entry per input annotation, in order: the GUID (existing, if
    adopted via the idempotency lookup, or newly created), or `None` if that
    annotation's create failed (exception) or Egeria's create response carried
    no `"guid"` key (see annotation-linking-plan.md Q1 — `.get("guid")` on a
    keyless dict returns `None` with no exception, so this is a real state to
    check for, not a "can't happen"). Added 2026-08-31 (annotation-linking-plan
    Phase 1) — previously `discovery.create_annotation(body=body)`'s return
    value was discarded here, on every caller of this shared function (the
    direct/no-registry path — the outbox path, `egeria_outbox.py::
    _create_annotation`, already captured its own copy of the same GUID via a
    different call site and was unaffected). Callers that have nowhere to put
    the GUID today (no registry, no per-row identity) are free to ignore the
    return value, same as before this change; nothing about the create/lookup
    behaviour below changed.

    The shared CREATE loop for EgeriaPublisher (repo), EgeriaDatabaseSurveyor
    and EgeriaFileSystemSurveyor — previously three near-identical copies, all
    of which called `DataDiscovery.create_annotation` blind, with no check for
    an existing element of the same qualifiedName. Replaying a publish (e.g.
    after a crash between Egeria recording the write and the caller recording
    success) therefore created a duplicate annotation.

    `find_element_guid` is injected rather than this module calling
    `AutomatedCuration.get_guid_for_name` itself: all three callers already
    have a connected `_find_element_guid(name) -> str` method that does
    exactly this lookup for the identical purpose on sub-resources
    (`EgeriaPublisher.publish_sub_resources`, D8) and on ExternalReferences
    (`_publish_homepage_reference`) — reusing it means one fewer live call
    path to test and maintain. It returns "" for a real miss and RAISES when
    the look-up could not be made; that annotation is then skipped (None in
    the result), never created blind.
    `DataDiscovery.find_annotations(search_string=qn, starts_with=False)`
    would also work, but it is a second, narrower search purely for
    Annotation-typed elements where the existing generic-name lookup is
    already proven correct here and cheap (unscoped, but qualifiedName
    embeds project slug + run timestamp + index, so a same-name collision
    with something else is not a realistic risk) — see get_guid_for_name's
    own docstring: it raises only when *more than one* element matches.

    qualified_name_prefix is everything before the trailing "::{i}" — the
    caller owns that format (and its run timestamp) entirely; this function
    only appends the per-annotation index.

    Error handling unchanged from the three pre-existing copies: a failure
    creating one annotation is logged and the loop continues rather than
    aborting the run.
    """
    assert_unique_qualified_names(qualified_name_prefix, annotations)

    guids: list[str | None] = []
    for i, ann in enumerate(annotations):
        if i in skip:
            guids.append(None)
            continue
        qualified_name = annotation_qualified_name(qualified_name_prefix, i, ann)

        try:
            existing_guid = find_element_guid(qualified_name)
        except Exception as exc:
            # Could not check is not "not there": this annotation is NOT created (it used to be, with only
            # Egeria's duplicate refusal as the backstop). Counted like a failed create, so the caller's
            # count of what landed is what Egeria holds.
            from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

            if not lookup_exception_is_absent(exc):
                log.warning("Annotation %d not published: %s", i, lookup_could_not_be_made(qualified_name, exc))
                guids.append(None)
                continue
            existing_guid = ""
        if existing_guid:
            log.debug("Annotation %s already exists (GUID %s) — not re-creating",
                      qualified_name, existing_guid)
            guids.append(existing_guid)
            continue

        body = build_annotation_body(ann, qualified_name, report_guid)
        try:
            guid = discovery.create_annotation(body=body)
        except Exception as exc:
            # pyegeria's text for a 400 names only the URL; Egeria's own reason is in the response body.
            from resource_explorer.egeria_absence import egeria_error_detail

            said = egeria_error_detail(exc)
            log.warning(
                "Failed to create annotation %d (%s, %s): %s%s",
                i, ann.annotation_type.value, qualified_name, exc,
                f"\n  Egeria said: {said}" if said else "",
            )
            guids.append(None)
            continue

        if not guid:
            # Real failure would have raised (see pyegeria's
            # _async_make_request contract, cited in the plan) — this is the
            # "200 OK, no guid key" edge case, not a network/API error. Log it
            # distinctly so it isn't mistaken for the exception path above.
            log.warning(
                "Annotation %d (%s) created (qualifiedName %s) but Egeria's "
                "response carried no GUID",
                i, ann.annotation_type.value, qualified_name,
            )
            guids.append(None)
            continue

        guids.append(guid)
    return guids


def publish_annotation_links(metadata_expert, links: list[dict]) -> list[str | None]:
    """Create one `AnnotationExtension` relationship per link, direct path
    (no registry, no outbox — this is `EgeriaPublisher`'s fallback for
    instances built without a registry, mirroring `publish_annotations`
    above which writes THIS run's annotations the same way for the same
    reason). annotation-linking-plan Phase 2, Tier 1.

    `links` are dicts with `summary_guid`/`evidence_guid` — both real Egeria
    GUIDs already, from annotations created earlier in this same call by
    `publish_annotations`. `evidence_guid` is `metadataElement2GUID`,
    `summary_guid` is `metadataElement1GUID` — Phase 0's live measurement
    (2026-09-01) confirmed no reversal and that `get_annotation_extensions`/
    `get_all_related_elements` called on the element at end 1 return whatever
    is at end 2, so this direction makes `get_annotation_extensions(summary_
    guid)` the natural "what backs this summary" query for a consumer.

    Deliberately create-blind — no lookup-then-create, no `get_all_related_
    elements` pre-check. `AnnotationExtension` was measured live as UNI_LINK
    (docs/annotation-linking-plan.md, Phase 0): two identical creates against
    the same ordered pair returned the SAME relationship GUID, confirmed by
    two independent reads showing exactly one relationship. A relationship
    has no qualifiedName to search for in the first place (point 4 of the
    plan's discovery findings) — this is the situation `egeria_outbox.py`'s
    own comment on `CollectionMembership` describes, applied here without an
    outbox row to back it.

    Returns one entry per input link, in order: the new relationship's GUID,
    or `None` if that create failed (exception, or a 200 response with no
    `"guid"` key — same "not a can't-happen" reasoning as `publish_
    annotations` above). A caller with nowhere to persist a per-row outbox
    record still needs a per-item signal, not a single count that can't say
    which pair failed.
    """
    guids: list[str | None] = []
    for link in links:
        body = {
            "class": "NewRelatedElementsRequestBody",
            "typeName": "AnnotationExtension",
            "metadataElement1GUID": link["summary_guid"],
            "metadataElement2GUID": link["evidence_guid"],
        }
        try:
            guid = metadata_expert.create_related_elements(body=body)
        except Exception as exc:
            log.warning(
                "Failed to create AnnotationExtension link (summary %s -> evidence %s): %s",
                link["summary_guid"], link["evidence_guid"], exc,
            )
            guids.append(None)
            continue

        if not guid:
            log.warning(
                "AnnotationExtension link created (summary %s -> evidence %s) but "
                "Egeria's response carried no GUID",
                link["summary_guid"], link["evidence_guid"],
            )
            guids.append(None)
            continue

        guids.append(guid)
    return guids
