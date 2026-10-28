"""The gaps collection — findings about the ANALYSIS, owned by the project.

SPEC-ACTIONABLE-AND-HONEST.md §3: "A finding about the analysis belongs in a
gaps list the project owns." Two shapes land here, and only these two —
everything else about the repository names a `destination` instead (see
destinations.py):

    not_measurable   a check that ran (or could have) and could not be
                      established for this resource — result_status's
                      NOT_ESTABLISHED / "not_established" label.
    disagreement      two measures point different ways for the same
                      underlying question (today: community_support's
                      participation vs. chaoss_metrics' authorship
                      concentration — facts.py's `measures_disagree`, the
                      "existing disagreement computation" this reuses rather
                      than re-deriving).

`collect_gaps` is pure — it reads the same sources the page reads
(project_analysis_findings via query_findings, and the resource-state facts
in facts.py) and returns gap dicts. It never writes. `record_gaps_for` is the
one caller that turns that into persistence, upserting through
ProjectRegistry.upsert_gap — called from FactLayer.facts() (see that
module), the one choke point every consumer of "what is known about this
resource" already goes through, so the collection is current whenever the
page is without a second scheduler loop.
"""
from __future__ import annotations

import logging

from resource_explorer.destinations import OURS, analyses_with_checks
from resource_explorer.surveyors.result_status import NOT_ESTABLISHED

log = logging.getLogger(__name__)

NOT_MEASURABLE = "not_measurable"
DISAGREEMENT = "disagreement"

#: Who noticed the gap. Stored inside `evidence` (no schema change) and
#: surfaced as a top-level `source` by `gaps_summary`, because "two of our
#: own measures point different ways" and "a person read the answer and said
#: it is wrong" are different claims that must not render alike — the same
#: rule result_status.py applies to a measurement, applied to its origin.
SOURCE_MEASURED = "measured"
SOURCE_PERSON = "person"

#: `check_name` prefix for a gap raised by a person against one question's
#: answer. Prefixed so it can never collide with a real check name from
#: check_registry.yaml, and so the identity index
#: (project_slug, analysis_id, gap_kind, check_name) keys one gap per
#: (resource, analysis, question) — a second person disagreeing with the same
#: answer refreshes that gap rather than creating a parallel one.
QUESTION_PREFIX = "question:"


def question_check_name(question: str, question_id: str = "") -> str:
    """The `check_name` a person's disagreement about `question` is stored
    under. Deterministic and reversible by inspection -- the question is
    carried whole rather than hashed, so a row in the gaps table can be read
    without a lookup table.

    With the question's stable id the key is `question:Q-0001`, which a
    rewording of the text cannot change; without one (an uncatalogued
    question) it is the text, as every row written before ids existed is."""
    qid = (question_id or "").strip()
    return f"{QUESTION_PREFIX}{qid or (question or '').strip()}"


def gap_is_about_question(gap: dict, question: str = "", question_id: str = "") -> bool:
    """Whether a stored gap row is a person's disagreement about this question, by id or by text.

    A row written after ids existed carries the id in its check_name and in `evidence.question_id`; a row
    written before carries only the text in its check_name. Both match, so a text edit orphans neither."""
    qid = (question_id or "").strip()
    name = gap.get("check_name") or ""
    if not name.startswith(QUESTION_PREFIX):
        return False
    evidence = gap.get("evidence") or {}
    if qid and (name == question_check_name("", qid) or evidence.get("question_id") == qid):
        return True
    text = (question or "").strip()
    return bool(text) and name == question_check_name(text)


def _not_measurable_gaps(registry, slug: str) -> list[dict]:
    """One gap per (analysis, check) whose latest finding row is labelled
    `not_established` — "ran, but nothing shows this analysis produced the
    result" (result_status.py). Scoped to check_registry.yaml's `analyses`
    section (the per-check ones query_findings can resolve a check_name
    against); `whole_analysis_only` ids have no check-level rows to read this
    way — a NOT_ESTABLISHED whole-analysis Fact is visible through
    FactLayer.fact() instead, not duplicated here."""
    out = []
    for analysis_id, spec in analyses_with_checks().items():
        kind = spec.get("findings_kind", analysis_id)
        rows = registry.query_findings(slug, kind) or []
        by_check = {r["check_name"]: r for r in rows}
        for check_name in spec.get("checks", []):
            row = by_check.get(check_name)
            if row and (row.get("label") or "") == NOT_ESTABLISHED:
                out.append({
                    "analysis_id": analysis_id,
                    "gap_kind": NOT_MEASURABLE,
                    "destination": OURS,
                    "check_name": check_name,
                    "sentence": row.get("summary") or (
                        f"{check_name} could not be established for this "
                        f"resource by {analysis_id}."
                    ),
                    "evidence": {
                        "check_name": check_name, "label": row.get("label"),
                        "confidence": row.get("confidence"),
                        "source": SOURCE_MEASURED,
                    },
                })
    return out


