# Tests fail fast without a reachable Egeria — implemented

**Coordinator brief:** a Backlog item this session wrote while investigating
Slice 21a's CI timeout (run 36290350357) — a cluster of ~29 tests sleeping
30-60s each when Egeria is unreachable, unrelated to 21a's own diff but a
latent risk for any branch's CI run. Own branch off main (NOT on top of
21a/21b), so it lands independently.
**PR:** #TBD (`re/tests-fail-fast-without-egeria`).

## Root cause

`pyegeria.core._base_server_client.BaseServerClient.__init__` calls
`self.check_connection()` synchronously — a real HTTP round trip against
`cfg.platform_url`, with a 30-second default timeout
(`settings.Debug.timeout_seconds or 30`) — on EVERY pyegeria client
construction (`ProjectManager`, `CollectionManager`, `MetadataExpert`,
`EgeriaTechTypeCatalog`, etc.).

The ~29 slow tests all inject a stub `project_manager`/`collection_manager`
at their own top-level call site (`EgeriaInvestigationPublisher(reg,
project_manager=_StubPM(), collection_manager=_StubCM())`), which correctly
prevents `_managers()`'s own client construction — but several call paths
construct their OWN pyegeria client directly, bypassing that injection
entirely:

- `egeria_investigation_publisher.py::_apply_investigation_marker` builds a
  fresh `MetadataExpert(cfg.view_server, cfg.platform_url, ...)` regardless
  of what `project_manager`/`collection_manager` the caller supplied — one
  unmocked construction, ~30s when Egeria is unreachable.
- The Curate materialization path (exercised by `test_curate_blueprints_
  route.py`, `test_web.py`'s Curate router tests, `test_cli_workflow_
  commands.py::TestCurateCommand`) constructs two such clients sequentially
  — ~60s.
- `dependency_support.py::egeria_technology_types_present` constructs an
  `EgeriaTechTypeCatalog` directly — ~30s — before its own three-state
  return value (`None, "unreachable", detail`) lets `TestAgainstLiveEgeria`
  skip.

None of the ~28 Investigation/Curate tests assert on that auxiliary
client's own success — they assert on registry state, stub call captures,
and error message text (confirmed by reading each one before applying the
fix; e.g. `test_the_chosen_classification_actually_reaches_egeria` only
reads `pm.calls[0][3]` and `res.classification_requested`). The 29th,
`TestAgainstLiveEgeria::test_every_linked_type_exists`, is different in
kind: its entire point IS a live Egeria round trip, so it gets the other
treatment (skip, not mock) — see below.

Confirmed via `pytest --timeout=5 --timeout-method=thread` (which prints a
stack trace at the moment it fires): the trace for `test_a_private_project_
that_cannot_be_zoned_is_reported_not_hidden` (a zoning test that already
injects `_StubPM()`/`_StubCM()`) stopped inside `_apply_investigation_
marker` → `MetadataExpert.__init__` → `BaseServerClient.__init__` →
`check_connection()` → `_async_check_connection()`, exactly confirming the
unmocked construction site and ruling out any involvement of Slice 21a's
own diff.

## Fix

**`tests/conftest.py`** gained `mock_egeria_client_connections` (a fixture,
not autouse globally): monkeypatches `BaseServerClient.check_connection` to
raise `PyegeriaConnectionException()` immediately, for the duration of a
test that requests it. This is the ONE shared base class every pyegeria
client inherits from, so it fixes every unmocked construction site at once
— including ones this investigation didn't individually trace — rather
than patching each call site (`_apply_investigation_marker`,
`EgeriaTechTypeCatalog`, the Curate materializer's client) separately.
Raises pyegeria's own real "could not connect" exception, which every
`resource_explorer` call site already catches via a broad `except
Exception` — the test still exercises the identical error-handling path a
real refused/unreachable connection would produce, just without the wait.

Applied via `pytestmark = pytest.mark.usefixtures(...)` (file-wide) to
`test_investigation_routes.py`, `test_investigation_reclassification.py`,
`test_curate_blueprints_route.py`, and via `@pytest.mark.usefixtures(...)`
(class-scoped, alongside existing `signed_in_curator`) to `test_web.py`'s
`TestCurateComponentVerdictsRouter`/`TestCurateBlueprintVerdictsRouter` and
`test_cli_workflow_commands.py::TestCurateCommand`.

**`test_dependency_support.py::TestAgainstLiveEgeria`** got the OTHER
treatment: `@pytest.mark.requires_egeria` — the pre-existing, already-fast
(~2s `httpx` probe, `conftest.py`'s `_egeria_reachable()`) reachability
auto-skip every other live-Egeria test in this suite uses. This test's own
purpose is a genuine live round trip against Egeria's technology-type
catalog; mocking the client boundary here would make it always skip and
never actually verify anything — a real loss of coverage, not a speedup.
`requires_egeria` gets it the fast skip when unreachable while keeping the
real check when Egeria IS up. Its own manual `pytest.skip()` (based on
`egeria_technology_types_present()`'s three-state return) stays as defense
in depth for a non-reachability failure (e.g. a bad credential).

Production code is untouched — the 30s retry/timeout in pyegeria's own
`BaseServerClient` stays exactly as it is; only test fixtures changed, per
the coordinator's own rule.

## Proof

Ran the affected test files twice, `--durations=40`, `EGERIA_PLATFORM_URL`
pointed at two different kinds of unreachable target each time — a
blackholed address (`https://192.0.2.1:9443`, RFC 5737 — connections there
hang/timeout, the condition that produced the original 30/60s durations)
and a refused one (`https://localhost:1` — nothing listens, fast
`ECONNREFUSED`, closer to what CI's own unreachable-Egeria condition
probably looks like). Top duration in both cases is under 1 second — well
under the 5s bar.

