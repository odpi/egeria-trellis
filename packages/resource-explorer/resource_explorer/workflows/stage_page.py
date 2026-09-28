"""Backend facts for the stage page's owner's-round redesign (2026-09-14).

Designer's spec: `STAGE-PAGE-ROUND.md`, point 10 (FactInPlace's "the numbers
behind this N") and points 1-3 (AnalysesIndex's definition rows and analysis
rows). The /next session builds the pane shell; this module supplies the
three facts the drawings need and the code did not have:

  1. `build_measurements` — the analysis's metrics rows for one resource,
     with what each measurement opens (see `_OPENS_FOR` below).
  2. `fetch_step_counts` — how many of a definition's steps fetch, from
     `StepInfo.requires_resources`.
  3. `build_analyses_index` — one row per catalog analysis, with the
     questions that name it, last run, price, and what it serves.

Kept pure and registry-only (no FastAPI import), same reasoning as
`workflows/analysis.py` and `workflows/depth_offer.py` — the routes stay
thin adapters, and these are unit-testable without the app.
"""
from __future__ import annotations

from datetime import UTC, datetime

# ── measurements (point 10) ─────────────────────────────────────────────────
#
# analysis_id -> the project_analysis_metrics `kind`(s) whose latest row(s)
# make up this analysis's measurements. Most analyses write one metrics kind
# under their own id (cve_scan, foss_scorecard, rag_ingestion,
# website_ingestion, architecture_summary, repository_health,
# architecture_recovery); the entries below are the ones that don't, found by
# grepping every `registry.query_metrics(slug, ...)` call in
# `repo_survey_definition_adapter.py`'s results readers (the mapping
# `projects.py`'s `get_scoped_analysis_results` route uses at ~1489 covers
# only api_structure/data_file_profiling — reused here verbatim, not
# reinvented, and extended with the rest of the readers' own kinds):
#
#   language_file_classification -> "code_volume"   (_file_classification_results)
#   api_structure                -> "api_structure" (already in the ~1489 map)
#   code_symbol_extraction       -> "symbol_extraction" (_symbol_extraction_results)
#   data_file_profiling          -> "data_profile"  (already in the ~1489 map)
#   manifest_parse                -> the three manifest_parse_* sub-kinds
#                                    (_manifest_parse_results)
_METRICS_KINDS: dict[str, list[str]] = {
    "language_file_classification": ["code_volume"],
    "api_structure": ["api_structure"],
    "code_symbol_extraction": ["symbol_extraction"],
    "data_file_profiling": ["data_profile"],
    "cve_scan": ["cve_scan"],
    "foss_scorecard": ["foss_scorecard"],
    "sub_resource_survey": ["repo_sub_resource_survey"],
    "manifest_parse": [
        "manifest_parse_dependencies", "manifest_parse_ci_quality", "manifest_parse_conventions",
    ],
    "rag_ingestion": ["rag_ingestion"],
    "website_ingestion": ["website_ingestion"],
    "architecture_recovery": ["architecture_recovery"],
    "architecture_summary": ["architecture_summary"],
    "repository_health": ["repository_health"],
}

#: `manifest_parse`'s three sub-kinds all write the SAME raw metric name,
#: `"count"` (ManifestParseSurveyor._record_snapshot) — one column per kind,
#: distinguished only by the kind string, not the metric name. Renamed here
#: to the name `members._READERS` actually keys on
#: (`("manifest_parse", "dependency_count")`) so `opens` can match it; the
#: other two sub-kinds have no reader, so their renamed names are just for a
#: readable `measurements[].name` and never light up `opens`.
_MANIFEST_PARSE_METRIC_NAMES = {
    "manifest_parse_dependencies": "dependency_count",
    "manifest_parse_ci_quality": "ci_quality_count",
    "manifest_parse_conventions": "conventions_count",
}

#: The caveat `language_file_classification`'s measurements carry as their
#: note: the census this analysis reads (`code_volume`) skips vendored and
#: generated paths — `resource_explorer/ingestion/vendored.py` is where that
#: rule lives ("a measurement of this repository's code should not measure
#: TypeScript's"). The footer is one clause, not the docstring: a person
#: reading a number needs to know the exclusion, not the argument for it.
_VENDORED_EXCLUSION_NOTE = "vendored and generated paths excluded"


