# Phases 1–3 implemented — execution-modes verification

**Status:** done, 2026-09-18. Implements phases 1–3 of
`PLAN-EXECUTION-MODES-VERIFICATION.md`'s §4 table, on branch
`re/execution-modes-verification-phases-1-3`. Phase 0 (the two Backlog
corrections) was already folded into the Backlog entry and the plan itself
before this work started, so it needed no further action here. Phases 4+
(live-Egeria harness, Path B2 live trigger/poll/attribute, Path A/C live
variants, the `egeria-hybrid` fold-in, Path B3) are explicitly **not**
attempted — they need a live dev Egeria and project-owner coordination on
shared writes, both out of scope for this pass.

## What was built

Three new test files, 17 tests total, all passing, no existing test
regressed:

### `tests/test_execution_modes_path_c_hybrid.py` (Phase 1)

Characterisation tests over `HybridDatabaseSurveyor.survey()`
(`surveyors/database/hybrid_database_surveyor.py`) and
`run_hybrid_filesystem_survey()`
(`surveyors/filesystem/hybrid_filesystem_surveyor.py`), mocking only
`_check_egeria_available`/the Egeria-side surveyor and letting the real
strategy logic run:

- Egeria unreachable → `source: "custom"`.
- Reachable, `refresh=False`, existing survey found → the cached Egeria
  report is returned via `_retrieve_egeria_survey`, and neither
  `DatabaseSurveyor` nor `publish_local_survey`/`catalog_and_survey` is ever
  called — the actual "no survey runs" assertion, not just a status check.
- `refresh=True` with credentials → local scan runs first, then
  `publish_local_survey`, `source: "egeria-custom"`.
- `publish_local_survey` raises → falls back to `source: "custom"` with the
  local result intact and the Egeria failure appended to `errors`, not
  swallowed and not replacing the local data.
- No credentials, Egeria unreachable → explicit `source: "error"`, never an
  empty success. (Added, beyond the plan's list: unregistered `db_slug`
  produces the same `error` shape via a different branch — the other place
  `survey()` can fail before strategy selection even starts.)
- Filesystem hybrid: the local survey is saved with `source="local"` via
  `registry.add_filesystem_survey` **before** any Egeria call — asserted via
  an explicit call-order list, not just "both were called". A publish
  failure calls `registry.update_filesystem_status` with the error message
  and leaves the already-returned `survey_data` intact (does not lose the
  survey). A filesystem with no Egeria credentials and no `force_egeria_publish`
  never even constructs `EgeriaFileSystemSurveyor`.

### `tests/test_execution_modes_path_b1_failure_modes.py` (Phase 2)

The three failure modes at `survey_definition_executor.py:473-514`, tested
against the **real** `database` and `repo` adapters, not only test doubles:

- `_trigger_egeria_native_survey` (`database/survey_definition_adapter.py`)
  raises `RuntimeError` with "no stored Egeria asset guid" for an
  uncatalogued `DatabaseEntity` — tested both directly (unit) and through
  the full `SurveyDefinitionExecutor.run()` (the raise becomes a reported
  `"error"` step status and an `errors` entry, not a crash out of `run()`).
  A same-shape happy-path test (guid present, `trigger_survey_by_guid`
  called) is included as the negative control.
- The real `repo` adapter is confirmed to register no
  `other_engine_handlers["egeria"]` at all (the live fact behind Backlog's
  "repos have no Egeria-coordinated path"), and a repo Survey Definition
  step tagged `executes_at="egeria"` is confirmed to produce
  `not_executed_no_egeria_handler` **and** an `errors` entry — not silently
  skipped.
- An unrecognized `executes_at` value (`"airflow"`) on a fake adapter
  produces `unrecognized_engine` and an `errors` entry.

### `tests/test_execution_modes_path_a_end_to_end.py` (Phase 3)

A fixtured Survey Definition (`SurveyDefinition`/`SurveyStep`/`StepLink`, the
same dataclasses `SurveyDefinitionReader` builds), run through the real
`SurveyDefinitionExecutor` against a real SQLite-backed `ProjectRegistry`
(`tmp_path`-scoped) and a real small repo fixture on disk (an actual
directory with two `.py` files, a `README.md`, no `SECURITY.md`, `git init`'d
for realism), publishing off:

- Three steps: two consecutive, `run_batch`-eligible, "resource-explorer"
  steps (`fixture_inventory` → `fixture_classification`, unconditional link)
  feeding a third (`fixture_security`) gated by a real, non-`"Any"` guard
  (`has_python`) on the `fixture_classification → fixture_security` link.
- Asserts every step lands in `steps_report` with status `"ok"`.
- Asserts annotations are genuinely written to each step's own table via
  `registry.query_findings(slug, kind)` reads (`fixture_inventory`,
  `fixture_classification`, `fixture_security`) — not a mock assertion.