**Blackholed** (`tests/test_investigation_routes.py tests/test_
investigation_reclassification.py tests/test_curate_blueprints_route.py
tests/test_dependency_support.py`, top 10):

```
0.77s call     tests/test_investigation_routes.py::test_next_steps_retire_as_the_gaps_close
0.74s call     tests/test_investigation_routes.py::test_partly_judged_scope_is_not_reported_as_judged
0.72s call     tests/test_investigation_routes.py::test_only_a_linked_investigation_supplies_a_binding
0.68s call     tests/test_investigation_routes.py::test_list_investigations_default_view_includes_suspended_but_not_closed
0.67s call     tests/test_investigation_routes.py::test_membership_goes_through_a_working_set
0.66s call     tests/test_investigation_routes.py::test_complete_means_every_resource_judged
0.66s call     tests/test_investigation_routes.py::test_suspend_then_reopen_round_trips_with_everything_intact
0.63s call     tests/test_curate_blueprints_route.py::TestTheRoute::test_404_for_an_unknown_project
0.59s setup    tests/test_investigation_routes.py::test_purposes_are_served_not_hardcoded_in_the_spa
0.46s call     tests/test_investigation_routes.py::test_a_failed_adoption_check_is_reported_because_that_is_when_duplicates_happen
```
136 passed, 1 skipped, 51.82s total (was ~19 minutes for the slow subset
alone before this fix).

Plus, same run, the two Curate classes and CLI command
(`tests/test_web.py::TestCurateComponentVerdictsRouter tests/test_web.py::
TestCurateBlueprintVerdictsRouter tests/test_cli_workflow_commands.py::
TestCurateCommand`): 24 passed, 7.23s total, top duration 0.07s (call) /
0.21s (setup, pre-existing `signed_in_curator` overhead unrelated to this
fix).

**Refused** (`https://localhost:1`, same file set plus the two Curate
classes and CLI command in one run, top 10):

```
0.96s call     tests/test_investigation_routes.py::test_partly_judged_scope_is_not_reported_as_judged
0.88s call     tests/test_investigation_routes.py::test_next_steps_retire_as_the_gaps_close
0.72s call     tests/test_investigation_routes.py::test_list_investigations_default_view_includes_suspended_but_not_closed
0.71s call     tests/test_investigation_routes.py::test_only_a_linked_investigation_supplies_a_binding
0.71s call     tests/test_investigation_routes.py::test_membership_goes_through_a_working_set
0.68s call     tests/test_investigation_routes.py::test_suspend_then_reopen_round_trips_with_everything_intact
0.58s call     tests/test_investigation_routes.py::test_complete_means_every_resource_judged
0.51s call     tests/test_investigation_routes.py::test_dispositions_route_returns_what_the_filter_needs
0.48s setup    tests/test_investigation_routes.py::test_purposes_are_served_not_hardcoded_in_the_spa
0.48s call     tests/test_investigation_routes.py::test_suspended_still_inherits_egeria_project_but_closed_does_not
```
160 passed, 1 skipped, 57.11s total.

The 1 skip in both runs is `TestAgainstLiveEgeria::test_every_linked_type_
exists`, correctly auto-skipped by `requires_egeria` in both unreachable
conditions (and would run for real against a reachable Egeria — not
re-verified here since that's this test's pre-existing, unchanged
behavior).

## `pytest-timeout` installed the CLAUDE.md way

Installed via `uv sync --all-packages --extra dev` from the workspace root
(not a bare `uv sync`, and not scoped to just `packages/resource-explorer`)
— confirmed `pytest-timeout` present afterward
(`uv run python -c "import pytest_timeout"` no longer raises
`ModuleNotFoundError`). This is the same finding recorded in Slice 21b's
Backlog entry (`pytest-timeout is declared but was not installed in the
local dev venv`) — fixed here for this checkout's own venv per that entry's
own recommendation.

## Tests

No new test files — this branch changes test FIXTURES (a shared
conftest.py fixture, and `pytestmark`/class-decorator wiring in the six
already-existing files/classes named above), not test assertions or
production code. The proof runs above (136 + 24 = 160 tests, 1 skip, 0
failures, in both unreachable conditions) are the verification: every
existing assertion in every affected test still passes, now dramatically
faster.

Full suite: 6494 passed, 104 skipped, 0 failed.

## Not done here

- `determine_grain`/`check_conventions`'s per-table `keys_captured`
  awareness, and the general coverage-percentage threshold question — both
  Slice 21b Backlog items, unrelated to this branch.
- Any other unmocked pyegeria client construction site this investigation
  didn't individually trace is still covered by the SAME
  `mock_egeria_client_connections` fixture wherever it's applied (the
  shared-base-class patch point was chosen specifically so this isn't
  file-by-file whack-a-mole) — but a construction site in a file this
  branch didn't apply the fixture to would still be exposed to the same
  30/60s hang. If another slow-fail cluster turns up elsewhere, the fix is
  the same one-line `pytestmark`/decorator addition, not a new mechanism.

## Report

Branch `re/tests-fail-fast-without-egeria`, tip: see PR.
