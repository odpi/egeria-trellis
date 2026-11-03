"""Profile publish reshape: re-link an unchanged value-profile measure, create a changed one.

BRIEF-PROFILE-PUBLISH-RESHAPE (ruling 2026-10-22): Egeria's own survey re-links unchanged annotations to its new
report and creates annotations only for changed measures. RE does the same for the value profile's per-column
`ResourceProfileAnnotation`s (`value_profile.build_annotations`).

The key is (resource, analysis step, `item_key`), the column path as the compact JSON array
`["schema","table","column"]` (`value_profile.measure_key`). A measure is unchanged when the sha256 of its canonical
JSON (`content_hash`: `PROFILE_SHAPE_VERSION`, `value_count` plus `generalType`, `profileDates` and `sampleRatio`
from `json_properties`) equals the hash on RE's own LATEST HASHED proof row for that key across every RE report of
the resource (`registry.latest_profile_publish`), never a re-read of Egeria. Pure: no Egeria, no registry.

Round 2 (architect rulings 2026-10-24):
- a relative-to-now measure (`value_profile.RELATIVE_TO_NOW_MEASURES`, `future_date_ct`) and `profileDates.asOf`
  are never hashed: a static table profiled an hour later is unchanged;
- the `ResourceMeasureAnnotation` of a table not profiled (`item_key` `["schema","table"]`, `value_profile.table_key`)
  and the proposals step's one annotation (the `RequestForAction`, or its 0-proposed measure; `item_key`
  `propose.ITEM_KEY`) take the same hash, plan and re-link path (`relink_kind`). A run-specific field (the run id)
  is never hashed, so an unchanged one is re-linked carrying the run that first published it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from resource_explorer.surveyors.database.quality.value_profile import ANALYSIS_STEP

#: The shape of the canonical measures, hashed with them: a change to what is compared (a new key, a renamed one)
#: bumps it, so every measure reads as changed once instead of matching an old hash by accident.
PROFILE_SHAPE_VERSION = 2

#: The json_properties keys a measure is compared on. `ranAs` (who ran the profile) is provenance, not a measure.
COMPARED_PROPERTIES = ("generalType", "profileDates", "sampleRatio")
#: `profileDates` keys that are the instant a measure was taken against, not a measure.
UNCOMPARED_DATES = frozenset({"asOf"})
#: The proposals annotation's fields that name the run that made it, never compared.
RUN_FIELDS = frozenset({"runId", "measuredAt", "producingRun"})

#: Ruling 5 (architect, 2026-10-24): the card says WHAT KIND of change a changed column measure was. COUNTS: the raw
#: counts of `value_count` (record_ct, value_ct, null_value_ct, distinct_value_ct, and the other raw counts). SHAPE:
#: what the counts and properties say about the column, not how many: general type, null pattern, distinct-ratio band,
#: min/max text (`profileDates` less `asOf`), sample ratio. Both still hash and re-link together (`content_hash`);
#: each created row records which group moved (`moved`), so the card's words come from proof rows.
COUNTS, SHAPE = "counts", "shape"
#: `moved` for a created measure that is not a change of an earlier one (a new column), and for one whose earlier
#: proof row carries no group hashes to compare with (published before ruling 5, or under another shape version).
NEW, NOT_COMPARED = "new", "not_compared"
#: Distinct-ratio bands (distinct_value_ct / value_ct), highest first: the first lower bound the ratio reaches.
DISTINCT_RATIO_BANDS = ((1.0, "unique"), (0.5, "high"), (0.05, "medium"), (0.0, "low"))
#: Shape compares the BAND, never the ratio, and the band is held (architect, 2026-10-24, amending ruling 5): a column
#: leaves its previous band only when its ratio is past one of that band's edges by MORE than this. The band a measure
#: was given is kept on its proof row (`groups["band"]`, detail_json) so the next publish knows what to hold.
BAND_HYSTERESIS = 0.02

#: What `relink_kind` says an annotation is: a column measure, a table not profiled, the proposals annotation.
MEASURE, TABLE_NOT_PROFILED, PROPOSALS = "measure", "table_not_profiled", "proposals"

CREATED, RELINKED, NO_LONGER_MEASURED = "created", "relinked", "no_longer_measured"
#: A previous measure whose TABLE was not profiled this run (timed out, refused, not profiled): not measured, which
#: is never "no longer measured" (the column may well still be there).
NOT_MEASURED_THIS_RUN = "not_measured_this_run"
#: The proof-row note of a measure planned for re-link that Egeria refused (the annotation archived, deleted or
#: wiped), and so created again in the same run.
RECREATED_NOTE = "re-created: the re-used annotation was gone"


def _key_parts(item_key: str) -> list | None:
    try:
        parts = json.loads(item_key)
    except (TypeError, ValueError):
        return None
    return parts if isinstance(parts, list) else None


def table_of(item_key: str) -> str:
    """The table key (`value_profile.table_key`, `["schema","table"]`) of a column measure's key, as the value
    profile names a table it did not profile; "" for a key that is not a column's JSON array."""
    from resource_explorer.surveyors.database.quality.value_profile import table_key

    parts = _key_parts(item_key)
    return table_key(parts[0], parts[1]) if parts and len(parts) == 3 else ""


