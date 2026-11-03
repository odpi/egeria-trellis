# Audit: trellis on pyegeria 6.2.3

Branch `re/pyegeria-6.2.3`, from main `6804eb58`, 2026-10-13. pyegeria 6.2.3 is the release at
https://github.com/odpi/egeria-python/releases/tag/v6.2.3. The lock moves `pyegeria 6.2.2 -> 6.2.3` and nothing
else (`uv lock --upgrade-package pyegeria`; the diff is the version, the sdist and the wheel, plus the two
`specifier` lines). The pin is raised in `packages/resource-explorer/pyproject.toml` and
`packages/trellis-analytics/pyproject.toml`. `packages/egeria-advisor` (`>=6.1.10`) and `packages/trellis-auth`
(`egeria` extra, `>=6.1.10`) keep their floors. Neither Dockerfile (`docker/Dockerfile.resource-explorer`,
`docker/Dockerfile.egeria-advisor`) nor `.github/workflows/*.yml` names a pyegeria version: both build from
`uv sync --frozen`, so they take the new lock.

## Method

The 6.2.2 and 6.2.3 sdists were downloaded from PyPI and diffed (`diff -ru`, `__pycache__` excluded). Eleven
files under `pyegeria/` differ. Nothing outside `pyegeria/` matters to trellis. Every change is listed below,
including the ones the release notes do not mention (marked **not in the notes**).

## 1. What changed

| # | File | Change | Issue |
|---|---|---|---|
| 1 | `view/analytic_registry.py` | `AnalyticFunctionSpec` gains `kind` (`Literal["routine","step"]`, default `routine`), `source` (str, default `""`; a validator **warns** on a value outside `KNOWN_ANALYTIC_SOURCES = {ods, egeria, resource}` and keeps it), `version`, `envelope` (bool, default False), `annotation_type`, `resource_types`, `family`, `tags`. All optional. `KNOWN_ANALYTIC_SOURCES` is exported. | 149 |
| 2 | `view/_output_format_models.py` | `ActionParameter.analytic_binders: Dict[str,str]` (parameter name -> dotted path of a factory). | 138 |
| 3 | `view/format_set_executor.py` | `_apply_analytic_binders` builds a routine's non-JSON arguments from the call params. `_result_envelope` reads a state envelope off a result (dict `state`/`state_reason`/`subtitle`, `resultState`, nested `envelope`, or an object's `.envelope`); every analytic output now carries `"envelope": {state, reason, subtitle}`. A chart gets `title: {text, subtitle}`. BAR/PIE/SERIES are drawn only for `measured` or `nothing_found`; `not_established` / `not_applicable` return `{"kind":"json","data": <the result>}` plus the envelope. Pydantic results are dumped (`_jsonable`). | 138, 139, 140 |
| 4 | `omvs/asset_maker.py` | `activity_status_list` defaults to `None` (no filter) instead of `["IN_PROGRESS"]` / `[]` on `find_processes`, `get_processes_by_category`, `get_action_targets`, `get_actions_for_action_target`, `get_assigned_actions`, `get_actions_for_sponsor`, `get_actions_for_requester` (sync and async). Note the async `_async_find_processes` default was `[]` and the sync `find_processes` was `["IN_PROGRESS"]`; both are now `None`. | 148 |
| 5 | `omvs/automated_curation.py` | `get_engine_actions` / `_async_get_engine_actions` removed (the View Server has no list-all endpoint). `print_engine_actions` now calls `find_engine_actions("*")`. | 151 |
| 6 | `view/overview_metrics.py` | `count_elements`, `count_elements_by_property`, `counts_by_type`, `people_counts`, `usage_context_counts` take `strict=False`; with `strict=True` a count that could not be measured (query failed, or the fallback hit `DEFAULT_CAP`) is `None`, not 0. New `_find_or_none`. Registry: a `strict` `AnalyticParam` is added to the built-in analytics that count. Default behaviour is unchanged. **Not in the notes as a trellis concern.** | 123 |
| 7 | `core/_base_server_client.py` | A wrapped Egeria error with `relatedHTTPCode` 401/403 raises `PyegeriaUnauthorizedException` only if `_is_authorization_failure`: the exception class ends `UserNotAuthorizedException`, or there is no class and the code is 401. Any other 403 (e.g. `OMAG-GENERIC-HANDLERS-403-005`, deleting an element that still has dependents) is now a plain `PyegeriaAPIException`. The unauthorized exception's `additional_info` now also carries `userid`. **Not in the notes as a trellis concern.** | 142 |
| 8 | `core/_server_client.py` | `delete_note` posts to `.../metadata-expert/metadata-elements/{guid}/delete` (built from `platform_url`) instead of `feedback-manager/assets/{guid}/delete`. | 146 |
| 9 | `omvs/classification_explorer.py` | A docstring example body key `status` -> `termAssignmentStatus`. Docs only. | |
| 10 | `view/base_report_formats.py` | The generated FormatSets gain `Reference-Data-Set-DrE-*` and `Reference-Data-Value-DrE-*`; some `Agreement`/`Data-Sharing-Agreement`/`Digital-Subscription` entries were regenerated. **Not in the notes.** | |

