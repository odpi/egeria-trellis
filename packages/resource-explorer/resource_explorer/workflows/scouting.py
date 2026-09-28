"""The scouting-scan workflow, plus the two "did we actually measure this"
helpers that went with it.

Moved out of `web/routes/projects.py` (`_run_scouting_scan_sync` at :366,
`_question_has_data` at :519, `_results_have_data` at :1110) in step 2b. The
two helpers move with the scan because they answer the same question from the
other side — the scan collects, they report whether anything was collected —
and because both are pure functions over the registry with no HTTP in them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class ScoutingScanResult:
    status: str  # "ok" | "error"
    slug: str
    message: str = ""
    error: str = ""


def run_scouting_scan(slug: str, registry=None) -> ScoutingScanResult:
    """Fast, API-only coarse scan (repo stats + language classification).

    Runs the "Repo Coarse Scout" Survey Definition through the same executor
    Discovery's manual "Run" button uses, and falls back to running its two
    steps directly when Egeria has lost the definition.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_COARSE_SCOUT_SURVEY_DEFINITION_QN,
    )
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        run_survey_definition,
    )
    from resource_explorer.surveyors.survey_definition_reader import (
        SurveyDefinitionReaderError,
    )

    registry = registry or ProjectRegistry()
    try:
        # fast=True — Coarse Scout's whole premise is being the cheap, fast
        # tier; without this, HealthSurveyor's stats refresh makes one GitHub
        # API call per commit in the last 90 days (a confirmed real slowness
        # bug for active repos, easily several minutes). See
        # StepInfo.accepts_fast / HealthSurveyor.__init__'s own docstrings.
        result = run_survey_definition(
            "repo", slug, registry=registry,
            survey_definition_ref=REPO_COARSE_SCOUT_SURVEY_DEFINITION_QN,
            fast=True,
        )
    except (SurveyDefinitionExecutorError, SurveyDefinitionReaderError) as exc:
        # Egeria-side Survey Definitions don't survive an Egeria database reset
        # — a real, recurring event in development, not a one-off mistake.
        # Without a fallback, that silently breaks Scouting Scan entirely
        # (including the git-stats refresh HealthSurveyor does at scan time —
        # the reason stale stats and a failed scan are usually the same
        # underlying problem, not two bugs) until someone notices and manually
        # re-authors the definition in Egeria. Scouting Scan never actually
        # needed Egeria to know *what* the two steps are, only to name/bundle
        # them — so fall back to running them directly.
        try:
            from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

            survey_result = SurveyOrchestrator(registry).run(
                slug, steps=["repo_health", "repo_language"], fast=True,
            )
        except Exception as fallback_exc:
            return ScoutingScanResult(
                status="error", slug=slug,
                error=f"{exc} (local fallback also failed: {fallback_exc})",
            )
        if survey_result.errors:
            return ScoutingScanResult(
                status="error", slug=slug, error="; ".join(survey_result.errors),
            )
        return ScoutingScanResult(
            status="ok", slug=slug,
            message=(
                "Coarse scan complete — ran locally. Egeria's 'Repo Coarse Scout' "
                "Survey Definition wasn't found (likely an Egeria database reset); "
                "re-author it if you want scans to route through Egeria again."
            ),
        )

    errors = result.get("errors") or []
    if errors:
        return ScoutingScanResult(status="error", slug=slug, error="; ".join(errors))
    return ScoutingScanResult(status="ok", slug=slug, message="Coarse scan complete.")


