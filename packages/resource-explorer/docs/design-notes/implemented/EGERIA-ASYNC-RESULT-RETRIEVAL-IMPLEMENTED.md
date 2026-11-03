# Egeria Async Survey Result Retrieval — Implemented (2026-09-19)

Closes the gap documented in `docs/survey-model.md` §D5 ("Async Survey Result
Retrieval") for Survey Definition steps tagged `executes_at="egeria"`: RE used
to trigger Egeria's native survey engine and immediately report status
`"triggered"`, never waiting for the result, never retrieving it, and never
parsing it back into RE's own findings. This document records what changed,
the attribution mechanism it depends on, what was and wasn't verified, and
what's still open.

## What changed

`resource_explorer/surveyors/database/survey_definition_adapter.py` and
`resource_explorer/surveyors/filesystem/survey_definition_adapter.py`'s
`_trigger_egeria_native_survey` functions (the `other_engine_handlers["egeria"]`
handlers registered by both adapters) now:

1. Record the wall-clock time just before triggering.
2. Trigger Egeria's native survey (`EgeriaDatabaseSurveyor.trigger_survey_by_guid`
   / `EgeriaFileSystemSurveyor.trigger_survey_by_guid` — unchanged, already
   worked).
3. **Synchronously poll** the resulting engine action to a terminal
   `activityStatus`, via a new shared module,
   `resource_explorer/surveyors/egeria_async_survey_result.py`, which reuses
   (imports directly, does not reimplement) `egeria_delegated_step.
   _poll_action_status` — the same live-verified polling logic already used by
   `EgeriaDelegatedStepSurveyor` (interval/timeout, terminal-state detection
   via `_ACTIVE_STATUSES`, the `activityStatus`-not-`actionStatus` wire-key
   fix documented in that module).
4. Identify the *specific* new `SurveyReport` this trigger produced (see
   "Report attribution" below).
5. Read back that report's real annotations
   (`egeria_survey_reader.get_annotations_by_report_guid`, already existed,
   unchanged) and convert them into RE's own `Annotation` dataclass shape
   (new: `egeria_async_survey_result.convert_wire_annotation`).

The handler now returns `{"status": "ok", "engine_action_guid", "final_status",
"completion_message", "report_guid", "report_qualified_name", "annotations"}`
on success, or **raises** `EgeriaEngineActionTimeoutError` (poll timeout — the
action is left running in Egeria) or `SurveyReportAttributionError`
(ambiguous/missing report) on failure. Both propagate to
`survey_definition_executor.py`'s existing per-step `except Exception` clause
(around line 498), which already turns a handler exception into a specific
`errors` entry and an `"error"` step status — no new exception handling was
needed there, only:

- `other_engine_handlers` dispatch (`survey_definition_executor.py` ~line 473)
  now reports the handler's own `outcome["status"]` when the handler sets one,
  instead of hardcoding `"triggered"` for every non-exception outcome. A
  handler that still only fires-and-forgets (returns a dict with no `"status"`
  key) keeps the historic `"triggered"` wording — this is backward compatible
  with any other resource type's `other_engine_handlers` that doesn't wait.
- The outcome dict is now also appended to `step_outputs` and passed through
  `_stamp_definition_provenance` (previously only steps_report got a `detail`
  entry) — the same treatment every other executed step already gets, so a
  step that waited for a real result participates in the same
  provenance-stamping path as a locally-run step.
- `steps_report`'s `detail` field summarizes the outcome rather than embedding
  it whole, because `detail` feeds a `json.dumps()` call later in the same
  function (the activity-log summary) and the outcome's `annotations` value is
  a list of real `Annotation` dataclass instances, not JSON-serializable
  dicts. `detail` carries every other key plus an `annotation_count`; the
  `Annotation` instances themselves still flow to `step_outputs`.

## Report attribution mechanism

**The problem:** after triggering, a resource's asset can have multiple
`SurveyReport`s linked via the real `ReportSubject` relationship — an old one
from a previous run, one from a concurrent unrelated survey, and (hopefully)
one from this trigger. `get_survey_reports_by_guid` returns all of them with no
inherent way to tell which is "the one this call produced."

**What was checked and not found:** a direct EngineAction→SurveyReport
relationship. This build inspected `AssetMaker.get_asset_by_guid(engine_action_
guid, body={"graphQueryDepth": 1})`'s output shape for a completed engine
action and found no relationship key analogous to `reports`/`reportedAnnotations`
that points at a specific output report. This was a code-level/documentation
check within the time available for this build, not an exhaustive live probe
against every possible relationship type in Egeria's own type system — **if a
future investigation finds one, querying it directly is simpler and more
precise than the mechanism below, and should replace it.**

**What was built instead — timestamp-based attribution:**
`egeria_async_survey_result.resolve_triggered_survey_report(reports,
triggered_at)`:

1. Parses each candidate report's `surveyed_at` (handles both an epoch-millis
   wire value and an ISO-8601 string; anything else is treated as unparseable,
   never guessed).
2. Filters to reports whose parsed timestamp is *strictly after* the recorded
   pre-trigger time.