def is_column_key(item_key: str) -> bool:
    """A column measure's key (`["schema","table","column"]`): the only keys that can be "no longer measured"."""
    parts = _key_parts(item_key)
    return bool(parts) and len(parts) == 3


def tables_not_profiled(annotations: list) -> set[str]:
    """The tables this run's value profile did not profile: its `ResourceMeasureAnnotation`s (one per such table,
    `item_key` `["schema","table"]`)."""
    return {a.item_key for a in annotations if relink_kind(a) == TABLE_NOT_PROFILED}


def is_profile_measure(annotation) -> bool:
    """One of the value profile's per-column measures (the card counts only these)."""
    from resource_explorer.surveyors.survey_report import ResourceProfileAnnotation

    return (isinstance(annotation, ResourceProfileAnnotation) and annotation.analysis_step == ANALYSIS_STEP
            and bool(annotation.item_key))


def relink_kind(annotation) -> str:
    """What this reshape re-links: MEASURE (a column's ResourceProfileAnnotation), TABLE_NOT_PROFILED (the value
    profile's ResourceMeasureAnnotation for a table it did not profile), PROPOSALS (the proposals step's one
    annotation); "" for anything else (created every run, as before: QualityAnnotations among them)."""
    from resource_explorer.surveyors.database.quality import propose
    from resource_explorer.surveyors.survey_report import (
        RequestForActionAnnotation,
        ResourceMeasureAnnotation,
    )

    if not getattr(annotation, "item_key", ""):
        return ""
    if is_profile_measure(annotation):
        return MEASURE
    step = getattr(annotation, "analysis_step", "")
    if step == ANALYSIS_STEP and isinstance(annotation, ResourceMeasureAnnotation):
        return TABLE_NOT_PROFILED
    if (step == propose.ANALYSIS_STEP and annotation.check_name == propose.CHECK_NAME
            and isinstance(annotation, (RequestForActionAnnotation, ResourceMeasureAnnotation))):
        return PROPOSALS
    return ""


def annotation_type_of(annotation) -> str:
    """The Egeria type name a proof row records (`native_survey_annotations.annotation_type`)."""
    from resource_explorer.surveyors.survey_report import RequestForActionAnnotation

    return "RequestForAction" if isinstance(annotation, RequestForActionAnnotation) else type(annotation).__name__


def canonical_measures(annotation) -> dict:
    """What a measure is compared on: `value_count`, and the compared keys of `json_properties`. A key absent
    from json_properties is absent here (absent then present is a change). A relative-to-now measure and
    `profileDates.asOf` are left out (round 2, ruling (b)): they move with the clock, not the data.

    The other kinds compare their published words and properties, less `RUN_FIELDS`."""
    from resource_explorer.surveyors.database.quality.value_profile import RELATIVE_TO_NOW_MEASURES

    kind = relink_kind(annotation)
    if kind == TABLE_NOT_PROFILED:
        return {"shape_version": PROFILE_SHAPE_VERSION, "kind": kind, "label": annotation.label,
                "summary": annotation.summary, "explanation": annotation.explanation,
                "resource_properties": dict(annotation.resource_properties or {})}
    if kind == PROPOSALS:
        # The explanation repeats the counts and names the run, so it is not compared; the counts are.
        return {"shape_version": PROFILE_SHAPE_VERSION, "kind": kind, "type": annotation_type_of(annotation),
                "label": annotation.label, "summary": annotation.summary,
                "action_requested": getattr(annotation, "action_requested", None),
                "action_target_name": getattr(annotation, "action_target_name", None),
                "resource_properties": _without_run(getattr(annotation, "resource_properties", None)),
                "additional_properties": _without_run(annotation.additional_properties)}
    props = annotation.json_properties or {}
    compared = {k: props[k] for k in COMPARED_PROPERTIES if k in props}
    if isinstance(compared.get("profileDates"), dict):
        compared["profileDates"] = {k: v for k, v in compared["profileDates"].items() if k not in UNCOMPARED_DATES}
    return {"shape_version": PROFILE_SHAPE_VERSION,
            "value_count": {k: v for k, v in (annotation.value_count or {}).items()
                            if k not in RELATIVE_TO_NOW_MEASURES},
            "json_properties": compared}