- Asserts `_stamp_definition_provenance` gave every keyless annotation the
  definition's own `qualified_name` as `item_key`, by holding references to
  the actual `Annotation` objects the step runners created and reading their
  `item_key` back after the run (the executor mutates them in place; the
  result dict alone doesn't expose them).
- Asserts `survey_report.assert_unique_qualified_names` does not raise on
  the resulting annotation set.
- Asserts publishing is off by default for a fresh `Project` with no
  assigned Egeria project context: `adapter.publish` is never called,
  `result["published"] is False`, `result["egeria_report_guid"] == ""`.
- A second test is a negative control for the guard: run the same
  definition against an empty fixture repo (no `.py` files) and confirm the
  guard actually gates execution — `fixture_security` is `skipped_by_design`
  and writes nothing — proving the happy-path guard assertion wasn't
  vacuously true.

## Deviations from the plan, found once the real code was read

- **§2 Path C's bullet list undersold what's easy to get wrong about the
  "reachable, no refresh" branch.** The plan says "the cached Egeria report
  is returned and no survey runs" — true, but the interesting failure mode
  worth pinning explicitly (added here, not in the plan's own bullet list)
  is that `_check_egeria_available()`'s cached `True` plus `refresh=False`
  must reach `_retrieve_egeria_survey` **without ever instantiating
  `DatabaseSurveyor`** — a regression here (e.g. a future refactor that
  moves the local-scan call earlier) would look identical from the outside
  (same `source: "egeria"`) unless the mock's "was I even constructed"
  assertion is explicit. Added `mock_local_surveyor_cls.assert_not_called()`
  for exactly that reason.
- **The plan's Phase 2 framing ("In survey_definition_executor.py, test the
  three failure modes") undersells where the first failure mode's message
  actually lives.** "No stored Egeria asset guid" is raised by
  `_trigger_egeria_native_survey` in
  `database/survey_definition_adapter.py`, not in
  `survey_definition_executor.py` itself — the executor only catches
  whatever the handler raises. Tested both the raise itself (against the
  real adapter function) and the executor's handling of it, rather than
  only the latter, since a test that patches the message out entirely could
  still pass the executor-level assertion for the wrong reason.
- **The repo adapter's `other_engine_handlers` gap is provably real, not
  assumed.** Rather than trust the plan's or Backlog's prose claim that
  "repos have no Egeria-coordinated path", `test_repo_adapter_has_no_egeria_
  handler_registered` asserts it directly against the imported, registered
  `ResourceTypeAdapter` for `entity_type="repo"`. If a future change adds a
  repo `other_engine_handlers["egeria"]` (Phase 8/B3 in the plan), this test
  fails loudly and by name, rather than the "not_executed" test simply
  starting to fail for a confusing reason.
- **Phase 3's fixture uses a synthetic `entity_type="fixture_repo"` adapter,
  not the real `repo` adapter.** The plan's own §2 Path A description says
  "a small real repo/filesystem fixture" without mandating the real `repo`
  `ResourceTypeAdapter` specifically, and the real one
  (`repo_survey_definition_adapter.py`, ~5,000 lines) wires `re_analysis_steps`
  through `SurveyOrchestrator` → `GitHubClient.zipball_root()`/`git_clone_root()`,
  which require network access (a real GitHub repo) even for steps whose own
  logic needs no network at all. Driving that live end to end from a fixture
  directory would mean mocking `GitHubClient` deeply enough that the "real
  filesystem work" the plan wants to see exercised would be mocked away
  too — the opposite of the point. The synthetic adapter here mirrors the
  real one's *shape* exactly (a `run_batch` grouping two consecutive steps,
  guard-gated dispatch, `_stamp_definition_provenance`, `query_findings`
  persistence, the publish gate) using real filesystem I/O against a real
  directory, so the dispatch/persistence/provenance contract under test is
  the real production code path (`survey_definition_executor.py` unmodified)
  — only the resource-type-specific surveyor bodies are fixtures, exactly as
  the existing `test_survey_definition_executor.py` file already does
  throughout. A live run against the real `repo` adapter and a real GitHub
  fixture repo is exactly what the plan defers to Phase 6 ("Path A/C live
  variants").
- **`ResourceMeasureAnnotation`/`ClassificationAnnotation` collision-guard
  behaviour required picking distinct `check_name`s deliberately.** An
  initial draft gave two fixture steps the same `check_name` to more closely
  mirror the real-world collision this repo has hit before
  (`repo_secret_scan`'s `scan_summary`, see
  `test_annotations_from_two_definitions_sharing_a_step_get_distinct_
  provenance` in `test_survey_definition_executor.py`) — but because
  `_stamp_definition_provenance` stamps *every* keyless annotation from a
  single run with the *same* `item_key` (the one Survey Definition's own
  qualified_name), two same-`check_name` annotations from **one** run would
  still collide (`disambiguate_shared_check_names` only helps when
  `item_key` is still empty at that point, which it isn't after stamping).
  That distinct mechanism — provenance stamping disambiguates ACROSS
  definitions sharing a step, not WITHIN one run emitting the same check
  twice — is already covered by the existing test named above, so Phase 3's
  fixture uses distinct `check_name`s per step (as any real, correctly
  written surveyor does) and its "no collision" assertion is the intended
  sanity check the plan actually asked for, not a re-test of that other
  mechanism.

## Verification

```
uv run pytest tests/test_execution_modes_path_c_hybrid.py \
               tests/test_execution_modes_path_b1_failure_modes.py \
               tests/test_execution_modes_path_a_end_to_end.py -v
```

17 passed. Full suite (`uv run pytest tests/ -q`) run afterward with no
regressions attributable to this change (see the branch's CI run / this
session's own full-suite pass for the complete count — pre-existing
`requires_pgvector`-marked and `live_egeria`-marked tests skip in this
environment exactly as they do for every other contributor, unrelated to
this change).