3. Requires **exactly one** match. Zero matches or more than one both raise
   `SurveyReportAttributionError` with the candidate GUIDs (for the ambiguous
   case) or the total/unparseable counts (for the zero-match case) in the
   message — reported as an explicit, actionable ambiguity, never resolved by
   picking "the newest" or "the first."

This deliberately does **not** use "the newest report for this asset" (a
concurrent unrelated survey could be newer than this trigger's own report) or
"the first result returned" (the API's ordering is not a documented contract).

**A related failure mode flagged by a peer during scoping, and how it's
handled:** "the engine action reached COMPLETED" and "the report has real
annotation content" are two separate facts. This build does NOT treat an empty
`annotations` list as a failure — a genuinely-empty survey result (nothing
found) is a legitimate, correctly-attributed outcome, distinct from "the wrong
report was picked" or "the report doesn't exist yet." The report-attribution
step's own success/failure (exactly one report matched) is what's asserted
strictly; the *content* of that report is passed through as-is, not
second-guessed. This is a deliberate scope boundary: verifying that a
specific, well-attributed report is empty vs. non-empty is a downstream
concern (findings/reconciliation), not something this retrieval layer should
paper over by treating "empty" as ambiguous.

## Annotation conversion

`convert_wire_annotation(raw, analysis_step)` maps the flat wire-dict shape
`egeria_survey_reader.get_annotations_by_report_guid` returns (`guid`,
`annotation_type`, `summary`, `confidence`, `analysis_step`, `explanation`,
`expression`, `json_properties`) onto the matching `Annotation` dataclass
subclass in `survey_report.py`, keyed by `AnnotationType`'s enum values
(`ResourceMeasureAnnotation`, `SchemaAnalysisAnnotation`,
`RequestForActionAnnotation`, etc.). An unrecognized `annotation_type` string
falls back to `ResourceMeasureAnnotation` rather than dropping the annotation
or crashing.

`analysis_step` is set to RE's own `re_analysis_step` key (e.g.
`postgres_schema_and_stats`), not Egeria's own native-survey step name for the
annotation — the wire's own `analysis_step` value is preserved unaltered in
`check_name` instead, so nothing is lost, but every `Annotation` this run
produces stays consistent in RE's own vocabulary. `source="egeria"` and
`item_key=<the annotation's own guid>` are set on every converted annotation.

Type-specific extra fields (`resource_properties`, `action_requested`/
`action_target_name`, etc.) are populated where the wire shape maps cleanly
(`ResourceMeasureAnnotation.resource_properties` ← `json_properties`;
`RequestForActionAnnotation.action_requested`/`action_target_name` ←
`explanation`/`guid`). Other subclasses' distinguishing fields
(`candidate_classifications`, `schema_name`, `quality_scores`,
`related_entity_name`, ...) are left at their dataclass defaults — nothing in
the wire dict maps onto them without guessing, and guessing was judged worse
than an honest default.

**Open question, deliberately left open by this build:** these converted
`Annotation` objects currently flow into `step_outputs` (for provenance
stamping and test/programmatic access) but are **not** fed into either
adapter's `_publish` function, so they are not re-published to Egeria under
RE's own `SurveyReport` naming convention. This is deliberate — the content
already exists in Egeria, under Egeria's own native-survey `SurveyReport` —
but whether RE's local views (findings, per-resource annotation lists) should
also persist them locally, and via what path, was not decided or built here.
Flagged for a follow-up design pass rather than guessed at.

## Scheduling / timeout consideration

Investigated per the scoping brief: whether making this step synchronous
changes anything about how it should be scheduled. Found:
`survey_definition_executor._use_prefect()` only recognizes `executes_at ==
"prefect"` and (opt-in) `"resource-explorer"` — `other_engine_handlers` steps
are never routed to Prefect regardless of this change. So this now-synchronous
wait (bounded by `DEFAULT_TIMEOUT_SECONDS=300s` from `egeria_delegated_step.py`,
reused as-is) runs directly in whatever thread executes the Survey Definition,
with no separate cancellation or visibility beyond the poll's own internal
timeout — the same visibility gap CLAUDE.md already documents for
`executes_at: egeria` steps generally ("a separate, deferred problem"). No
scheduling change was made: no problem beyond the pre-existing, documented gap
was confirmed, and this step's own timeout already bounds the worst case.
Noted here as an open item for whoever next revisits Prefect routing for
`other_engine_handlers`.

## Testing

### Layer 1 — mocked, no live Egeria (all passing)

New file: `tests/test_egeria_async_survey_result.py`.

- `TestConvertWireAnnotation` — known-type mapping to the right dataclass,
  unknown-type fallback, `RequestForActionAnnotation`'s extra fields, and
  non-numeric confidence defaulting safely.
- `TestResolveTriggeredSurveyReport` — the core attribution logic:
  - picks the single report created after trigger time;
  - zero matches raises `SurveyReportAttributionError`;
  - **two reports created in the same window raises an explicit ambiguity
    error, not a guess** (the exact hazard flagged during scoping);
  - an unparseable timestamp is excluded, not silently included;
  - epoch-millis timestamps are handled, not just ISO strings.
- `TestPollTriggerAndRetrieveAnnotations` — the end-to-end helper:
  - happy path: polls to `COMPLETED`, resolves the right report, converts its
    annotations;
  - **poll timeout raises `EgeriaEngineActionTimeoutError`** and never even
    calls `get_survey_reports_by_guid` (no result is fabricated on a timeout);
  - an ambiguous report attribution propagates as its own error and never
    reaches annotation retrieval.

Existing tests updated to match the new (real) behavior instead of the old
fire-and-forget shape, since they called the real handler function directly:

- `tests/test_execution_modes_path_b1_failure_modes.py::
  TestUncataloguedAssetRaises::test_a_guid_present_does_not_raise_and_triggers_the_native_survey`
- `tests/test_filesystem_survey_definition_adapter.py::
  test_trigger_egeria_native_survey_calls_pyegeria_with_confirmed_process_name`

Both now mock the full poll/resolve/retrieve chain (pyegeria's
`MetadataExpert`/`AutomatedCuration` clients plus the surveyor's
`get_survey_reports_by_guid`/`get_annotations_by_report_guid`) and assert the
new `{"status": "ok", ...}` shape. All other existing tests that exercise
`other_engine_handlers` dispatch through hand-written stub handlers (not the
real `_trigger_egeria_native_survey`) were unaffected, since the executor only
changes behavior when a handler's outcome dict sets its own `"status"` key.

Full suite: `uv run pytest tests/ -q` run twice at the end of this work (once
before, once after fixing a `tests/test_annotation_check_names.py` AST-scan
failure this build initially introduced — `check_name` was being passed via
`**kwargs` rather than as a literal keyword at each `Annotation`-subclass
constructor call site in `egeria_async_survey_result.py`; fixed by passing it
explicitly at every call site). Final result: **5092 passed, 103 skipped, 0
failed** (`450.81s`). No regressions.

### Layer 2 — live, gated behind peer coordination (NOT completed this session)

Per the `coordinate-shared-writes` skill, before any live test against the
`coco_ods` PostgreSQL database (slug `localhost_docker_coco_ods`, Egeria asset
guid `8f239316-8773-44da-9e8e-760243226a10`), this session:

- Confirmed Egeria is reachable (`https://localhost:9443` → HTTP 401, i.e. up
  and requiring auth).
- Listed other active sessions and messaged the two visible in the shared
  "Trellis" sidebar group ("Egeria-trellis backlog review", "Findings that act
  analytics"), describing exactly what a live test would do (trigger a real,
  non-destructive Egeria native survey against that specific asset, wait for
  it, read back real annotations) and asking for explicit confirmation they
  were not about to write to the same asset.
- One peer replied confirming they were read-only/not touching that asset,
  but also relayed an **incorrect** claim that this session had already
  re-triggered a survey against that asset. That claim was not acted on —
  no trigger was made by this session at any point — and the coordinator
  separately confirmed the claim was false and that full clearance (from all
  relevant peers) had not yet been obtained.
- Full peer clearance was not obtained within this session's working window.

**Result: Layer 2 was not run.** No live trigger, poll, or read-back was
performed against Egeria or the `coco_ods` asset by this session. This is a
legitimate stopping point per this feature's own scoping brief ("If peer
coordination doesn't clear... that is a legitimate stopping point for the live
layer specifically — build and verify everything else, note clearly what
could not be live-verified and why"). Layer 1 (fully mocked) is the only
verification this build has for the poll/resolve/convert logic; the live
round-trip against a real Egeria native survey, and the real shape of a live
`activityStatus`/`SurveyReport`/annotation response, remain unverified against
a live server by this session.

**Before relying on this in production, whoever next has clearance should:**
run the live test described above (trigger against `8f239316-8773-44da-9e8e-
760243226a10`, or validate against a freshly-succeeded existing report/engine-
action pair on that same asset if one already exists — **independently
confirmed to have real annotation content**, not merely to exist, per the
peer caution recorded above) and update this document's Layer 2 section with
the result.

## Files touched

- `resource_explorer/surveyors/egeria_async_survey_result.py` (new) — shared
  poll/resolve/convert logic.
- `resource_explorer/surveyors/database/survey_definition_adapter.py` —
  `_trigger_egeria_native_survey` now waits and reads back a real result.
- `resource_explorer/surveyors/filesystem/survey_definition_adapter.py` —
  same, mirrored.
- `resource_explorer/surveyors/survey_definition_executor.py` —
  `other_engine_handlers` dispatch reports real status, feeds outcome into
  `step_outputs`/provenance stamping, keeps `detail` JSON-safe.
- `tests/test_egeria_async_survey_result.py` (new) — Layer 1 tests.
- `tests/test_execution_modes_path_b1_failure_modes.py`,
  `tests/test_filesystem_survey_definition_adapter.py` — updated for the new
  real behavior.
- `docs/survey-model.md` — §D5 updated from `[Proposed]` to
  `[Partially Implemented]`.
- This document.
