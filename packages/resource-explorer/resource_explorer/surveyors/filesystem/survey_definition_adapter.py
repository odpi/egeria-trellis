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
EGERIA-ASYNC-RESULT-RETRIEVAL-IMPLEMENTED.md.

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
    PUBLISH_NOT_CHOSEN,
    PUBLISHED,
    ResourceTypeAdapter,
    register_adapter,
)

log = logging.getLogger(__name__)


class FolderSurveyNotRunHere(RuntimeError):
    """The folder-survey step was asked where Resource Explorer's walk does not lead; the words say why."""


def _run_filesystem_inventory(fs_entity, registry, fork=None, **_) -> dict:
    """The file system's folder survey, led by whoever can read the path (Files step 2).

    The stored reachability state decides who leads (the probe launches nothing in Egeria):

    * Egeria is expected to read (or has been shown to read) the canonical mount: Egeria's Folder Survey leads, run
      from the survey card. This step measures NOTHING Egeria lists as its own, and REFUSES (`FolderSurveyNotRunHere`,
      the executor records it as the step's error), exactly as the folder-survey route does; it never succeeds empty.
    * Resource Explorer leads (only it can read the path, Egeria's mounts are unknown, or Egeria failed to read it): the walk runs here, names sizes and times
      only (no file is opened), and PERSISTS the result. Whether it may be PUBLISHED as "Egeria could not reach the
      path" is decided at publish, from the stored probe row (`_publish`).

    Persisting here rather than only as a side effect of a successful Egeria publish. Previously this returned the
    inventory and stored nothing: the only `add_filesystem_survey` call on this path lived inside
    `_publish_step_annotations`, which raises when the filesystem has no Egeria asset yet, and
    `SurveyDefinitionExecutor` catches that into `errors`. A freshly-walked inventory was then discarded, with
    `last_surveyed_at` never touched. The local route (`web/routes/filesystems.py`) has always persisted unconditionally
    before any Egeria involvement; a survey is evidence about the resource whether or not Egeria ever hears about it.

    Best-effort on the write: a persistence failure must not discard the inventory from the step output too, so it is
    reported and the data still flows to the caller.
    """
    from resource_explorer import file_reach
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    if fork is None:
        # The stored state (the mounted-prefix check runs only if there is no row): this step launches nothing in Egeria.
        try:
            fork = file_reach.ensure_checked(fs_entity.slug, registry)
        except Exception:  # noqa: BLE001
            log.warning("%s: the reachability state could not be read", fs_entity.slug, exc_info=True)
            fork = file_reach.ForkState()
    if fork.surveyor != file_reach.SURVEYOR_RE:
        # Same refusal as the folder-survey route, never an empty success: this step is Resource Explorer's walk, and
        # it says why it did not run.
        if fork.state in (file_reach.EGERIA, file_reach.EXPECTED):
            raise FolderSurveyNotRunHere(
                f"{fork.sentence} · Egeria's Folder Survey leads; run it from the file system's folder survey card. "
                "Resource Explorer measures none of what that survey lists here")
        raise FolderSurveyNotRunHere(file_reach.refusal_words(fork, for_egeria_lead=False))

    from resource_explorer.surveyors.filesystem import walk_extensions as we

    from resource_explorer import reach_mechanisms

    reader = None
    if reach_mechanisms.is_remote(fs_entity):
        # Outcome 3: the same walk through the file system's reach mechanism (S3: list by prefix, no byte read but the
        # narrative files' capped reads). A refusal of the root is the step's error, in the source's redacted words.
        from resource_explorer.surveyors.filesystem.remote_filesystem_surveyor import RemoteFileSystemSurveyor

        try:
            remote = RemoteFileSystemSurveyor(fs_entity, registry, reach_mechanisms.mechanism_for(fs_entity))
            survey_data = remote.run(walk_extensions=True)
        except reach_mechanisms.ReachError as exc:
            raise FolderSurveyNotRunHere(f"{fork.sentence} · the walk could not list the root · {exc}") from None
        reader = remote.reader
    else:
        surveyor = LocalFileSystemSurveyor(fs_entity, registry)
        survey_data = surveyor.run(profile_contents=False, walk_extensions=True)
    # Files build step 1: the per-file rows are kept for THIS run, the narrative files are read, and the walk_extensions
    # block (counts only for the publish) is added to the result. Before the blob is stored: rows never ride in it.
    we.attach_extensions(fs_entity, registry, survey_data, led_by=we.LED_BY_RE, reader=reader)
    # Who led and what the probe proved, kept WITH the result, so the report's sentence comes from stored proof.
    survey_data["surveyed_by"] = {"surveyor": file_reach.SURVEYOR_RE, "reach": fork.as_dict()}

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

    return {"survey_data": survey_data, "led_by": fork.state, "persisted": persisted,
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
            "cannot trigger Egeria's native survey for an unpublished filesystem."
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


#: The words a run's outcome carries about Egeria (Brief I round 5): a run that did not publish
#: must never read like one whose publish failed. `PUBLISH_NOT_CHOSEN` and `PUBLISHED` now live in
#: survey_definition_executor (imported above, still importable from here), so the database run's
#: publish word is the same vocabulary.


def _run_egeria_adaptive(
    fs_entity, registry, step, force_egeria_publish: "bool | None" = None,
    egeria_url: str | None = None, egeria_server: str | None = None,
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

    # Who chose to publish (Brief I round 5). A person's route, Classic or the CLI passes True or
    # False explicitly. A Survey Definition run passes nothing: its step targeting Egeria
    # (`egeria-adaptive`) IS the choice, so a scheduled or queued definition publishes as before
    # whenever the file system names which Egeria (URL and view server).
    if force_egeria_publish is None:
        force_egeria_publish = bool((egeria_url or fs_entity.egeria_url) and (egeria_server or fs_entity.egeria_server))
    try:
        survey_data = run_hybrid_filesystem_survey(
            fs_entity.slug, registry=registry, force_egeria_publish=force_egeria_publish,
            egeria_url=egeria_url, egeria_server=egeria_server,
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
    publish_error = survey_data.pop("egeria_publish_error", "")
    # A pressed run whose presser's session is gone: nothing was written, and the step says the run-level words
    # (`presser_hold.SIGN_IN_EXPIRED_WORDS`), never "publish failed".
    publish_refused = survey_data.pop("egeria_publish_refused", "")
    source = "egeria-custom" if egeria_publish is not None else "custom"

    outcome = {"source": source, "status": "ok", **survey_data,
               "publish_state": (PUBLISHED if egeria_publish is not None
                                 else publish_refused if publish_refused
                                 else f"publish failed · {publish_error}" if publish_error
                                 else PUBLISH_NOT_CHOSEN)}
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
    """Publish Resource Explorer's walk under Egeria's own folder-survey annotation types, in RE's own SurveyReport.

    Only when the stored probe row proves Egeria could not reach the path: the report says "surveyed by Resource
    Explorer · Egeria could not reach the path", and that must be true. A walk with no such proof is kept locally and
    NOT published (the reason is the probe's own words). An Egeria-led run has nothing of RE's to publish."""
    from resource_explorer import file_reach
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
    from resource_explorer.surveyors.survey_definition_executor import NothingToPublish

    survey_data: dict = {}
    for output in step_outputs:
        survey_data = output.get("survey_data") or survey_data
    if not survey_data:
        raise NothingToPublish("Egeria's Folder Survey leads · there is no Resource Explorer walk to publish")

    fork = file_reach.fork_for(registry, entity)
    refusal = file_reach.refusal_words(fork, for_egeria_lead=False)
    if refusal:
        raise NothingToPublish(f"not published · {refusal}")

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
    # The installed library steps' kinds (`survey_step_host.attach` fills it), so the measurements route answers for
    # them instead of "Unknown analysis" (F3, 2026-10-29). `state_sources` and `analysis_source_steps` stay undeclared.
    analysis_kinds=lambda: FILESYSTEM_ANALYSIS_KINDS,
)

register_adapter(_ADAPTER)


#: analysis_id -> the re_analysis_step key(s) that produce it — the
#: filesystem equivalent of database/survey_definition_adapter's
#: DATABASE_ANALYSIS_RE_STEP_MAP (see that constant's docstring for the full
#: reasoning, and slice 17's fix — SLICE-17-RUNNABILITY-FROM-CATALOG-IMPLEMENTED.md — for the bug a
#: hand-maintained, un-derived
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
    # Where Egeria's Folder Survey leads, it answers and RE reads back (Files brief): RE's own walk rows are never made
    # on that branch, so reading only them said "not measured" over a file system Egeria had walked (F39/F41).
    egeria = _egeria_led_inventory(registry, slug)
    if egeria:
        return egeria
    entries = registry.query_detail_rows("filesystem_entries", slug)
    if not entries:
        return {}
    data_files = registry.query_detail_rows("filesystem_data_files", slug)
    files = [e for e in entries if (e.get("entry_type") or "file") == "file"]
    dirs = [e for e in entries if e.get("entry_type") == "directory"]
    # An unread size (None) is not a size of 0: leaving it out of the sum
    # silently under-counts, so say how many files it left out. With no
    # known size at all the total is absent, not 0 -- same `unmeasured_*_count`
    # wording as database's table counts.
    known_sizes = [e["size_bytes"] for e in files if e.get("size_bytes") is not None]
    total_size = sum(known_sizes) if known_sizes or not files else None
    return {
        "entry_count": len(entries),
        "file_count": len(files),
        "directory_count": len(dirs),
        "data_file_count": len(data_files),
        "total_size_bytes": total_size,
        "unmeasured_file_count": len(files) - len(known_sizes),
        "hidden_count": sum(1 for e in entries if e.get("is_hidden")),
        "symlink_count": sum(1 for e in entries if e.get("is_symlink")),
    }


def _egeria_leads(registry, slug: str) -> bool:
    """Does the fork, as the stored probe rows prove it, name Egeria's Folder Survey as this file system's surveyor?"""
    from resource_explorer import file_reach

    get = getattr(registry, "get_filesystem", None)   # a registry that holds no file systems has no fork to read
    fs = get(slug) if get else None
    return bool(fs) and file_reach.fork_for(registry, fs).surveyor == file_reach.SURVEYOR_EGERIA


def _egeria_led_inventory(registry, slug: str) -> dict:
    """The inventory as Egeria's Folder Survey measured it, read back, when the fork says Egeria leads and a completed
    folder survey is stored; {} otherwise. The surveyor is named on the result (`surveyed_by`, `report_sentence`) and
    `detail.source` is "egeria", so every surface says who measured it. A count Egeria's report does not carry stays
    None ("not measured"), never 0: Egeria's survey does not single out data files, so `data_file_count` is None."""
    from resource_explorer import file_reach
    from resource_explorer.folder_survey_view import _egeria_candidate, extensions_view
    from resource_explorer.surveyors.filesystem.folder_survey_annotations import read_folder_survey

    if not _egeria_leads(registry, slug):
        return {}
    run = _egeria_candidate(registry, slug)
    if not run or run.get("none"):
        return {}
    read = read_folder_survey(run["annotations"])
    if not read["measured"]:
        return {}
    c = read["counts"]
    files, dirs = c["files"], c["subdirectories"]
    out = {
        "entry_count": files + dirs if files is not None and dirs is not None else None,
        "file_count": files,
        "directory_count": dirs,
        "data_file_count": None,
        "total_size_bytes": c["total_size_bytes"],
        "hidden_count": c["hidden"],
        "symlink_count": c["symlinks"],
        "unclassified_count": c["unclassified"],
        "inaccessible_count": c["inaccessible"],
        "file_types": read["file_types"],
        "extensions": read["extensions"],
        "surveyed_by": file_reach.SURVEYOR_EGERIA,
        "report_sentence": file_reach.EGERIA_REPORT_SENTENCE,
        "surveyed_at": run["at"],
        "_run": {"surveyed_at": run["at"]},
        "_not_measured": {"data_file_count": "Egeria's Folder Survey counts files by type, not data files apart"},
        "detail": {"source": "egeria", "report_guid": run["report_guid"], "surveyor": file_reach.SURVEYOR_EGERIA},
    }
    # The shape (how deep, where the bulk sits) is RE's walk beside Egeria's survey, named as such where it ran.
    ext = extensions_view(registry, slug) or {}
    folders = ext.get("folders") or {}
    if folders:
        out["walk_beside"] = {"surveyed_by": file_reach.SURVEYOR_RE, "surveyed_at": ext.get("surveyed_at", ""),
                              "deepest_folder_depth": folders.get("deepest_folder_depth"),
                              "largest_folders": folders.get("largest_folders") or []}
    return out


def _fmt_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{int(v)} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


def _filesystem_inventory_headline(registry, slug: str) -> dict | None:
    """One sentence that names who surveyed: "134 files in 17 folders · 57.3 MB · surveyed by Egeria's Folder Survey".
    Only for the Egeria-led read-back; RE's own walk keeps its board as before. A count not measured is not said."""
    value = _egeria_led_inventory(registry, slug)
    if not value:
        return None
    bits = []
    if value["file_count"] is not None:
        n = value["file_count"]
        folders = value["directory_count"]
        bits.append(f"{n:,} file{'' if n == 1 else 's'}"
                    + (f" in {folders:,} folder{'' if folders == 1 else 's'}" if folders is not None else ""))
    if value["total_size_bytes"] is not None:
        bits.append(_fmt_size(value["total_size_bytes"]))
    bits.append(value["report_sentence"])
    return {"label": " · ".join(bits), "status": "measured"}


FILESYSTEM_ANALYSIS_RESULTS_MAP: dict[str, tuple] = {
    "filesystem_inventory": (_filesystem_inventory_results, None),
}

#: See DATABASE_ANALYSIS_HEADLINE_MAP's docstring — same "not built yet,
#: doesn't block the map" gap, kept explicit rather than silently absent.
FILESYSTEM_ANALYSIS_HEADLINE_MAP: dict = {"filesystem_inventory": _filesystem_inventory_headline}


# ── user steps (extensibility brief 2c; Files build step 6) ─────────────────
#
# Installable steps found through the `pyegeria.analytics` entry point (fs_change_rates, fs_duplicates, fs_activity,
# fs_permissions, and Files build step 7's fs_layout_signals: zero-fetch derivations over the walk's kept per-file
# rows), attached BESIDE filesystem_inventory by `survey_step_host`, from the catalog entries the generator writes from their manifests, exactly as for databases.
# A file system has no built-in step registry of its own yet; these two start empty and hold only what attach adds.
from resource_explorer.surveyors.repo_survey_definition_adapter import AnalysisKind, StepInfo  # noqa: E402

from resource_explorer.surveyors.repo_survey_definition_adapter import AnalysisKindResults  # noqa: E402

FILESYSTEM_STEP_REGISTRY: dict[str, StepInfo] = {}
#: filesystem_inventory is a live read only where Egeria's Folder Survey leads: the fact layer asks the reader before its
#: run gate there, because an Egeria-led survey records no RE run of this analysis (F41). Where RE walks, its run gate stays.
FILESYSTEM_ANALYSIS_KINDS: dict[str, AnalysisKind] = {
    "filesystem_inventory": AnalysisKind("filesystem_inventory", ["filesystem_inventory"], results=AnalysisKindResults(
        _filesystem_inventory_results, None, render="metrics", headline_reader=_filesystem_inventory_headline,
        live_read=_egeria_leads)),
}

from resource_explorer.surveyors import survey_step_host as _survey_step_host  # noqa: E402

_survey_step_host.attach(
    "filesystem", _ADAPTER, FILESYSTEM_STEP_REGISTRY, FILESYSTEM_ANALYSIS_STEP_MAP,
    FILESYSTEM_ANALYSIS_RESULTS_MAP, FILESYSTEM_ANALYSIS_KINDS, FILESYSTEM_ANALYSIS_HEADLINE_MAP)