## 2. Trellis call sites

Searched: `packages/resource-explorer/resource_explorer`, `packages/egeria-advisor`, `packages/trellis-analytics`,
`packages/trellis-auth` (excluding `node_modules`, `.venv`, the RAG corpus).

### ISSUE-148 (`activity_status_list` default): no call site, no change needed

None of the seven methods is called anywhere in trellis (`find_processes`, `get_processes_by_category`,
`get_action_targets`, `get_actions_for_action_target`, `get_assigned_actions`, `get_actions_for_sponsor`,
`get_actions_for_requester`: zero hits in code). RE reads engine actions and process status through
`get_metadata_element_by_guid` / `get_related_metadata_elements` (`native_survey_run`, `catalogue_gateway`,
`reachability`), which this release does not touch. The one RE call that takes an activity-status list is
`rfa_egeria_sync.py:253` `my_profile.get_my_to_dos(activity_status_list=_OPEN_ACTIVITY_STATUSES)`; it passes the
list explicitly and `get_my_to_dos` is not among the changed methods. So RE's in-progress behaviour is unchanged
and no explicit filter had to be added.

### ISSUE-151 (`get_engine_actions` removed): nothing calls it

Zero hits for `get_engine_actions`, `_async_get_engine_actions`, `find_engine_actions` or `print_engine_actions`
in any package. Nothing to migrate.

### ISSUE-149 / 138 / 139 / 140 (analytics): trellis-analytics only

- `trellis_analytics/manifest.py` (changed here, section 3).
- `trellis_analytics/portal.py` is the FormatSet adapter (section 3).
- `egeria-advisor/advisor/report_pipeline.py:~700` reads an `exec_report_spec` result by `kind` and `data` only.
  It ignores the new `"envelope"` key. It is unchanged and works, but it **drops the state**: an analytic chart
  that is not_established now arrives as `kind: json` data (a dict, no chart) and the Advisor prints that JSON
  without saying the state. That was a chart of zeros before; it is the honest data now, and it is a presentation
  gap in the Advisor that is not fixed here. No Advisor call currently reaches an analytic with an envelope
  (the Advisor does not load `trellis.analytics`), so nothing is wrong today.

### ISSUE-123 (`strict` counts): no call site

Nothing in trellis imports `overview_metrics` or calls the count helpers. The Portal (egeria-workspaces) does,
outside this repo.

### ISSUE-142 (403 is not always unauthorized): RE reads 403 in three places; none breaks, one is now imprecise

- `egeria_absence._classify_exception`: `PyegeriaUnauthorizedException` -> unreadable, and every non-404 API
  exception is also unreadable. A 403-005 used to be unauthorized -> unreadable; it is now an API exception ->
  unreadable. Same answer.
- `egeria_outbox.is_security_refusal`: true for the `OPEN-METADATA-SECURITY-403` text marker **or** an HTTP 403 on
  the exception (`_http_code_of`). A 403-005 still has `related_http_code` 403, so it is still classed as a
  security refusal. Behaviour is unchanged, but it was wrong before and is wrong now: an `InvalidParameter`
  refusal ("still has dependents") is labelled "needs a permission or zone change, not a retry". It could now be
  fixed (the exception class and message id are now truthful), by letting the 403 branch skip a message id that
  starts `OMAG-GENERIC-HANDLERS-403`. **Not done in this branch**: it changes outbox retry policy on a write
  path, so it is raised for a separate, reviewed change.