def execute_and_record_scouting_scan(slug: str, activity_id: str,
                                     *, registry=None) -> ScoutingScanResult:
    """Run the scan and write its terminal status onto `activity_id`.

    A fresh ProjectRegistry by default, as the route's background thread used:
    this runs off the request thread either way, and connections are not shared
    across threads.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = registry or ProjectRegistry()
    try:
        result = run_scouting_scan(slug, registry)
    except Exception as exc:  # pragma: no cover — genuinely unexpected
        log.exception("Scouting Scan crashed for %s", slug)
        registry.update_activity_status(
            activity_id, "error", summary=f"Scouting Scan crashed: {exc}",
        )
        return ScoutingScanResult(status="error", slug=slug, error=str(exc))
    registry.update_activity_status(
        activity_id, result.status, summary=result.message or result.error or "",
    )
    return result


# ── "has anything actually been measured here" ───────────────────────────────


def question_has_data(registry, slug: str, analysis_ids: list[str], entity_type: str = "repo") -> bool | None:
    """Best-effort "has RE actually run this for this resource" check.

    True as soon as ANY of the question's mapped analysis_ids has data, False if
    none do, **None if the question has no analysis_ids at all** — nothing to
    check is not the same answer as checked-and-found-nothing, and the caller
    renders the two differently.

    `repository_health` has no results_reader in REPO_ANALYSIS_RESULTS_MAP
    (repo_survey_definition_adapter.py) — it's checked via project_stats
    directly instead, the same signal scouting-overview itself reads. Every
    reader call is wrapped: a results_reader raising must never break the whole
    checklist.

    `entity_type` (added for the database/filesystem generalization, docs/
    Backlog.md's "scouting-questions was repo-only" entry) picks which
    *_ANALYSIS_RESULTS_MAP to check against — see `workflows.analysis.
    _results_map_for` for the same three-way switch used by the survey-
    results dashboards. The `repository_health` special case stays repo-only:
    it is a repo-specific project_stats signal with no database/filesystem
    analogue, not a generic fallback other entity_types should also hit.
    """
    if not analysis_ids:
        return None
    from resource_explorer.workflows.analysis import _results_map_for

    results_map, _headline_map = _results_map_for(entity_type)

    for analysis_id in analysis_ids:
        if entity_type == "repo" and analysis_id == "repository_health":
            if registry.get_latest_project_stats(slug):
                return True
            continue
        entry = results_map.get(analysis_id)
        if not entry:
            continue
        results_reader, _ = entry
        try:
            data = results_reader(registry, slug)
        except Exception:
            continue
        if data and (not isinstance(data, dict) or any(v for v in data.values())):
            return True
    return False


def build_question_checklist(
    registry, entity_type: str, slug: str,
    phase: str = "scouting", perspectives: list[str] | None = None, purposes: list[str] | None = None,
) -> dict:
    """The Question checklist for any entity_type, generalized out of
    `projects.py`'s `GET /{slug}/scouting-questions` (repo-only route, added
    first — see docs/Backlog.md's "scouting-questions was repo-only" entry).

    `question_catalog_reader.get_questions()` was already resource-type-
    generic before this change; only the route calling it, and the has_data
    scoring behind it, were repo-only. This function is that route's body,
    parametrized over entity_type so `projects.py`, `databases.py` and
    `filesystems.py` can each expose it as a thin wrapper — same shape as
    `build_analysis_last_activity`/`build_survey_results` above it.
    """
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    entries = get_questions(entity_type, phase=phase, perspectives=perspectives or None, purposes=purposes or None)

    questions = []
    for e in entries:
        answering = e["answering"]
        has_data = (
            question_has_data(registry, slug, answering["analysis_ids"], entity_type=entity_type)
            if answering["kind"] in ("analysis", "partial", "mixed")
            else None
        )
        questions.append({
            "question": e["question"],
            "stage": e["stage"],
            "perspectives": e["perspectives"],
            "kind": answering["kind"],
            "analysis_ids": answering["analysis_ids"],
            "checks": list(answering.get("checks") or []),
            "note": answering["note"],
            "answering_mechanism": e.get("answering_mechanism", ""),
            "rationale": e.get("rationale", ""),
            "catalog_history": e.get("catalog_history", ""),
            "has_data": has_data,
            "purposes": e.get("purposes", []),
            "derivation": e.get("derivation", {}),
            # Slice 21a point 4: dropped here before this fix — `entries`
            # (question_catalog_reader.get_questions()) already carries
            # `levels` on every entry, but this function builds its own,
            # separate per-question dict and never copied it across, so
            # app.js's `primaryQuestionLevel(entry)` always saw `undefined`
            # and silently fell back to "resource" for every question,
            # including container-only ones. Found live gating Slice 21a,
            # 2026-09-26 — the container-level evidence table never
            # actually reached the container reader through this route.
            "levels": e.get("levels") or ["resource"],
        })

    return {
        "phase": phase, "perspectives": perspectives or [], "purposes": purposes or [], "questions": questions,
    }


def results_have_data(results) -> bool:
    """Does a results payload actually contain anything?

    Truthiness is not enough and that is the whole point of this function: the
    readers return a shaped-but-empty dict when a step has never run for a repo
    — `{"findings": [], "gap_count": 0}` — which is truthy, so `any(results)`
    reported five-of-five analyses present for a repo that had never been
    surveyed for any of them.

    A payload counts as having data when it holds a non-empty collection or a
    non-zero number. An all-empty, all-zero payload is what "never ran" looks
    like. A genuinely all-zero result is therefore hidden too, which is the
    accepted trade: it reads as "nothing to show" either way, and
    include_empty=true returns everything for anyone who needs the distinction.
    """
    if not results:
        return False
    if isinstance(results, dict):
        # Caught 2026-08-31 wiring architecture_recovery/architecture_summary/
        # architecture_doc_lens into a dashboard for the first time (docs/
        # Backlog.md "Survey Results dashboards cover 14 of 29 analyses") —
        # the exact "shaped but empty" bug this function exists for, just
        # never triggered before because none of the three had ever reached
        # this code path. A never-run result here isn't an empty collection,
        # it's `{"state": "never_run", "message": "..."}` — result_status.py's
        # vocabulary, and the explanatory `message` is non-empty text, so the
        # general envelope exclusion below didn't catch it.
        #
        # `state` alone can't be blanket-excluded: result_status.py's ladder
        # gives `nothing_found` the SAME shape and it is "a real, final
        # answer" (a genuine negative result), not an absence — excluding it
        # here would hide real content the same way this function exists to
        # stop pretending emptiness is content. Only the never-run state
        # itself means nothing was produced.
        from resource_explorer.surveyors import result_status
        if results.get("state") == result_status.NEVER_RUN:
            return False

        # `_status` is an ENVELOPE, not content — it describes why a payload is
        # empty, so counting it as data makes every explained emptiness claim to
        # hold something. That is the opposite of what this function exists for:
        # a card saying "skipped, here is why" would be reported as having
        # results and then render as an explanation of nothing.
        #
        # `surveyed_at` and `detail` are excluded for the same reason — a
        # timestamp is evidence a step ran, not evidence it found anything, and
        # a reader that returns only a timestamp has nothing to show.
        # (_renderMetricsResults in index.html already filters exactly these
        # three for the same reason; this is the server-side half of it.)
        #
        # `documentation` is architecture_recovery's own decorative field —
        # the documentation-SITE ingestion status, carried on this card for
        # unrelated presentation reasons (_doc_ingestion_state) — never the
        # recovery analysis's own findings. Confirmed no other reader uses
        # "documentation" as a real top-level content key.
        # `slug` is the resource's own identifier, echoed back by
        # `_architecture_recovery_results` for the renderer's convenience. It is
        # ALWAYS non-empty, so leaving it out of this set makes every payload
        # containing it read as data — which is this function's failure mode
        # exactly, not an edge of it. Caught 2026-08-31 in integration: the
        # architecture_overview dashboard passed on its own branch and failed
        # once merged, because `test_shaped_but_empty_results_do_not_count_as_data`
        # only exists here.
        #
        # Same category as `surveyed_at` above: a name is evidence the resource
        # exists, never evidence an analysis found anything about it.
        envelope = {"_status", "surveyed_at", "detail", "documentation", "slug"}
        return any(results_have_data(v) for k, v in results.items() if k not in envelope)
    if isinstance(results, (list, tuple, set)):
        return len(results) > 0
    if isinstance(results, bool):
        return results
    if isinstance(results, (int, float)):
        return results != 0
    if isinstance(results, str):
        return bool(results.strip())
    return True