def _without_run(props) -> dict:
    return {k: v for k, v in (props or {}).items() if k not in RUN_FIELDS}


def _sha(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(annotation) -> str:
    """sha256 over the canonical JSON: keys sorted, no whitespace, numbers exact (an int and a float are
    different text, so they never compare equal by accident), dates as strings."""
    return _sha(canonical_measures(annotation))


def null_pattern(counts: dict) -> str | None:
    """"none", "some" or "all" of the rows null; None when either count was not measured."""
    nulls, rows = counts.get("null_value_ct"), counts.get("record_ct")
    if nulls is None or rows is None:
        return None
    return "none" if nulls == 0 else "all" if nulls == rows else "some"


def _band_edges(band: str) -> tuple[float, float] | None:
    """[lower, upper) of a band's ratios; None for a name that is not a band."""
    upper = float("inf")
    for floor, name in DISTINCT_RATIO_BANDS:
        if name == band:
            return floor, upper
        upper = floor
    return None


def distinct_band(counts: dict, previous: str | None = None) -> str | None:
    """The distinct-ratio band (`DISTINCT_RATIO_BANDS`); None when there is no value to divide by.

    With `previous` (the band the last publish gave this column) the band is HELD: it changes only when the ratio is
    below the previous band's lower edge, or above its upper edge, by more than `BAND_HYSTERESIS`. "unique" is the one
    band whose upper edge no ratio can pass (a ratio is at most 1.0), so entering it takes the ratio reaching 1.0;
    leaving it takes the same 0.02 as every other edge."""
    distinct, values = counts.get("distinct_value_ct"), counts.get("value_ct")
    if distinct is None or not values:
        return None
    ratio = distinct / values
    band = next(name for floor, name in DISTINCT_RATIO_BANDS if ratio >= floor)
    edges = _band_edges(previous) if previous else None
    if edges is None or band == previous or band == DISTINCT_RATIO_BANDS[0][1]:
        return band
    lower, upper = edges
    return previous if lower - BAND_HYSTERESIS <= ratio <= upper + BAND_HYSTERESIS else band


def measure_groups(annotation, previous_band: str | None = None) -> dict[str, str]:
    """{COUNTS: hash, SHAPE: hash, "band": the held distinct-ratio band} of a column measure. The two hashes together
    cover everything `content_hash` covers except the ratio inside a held band, so a changed measure always moved at
    least one group, or only its counts. `previous_band`: the band on its last proof row (`distinct_band`)."""
    canonical = canonical_measures(annotation)
    counts, props = canonical["value_count"], canonical["json_properties"]
    band = distinct_band(counts, previous_band)
    shape = {"generalType": props.get("generalType"), "nullPattern": null_pattern(counts),
             "distinctBand": band, "profileDates": props.get("profileDates"),
             "sampleRatio": props.get("sampleRatio")}
    return {COUNTS: _sha([PROFILE_SHAPE_VERSION, counts]), SHAPE: _sha([PROFILE_SHAPE_VERSION, shape]),
            "band": band}


def moved_groups(groups: dict, before: dict | None) -> list[str]:
    """Which groups of a created measure moved since `before` (its latest hashed proof row, or None)."""
    if not before:
        return [NEW]
    earlier = before.get("groups") or {}
    moved = [g for g in (COUNTS, SHAPE) if earlier.get(g) and earlier.get(g) != groups.get(g)]
    if not all(earlier.get(g) for g in (COUNTS, SHAPE)) or not moved:
        return [NOT_COMPARED]
    return moved


@dataclass
class ProfilePlan:
    """For each re-linkable annotation in this publish (by its index in the publish's annotation list,
    `relink_kind`): its key, its hash, its step, and the previous annotation's GUID when it is to be re-linked.
    `gone`: previous column measures this run did not measure and whose table it did profile ({item_key: previous
    annotation GUID}), each counted once (a key whose latest proof row already says so is not counted again).
    `not_measured`: previous column measures whose table this run did not profile. `recreated`: indices planned
    for re-link that Egeria refused, created instead. `previous_by_step`: {step: (report GUID, report_at)} of the
    publish each step was compared with; `previous_report_*` is the value profile's."""
    previous_report_guid: str = ""
    previous_report_at: str = ""
    previous_by_step: dict[str, tuple[str, str]] = field(default_factory=dict)
    steps: dict[int, str] = field(default_factory=dict)
    #: Column measures only: their group hashes, and for a created one which groups moved (ruling 5).
    groups: dict[int, dict] = field(default_factory=dict)
    moved: dict[int, list] = field(default_factory=dict)
    keys: dict[int, str] = field(default_factory=dict)
    hashes: dict[int, str] = field(default_factory=dict)
    relink: dict[int, str] = field(default_factory=dict)
    gone: dict[str, str] = field(default_factory=dict)
    not_measured: dict[str, str] = field(default_factory=dict)
    recreated: set[int] = field(default_factory=set)

    @property
    def create(self) -> list[int]:
        return [i for i in self.keys if i not in self.relink]

    def recreate(self, indices) -> None:
        """Egeria refused these re-links (the annotation is gone): create them in this run instead."""
        for i in indices:
            if self.relink.pop(i, None) is not None:
                self.recreated.add(i)


def plan_profile_publish(annotations: list, previous: dict | None,
                         others: dict[str, dict | None] | None = None) -> ProfilePlan:
    """Which re-linkable annotations to re-link and which to create, against `previous`
    (`registry.latest_profile_publish` of the value profile step, or None when nothing was published before) and
    `others` ({analysis step: the same for that step}, the proposals step's)."""
    by_step = {ANALYSIS_STEP: previous, **(others or {})}
    measures = (previous or {}).get("measures") or {}
    already_gone = set((previous or {}).get("gone_keys") or ())
    unprofiled = tables_not_profiled(annotations)
    plan = ProfilePlan(previous_report_guid=(previous or {}).get("report_guid", ""),
                       previous_report_at=(previous or {}).get("report_at", ""),
                       previous_by_step={step: (p.get("report_guid", ""), p.get("report_at", ""))
                                         for step, p in by_step.items() if p})
    seen: set[str] = set()
    for i, ann in enumerate(annotations):
        if not relink_kind(ann):
            continue
        key, step = ann.item_key, ann.analysis_step
        plan.keys[i], plan.hashes[i], plan.steps[i] = key, content_hash(ann), step
        if step == ANALYSIS_STEP:
            seen.add(key)
        before = ((by_step.get(step) or {}).get("measures") or {}).get(key)
        if before and before.get("content_hash") == plan.hashes[i] and before.get("annotation_guid"):
            plan.relink[i] = before["annotation_guid"]
        if relink_kind(ann) == MEASURE:
            plan.groups[i] = measure_groups(ann, ((before or {}).get("groups") or {}).get("band"))
            if i not in plan.relink:
                plan.moved[i] = moved_groups(plan.groups[i], before)
    for key, before in measures.items():
        # Only a column measure is "no longer measured": a table key absent this run was profiled this time.
        if key in seen or not before.get("annotation_guid") or not is_column_key(key):
            continue
        if table_of(key) in unprofiled:
            plan.not_measured[key] = before["annotation_guid"]
        elif key not in already_gone:
            plan.gone[key] = before["annotation_guid"]
    return plan


def _when(at: str) -> str:
    """A report time as the card shows it (round 2, ruling (a)): date AND time in UTC with Z, "2026-10-24 21:09Z",
    so two runs on one day read apart. A time with no zone is UTC (RE writes them so); one that cannot be read is
    shown as stored, never a guessed time."""
    from datetime import UTC, datetime

    try:
        instant = datetime.fromisoformat(at or "")
    except ValueError:
        return at or ""
    instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant.astimezone(UTC)
    return instant.strftime("%Y-%m-%d %H:%MZ")


def profile_publish_words(counts: dict | None, live_share: float | None = None) -> str:
    """The run card's line, from the publish's proof rows only (`registry.profile_publish_counts`):
    "N measures published · M changed since <previous report date and time, UTC> · K re-used · J no longer measured", and
    " · T not measured this run" when a previous measure's table was not profiled this run.
    With no earlier publish to compare with, "N measures published · first profile published". "" when the
    publish has no profile proof rows.

    PUBLISH-TIME words only (fix round 1, review item 7): the line is written when the publish runs and stored on
    the run's activity row. A reset recorded AFTER that does not rewrite the stored line; the run's own publish word
    (`ProjectRegistry._attach_publish_proofs` -> `pre_reset`) is what turns ◌ on screen for an older run. With
    `pre_reset_at` (the counts read after the reset) it says "N measures published before the <date> reset · not in
    Egeria now".

    Ruling 5: "M changed since <time>: C counts moved · S shape changed" (`changed_kinds_words`), and
    " · this database is written live · see change rates" at the end when `written_live(counts, live_share)`.
    A re-linked proposals request keeps the run that first published it, so the line says
    " · proposals unchanged since <that report's time>" (`proposals_unchanged_since`, from the proof rows), and a
    re-used request is never read as new work."""
    proposals = (f"proposals unchanged since {_when(counts['proposals_unchanged_since'])}"
                 if counts and counts.get("proposals_unchanged_since") and not counts.get("pre_reset_at") else "")
    if not counts or not counts.get("rows"):
        return proposals
    proposals = f" · {proposals}" if proposals else ""
    n, k, j, t, r = (counts.get(x, 0) for x in ("published", RELINKED, NO_LONGER_MEASURED,
                                                  NOT_MEASURED_THIS_RUN, "recreated"))
    m = counts.get("changed", counts.get(CREATED, 0) - r)
    if counts.get("pre_reset_at"):
        # Its report predates the latest Egeria reset: what it published is not in Egeria now (never "published").
        from resource_explorer.catalogue_commit import pre_reset_words

        return f"{n} measures {pre_reset_words(counts['pre_reset_at'])}"
    live = f" · {WRITTEN_LIVE_WORDS}" if written_live(counts, live_share) else ""
    if not counts.get("previous_report_at"):
        return f"{n} measures published · first profile published" + proposals
    return (f"{n} measures published · {m} changed since {_when(counts['previous_report_at'])}"
            + (changed_kinds_words(counts) if m else "")
            + f" · {k} re-used" + (f" · {r} re-created (the re-used annotation was gone)" if r else "")
            + f" · {j} no longer measured" + (f" · {t} not measured this run" if t else "") + proposals + live)


def changed_kinds_words(counts: dict) -> str:
    """Ruling 5's breakdown of "M changed", from the proof rows' `moved`: ": C counts moved · S shape changed", then
    " · N new" and " · X not compared" when there are any. A measure whose counts AND shape moved is counted in
    both."""
    c, s, n, x = (counts.get(k, 0) for k in ("counts_moved", "shape_changed", "new", "not_compared"))
    return (f": {c} counts moved · {s} shape changed" + (f" · {n} new" if n else "")
            + (f" · {x} not compared" if x else ""))


#: Ruling 5: the words the card adds when counts moved on more than the declared share of the measured tables.
WRITTEN_LIVE_WORDS = "this database is written live · see change rates"


def written_live(counts: dict | None, share: float | None) -> bool:
    """Counts moved on MORE than `share` (`config.ValueProfileSettings.live_write_table_share`) of the tables this
    publish measured, from the proof rows (`tables_counts_moved` of `tables_measured`). False with no share, no
    earlier publish to compare with, or no table measured."""
    if share is None or not counts or not counts.get("previous_report_at") or counts.get("pre_reset_at"):
        return False
    measured = counts.get("tables_measured") or 0
    return bool(measured) and (counts.get("tables_counts_moved") or 0) / measured > share