- `egeria_outbox._describe_refusal` adds "Egeria refused the write for service identity X" only when a
  `PyegeriaUnauthorizedException` is in the chain. A 403-005 will now get the plain Egeria text, which is
  correct (it was not an identity refusal). The work-list docstring at `work_lists.py:434` ("pyegeria
  misreports as an AUTHORIZATION 401") describes pyegeria's old behaviour; it is now stale but harmless.
  The test fake `tests/fake_egeria_catalogue.py:407` raises a `GatewayError` text, not the pyegeria class, so no
  test depended on the old class.

### ISSUE-146 (`delete_note`): no call site

`feedback_egeria.py:23` states RE never deletes a note in Egeria. The `delete_note` names in RE are the local
registry's and the `/curate` route's.

## 3. trellis-analytics

`packages/trellis-analytics/src/trellis_analytics/manifest.py`:

Dropped (the base class now carries the same field, type and default): `annotation_type`, `resource_types`,
`family`, `tags`, and the `Kind` alias.

Kept as narrower overrides, deliberately:
- `source: Literal["ods","egeria","resource"]`, required. The base is an open string that only logs a warning
  and keeps the value ("suggestive"). A trellis manifest is checked when it is built.
- `version: str`, required. The base default `""` would stamp an unversioned result.
- `envelope: bool = True`. Every trellis routine returns an Envelope; the base default is False.
- `RoutineManifest.kind: Literal["routine"]`, `StepManifest.kind: Literal["step"]`. The base accepts either word
  on either class; a RoutineManifest cannot now be built as a step. (This is a tightening: the old
  `Kind = Literal["routine","step"]` override allowed it.)
- `StepManifest.annotation_type = "ResourceMeasureAnnotation"` and `credential_tier`, `purposes` stay: not in the
  base.

Registry safety: `register()` still registers the subclass instances into pyegeria's registry; the base
`source` validator runs on them and sees only the three known values.

`tests/test_portal_tile.py`: the "there is NO subtitle" assertion (a recorded GAP) is flipped. The BAR output now
asserts `data["title"]["subtitle"] == envelope["subtitle"] == "measured"` and `out["envelope"]["state"]`. A new
test, `test_dict_output_carries_the_envelope_beside_the_tile`, asserts the DICT output's `envelope` equals the
tile's own `state_reason` / `subtitle`. The not_established and not_applicable BAR tests already assert no chart,
and still pass: the executor and the adapter now both refuse to draw it.

### Does `analytic_binders` simplify the tile? No; the adapter stays.

`curation_coverage_tile` is not just a reader factory in front of the routine:
- The routine returns a `CoverageResult` (groups, lines, ids). The tile's DICT shape (`kpis`, `manifest`,
  `provenance`, `summary`) and its BAR shape (a flat `{"Published": n, "Not yet published": n}`) are built from
  it. The executor's BAR/PIE reads only a flat `{category: number}` dict (`_as_category_value_dict`), so
  pointing `analytic_function` at the routine would chart nothing.
- A binder would remove only `OdsReader(ods_url or env)`. That factory takes `url`, the tile's call param is
  `ods_url`, and the environment fallback (`REGISTRY_DATABASE_URL`) would be lost or moved into the factory.
  That is a net change in calling convention for no lines saved in the adapter.

What did simplify: the tile's own `state`, `state_reason` and `subtitle` keys are exactly what
`_result_envelope` reads, so the envelope on the output and the chart subtitle now come from the adapter's
existing keys with no extra code. The `_CHARTED` guard in the adapter stays: it also skips computing the two
bar values when there is nothing to chart, and the executor's own gate is a second line of defence.

## 4. RE

No RE source change was required. Confirmed:
- nothing calls `get_engine_actions`;
- no call site depends on the old `activity_status_list` default (section 2);
- the 6.2.2 audit's findings (None unreadable, the lookup-never-creates work) are unaffected: the changed files
  do not touch `get_guid_for_name`, `get_metadata_element_by_*` or `get_related_metadata_elements`.

`packages/resource-explorer/pyproject.toml` gets a comment on the new floor.

## 5. Findings left open

1. `is_security_refusal` treats any HTTP 403 as a security refusal, including `OMAG-GENERIC-HANDLERS-403-005`
   (section 2, ISSUE-142). Candidate fix, for its own PR with a peer round.
2. The Advisor drops the analytic `envelope` (section 2). A presentation gap, no live path today.
3. Order-dependent test failures that are **not** from 6.2.3 (section 6).

## 6. Test results

All with `CI=true PGVECTOR_PORT=1 EGERIA_PLATFORM_URL=https://127.0.0.1:1 REGISTRY_DATABASE_URL=sqlite:///...`.

| Run | Result |
|---|---|
| RE full suite, five chunks by file-name range (`test_[a-c]`, `[d-h]`, `[i-o]`, `[p-r]`, `[s-z]`) | 10,081 passed, 531 skipped, 2 failed |
| analytics CI command from `resource-explorer.yml`, as written | 81 passed |
| `test_no_silent_success` + tailwind class coverage + tailwind freshness | 11 passed, 1 skipped (freshness: `frontend-build/node_modules` absent; `npm ci` failed in this worktree; independent of pyegeria) |
| egeria-advisor `tests/unit` (no CI command exists for it; the workflow has none) | with `PGVECTOR_PORT=1`: 263 passed, 5 failed, 13 skipped, 33 errors, all `Connection refused` to the pgvector database. With the default port: 314 passed. |
| uvloop boot (`resource-explorer web --port 8899`, sqlite registry, no `.env`) | booted, `GET /api/projects` -> 401 |

The 2 failures, `test_prefect_dispatch.py::...::test_the_fallback_names_the_exception_type` and
`test_query_names_another_resource.py::...::test_a_failed_check_is_reported_not_read_as_names_nothing`, both
assert on `caplog.text` and get an empty string. Each passes alone and passes in the first 16 files of the
chunk; they fail when roughly 20 later-collected modules are in the same session (bisected to the
`test_[p-r]` files near `test_reports`). They fail the same way on **pyegeria 6.2.2**: the same tests, run
under wt-relgraph's 6.2.2 venv against this worktree's code, give the same empty `caplog`. So it is a
collection-order logging interaction, not from this upgrade.
