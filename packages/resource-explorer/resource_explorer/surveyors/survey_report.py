"""
Egeria-aligned survey result dataclasses.

All types are plain Python — no pyegeria dependency here.
EgeriaPublisher (egeria_publisher.py) converts these to API calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Sequence


class AnnotationType(str, Enum):
    """RE's annotation kinds. Each value is the Egeria open metadata type
    name it publishes as, so the value can be compared directly against
    analysis_catalog.yaml's `annotation_types` and against what
    record_published_annotation_types() writes.

    Two values were not, until 2026-09-11: SCHEMA_ANALYSIS was
    "SchemaAnalysis" and REQUEST_FOR_ACTION was "RequestForAction". Found by
    the declared-vs-received reconciliation, which reported both as "never
    published, ever" — 92 verdicts across 150 runs — when both had been
    published every time under the shorter name. The catalog and the
    bookkeeping disagreed on 2 of 7 names and nothing had ever joined them.
    The publish path never used the value as an Egeria type (it maps the
    member to a Properties class), so the only things the old values reached
    were two log lines and the bookkeeping table, which is migrated on open.
    """
    RESOURCE_MEASURE = "ResourceMeasureAnnotation"
    CLASSIFICATION = "ClassificationAnnotation"
    SCHEMA_ANALYSIS = "SchemaAnalysisAnnotation"
    DATA_CLASS = "DataClassAnnotation"
    QUALITY_SCORE = "QualityScoreAnnotation"
    RELATIONSHIP = "RelationshipAnnotation"
    REQUEST_FOR_ACTION = "RequestForActionAnnotation"
    #: Added 2026-09-21 (Phase 1 slice 8, `postgres_operations`). Egeria's
    #: real `ResourcePhysicalStatusAnnotationProperties` type — one of the
    #: twelve RE left unused per `docs/egeria-integration.md` §2's audit —
    #: is the correct fit for `db_resilience`'s findings (replication role,
    #: replica lag, WAL archiving, backup/clustering signals) rather than
    #: publishing them as a generic ResourceMeasureAnnotation.
    RESOURCE_PHYSICAL_STATUS = "ResourcePhysicalStatusAnnotation"
    #: Added 2026-09-21 (Phase 1 slice 9, `db_derived`). Egeria's real
    #: `DataGrainAnnotationProperties` type — the grain of a table, "one row
    #: per what". Its native fields are known and typed (see
    #: `docs/egeria-support-for-multi-resource.md` §3: `granularityBasis`,
    #: `grainStatement`, `interval`, `candidateDataGrainGUIDs`), so unlike
    #: FINGERPRINT below this one publishes as typed fields rather than
    #: through `additionalProperties`.
    DATA_GRAIN = "DataGrainAnnotation"
    #: Added 2026-09-21 (Phase 1 slice 9, `db_derived`). Egeria's real
    #: `FingerprintAnnotationProperties` type — "an annotation capturing
    #: digital resource fingerprint information" (`docs/egeria-integration.md`
    #: §2's audit of the twelve unused types). Its native field names could
    #: NOT be verified in this environment (no Egeria Java source checkout is
    #: present, and no probe has sent one), so its payload travels as
    #: `additionalProperties` — see `annotation_props.py`.
    FINGERPRINT = "FingerprintAnnotation"
    #: Added 2026-10-07 (file types as annotations ruling). Egeria's real
    #: `ResourceProfileAnnotationProperties` / `ResourceProfileLogAnnotationProperties`,
    #: the types its own folder survey writes ("Profile File Extensions",
    #: "Profile File Types", "Profile Asset Types", "Profile File Names to External Log").
    RESOURCE_PROFILE = "ResourceProfileAnnotation"
    RESOURCE_PROFILE_LOG = "ResourceProfileLogAnnotation"


@dataclass
class Annotation:
    """Base annotation — mirrors Egeria Area 6 base Annotation entity."""
    annotation_type: AnnotationType
    summary: str
    analysis_step: str
    #: Optional override for Egeria's `annotationType` — the NAME of the result,
    #: not its shape. Left empty, it is derived from analysis_step via the step
    #: registry. Set it only where one step emits several distinguishable kinds
    #: of result and the derived, per-step name would blur them together.
    annotation_type_name: str = ""
    confidence: int = 100                   # 0–100
    expression: str = ""                    # relationship detail to the asset
    explanation: str = ""
    json_properties: dict[str, Any] = field(default_factory=dict)
    additional_properties: dict[str, Any] = field(default_factory=dict)
    source: str = "local"                   # 'local' | 'egeria' | 'pending'
    #: Index, within this same run()'s returned annotations list, of the
    #: annotation this one is evidence *for* — e.g. a per-file
    #: A stable identity for the CHECK this annotation reports, within its
    #: analysis. Optional only until every surveyor supplies one.
    #:
    #: Added 2026-09-02 because the published qualifiedName was
    #: f"{prefix}::{i}" — the enumeration index within the run — and positions
    #: are not stable across runs. Measured on egeria_git's two largest
    #: published runs, over 115 overlapping indices, the same index meant the
    #: same finding 0 times. So a check could not be followed through time
    #: even though every value worth trending was already published.
    #:
    #: `check_name` matches the vocabulary project_analysis_findings already
    #: uses, so an annotation and its finding row name the same thing.
    check_name: str = ""
    #: Discriminates one item within a LIST-SHAPED check — a check that emits
    #: one finding per item rather than one per check.
    #:
    #: Required whenever a check emits more than one annotation in a run:
    #: measured over 27,710 (slug, kind, run, check_name) groups, 15.1%
    #: collide, all of them list-shaped (architecture_*, cve_scan,
    #: secret_scan). Only the surveyor knows what identifies its item —
    #: "path:line" for a secret, the term for a doc-lens finding, the package
    #: for an advisory. scope_locator does not discriminate them, and hashing
    #: the detail would produce an identity that changes when an unrelated
    #: field does.
    item_key: str = ""
    #: The finding's VERDICT — "pass" / "gap" / "unverified" for a check,
    #: a quality tier for a graded one. Distinct from `summary` (prose) and
    #: from `confidence` (how sure we are of it).
    #:
    #: Added alongside check_name so the project_analysis_findings row can be
    #: DERIVED from the annotation instead of hand-written beside it. The two
    #: had already drifted: security_hygiene's SECURITY.md check published
    #: confidence=90 on the annotation and stored a finding that omitted
    #: confidence and so defaulted to 100 — same check, same run, two numbers.
    #: Derivation makes that class of drift impossible rather than merely
    #: avoided by care. See docs/annotation-finding-collapse-design.md.
    label: str = ""
    #: SchemaAnalysisAnnotation's evidence_of points at the index of its
    #: sub-surveyor's aggregate ResourceMeasureAnnotation. `None` (default)
    #: means "not evidence of anything in this run" — every existing
    #: annotation, unchanged. A same-run, list-index signal, not a GUID: the
    #: GUID does not exist until after publish, and append order is NOT safe
    #: to reconstruct blind, cross-run, after the fact (see
    #: docs/annotation-linking-plan.md Q3) — this field is safe only because
    #: the sub-surveyor sets it deliberately, about its own list, in the same
    #: run that produces it. Consumed by Phase 2 (not implemented yet); Phase 1
    #: only adds the field so sub-surveyors can start setting it.
    evidence_of: int | None = None
    #: Egeria's `contentStatus` — whether the annotation's CONTENT is complete,
    #: which is a different field from the element's own lifecycle status
    #: (`ElementStatus`). A domain property on
    #: `AuthoredReferenceableProperties`, which `AnnotationProperties` extends.
    #:
    #: Added 2026-09-21 (Phase 1 slices 9/10). `"DRAFT"` marks a PROPOSAL — a
    #: finding that asks a curator to declare a governance element that does
    #: not exist yet (a DataGrain, a DataScope, a candidate Data Class or
    #: reference-data set). This is the mechanism the project owner's
    #: 2026-09-21 decision in `docs/egeria-support-for-multi-resource.md` §3
    #: settled on, after the corrected probe 4 showed `contentStatus: DRAFT`
    #: round-trips cleanly on both an element and an annotation — replacing
    #: the earlier plan to carry proposals as an RFA convention or to ask
    #: Egeria for new `candidate…Specification` fields. Verified live the same
    #: day (probes 4/5, `PROBES-2026-09-21.md`):
    #: `contentStatus: DRAFT` inside `properties` round-trips as `DRAFT` on
    #: read-back, while the element's `ElementStatus` stays `ACTIVE`
    #: throughout — the two are independent.
    #:
    #: NOT `initialStatus`: that sets `ElementStatus`, means something else
    #: entirely, and is silently dropped by pyegeria's `NewElementRequestBody`
    #: (pyegeria ISSUE-113). Probe 4's first pass tested it by mistake.
    #:
    #: §3's own closing paragraph flagged an open question — whether an
    #: ordinary read path surfaces `contentStatus` at all, or whether a DRAFT
    #: proposal renders identically to a confirmed finding — as something that
    #: "needs its own probe" before this field is safe to rely on. Phase 1
    #: slice 10 checked: nothing did. It added the surfacing (the survey
    #: reader, all three `EgeriaAnnotationItem` copies, and `renderAnnotations`,
    #: which draws an amber DRAFT badge and deliberately draws nothing for an
    #: empty status). The older `/{slug}/annotations` read path still does not
    #: carry it (`docs/Backlog.md`, slice 10 follow-up #3).
    #:
    #: Empty (the default) means "no contentStatus stated", which is what every
    #: annotation published before this slice carries and is NOT the same as
    #: confirmed content — a renderer must not draw a badge for "".
    content_status: str = ""


@dataclass
class ResourceMeasureAnnotation(Annotation):
    """File/size/language counts for a project or sub-scope."""
    annotation_type: AnnotationType = field(default=AnnotationType.RESOURCE_MEASURE, init=False)
    resource_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResourceProfileAnnotation(Annotation):
    """A profile of a resource: a count per value (per extension, per file type,
    per asset type). Egeria's `ResourceProfileAnnotationProperties`; the counts
    travel in its `valueCount` map. The annotation NAME (Egeria's own, verbatim:
    "Profile File Extensions" ...) is carried by `annotation_type_name`."""
    annotation_type: AnnotationType = field(default=AnnotationType.RESOURCE_PROFILE, init=False)
    value_count: dict[str, int] = field(default_factory=dict)


@dataclass
class ResourceProfileLogAnnotation(Annotation):
    """A profile too large to hold in the report, logged to a file the annotation
    points at ("Profile File Names": per-name counts, a CSV). Published only when
    a person asks for it."""
    annotation_type: AnnotationType = field(default=AnnotationType.RESOURCE_PROFILE_LOG, init=False)
    log_file: str = ""


@dataclass
class ClassificationAnnotation(Annotation):
    """Category or label assignments (language, project type, doc presence, etc.)."""
    annotation_type: AnnotationType = field(default=AnnotationType.CLASSIFICATION, init=False)
    candidate_classifications: list[str] = field(default_factory=list)
    #: Published in Egeria 6.2's own fields for this type (ClassificationAnnotationProperties: `classificationName`,
    #: String, and `properties`, map<string,string>; typedef OpenMetadataTypesArchive1_2 CLASSIFICATION_NAME,
    #: PROPERTIES), by annotation_props.classification_fields: `classificationName` is `classification_name`, else the
    #: first candidate; `properties` holds `classification_properties` and, when more than one classification is
    #: proposed, the whole ordered list (name first) as a JSON list under "candidateClassifications". Egeria has no
    #: top-level `candidateClassifications` on this type and its bean drops one (found 2026-10-15).
    classification_name: str = ""
    classification_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class SchemaAnalysisAnnotation(Annotation):
    """Module/API structure — public functions, classes, endpoints."""
    annotation_type: AnnotationType = field(default=AnnotationType.SCHEMA_ANALYSIS, init=False)
    schema_name: str = ""
    schema_type: str = ""


@dataclass
class DataClassAnnotation(Annotation):
    """Dependency classification — package name, version, ecosystem."""
    annotation_type: AnnotationType = field(default=AnnotationType.DATA_CLASS, init=False)
    candidate_data_class_names: list[str] = field(default_factory=list)


@dataclass
class QualityScoreAnnotation(Annotation):
    """Health and quality scores derived from GitHub stats."""
    annotation_type: AnnotationType = field(default=AnnotationType.QUALITY_SCORE, init=False)
    quality_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class RelationshipAnnotation(Annotation):
    """Discovered relationship between two components."""
    annotation_type: AnnotationType = field(default=AnnotationType.RELATIONSHIP, init=False)
    related_entity_name: str = ""
    relationship_type_name: str = ""


@dataclass
class RequestForActionAnnotation(Annotation):
    """Flag for human review — missing artifact, security gap, stale dep, etc."""
    annotation_type: AnnotationType = field(default=AnnotationType.REQUEST_FOR_ACTION, init=False)
    action_requested: str = ""
    action_target_name: str = ""


@dataclass
class ResourcePhysicalStatusAnnotation(Annotation):
    """Physical/operational state of a resource — Egeria's real
    `ResourcePhysicalStatusAnnotationProperties` type.

    Its own fixed fields (`resourceCreateTime`, `resourceUpdateTime`,
    `resourceLastAccessedTime`, `size`, `encodingType`) describe a
    filesystem-shaped resource and do not fit `postgres_operations`'
    `db_resilience` finding (replication role, replica lag, WAL archiving,
    backup-tool/clustering signals) — see design doc §5.5. Rather than
    fabricating values for fields this finding does not have, this
    dataclass carries a generic `physical_properties` bag, published under
    `additionalProperties` (`annotation_props.py`), the same convention
    already used for any field not yet natively typed on the wire. A
    future filesystem use of this type (design doc §6, Phase 2) is the one
    expected to populate the four named fields for real.
    """
    annotation_type: AnnotationType = field(default=AnnotationType.RESOURCE_PHYSICAL_STATUS, init=False)
    physical_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataGrainAnnotation(Annotation):
    """The grain of a table — "one row per what" — Egeria's real
    `DataGrainAnnotationProperties` type.

    Field names mirror the Egeria type exactly (`docs/egeria-support-for-
    multi-resource.md` §3 quotes them from the Java source:
    `granularityBasis`, `grainStatement`, `interval`,
    `candidateDataGrainGUIDs`), so the mapping in `annotation_props.py` is a
    rename-free copy and a future curator "accept this grain" action can
    prefill `create_data_grain` key-for-key.

    `candidate_data_grain_guids` stays empty in `db_derived`: the whole point
    of the slice-9 finding is that no `DataGrain` element exists yet to point
    at. The proposal is carried by `content_status = "DRAFT"` on the base
    class instead, per §3's project-owner decision.
    """
    annotation_type: AnnotationType = field(default=AnnotationType.DATA_GRAIN, init=False)
    #: e.g. "one row per (customer_id, order_date)"
    grain_statement: str = ""
    #: WHAT the statement was derived from — "primary_key", "unique_column",
    #: "temporal_key" — so a reader can weigh it without re-deriving it.
    granularity_basis: str = ""
    #: Egeria's own `interval`: a Java `long`, "the granularity of the data expressed in
    #: the units of the grain" (OpenMetadataProperty.INTERVAL, DataType.LONG, example "60").
    #: None = not known, and then nothing is sent. Only a whole number may go here: a word
    #: was refused live (400, Jackson "Cannot deserialize value of type `long`", 2026-10-14).
    interval: int | None = None
    #: RE's name for the period ("per-date", "daily", "monthly", ...), which is a word, not
    #: Egeria's count; published as additionalProperties.intervalName (a named departure:
    #: Egeria has no string field for it).
    interval_name: str = ""
    candidate_data_grain_guids: list[str] = field(default_factory=list)


@dataclass
class FingerprintAnnotation(Annotation):
    """A structural signature of a resource, and its nearest known matches —
    Egeria's real `FingerprintAnnotationProperties` type.

    **The native field names are not known here.** `docs/egeria-integration.md`
    §2 confirms the type exists and quotes its description ("an annotation
    capturing digital resource fingerprint information"), but no Egeria Java
    source checkout is available in this environment to read its properties
    from, and probe 5 established only that `create_annotation` ACCEPTS the
    type — not what fields it carries. So the payload travels as
    `additionalProperties` (the same fallback `ResourcePhysicalStatusAnnotation`
    uses for its own unmapped bag) rather than guessing at typed field names
    that would silently land in the wrong place. Logged to `docs/Backlog.md`
    as a probe worth running before anything depends on the field names.
    """
    annotation_type: AnnotationType = field(default=AnnotationType.FINGERPRINT, init=False)
    fingerprint_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class SurveyResult:
    """
    Complete output of one survey run against a project.

    Consumed by:
      - SurveyOrchestrator (assembles it from sub-surveyor outputs)
      - EgeriaPublisher    (converts to pyegeria API calls)
      - CLI survey command (renders as markdown without Egeria)
    """
    resource_slug: str
    project_display_name: str
    github_url: str
    surveyed_at: datetime = field(default_factory=datetime.utcnow)
    #: The commit of the zipball/clone this run read (artifacts slice 1); None when it read no code.
    survey_commit_sha: str | None = None
    #: step_key -> that SHA, for each step that COMPLETED having read it (step_cost_observer.completed).
    commit_by_step: dict = field(default_factory=dict)
    annotations: list[Annotation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # non-fatal issues during survey
    #: The re_analysis_step keys this run actually executed, set by
    #: SurveyOrchestrator, which is the one place that knows exactly.
    #:
    #: Recorded so a publish can say WHICH analyses it published rather than
    #: inferring it from annotation types afterwards. That inference held only
    #: while every type had a single producer, and stopped being true the
    #: moment a second analysis produced a QualityScoreAnnotation — after which
    #: no analysis could earn an attributable publish at all.
    steps_run: list[str] = field(default_factory=list)
    #: {step_key: reason} for steps a precondition declined to dispatch, kept
    #: SEPARATE from `steps_run` and from `step_errors` because a skip is neither.
    #: A skipped step did not run, so it is not in steps_run; it did not fail, so
    #: it is not an error. Folding it into either would recreate the ambiguity
    #: `result_status.SKIPPED_BY_DESIGN` exists to remove — and the reason travels
    #: with it, because a skip without one is indistinguishable from a failure on
    #: a screen. See surveyors/step_preconditions.py.
    skipped_steps: dict[str, str] = field(default_factory=dict)
    #: {step_key: reason} for prerequisite steps this run executed on its own
    #: initiative (design §17.1). A FOURTH bucket for the same reason
    #: `skipped_steps` is a third: an auto-run step ran, so it IS in
    #: `steps_run`, but it was not asked for, and "why did this take three
    #: minutes" is unanswerable without the distinction. The annotation says
    #: the same thing in the report; this is the branchable form.
    auto_ran_steps: dict[str, str] = field(default_factory=dict)
    #: {step_key: proposal} for chains that need the user's consent before
    #: running (design §17.1). Carried beside `skipped_steps`, not instead of
    #: it: the step WAS skipped, and the proposal is what to offer next.
    proposals: dict[str, dict] = field(default_factory=dict)
    # Same failures as `errors`, keyed by the step that raised. Needed because a
    # single run can now carry steps belonging to several different scheduled
    # analyses (scheduler.py coalesces same-repo due schedules into one run so
    # the zipball downloads once), and each of those schedules records its own
    # pass/fail. Without per-step attribution the batch would have to mark every
    # analysis in it failed because one step raised, which turns one real
    # failure into several false ones. `errors` stays the flat list every
    # existing caller reads.
    step_errors: dict[str, str] = field(default_factory=dict)
    #: {step_key: [annotations]} for every step that RAN to completion in this run (including a step
    #: that found nothing: an empty list, which is "ran, nothing found" and not "never ran"). The
    #: orchestrator keeps these as the repository's latest survey (surveyors/survey_snapshot.py,
    #: brief section 1) so a publish can send the survey a person decided on without running one.
    step_annotations: dict[str, list] = field(default_factory=dict)
    #: Why the survey could not be kept for later publishing, or "" (brief section 1). A survey that ran
    #: but could not be kept is reported, not hidden: the next publish would otherwise say there is no
    #: survey, with no word of why.
    snapshot_error: str = ""

    def add(self, annotation: Annotation) -> None:
        self.annotations.append(annotation)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def by_type(self, annotation_type: AnnotationType) -> list[Annotation]:
        return [a for a in self.annotations if a.annotation_type == annotation_type]


ANNOTATION_TYPES_REGISTRY = [
    {
        "type": "ResourceMeasureAnnotation",
        "display_name": "Resource Measure",
        "description": "Represents quantitative physical measurements of a resource (e.g. file count, size, language breakdown, row count, etc.).",
        "properties": ["resource_properties (dict)"],
        "egeria_type": "ResourceMeasureAnnotationProperties",
        "python_class": "ResourceMeasureAnnotation",
    },
    {
        "type": "ResourceProfileAnnotation",
        "display_name": "Resource Profile",
        "description": "Represents a count per value over a resource (e.g. files per extension, per file type, assets per asset type), as Egeria's own folder survey writes it.",
        "properties": ["value_count (dict[str, int])"],
        "egeria_type": "ResourceProfileAnnotationProperties",
        "python_class": "ResourceProfileAnnotation",
    },
    {
        "type": "ResourceProfileLogAnnotation",
        "display_name": "Resource Profile Log",
        "description": "Represents a profile too large to hold in the report (e.g. a count per file name), logged to a CSV the annotation points at. Published only on request.",
        "properties": ["log_file (str)"],
        "egeria_type": "ResourceProfileLogAnnotationProperties",
        "python_class": "ResourceProfileLogAnnotation",
    },
    {
        "type": "ClassificationAnnotation",
        "display_name": "Classification",
        "description": "Represents categorization or labels assigned to a resource (e.g. language name, framework/technology detected, project type, file format, etc.).",
        "properties": ["classificationName (str: the proposed classification, the first candidate)",
                       "properties (map<string,string>; candidateClassifications = JSON list of every candidate, "
                       "name first, when more than one)"],
        "egeria_type": "ClassificationAnnotationProperties",
        "python_class": "ClassificationAnnotation",
    },
    {
        "type": "SchemaAnalysis",
        "display_name": "Schema Analysis",
        "description": "Represents structural metadata discovered within a resource (e.g. modules, API symbols, classes, functions, database schemas, tables, columns, etc.).",
        "properties": ["schema_name (str)", "schema_type (str)"],
        "egeria_type": "SchemaAnalysisAnnotationProperties",
        "python_class": "SchemaAnalysisAnnotation",
    },
    {
        "type": "DataClassAnnotation",
        "display_name": "Data Class",
        "description": "Represents classification of data patterns or formats (e.g. dependency package ecosystems, email addresses, credit cards, custom formats).",
        "properties": ["candidate_data_class_names (list[str])"],
        "egeria_type": "DataClassAnnotationProperties",
        "python_class": "DataClassAnnotation",
    },
    {
        "type": "QualityScoreAnnotation",
        "display_name": "Quality Score",
        "description": "Represents calculated health/quality scores or metrics for a resource (e.g. repository bus factor, documentation coverage, test coverage, code quality).",
        "properties": ["quality_scores (dict[str, float])"],
        "egeria_type": "QualityAnnotationProperties",
        "python_class": "QualityScoreAnnotation",
    },
    {
        "type": "RelationshipAnnotation",
        "display_name": "Relationship Advice",
        "description": "Represents discovered dependencies or links between different resources (e.g. database foreign key relationships, imports, API consumers).",
        "properties": ["related_entity_name (str)", "relationship_type_name (str)"],
        "egeria_type": "RelationshipAdviceAnnotationProperties",
        "python_class": "RelationshipAnnotation",
    },
    {
        "type": "RequestForAction",
        "display_name": "Request For Action",
        "description": "Represents a flagged issue requiring human attention or stewardship action (e.g. missing SECURITY.md, inaccessible folder, profiling error, stale dependency).",
        "properties": ["action_requested (str)", "action_target_name (str)"],
        "egeria_type": "RequestForActionProperties",
        "python_class": "RequestForActionAnnotation",
    },
    {
        "type": "ResourcePhysicalStatusAnnotation",
        "display_name": "Resource Physical Status",
        "description": "Represents the physical/operational state of a resource (e.g. database replication role, replica lag, backup/archiving/clustering signals; a filesystem entry's create/update/access timestamps and size).",
        "properties": ["physical_properties (dict)"],
        "egeria_type": "ResourcePhysicalStatusAnnotationProperties",
        "python_class": "ResourcePhysicalStatusAnnotation",
    },
    {
        "type": "DataGrainAnnotation",
        "display_name": "Data Grain",
        "description": "Represents the granularity of a data collection — what one row means (e.g. one row per customer, one row per account per day). Produced as a PROPOSAL (contentStatus DRAFT) where no DataGrain element exists yet.",
        "properties": [
            "grain_statement (str)", "granularity_basis (str)",
            "interval (int | None)", "interval_name (str)", "candidate_data_grain_guids (list[str])",
        ],
        "egeria_type": "DataGrainAnnotationProperties",
        "python_class": "DataGrainAnnotation",
    },
    {
        "type": "FingerprintAnnotation",
        "display_name": "Fingerprint",
        "description": "Represents a structural signature of a resource and its nearest known matches, for duplicate/copy/subset detection across databases or files.",
        "properties": ["fingerprint_properties (dict)"],
        "egeria_type": "FingerprintAnnotationProperties",
        "python_class": "FingerprintAnnotation",
    },
]


_MAX_GROUP_ITEMS = 25


def step_of(annotation) -> str:
    """The step an annotation is attributed to: RE's `producingStep` first (additionalProperties),
    then `analysis_step`, so annotations written before producingStep existed keep working.
    Accepts an Annotation or the flat dict a reader returns."""
    if isinstance(annotation, dict):
        extra, step = annotation.get("additional_properties"), annotation.get("analysis_step")
    else:
        extra, step = getattr(annotation, "additional_properties", None), getattr(annotation, "analysis_step", "")
    produced = extra.get("producingStep") if isinstance(extra, dict) else ""
    return (produced or step or "")


def summarise_annotations(annotations, limit: int = 20) -> list[dict]:
    """Group a run's annotations for the activity log, by (step, type).

    Extracted 2026-09-02 so there is ONE grouping rule rather than two. It
    lived inline in SurveyOrchestrator.run(); the Analyses-card path
    (projects.py::run_single_analysis) needed the same shape, and copying it
    is how the original defect would have been reintroduced in a second
    place.

    Keying on (step, annotation_type) rather than step alone: a sub-surveyor
    emitting several annotation kinds in one run — a passing
    ClassificationAnnotation beside a failing RequestForActionAnnotation —
    used to collapse into one dict where `summary` kept the FIRST seen and
    `annotation_type` kept overwriting to the LAST, so a passing check's
    words wore a RequestForAction label in the drawer.

    `explanation`/`action_requested`/`action_target_name` are taken from the
    SAME annotation that donated the summary, never a different member of the
    group — two independent first/last-wins fields on one group is precisely
    the shape that broke this once.
    """
    from collections import defaultdict

    by_step: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "summary": "", "explanation": "",
                 "action_requested": "", "action_target_name": "", "items": []})
    for a in annotations:
        # Skip anything that is not shaped like an annotation rather than
        # raising. This function builds a DISPLAY summary; letting it throw
        # took down the whole analysis run — the background thread crashed
        # and a survey whose findings were computed and stored reported
        # "crashed" to the user, because a summary of it could not be built.
        ann_type = getattr(a, "annotation_type", None)
        value = getattr(ann_type, "value", None)
        if not value:
            continue
        step = step_of(a) or value
        group = by_step[(step, value)]
        group["count"] += 1
        # Every member kept, not just the first (2026-09-02).
        #
        # The group is a compact preview for the activity log, and that is
        # what it was built for. But GET /api/activity/rfas uses it as the
        # ACTIONABLE list, and a request for action that was summarised away
        # cannot be acted on. Measured on `workshops`: SecurityHygieneCheck
        # emits three RequestForActions — no SECURITY.md, no CI config, no
        # license — which share a step and a type, so the group carried
        # count=3 and the text of the first. The drawer rendered
        # "SecurityHygieneCheck 3 / No SECURITY.md found", which is exactly
        # Dan's original report: "they all seem to be SecurityHygieneCheck 3
        # - there is little description". The other two were not hidden.
        # They were never written.
        #
        # Capped: a group is a preview, and an unbounded list here would put
        # a whole survey's annotations into every activity row.
        if len(group["items"]) < _MAX_GROUP_ITEMS:
            group["items"].append({
                "summary": (getattr(a, "summary", "") or "")[:200],
                "explanation": getattr(a, "explanation", "") or "",
                "action_requested": getattr(a, "action_requested", "") or "",
                "action_target_name": getattr(a, "action_target_name", "") or "",
            })
        if not group["summary"]:
            group["summary"] = (getattr(a, "summary", "") or "")[:200]
            group["explanation"] = getattr(a, "explanation", "") or ""
            group["action_requested"] = getattr(a, "action_requested", "") or ""
            group["action_target_name"] = getattr(a, "action_target_name", "") or ""
    return [
        {"analysis_name": step, "annotation_type": ann_type,
         "count": v["count"], "status": "local", "summary": v["summary"],
         "explanation": v["explanation"],
         "action_requested": v["action_requested"],
         "action_target_name": v["action_target_name"],
         "items": v["items"]}
        for (step, ann_type), v in list(by_step.items())[:limit]
    ]


def disambiguate_shared_check_names(annotations) -> int:
    """Give keyless annotations whose `check_name` is emitted by more than one
    analysis step an `item_key` naming their step, so the published
    qualifiedNames stay distinct.

    Found on trevor 2026-09-04: four steps (SecretScan, ContributionProvenance,
    SlaContent, TelemetryScan) each emit one keyless `scan_summary`; their
    readers look the row up by (kind, check_name), so the check_name must stay
    `scan_summary` and the step is the natural key. Only the shared case is
    touched: a check_name emitted by a single step keeps the exact name it has
    always had, so nothing already published is renamed. Returns how many
    annotations were stamped.
    """
    steps_by_check: dict[str, set[str]] = {}
    for ann in annotations:
        check = getattr(ann, "check_name", "") or ""
        if check and not (getattr(ann, "item_key", "") or ""):
            steps_by_check.setdefault(check, set()).add(step_of(ann))
    shared = {c for c, steps in steps_by_check.items() if len(steps) > 1}
    stamped = 0
    for ann in annotations:
        check = getattr(ann, "check_name", "") or ""
        if check in shared and not (getattr(ann, "item_key", "") or "") and hasattr(ann, "item_key"):
            step = step_of(ann)
            if step:
                ann.item_key = step
                stamped += 1
    # Second pass: the executor stamps every keyless annotation with the
    # Survey Definition's qualified name (survey_definition_executor
    # `_stamp_definition_provenance`), which runs BEFORE this and so made the
    # pass above inert on that path: scan steps' `scan_summary` all arrived
    # carrying the same definition key (found 2026-10-08, egeria_workspaces_git
    # Full Survey). Where one (check_name, item_key) is shared by annotations
    # from DIFFERENT steps, qualify the key with the step. Only that ambiguous
    # case changes: a key held by a single step is left exactly as it was, and
    # two annotations from the SAME step with the same key are left alone so
    # assert_unique_qualified_names still refuses them.
    steps_by_key: dict[tuple[str, str], set[str]] = {}
    for ann in annotations:
        check = getattr(ann, "check_name", "") or ""
        key = getattr(ann, "item_key", "") or ""
        if check and key:
            steps_by_key.setdefault((check, key), set()).add(step_of(ann))
    ambiguous = {k for k, steps in steps_by_key.items() if len(steps) > 1}
    for ann in annotations:
        check = getattr(ann, "check_name", "") or ""
        key = getattr(ann, "item_key", "") or ""
        step = step_of(ann)
        if (check, key) in ambiguous and step and hasattr(ann, "item_key"):
            ann.item_key = f"{key}::{step}"
            stamped += 1
    return stamped


def assert_unique_qualified_names(prefix: str, annotations) -> None:
    """Raise if two annotations in one run would publish the same identity.

    This is the guard the whole scheme rests on. A colliding qualifiedName does
    not fail loudly on its own: the second create is rejected by Egeria as a
    duplicate, apply_element adopts the existing GUID, and the run reports
    success having published ONE element where two were produced. Silent loss,
    reported as a clean publish.

    A collision means a list-shaped check emitted several annotations without
    an `item_key`. Measured over 27,710 stored (slug, kind, run, check_name)
    groups, 15.1% are list-shaped, so this is the common case for exactly the
    analyses most likely to be extended: architecture_*, cve_scan, secret_scan.

    Raised rather than logged, and raised BEFORE anything is written: a partial
    publish is harder to reason about than a refused one.
    """
    disambiguate_shared_check_names(annotations)
    seen: dict[str, int] = {}
    for i, ann in enumerate(annotations):
        qn = annotation_qualified_name(prefix, i, ann)
        if qn in seen:
            check = getattr(ann, "check_name", "") or "(none)"
            raise ValueError(
                f"Two annotations would publish the same qualifiedName {qn!r} "
                f"(indices {seen[qn]} and {i}). check_name={check!r} emits more "
                f"than one annotation per run, so it is list-shaped and each "
                f"one needs a distinct `item_key` — the surveyor knows what "
                f"identifies its item (a path and line, a term, a package)."
            )
        seen[qn] = i


def finding_from_annotation(annotation) -> dict:
    """Derive one project_analysis_findings row from the annotation that IS
    the observation.

    The point is not convenience. Before this, a surveyor wrote the annotation
    and then wrote the finding again by hand, and the two drifted with nothing
    to catch it — no exception, no failing test, two stores quietly disagreeing
    about the same check in the same run. Deriving means a value can only be
    wrong in both places at once, which is a discrepancy a reader can see.

    `detail` is assembled from the fields that carry the annotation's specifics
    (candidate_classifications, the RFA's action pair) plus json_properties, so
    nothing the hand-written rows carried is dropped.
    """
    # json_properties is the free-form bag and goes in FIRST, so a key
    # collision cannot be how a structured field silently disappears — the
    # typed field is the annotation's own answer and wins.
    detail: dict[str, Any] = dict(annotation.json_properties)
    candidates = getattr(annotation, "candidate_classifications", None)
    if candidates:
        detail["candidate_classifications"] = list(candidates)
    action_requested = getattr(annotation, "action_requested", "")
    if action_requested:
        detail["action_requested"] = action_requested
    action_target = getattr(annotation, "action_target_name", "")
    if action_target:
        detail["action_target_name"] = action_target

    # project_analysis_findings has no column for these two, so they ride in
    # detail_json rather than being dropped — the explanation is exactly what
    # the hand-written finding rows were missing.
    if annotation.explanation:
        detail.setdefault("explanation", annotation.explanation)
    if annotation.item_key:
        detail.setdefault("item_key", annotation.item_key)

    return {
        "check_name": annotation.check_name,
        "item_key": annotation.item_key,
        "label": annotation.label,
        "summary": annotation.summary,
        "confidence": annotation.confidence,
        "detail": detail or None,
    }


def findings_from_annotations(annotations: Sequence) -> list[dict]:
    """Derive finding rows for every annotation carrying a `label`.

    An annotation without one is not a finding — it is a measure, a
    relationship, or a warning — so it is skipped rather than stored with an
    empty verdict.
    """
    return [finding_from_annotation(a) for a in annotations if a.label]


def annotation_qualified_name(prefix: str, index: int, annotation) -> str:
    """The published identity of one annotation.

    ONE function, because there are two call sites — the outbox
    (egeria_outbox.enqueue_annotations) and the direct path
    (annotation_props.publish_annotations) — and a qualifiedName that differed
    between them would make a retry create a second element instead of
    converging on the one already written.

    Shape:  {prefix}::{check_name}[::{item_key}]     when check_name is set
            {prefix}::{index}                        otherwise (legacy)

    The legacy form is the positional scheme this replaces: the enumeration
    index within the run. Positions are not stable — measured on egeria_git's
    two largest published runs, across 115 overlapping indices, the same index
    meant the same finding 0 times — so a check could not be followed through
    time. It remains the fallback only while surveyors are migrated, and it is
    a fallback rather than a removal so that a surveyor which has not been
    updated still publishes rather than failing.

    Separators are escaped, not stripped: a check_name or item_key containing
    "::" would otherwise silently change the shape of the name and could
    collide with a different annotation's identity.
    """
    check = _sanitise_name_part(getattr(annotation, "check_name", "") or "")
    if not check:
        return f"{prefix}::{index}"
    item = _sanitise_name_part(getattr(annotation, "item_key", "") or "")
    return f"{prefix}::{check}::{item}" if item else f"{prefix}::{check}"


def _sanitise_name_part(value: str) -> str:
    """Make one qualifiedName segment safe to join with '::'."""
    return value.replace("::", ":_:").strip()