def _opens_for(analysis_id: str, metric_name: str) -> dict | None:
    """`{analysis_id, metric}` exactly where `members._READERS` has a reader
    for this (analysis, metric) pair, or where the `(analysis_id, None)`
    fallback reader is documented as listing what THIS metric counts — never
    guessed. One decision per reader, recorded here and in the PR body:

    * `("cve_scan", None)` -> `_cve_members` lists advisories, by package —
      exactly what `cve_scan`'s own `"advisories"` metric counts (the other
      three metrics on that kind — packages_affected/checked/unqueryable —
      are not what that reader lists).
    * `("api_structure", "symbol_count")` and
      `("code_symbol_extraction", "symbol_count")` -> `_symbol_members`,
      exact key matches. `relationship_count` on either kind has no reader —
      `_symbol_members` lists symbols, not relationships.
    * `("data_file_profiling", None)` -> `_data_file_members` lists data
      files, by format — matches `data_profile`'s `"total_files"`, not
      `"total_size_bytes"`.
    * `("architecture_recovery", None)` -> `_component_members` lists
      materialized components — matches the run-summary metric named
      `"<detect|coupling>_component_count"`, not `"*_withdrawn_count"`.
    * `("architecture_summary", "components")` -> `_component_members`,
      exact key match against `architecture_summary`'s `"components"` metric.
    * `("manifest_parse", "dependency_count")` -> `_dependency_members`,
      exact key match once the raw `"count"` metric is renamed (see
      `_MANIFEST_PARSE_METRIC_NAMES`) — the other two sub-parses have no
      reader.

    Every other (analysis_id, metric) pair — including every metric of
    `language_file_classification`, which has no `_READERS` entry at all —
    opens nothing.
    """
    from resource_explorer.members import _READERS

    exact = (analysis_id, metric_name) in _READERS
    if exact:
        return {"analysis_id": analysis_id, "metric": metric_name}

    fallback = (analysis_id, None) in _READERS
    if not fallback:
        return None

    listed = {
        "cve_scan": {"advisories"},
        "data_file_profiling": {"total_files"},
        "architecture_recovery": {"detect_component_count", "coupling_component_count"},
    }.get(analysis_id, set())
    if metric_name in listed:
        return {"analysis_id": analysis_id, "metric": metric_name}
    return None


def _humanise_age(seconds: float) -> str:
    """Same coarse "N units ago" wording the freshness gate uses
    (`workflows/analysis.py::_humanise_age`) — reused, not reimplemented, so
    a run's age reads identically wherever it's shown."""
    from resource_explorer.workflows.analysis import _humanise_age as _impl

    return _impl(seconds)


def _age_seconds(run_at: str) -> float | None:
    if not run_at:
        return None
    try:
        ts = datetime.fromisoformat(str(run_at))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


def _last_run_info(registry, slug: str, analysis_id: str, entity_type: str = "repo") -> dict:
    """`{last_run_at, last_run_status, last_run_via}` for one analysis,
    widened to its derivation sources exactly the way
    `workflows/depth_offer.py::_has_run` widens presence: `analysis_id`'s own
    entry in `get_analysis_last_run` wins if it has one; otherwise, if this
    is a derived analysis (e.g. `architecture_diagram`, which owns no steps
    of its own — see `AnalysisKind.derives_from`) and its SOURCE has run
    directly, that source's run is reported with `last_run_via` naming the
    source — never a bare "never run" for a derived id whose data is, in
    fact, current.

    `entity_type` defaults to "repo" for backward compatibility. The
    derives-from widening below is a repo-only concept today — database and
    filesystem adapters declare no `derives_from` analyses (same as
    `registry._analysis_derived_sources`, which returns `{}` for every
    entity_type but "repo") — so it is skipped for any other entity_type
    rather than importing repo's own derivation table for a resource it does
    not describe."""
    last_run = registry.get_analysis_last_run(entity_type, slug)
    own = last_run.get(analysis_id)
    if own:
        return {
            "last_run_at": own.get("last_run_at", ""),
            "last_run_status": own.get("last_run_status", ""),
            "last_run_via": own.get("last_run_via") or analysis_id,
        }
    if entity_type == "repo":
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            repo_analysis_derived_sources,
        )

        for source_id in repo_analysis_derived_sources(analysis_id):
            source = last_run.get(source_id)
            if source:
                return {
                    "last_run_at": source.get("last_run_at", ""),
                    "last_run_status": source.get("last_run_status", ""),
                    "last_run_via": source_id,
                }
    return {"last_run_at": "", "last_run_status": "", "last_run_via": ""}