def _disagreement_gaps(registry, slug: str, entity_type: str = "repo") -> list[dict]:
    """One gap per resource-state resolver whose value carries
    `measures_disagree` — facts.py's own disagreement computation
    (`_r_community`, ~line 329), read rather than re-derived so this can never
    disagree with the card the user is looking at.

    Dispatches via `get_adapter(entity_type)` rather than importing
    `RESOURCE_STATE_SOURCES` directly (which is repo's own table — see its
    docstring in facts.py) and rather than `registry.get(slug)` (the
    repo-only `projects` table lookup, which returns None for a database or
    filesystem slug even when it exists).

    Two DIFFERENT absences are distinguished here on purpose, per
    find-absence-as-answer: "this resource type declares no disagreement
    checks at all" (repo is the only type with a `state_sources` provider
    today — `[]` is a true, checked absence) is not the same claim as "we
    could not find/evaluate the resource" (logged, not silently folded into
    the same `[]`). Both still return `[]` to the caller because this
    function's return shape has no third gap_kind for "not applicable to
    this type" — see the module docstring's two shapes — but the distinction
    is now visible in the logs rather than indistinguishable in the result.
    """
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        get_adapter,
    )

    try:
        adapter = get_adapter(entity_type)
    except SurveyDefinitionExecutorError:
        log.debug("_disagreement_gaps: no Survey Definition adapter registered "
                  "for entity_type=%r — nothing to check (never established, "
                  "not the same as 'checked, no disagreement')", entity_type)
        return []

    state_sources_provider = adapter.state_sources
    if not callable(state_sources_provider):
        # Undeclared, not empty. Today only repo declares a `state_sources`
        # provider (RESOURCE_STATE_SOURCES) — database/filesystem leave it
        # None deliberately (their own adapter modules say so). A resource
        # type with no provider has genuinely nothing for this function to
        # check, which is a true `[]` — but it must never be reached by
        # accidentally resolving repo's table for a non-repo slug, which is
        # what `registry.get(slug)` returning None used to collapse into the
        # same silent `[]` as "no state sources for this type" without a way
        # to tell the two apart.
        log.debug("_disagreement_gaps: entity_type=%r declares no "
                  "state_sources — no disagreement checks exist for this "
                  "type (not the same as 'checked, found none')", entity_type)
        return []
    state_sources = state_sources_provider() or {}

    entity = adapter.get_entity(registry, slug)
    if entity is None:
        log.debug("_disagreement_gaps: entity_type=%r slug=%r not found — "
                  "could not evaluate its %d declared state-source "
                  "resolver(s) (unmeasured, not 'no disagreement')",
                  entity_type, slug, len(state_sources))
        return []

    out = []
    for question, (resolver, subject) in state_sources.items():
        try:
            value, _state = resolver(registry, entity)
        except Exception as exc:
            # A resolver failing to run is not itself a disagreement — it is
            # the kind of "could not check" the not_measurable half already
            # covers at the check level. Logged and skipped rather than
            # raised, because one resolver's failure must not stop every
            # other resolver in this loop from being checked.
            log.debug("gap resolver %s failed for %s: %s", subject, slug, exc)
            continue
        note = (value or {}).get("measures_disagree")
        if not note:
            continue
        out.append({
            "analysis_id": subject,
            "gap_kind": DISAGREEMENT,
            "destination": OURS,
            "check_name": subject,
            "sentence": note,
            "evidence": {
                "question": question, "value": value, "source": SOURCE_MEASURED,
            },
        })
    return out


def collect_gaps(registry, slug: str, entity_type: str = "repo") -> list[dict]:
    """Every gap about the analysis, for one resource. Pure — no write.

    `entity_type` is passed through to `_disagreement_gaps` so a database or
    filesystem slug is looked up via its own adapter rather than the
    repo-only `projects` table. `_not_measurable_gaps` is unaffected —
    `analyses_with_checks()`/`query_findings` are already resource-type
    agnostic (keyed by analysis/check name, not by table).

    Each dict: {analysis_id, gap_kind, check_name, sentence, evidence}."""
    return (_not_measurable_gaps(registry, slug)
            + _disagreement_gaps(registry, slug, entity_type))


def record_gaps_for(registry, slug: str, entity_type: str = "repo") -> list[dict]:
    """Collect and upsert — the write-side counterpart, called from the one
    choke point (FactLayer.facts()) so the collection is current whenever the
    page is. Returns what was collected (before persistence, which cannot
    fail on a duplicate — upsert_gap is idempotent by construction)."""
    gaps = collect_gaps(registry, slug, entity_type)
    for g in gaps:
        registry.upsert_gap(
            slug, g["analysis_id"], g["gap_kind"], g["check_name"],
            g["sentence"], evidence=g.get("evidence"),
        )
    return gaps


