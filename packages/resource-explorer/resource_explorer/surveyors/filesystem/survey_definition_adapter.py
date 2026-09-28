"""
Survey Definition adapter for filesystems.

Registers a ResourceTypeAdapter (entity_type="filesystem") with
resource_explorer.surveyors.survey_definition_executor, wiring the
"filesystem_inventory" re_analysis_step to the existing LocalFileSystemSurveyor,
and publishing results via EgeriaFileSystemSurveyor.publish_step_annotations (the
narrow publish path — does not auto-catalog the filesystem root or its files).

A step tagged executes_at="egeria" is handled separately, via
other_engine_handlers (added 2026-08-24, closing survey_definition_executor.py's
Egeria-trigger stub for this resource type — see EgeriaFileSystemSurveyor.
trigger_survey_by_guid's own docstring for the live-confirmed process/target
names and the one real caveat: not yet exercised end-to-end, since this
environment has no cataloged filesystem to test against). As of the async
result-retrieval build, this now also waits for the triggered engine action
to reach a terminal status and reads back its real result — see
egeria_async_survey_result.py, shared with the database adapter's identical
function above it, and
docs/design-notes/EGERIA-ASYNC-RESULT-RETRIEVAL-IMPLEMENTED.md.

A step tagged executes_at="egeria-adaptive" is a third, separate
other_engine_handlers entry: the folded-in run_hybrid_filesystem_survey
strategy (always local-scan-first, then best-effort publish) — see
`_run_egeria_adaptive` below and docs/design-notes/
EXECUTION-MODES-HYBRID-CLARIFICATION.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter,
    register_adapter,
)

log = logging.getLogger(__name__)


def _run_filesystem_inventory(fs_entity, registry, **_) -> dict:
    """Walk the filesystem and PERSIST the result, then return it.

    Persisting here rather than only as a side effect of a successful Egeria
    publish. Previously this returned the inventory and stored nothing: the only
    `add_filesystem_survey` call on this path lived inside
    `_publish_step_annotations`, which raises when the filesystem has no Egeria
    asset yet — and `SurveyDefinitionExecutor` catches that into `errors`. A
    freshly-walked inventory was then discarded, with `last_surveyed_at` never
    touched, so a run that did real work left no trace and the next run had
    nothing to compare against.

    The local route (`web/routes/filesystems.py`) has always persisted
    unconditionally before any Egeria involvement, and the DATABASE adapter does
    the same inside `DatabaseSurveyor.survey()`. Filesystem was the one path
    where the write was conditional on cataloguing succeeding — a survey is
    evidence about the resource whether or not Egeria ever hears about it.

    Best-effort on the write: a persistence failure must not discard the
    inventory from the step output too, so it is reported and the data still
    flows to the caller.
    """
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    surveyor = LocalFileSystemSurveyor(fs_entity, registry)
    survey_data = surveyor.run()

    persisted, persist_error = False, ""
    try:
        registry.add_filesystem_survey(
            fs_slug=fs_entity.slug,
            surveyed_at=survey_data.get("surveyed_at", ""),
            survey_data=survey_data,
            egeria_report_guid="",
            source="survey-definition",
        )
        persisted = True
    except Exception as exc:  # noqa: BLE001
        persist_error = f"{type(exc).__name__}: {exc}"
        log.exception("%s: could not persist filesystem inventory", fs_entity.slug)

    return {"survey_data": survey_data, "persisted": persisted,
            **({"persist_error": persist_error} if persist_error else {})}


def _get_filesystem_entity(registry, slug: str):
    return registry.get_filesystem(slug)


def _trigger_egeria_native_survey(fs_entity, registry, step, **_) -> dict:
    """Trigger Egeria's own native FileDirectory survey for a step tagged
    executes_at="egeria", then wait for it to reach a terminal status and read
    back its real result. Requires the filesystem to already be cataloged in
    Egeria (has a stored asset guid) — this does not catalog it as a side
    effect (mirrors database/survey_definition_adapter.py's identical
    function, sharing the same poll/resolve/convert helper).

    Raises on timeout (EgeriaEngineActionTimeoutError) or on an unresolvable
    report attribution (SurveyReportAttributionError) — both propagate to the
    executor's own per-step except clause, which reports them as a specific
    error rather than a silent "triggered" success.
    """
    from resource_explorer.surveyors.egeria_async_survey_result import (
        poll_trigger_and_retrieve_annotations,
    )
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    fs_guid = fs_entity.egeria_asset_guid
    if not fs_guid:
        raise RuntimeError(
            f"Filesystem '{fs_entity.slug}' has no stored Egeria asset guid — "
            "cannot trigger Egeria's native survey for an uncataloged filesystem."
        )
    surveyor = EgeriaFileSystemSurveyor()
    triggered_at = datetime.now(timezone.utc)
    engine_action_guid = surveyor.trigger_survey_by_guid(fs_guid)
    log.info(
        "Triggered Egeria native survey for filesystem %r: engine_action_guid=%s",
        fs_entity.slug, engine_action_guid,
    )
    result = poll_trigger_and_retrieve_annotations(
        surveyor=surveyor,
        engine_action_guid=engine_action_guid,
        resource_guid=fs_guid,
        triggered_at=triggered_at,
        analysis_step=step.re_analysis_step,
    )
    return {"status": "ok", **result}


def _run_egeria_adaptive(
    fs_entity, registry, step, force_egeria_publish: bool = False,
    egeria_url: str | None = None, egeria_server: str | None = None,
    egeria_user: str | None = None, egeria_password: str | None = None,
    **_,
) -> dict:
    """Strategy selector for a step tagged executes_at="egeria-adaptive".

    Folds in `run_hybrid_filesystem_survey`
    (surveyors/filesystem/hybrid_filesystem_surveyor.py) — the default
    web/CLI filesystem-survey path before this build — as a legal
    `executes_at` value, per docs/design-notes/
    EXECUTION-MODES-HYBRID-CLARIFICATION.md.

    The filesystem hybrid path is simpler than the database one: there is no
    cache-or-run (EgeriaFileSystemSurveyor has no `get_latest_survey`
    equivalent yet), so this always runs the local scan first — CLAUDE.md
    rule 15's "local scan immediately, Egeria's result is async" constraint
    is trivially satisfied here, since the local scan IS the synchronous
    result and Egeria's cataloging/publish is the side channel — then
    attempts to publish it into Egeria when credentials are configured (or
    `force_egeria_publish` is set). A publish failure is non-fatal: the
    local survey is still the real result, with the failure recorded on the
    entity's status rather than losing the survey (see
    `run_hybrid_filesystem_survey`'s own docstring).

    `source` follows the same three-way vocabulary as the database handler,
    minus "egeria" (there being no cache-or-run to ever produce a plain
    reused-from-Egeria result here): "egeria-custom" (local scan published to
    Egeria successfully), "custom" (local-only — either no Egeria
    credentials were configured, or a publish was attempted and failed), or
    "error" (the local scan itself raised).

    Delegates to `run_hybrid_filesystem_survey` rather than reimplementing
    its logic — `tests/test_execution_modes_path_c_hybrid.py` characterizes
    that function's behavior directly (call ordering, non-fatal publish
    failure, local-only-when-no-credentials), and this handler's job is to
    make it reachable via `executes_at` routing and label its `source`, not
    to duplicate the logic a second time. Not deleted here; see
    docs/Backlog.md for the fast-follow once nothing but this handler and
    its own tests reference it directly.
    """
    from resource_explorer.surveyors.filesystem.hybrid_filesystem_surveyor import (
        run_hybrid_filesystem_survey,
    )

    has_creds = bool(
        (egeria_url or fs_entity.egeria_url) and (egeria_server or fs_entity.egeria_server)
        and (egeria_user or fs_entity.egeria_user) and (egeria_password or fs_entity.egeria_password)
    )

    try:
        survey_data = run_hybrid_filesystem_survey(
            fs_entity.slug, registry=registry, force_egeria_publish=force_egeria_publish,
            egeria_url=egeria_url, egeria_server=egeria_server,
            egeria_user=egeria_user, egeria_password=egeria_password,
        )
    except Exception as exc:
        log.error("egeria-adaptive: filesystem survey failed for %s: %s", fs_entity.slug, exc)
        return {
            "source": "error",
            "status": "error",
            "filesystem_slug": fs_entity.slug,
            "surveyed_at": datetime.now(timezone.utc).isoformat(),
            "errors": [f"Filesystem survey failed: {exc}"],
        }

    egeria_publish = survey_data.pop("egeria_publish", None)
    source = "egeria-custom" if egeria_publish is not None else "custom"

    outcome = {"source": source, "status": "ok", **survey_data}
    # Kept out of any top-level key `_publish` below recognizes (it looks
    # for "survey_data", which this handler's local-scan fields are not, so
    # they're actually safe at the top level as-is) — nested under "result"
    # anyway, for the same reason the database handler relocates
    # "schema_info"/"statistics": the Egeria publish outcome, when this
    # handler already published it itself, must never look like fresh input
    # for a second, generic publish pass to pick up.
    if egeria_publish is not None:
        outcome["result"] = {"egeria_publish": egeria_publish}
    return outcome


def _publish(entity, step_outputs: list, surveyed_at: str, registry) -> str:
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    survey_data: dict = {}
    for output in step_outputs:
        survey_data = output.get("survey_data") or survey_data

    surveyor = EgeriaFileSystemSurveyor()
    result = surveyor.publish_step_annotations(entity, survey_data, registry)
    return result.get("report_guid", "")


_ADAPTER = ResourceTypeAdapter(
    entity_type="filesystem",
    # Confirmed live against Egeria's real Technology Type catalog (2026-07-13,
    # EgeriaTechTypeCatalog.get_tech_type_detail): the correct display name is
    # "File System Directory" (deployedImplementationType-(File System
    # Directory)); "File Folder" is only the underlying open-metadata type name
    # (FileFolder), not a Technology Type. The previous "File Folder" guess
    # meant a Survey Definition authored with the real name would never match
    # RE's candidate lookup.
    technology_type="File System Directory",
    re_analysis_steps={"filesystem_inventory": _run_filesystem_inventory},
    get_entity=_get_filesystem_entity,
    publish=_publish,
    re_analysis_step_info={
        "filesystem_inventory": {
            "description": (
                "Walks the filesystem (counts, sizes, hidden/symlink/executable/writable "
                "flags, timestamps, Egeria-reference-data classification), flags "
                "inaccessible/unclassified files, and profiles data file schemas."
            ),
            "annotation_types": [
                "ResourceMeasureAnnotation",
                "ClassificationAnnotation",
                "RequestForActionAnnotation",
                "SchemaAnalysisAnnotation",
            ],
        },
    },
    other_engine_handlers={
        "egeria": _trigger_egeria_native_survey,
        "egeria-adaptive": _run_egeria_adaptive,
    },
    egeria_technology_type_name="File System Directory",
    # Declared lazily (the maps are defined later in this module), same
    # reasoning as database/survey_definition_adapter.py's own comment at its
    # `_ADAPTER` (RULING-DB-QUESTION-CATALOG-CONSISTENCY.md §0): without this,
    # FactLayer and context_compile.py's results-reader fallback could not
    # read a filesystem's own results at all, for the same reason a database
    # question was invisible before that fix. `analysis_source_steps`/
    # `analysis_kinds`/`state_sources` stay undeclared for now, same as
    # database's.
    analysis_results_map=lambda: FILESYSTEM_ANALYSIS_RESULTS_MAP,
    analysis_headline_map=lambda: FILESYSTEM_ANALYSIS_HEADLINE_MAP,
)

register_adapter(_ADAPTER)


#: analysis_id -> the re_analysis_step key(s) that produce it — the
#: filesystem equivalent of database/survey_definition_adapter's
#: DATABASE_ANALYSIS_RE_STEP_MAP (see that constant's docstring for the full
#: reasoning, and slice 17's fix — docs/design-notes/SLICE-17-RUNNABILITY-
#: FROM-CATALOG-IMPLEMENTED.md — for the bug a hand-maintained, un-derived
#: copy of a map like this one can develop). Filesystem has exactly one
#: local re_analysis_step and one analysis_catalog.yaml entry today, so this
#: is a 1:1 map rather than a fan-out — kept as its own named constant
#: anyway, matching the per-resource-type convention, so a second filesystem
#: analysis added later has an obvious place to be attributed rather than a
#: special case bolted on. **Known risk, out of slice 17's scope (database-
#: only)**: if filesystem grows a second, zero-fetch-style analysis the way
#: database's `db_derived` did, this map would need the same "derive from
#: the real backing set" treatment rather than a second hand-maintained
#: entry — flagged, not fixed, since nothing here is broken today.
FILESYSTEM_ANALYSIS_STEP_MAP: dict[str, list[str]] = {
    "filesystem_inventory": ["filesystem_inventory"],
}


# ── Results reading — the filesystem equivalent of repo_survey_definition_
# adapter.REPO_ANALYSIS_RESULTS_MAP / database's own DATABASE_ANALYSIS_
# RESULTS_MAP (see that constant's docstring for the full reasoning; the
# same "one analysis, one thin wrapper over already-stored rows" shape
# applies here too). filesystem has exactly one analysis, and its detail
# rows (`filesystem_entries`/`filesystem_data_files`) are already
# materialized by every local survey (`result_materializer.
# filesystem_rows_from_survey_data`), so this is a plain summarization —
# no new domain logic, same as database's schema_inventory/row_count_
# snapshot readers.
def _filesystem_inventory_results(registry, slug: str) -> dict:
    entries = registry.query_detail_rows("filesystem_entries", slug)
    if not entries:
        return {}
    data_files = registry.query_detail_rows("filesystem_data_files", slug)
    files = [e for e in entries if (e.get("entry_type") or "file") == "file"]
    dirs = [e for e in entries if e.get("entry_type") == "directory"]
    total_size = sum(e.get("size_bytes") or 0 for e in files)
    return {
        "entry_count": len(entries),
        "file_count": len(files),
        "directory_count": len(dirs),
        "data_file_count": len(data_files),
        "total_size_bytes": total_size,
        "hidden_count": sum(1 for e in entries if e.get("is_hidden")),
        "symlink_count": sum(1 for e in entries if e.get("is_symlink")),
    }


FILESYSTEM_ANALYSIS_RESULTS_MAP: dict[str, tuple] = {
    "filesystem_inventory": (_filesystem_inventory_results, None),
}

#: See DATABASE_ANALYSIS_HEADLINE_MAP's docstring — same "not built yet,
#: doesn't block the map" gap, kept explicit rather than silently absent.
FILESYSTEM_ANALYSIS_HEADLINE_MAP: dict = {}