def build_measurements(
    registry, slug: str, analysis_id: str, entity_type: str = "repo",
    level: str = "resource",
) -> dict:
    """GET /api/projects/{slug}/analyses/{analysis_id}/measurements payload.

    Raises `LookupError` for an unknown slug or an analysis_id not in the
    local catalog for `entity_type` — routes translate both to 404, kept out
    of this pure function so it stays FastAPI-free (same convention as
    `workflows/depth_offer.py::build_depth_offer`).

    `entity_type` defaults to "repo" for backward compatibility (same
    default `answer_question()` in `web/routes/analyses.py` and
    `compile_endpoint()` in `web/routes/compile_context.py` use). Fixed
    2026-09-23: this used to call `registry.get(slug)` — a repo-only
    `Project` lookup — and import `ANALYSIS_KINDS` directly from
    `repo_survey_definition_adapter`, so a database/filesystem slug 404'd as
    "Project ... not found" even when it existed, and a database/filesystem
    analysis_id 404'd as "Unknown analysis" even when it was a real entry in
    that type's own catalog (the same "resource-type never threaded through"
    bug PR #226/#233/#236 fixed in the answer/chat routes).

    The existence check and the analysis-kind lookup now go through the same
    per-type `ResourceTypeAdapter` (`survey_definition_executor.get_adapter`)
    those routes use. The metrics assembly below stays repo-specific
    (`_METRICS_KINDS`/`registry.query_metrics`, unchanged from before this
    fix) for `entity_type == "repo"` — database/filesystem analyses do not
    write to the generic `project_analysis_metrics` table at all; their
    measurements live behind each `AnalysisKind.results.results_reader`
    already registered per-type (`DATABASE_ANALYSIS_RESULTS_MAP` etc.), so a
    non-repo `entity_type` reads its measurements from that reader instead —
    the exact live-data path "How big is this database" already uses for its
    headline sentence (`_row_count_snapshot_headline`), just flattened into
    rows here rather than summarized into one sentence.

    `level` (Slice 21a point 4, defaults to "resource" — every pre-existing
    caller): the ASKING question's own primary level (mirrors
    `FactLayer._primary_level`'s "resource wins when declared" rule, computed
    client-side in app.js from the question's own `levels` and passed as a
    query param). When it names a sub-resource level (container/member/
    field) AND the adapter registers a container-level reader for this
    `analysis_id` (`ResourceTypeAdapter.analysis_container_results_map`),
    that reader's own rows are returned instead of the resource-level ones —
    e.g. a per-schema table instead of the flat table/column/row scalars,
    for "Which schemas carry the data...?" found live, owner's question,
    2026-09-26. Every analysis with no container-level reader registered,
    and every repo `entity_type` (no container reader exists for repo
    analyses yet), is unaffected — falls through to the existing
    resource-level reading, same as `_headline_for`'s own fallback.
    """
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        get_adapter,
    )

    adapter = get_adapter(entity_type)
    try:
        entity = adapter.get_entity(registry, slug)
    except SurveyDefinitionExecutorError:
        entity = None
    if entity is None:
        # Same per-type label the resource's own not-found errors use
        # (databases.py/filesystems.py's "Database '...'"/"FileSystem
        # '...'"), not a bare `entity_type` — "Repo" would be a new label
        # nothing else in the codebase uses for entity_type == "repo".
        label = {"database": "Database", "filesystem": "FileSystem"}.get(entity_type, "Project")
        raise LookupError(f"{label} {slug!r} not found")

    kinds_provider = adapter.analysis_kinds
    kinds_map = kinds_provider() if kinds_provider else {}
    if analysis_id not in kinds_map:
        raise LookupError(f"Unknown analysis {analysis_id!r}")

    run_info = _last_run_info(registry, slug, analysis_id, entity_type=entity_type)
    run_at = run_info["last_run_at"]
    source = run_info["last_run_via"] or analysis_id

    if entity_type != "repo":
        return _build_measurements_via_results_reader(
            registry, slug, analysis_id, kinds_map, run_at, source,
            adapter=adapter, level=level)

    kinds = _METRICS_KINDS.get(analysis_id)

    if not kinds:
        # No metrics kind at all — findings-only, per the designer's round:
        # "records measurements, not members" moved this exact sentence's
        # sibling out of members.py (#89); this is its counterpart.
        return {
            "analysis_id": analysis_id, "run_at": run_at, "source": source,
            "measurements": [], "not_applicable": True,
            "reason": (f"{analysis_id} records findings, not measurements — "
                       "see the findings list."),
            "footer": _footer(analysis_id, run_at, []),
        }

    measurements: list[dict] = []
    notes_seen: list[str] = []
    for kind in kinds:
        row = registry.query_metrics(slug, kind) or {}
        for metric_name, value in row.items():
            if metric_name in ("surveyed_at", "detail"):
                continue
            # manifest_parse's three sub-kinds all write the same raw
            # metric name ("count") — rename it per-kind so it reads
            # sensibly and so `opens` can match the `_READERS` key it's
            # actually keyed under (see _MANIFEST_PARSE_METRIC_NAMES).
            name = _MANIFEST_PARSE_METRIC_NAMES.get(kind, metric_name) \
                if analysis_id == "manifest_parse" else metric_name
            note = _VENDORED_EXCLUSION_NOTE if analysis_id == "language_file_classification" else ""
            if note and note not in notes_seen:
                notes_seen.append(note)
            measurements.append({
                "name": name, "value": value,
                "opens": _opens_for(analysis_id, name),
                "note": note,
            })

    if not measurements:
        return {
            "analysis_id": analysis_id, "run_at": run_at, "source": source,
            "measurements": [], "not_applicable": False,
            "reason": f"{analysis_id} has not recorded measurements on this resource yet.",
            "footer": _footer(analysis_id, run_at, []),
        }

    return {
        "analysis_id": analysis_id, "run_at": run_at, "source": source,
        "measurements": measurements, "not_applicable": False, "reason": "",
        "footer": _footer(analysis_id, run_at, notes_seen),
    }