def record_disagreement(
    registry, slug: str, question: str, analysis_id: str, comment: str = "",
    who: str = "", extra_evidence: dict | None = None, question_id: str = "",
) -> dict:
    """A person read one answer and said it is wrong. Recorded as a gap about
    the ANALYSIS, `ours` — the same destination a disagreement between two of
    our own measures gets, for the same reason: the repository is not at
    fault for our answer about it (destinations.py's rule 1, which computes
    `ours` and never reads it from the registry).

    Returns the gap dict that was stored. `analysis_id` may be "" when the
    catalog names no analysis for this question — the disagreement is still
    real and is still recorded, attributed to no analysis rather than to a
    guessed one. A caller wanting to know which happened reads the returned
    `analysis_id`, not a separate flag.

    The write is the same `upsert_gap` the measured gaps use, so a second
    person disagreeing with the same answer refreshes one row (bumping
    `last_seen_at`) instead of stacking duplicates — the count stays "how
    many answers are disputed", which is the number a curator acts on, not
    "how many times someone clicked".
    """
    comment = (comment or "").strip()
    sentence = (
        f"A person disagreed with the answer to \u201c{(question or '').strip()}\u201d"
        + (f": {comment}" if comment else ".")
    )
    evidence = {
        "question": question,
        "comment": comment,
        "who": who,
        "source": SOURCE_PERSON,
        "analysis_attributed": bool(analysis_id),
    }
    if extra_evidence:
        evidence.update(extra_evidence)
    question_id = (question_id or evidence.get("question_id") or "").strip()
    if question_id:
        evidence["question_id"] = question_id
    check_name = question_check_name(question, question_id)
    if question_id:
        # A row an earlier disagreement wrote under the bare text is refreshed in place rather than
        # stacking a second one next to it under the id; it already counts as this question's gap.
        legacy = question_check_name(question)
        for row in registry.list_gaps(slug):
            if (row.get("check_name") == legacy and row.get("gap_kind") == DISAGREEMENT
                    and (row.get("analysis_id") or "") == (analysis_id or "")):
                check_name = legacy
                break
    gap = {
        "analysis_id": analysis_id or "",
        "gap_kind": DISAGREEMENT,
        "destination": OURS,
        "check_name": check_name,
        "sentence": sentence,
        "evidence": evidence,
    }
    registry.upsert_gap(
        slug, gap["analysis_id"], gap["gap_kind"], gap["check_name"],
        gap["sentence"], evidence=evidence,
    )
    return gap


def gaps_summary(registry, slug: str) -> dict:
    """The shape GET /api/projects/{slug}/gaps returns: every stored gap for
    this project, plus counts and `measures_total` per analysis so "3 of 11"
    is a real fraction rather than a bare numerator (§3's one-line collapse).

    Reads STORED gaps (registry.list_gaps), not a fresh collect_gaps() —
    the route is a read of what the choke point already recorded, matching
    every other "read what was persisted" route in this codebase; a route
    that re-collected on every call would duplicate the analysis work
    FactLayer.facts() already did for the same page load."""
    from resource_explorer.destinations import checks_per_analysis

    rows = registry.list_gaps(slug)
    for r in rows:
        # Every row in this table is, by construction, a finding about the
        # analysis — that is what the table is. Stated on the row rather than
        # left for a consumer to infer from the table it came out of, so the
        # UI reads the same word (`ours`) here and on a finding row.
        r["destination"] = OURS
        r["source"] = (r.get("evidence") or {}).get("source") or SOURCE_MEASURED
    open_rows = [r for r in rows if not r.get("resolved_at")]
    counts = {
        NOT_MEASURABLE: sum(1 for r in open_rows if r["gap_kind"] == NOT_MEASURABLE),
        DISAGREEMENT: sum(1 for r in open_rows if r["gap_kind"] == DISAGREEMENT),
        "total": len(open_rows),
        # A SUBSET of DISAGREEMENT above, not a fourth bucket — the two
        # numbers overlap on purpose and a consumer must not add them. It is
        # surfaced because the two kinds go to different people: a disputed
        # answer is work for whoever wrote the analysis, a measured
        # disagreement is work for whoever reconciles two measures.
        "disputed_by_a_person": sum(
            1 for r in open_rows if r.get("source") == SOURCE_PERSON
        ),
    }
    by_analysis: dict[str, int] = {}
    for r in open_rows:
        by_analysis[r["analysis_id"]] = by_analysis.get(r["analysis_id"], 0) + 1
    return {
        "slug": slug,
        "gaps": rows,
        "counts": counts,
        "by_analysis": by_analysis,
        "measures_total": checks_per_analysis(),
    }