def _build_measurements_via_results_reader(
    registry, slug: str, analysis_id: str, kinds_map: dict, run_at: str, source: str,
    adapter=None, level: str = "resource",
) -> dict:
    """The non-repo counterpart of the `_METRICS_KINDS` branch above.

    Database and filesystem analyses have no `project_analysis_metrics`
    rows to read — each has its own `AnalysisKindResults.results_reader`
    (e.g. `_row_count_snapshot_results`, registered on
    `DATABASE_ANALYSIS_KINDS`) returning a plain dict already read by the
    Results/Chat surfaces for that type. This flattens that same dict into
    the same `{name, value, opens, note}` row shape `build_measurements`
    returns for repo, so the frontend's one table renderer works for both.

    A nested value (a list or dict — e.g. `row_count_snapshot`'s own
    per-table `"tables"` breakdown) is skipped here rather than rendered:
    this table is for scalar measurements, and a per-table breakdown is
    exactly what `opens` and the members drill-down are for, not a row of
    its own. `opens` is always `None` — no per-type `_READERS`-style
    members table exists for these analyses yet (repo's `_opens_for` reads
    `resource_explorer.members._READERS`, which is repo-only), so nothing
    is invented here; that stays a real, separate gap.

    Slice 21a point 4: when `level` names a sub-resource level and
    `adapter.analysis_container_results_map` registers a reader for this
    `analysis_id`, that reader's own already-row-shaped list is returned
    directly instead of the flattening below — it returns
    `{name, value, opens, note}` rows itself (e.g. one row per schema), not
    a raw dict to flatten. Falls through to the resource-level reader,
    unchanged, when no container reader is registered for this analysis_id
    or `adapter` doesn't declare the map at all.
    """
    if level != "resource" and adapter is not None:
        container_map_provider = adapter.analysis_container_results_map
        container_map = container_map_provider() if container_map_provider else {}
        container_reader = (container_map or {}).get(analysis_id)
        if container_reader:
            rows = container_reader(registry, slug) or []
            if rows:
                return {
                    "analysis_id": analysis_id, "run_at": run_at, "source": source,
                    "measurements": rows, "not_applicable": False, "reason": "",
                    "footer": _footer(analysis_id, run_at, []),
                }
            return {
                "analysis_id": analysis_id, "run_at": run_at, "source": source,
                "measurements": [], "not_applicable": False,
                "reason": f"{analysis_id} has not recorded measurements on this resource yet.",
                "footer": _footer(analysis_id, run_at, []),
            }

    kind = kinds_map.get(analysis_id)
    reader = kind.results.results_reader if kind and kind.results else None
    if not reader:
        return {
            "analysis_id": analysis_id, "run_at": run_at, "source": source,
            "measurements": [], "not_applicable": True,
            "reason": (f"{analysis_id} records findings, not measurements — "
                       "see the findings list."),
            "footer": _footer(analysis_id, run_at, []),
        }

    raw = reader(registry, slug) or {}
    measurements = [
        {"name": name, "value": value, "opens": None, "note": ""}
        for name, value in raw.items()
        if not isinstance(value, (list, dict))
    ]

    if not measurements:
        return {
            "analysis_id": analysis_id, "run_at": run_at, "source": source,
            "measurements": [], "not_applicable": False,
            "reason": f"{analysis_id} has not recorded measurements on this resource yet.",
            "footer": _footer(analysis_id, run_at, []),
        }

    return {
        "analysis_id": analysis_id, "run_at": run_at, "source": source,
        "measurements": measurements, "not_applicable": False, "reason": "",
        "footer": _footer(analysis_id, run_at, []),
    }


def _footer(analysis_id: str, run_at: str, notes: list[str]) -> str:
    age = _age_seconds(run_at)
    age_clause = "run date not recorded" if age is None else f"run {_humanise_age(age)}"
    footer = f"Read from {analysis_id}, {age_clause}"
    if notes:
        footer += " · " + " · ".join(notes)
    return footer


# ── fetch-step counts (point 2) ─────────────────────────────────────────────

def fetch_step_counts(step_keys: list[str]) -> tuple[int, int, list[str]]:
    """(step_count, fetch_steps, unregistered_steps) for one definition's
    step-key list — `step_count` is just `len(step_keys)`; `fetch_steps`
    counts the ones registered in `STEP_REGISTRY` whose `StepInfo.
    requires_resources` is non-empty (a zipball or git-clone download); a key
    not in `STEP_REGISTRY` at all (an Egeria-native step, which this
    codebase has no surveyor for) counts toward `step_count` but not
    `fetch_steps`, and is named in `unregistered_steps` so the mismatch is
    visible rather than folded in."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    fetch_steps = 0
    unregistered: list[str] = []
    for key in step_keys:
        info = STEP_REGISTRY.get(key)
        if info is None:
            unregistered.append(key)
            continue
        if info.requires_resources:
            fetch_steps += 1
    return len(step_keys), fetch_steps, unregistered


# ── analyses index (points 1-3) ─────────────────────────────────────────────

def _first_sentence(text: str) -> str:
    """The first sentence of `text`, split on the first `". "` or `".\\n"` —
    the whole text when neither occurs."""
    if not text:
        return text
    candidates = [i for i in (text.find(". "), text.find(".\n")) if i != -1]
    if not candidates:
        return text
    idx = min(candidates)
    return text[: idx + 1]


def runnable_and_reason(analysis_id: str, entity_type: str = "repo") -> tuple[bool, str]:
    """Whether this entity type's run route would accept this id, and the
    reason when it would not — the SAME check and the SAME text
    `run_single_analysis` (web/routes/projects.py) uses via
    `resolve_analysis_plan`, so this row and that route can never disagree
    FOR REPO. A pure wrapper (no registry/slug needed — the gate is id-only),
    so it's also the boundary a test can call directly for an id the index
    itself never lists (`egeria_publish`, excluded below as `action:
    "publish"`).

    `entity_type` used to be unaccepted here (defaulting `resolve_analysis_
    plan` to "repo" regardless of caller) — every database/filesystem row in
    `build_analyses_index` reported "no mapped survey step(s)" for a real,
    runnable analysis (`db_activity_signals`, `schema_inventory`, …), because
    none of those ids resolve against repo's own step map. Live-reproduced
    2026-09-25: every "run"/"re-run" button on a database's Survey & Analyses
    pane was disabled with that message, including `schema_inventory`, which
    had run successfully (and produced 63 real annotations) moments earlier
    via a direct API call that bypasses this check entirely — the run route
    itself (`databases.py::run_single_database_analysis`) was never broken,
    only this precheck's idea of which catalog to resolve against."""
    from resource_explorer.workflows.analysis import resolve_analysis_plan

    is_ingest, steps = resolve_analysis_plan(analysis_id, entity_type)
    runnable = bool(is_ingest or steps)
    if runnable:
        return True, ""
    return False, (
        f"Analysis '{analysis_id}' has no mapped survey step(s) — "
        "either it's a publish action (not a survey) or an unknown id."
    )


def build_analyses_index(registry, slug: str, entity_type: str = "repo") -> dict:
    """GET /api/projects/{slug}/analyses-index payload.

    One row per `get_analyses(entity_type, include_egeria_live=False)` entry,
    excluding `action == "publish"` (that's an explicit write action, not
    something with results or a cost to preview — same exclusion
    `_offerable_repo_analyses` makes in `workflows/depth_offer.py`).

    `entity_type` defaults to "repo" for backward compatibility, same as
    `build_measurements` above. Fixed alongside it (2026-09-23) — this
    carried the identical repo-only bug: `registry.get(slug)` for the
    existence check and a direct `ANALYSIS_KINDS` import (repo's own) for
    the "does this analysis have a results reader" check, both now routed
    through the same per-type `ResourceTypeAdapter` `build_measurements`
    uses.

    `runnable_and_reason()` below now takes `entity_type` too (fixed
    2026-09-25, live-reproduced against a database's Survey & Analyses pane
    — see its own docstring) — every button on that pane was disabled with
    "no mapped survey step(s)" because `resolve_analysis_plan` always
    resolved against repo's own catalog. `web/routes/projects.py::
    run_single_analysis`, the actual REPO run route, is unaffected (it was
    already repo-only by design); a database's real run route is
    `databases.py::run_single_database_analysis`, which never shared this
    bug — only this precheck's idea of which catalog to resolve against.

    Raises `LookupError` for an unknown slug — routes translate to 404."""
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.question_catalog_reader import get_questions
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        get_adapter,
    )
    from resource_explorer.workflows.analysis import estimate_run_cost
    from resource_explorer.workflows.depth_offer import run_cost_as_dict

    adapter = get_adapter(entity_type)
    try:
        entity = adapter.get_entity(registry, slug)
    except SurveyDefinitionExecutorError:
        entity = None
    if entity is None:
        label = {"database": "Database", "filesystem": "FileSystem"}.get(entity_type, "Project")
        raise LookupError(f"{label} {slug!r} not found")

    kinds_provider = adapter.analysis_kinds
    kinds_map = kinds_provider() if kinds_provider else {}

    entries = [a for a in get_analyses(entity_type, include_egeria_live=False)
               if a.get("action") != "publish"]
    questions = get_questions(entity_type)

    rows: list[dict] = []
    never_run = 0
    no_question = 0
    for entry in entries:
        aid = entry["id"]
        matched_questions = [
            {"question": q["question"], "stage": q["stage"]}
            for q in questions if aid in (q["answering"]["analysis_ids"] or [])
        ]

        # Three-way "what it serves" (owner's ruling, docs/coverage-audit-
        # 2026-09-11.md §"orphans"): an analysis with no naming question
        # still serves chat's ad-hoc questions when it has a results reader
        # — a reader is data chat can answer FROM, whether or not any
        # authored question names this id. Only an analysis with neither a
        # question nor a reader serves nothing yet.
        kind = kinds_map.get(aid)
        has_results_reader = bool(kind and kind.results is not None)
        if matched_questions:
            serves = "question"
        elif has_results_reader:
            serves = "chat-only"
        else:
            serves = "nothing-yet"

        run_info = _last_run_info(registry, slug, aid, entity_type=entity_type)
        if not run_info["last_run_at"]:
            never_run += 1
        if serves != "question":
            no_question += 1

        runnable, runnable_reason = runnable_and_reason(aid, entity_type)
        cost = run_cost_as_dict(estimate_run_cost(registry, aid, resource_type=entity_type))

        rows.append({
            "analysis_id": aid,
            "name": entry.get("name", ""),
            "short_description": _first_sentence(entry.get("description", "")),
            "description": entry.get("description", ""),
            "tier": entry.get("intent", ""),
            "recommended": bool(entry.get("recommended")),
            "questions": matched_questions,
            "serves": serves,
            "last_run_at": run_info["last_run_at"],
            "last_run_status": run_info["last_run_status"],
            "last_run_via": run_info["last_run_via"],
            "cost": cost,
            "runnable": runnable,
            "runnable_reason": runnable_reason,
            "catalog": entry,
        })

    return {
        "slug": slug,
        "analyses": rows,
        "counts": {"total": len(rows), "never_run": never_run, "no_question": no_question},
    }
