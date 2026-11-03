# Audit: trellis on pyegeria 6.2.2

Branch `re/pyegeria-6.2.2`, from main `0a996a0b`, 2026-10-12. pyegeria 6.2.2 was released from
odpi/egeria-python #446 (`e66eefa7`) and #447 (`39b1ebed`). The release notes are `PYEGERIA_ISSUES.md` on that
repo's main. The lock moves `pyegeria 6.2.1 -> 6.2.2` and nothing else.

The AST diff of every class method's signature between the 6.2.1 and 6.2.2 wheels shows two groups of changes:
- ISSUE-135: the first argument of the `*_by_name` wrappers now defaults to `None`.
- ISSUE-136: the default type of the three note reads changed from `"Action"` to `"Note"`.

No other method signature changed. In particular `get_related_metadata_elements` and its two siblings keep
their parameters. What changed there is what they send (section 2).

## 1. ISSUE-134: `@dynamic_catch` re-raises

### How the flag is read (6.2.2 source)

- `pyegeria/core/utils.py:462` `dynamic_catch(func)`. When `app_settings.Debug.enable_logger_catch` is true,
  it returns `logger.catch(reraise=True)(func)`. Otherwise it returns `func` unwrapped. The decision is made
  once, at import time, when each decorated class body runs.
- `pyegeria/core/config.py:150`. `DebugConfig.enable_logger_catch` defaults to `False`.
- `config.py:315` overlays the OS environment variable `PYEGERIA_ENABLE_LOGGER_CATCH` on the value from the
  config file. The variable goes through `_parse_bool_env`, which reads `os.getenv` only.
- The config file is the first of these that exists:
  1. `$PYEGERIA_CONFIG_DIRECTORY/<file>`
  2. `$PYEGERIA_ROOT_PATH/<file>`
  3. `./config.json` in the process cwd (`config.py:239`)

### Does RE's runtime turn it on? No.

- No trellis code, config, `.env.example` or start script sets it. `grep -ri logger_catch` over the
  worktree, excluding `.venv` and the RAG corpus, finds only a docstring in `zone_access.py:413`.
  `~/.re-gate/start-8813.sh` and `~/.re-gate/scripts/` do not mention it.
- RE's `.env` was not opened. It was loaded the way 8813 loads it: `dotenv -f
  packages/resource-explorer/.env run --`, with the cwd set to the 8813 worktree. Under it, a probe
  printed only the facts below:
  - `PYEGERIA_ENABLE_LOGGER_CATCH` is unset.
  - `PYEGERIA_CONFIG_DIRECTORY`, `PYEGERIA_ROOT_PATH` and `PYEGERIA_CONFIG_FILE` are unset.
  - No config file is found. There is no `config.json` in the 8813 worktree root.
  - The resolved `settings.Debug.enable_logger_catch` is `False`.
  - `MetadataExpert.get_related_metadata_elements` has no `__wrapped__`, so it is not wrapped.

So, on RE's runtime, every pyegeria failure was already raised under 6.2.1, and still is. ISSUE-134 changes
RE's behaviour only for a process started with the flag on. Under 6.2.2 that process now raises too. The paths
below therefore matter for robustness, not for a live defect today.

### Is a None from pyegeria ever a real answer?

No, not for any read RE makes. Each one signals a miss with a sentinel string such as `"No elements found"`
or `"No element found"`, or with a raised not-found (404). Checked in the 6.2.2 source:
- `get_guid_for_name` (`_server_client.py` ~745-776)
- `get_metadata_element_by_guid` and `get_metadata_element_by_unique_name`
- `find_metadata_elements_with_string`
- `get_all_related_elements`
- `get_related_metadata_elements` (through `process_related_element_list`)

So under 6.2.2 a None can no longer come from a failure, whatever the flag says. Wherever RE reads None
as "absent", it is reading an answer that cannot arrive. The rule applied below: **None is unreadable,
never absent.** The cheap, central places are changed to say so. Everywhere else, the "does not depend
on the None" argument above is the answer.

### The paths, and what was done

A read-only sweep of `resource_explorer/` found about 30 places that map a falsy pyegeria result to "none
there". All line numbers are on `0a996a0b`. The places changed in this branch:

| Path | pyegeria call | Before (None meant) | Consequence | Change |
|---|---|---|---|---|
| `catalogue_gateway.py:441,451,468` `parse_element_answer` / `parse_elements_answer` / `parse_related_answer` | `get_metadata_element_by_unique_name`, `find_metadata_elements_with_string`, `get_all_related_elements`, `get_asset_by_guid` | element absent / `[]` | **write**: `catalogue_commit` creates a schema element (:1266), records a leave-out as "absent" (:1393/1415), proves a delete "gone" (:1437-1460), lets a delete through because "nothing hangs off" (:374, :1232, :1480) | a new `_no_answer()` raises `GatewayError("... unreadable, not absent")` on None; the `"No elements found"` string is still a real miss |
| `egeria_resync.py:219-235` `_resolves` | `get_element_by_guid`, `get_project_by_guid`, `get_collection_by_guid` | `bool(None)` = False, so "gone" | **write**: flags publishes and dbfs assets stale (:383, :437, :1191), clears Project / working-set / context pointers (:500, :508, :538) | None now returns None ("could not determine"), which is the docstring's own third value |
| `egeria_resync.py:295-299` `_scan_assets` | `find_assets` | not live, so stale | **write**: `clear_stale_assets` clears `egeria_asset_guid` | None goes to `undetermined` and nothing is cleared |
| `surveyors/egeria_publisher.py:1752` `_find_element_guid` (also injected into the outbox and work lists) | `get_guid_for_name` | `""`, so "create it" | **write**: create (:1141, :1574, outbox :343) | None raises. The outbox's pre-create lookup already catches a raised lookup and goes on to create, with Egeria's duplicate-name refusal as the backstop; that behaviour is unchanged |
| `surveyors/database/egeria_database_surveyor.py:965` `_guid_from_lookup` | `get_guid_for_name` | `""`, so "create it" | **write**: secrets-store, connector type, endpoint and connection creates (:297, :316-343); server and database from templates (:514) | None raises. `_find_element_guid` (:913) turns it into its existing logged "lookup failed, treating as absent" warning, and `_find_by_qualified_name` (:973) propagates it |
| `egeria_linkage.py:391` `recheck_all_linkages` | `get_metadata_element_by_guid` | `not element`, so a synthetic 404, so stale | **write**: `record_linkage_verdict(False)` and an RFA | None now raises a plain `RuntimeError`, which `is_unknown_guid_error` does not match, so it counts as an error and never as stale |
| `egeria_outbox.py:396-402` adoption after a duplicate-name rejection | the injected `find_element_guid` | a lookup that raised became `""`, then "cannot be found by that name" | the row burns attempts toward `dead` with words that claim an inconsistency | a lookup that raises now gives its own `OutboxApplyError` ("the look-up for it then failed ..."), kept distinct from "Egeria will not create it and we cannot find it" |
| `native_survey_run.py:396` `report_guid_for_action` | `get_related_metadata_elements` (ReportOriginator) | "no report yet" | the run lingers as awaiting | None raises `NativeSurveyError` |

These places were not changed. Each one does not depend on the None under 6.2.2, for the reasons given
under "Is a None from pyegeria ever a real answer?":

- **Creates guarded by a by-name miss.** Each uses `get_guid_for_name` or a `find_*` call, where a miss is a
  string. A None could arrive only from a swallowed failure, and that no longer exists.
  - `surveyors/filesystem/egeria_filesystem_surveyor.py:620`
  - `arch_recovery/materializer.py:158`
  - `arch_recovery/port_materializer.py:164`
  - `arch_recovery/blueprint_materializer.py:215`, `:292-335` (`_find_blueprint`) and `:892` (`find_content_pack_component`)
  - `surveyors/database/egeria_reference_catalog.py:575`
  - `surveyors/database/bootstrap_data_classes.py:98,140,172`
  - `bootstrap.py:406` (`canary_present`)
  - `doc_source_egeria.py:221` (`_find_ref_guid`)
  - `curation_egeria.py:607` (`_elements`, which also feeds the tag and folio detach)
  - `feedback_egeria.py:397` (`_elements`, which feeds `find_note_log` and `find_note`)
  - `surveyors/egeria_publisher.py:739-759` and `:811-835`
- **Display-only or wrong-words paths.**
  - `doc_source_egeria.py:383` (`read_back_doc_sources`, documented as a display fallback)
  - `surveyors/egeria_survey_reader.py:50,95`
  - `surveyors/egeria_reader.py:94-190`
  - `egeria_database_surveyor.py:990-1070`
  - `survey_definition_reader.py:429,536,1048`
  - `web/routes/egeria.py:362`
  - `egeria_identity.py` `current_zones`
  - `reachability.py:261`
- **Already strict before this branch.** These route through `egeria_absence.is_absent` and treat None as
  unreadable:
  - `egeria_actors.py`
  - `native_survey_run.asset_exists` / `read_action` / `read_report`
  - `catalog_and_survey.find_element` / `read_element`
  - `blueprint_materializer._child_guids` / `_composition_children`
  - `zone_access`
  - `egeria_identity.read_zones`
  - `feedback_egeria.create_rating:441`
  - `egeria_resync._scan_unlinked_members`

Not in scope here, and now fixed by `re/lookup-failure-never-creates`. Several paths treated a raised
lookup, not a None, as "absent, so create". That is the absence-as-answer shape, and it is independent of
ISSUE-134; Egeria's duplicate-qualifiedName refusal was the only thing stopping a duplicate. On that branch
a lookup that raised (other than a typed not-found, per `egeria_absence.is_absent`) or gave no answer is
"could not check", nothing is created, and the caller says so in its own words
(`egeria_absence.lookup_could_not_be_made`). The paths:
- `egeria_database_surveyor._find_element_guid`, which used to log "treating as absent". It now raises
  `EgeriaDatabaseSurveyorError`. The report's 409-then-adopt path says the lookup failed. Column lineage
  skips that one mapping and records it.
- `egeria_filesystem_surveyor._find_element_guid`. It also read a `_UUID_RE` the class never defined, so a
  FOUND element was re-created as well.
- `doc_source_egeria.ref_guid_exists` and `_find_ref_guid`, which now raise `DocSourceLookupError`
  (`publish_doc_source` returns `ok: False`).
- the outbox's pre-create lookup, which now raises `OutboxApplyError`, an ordinary retried attempt. No
  destructive kind reaches that lookup.
- `egeria_reference_catalog._find_existing`. It is now typed (`DataClass` / `ValidValueDefinition`) and
  raises `ProposalLookupError`. A member that could not be checked is listed in `members_not_checked`.
- found in the same sweep: the shared annotation loop (`annotation_props.publish_annotations`), the repo
  publisher's sub-resource loop (`sub_resources_not_checked`), and `BlueprintMaterializer.find_content_pack_component`.
  A failed content-pack search there let RE create its own component beside the pack's under another
  qualifiedName, which the duplicate refusal cannot catch.

The tests are in `tests/test_lookup_failure_never_creates.py`. The arch-recovery `_find_element_guid`
copies never caught a raised lookup, so they were not changed.

Round 2 (review of 7a565568, plus two live read-only probes; tests in `tests/test_lookup_failure_round2.py`):
- `egeria_absence`: a typed 404 whose Egeria message id is `OMAG-MULTI-TENANT-404-*` is unreadable, never
  absent. A view server that is not running or not known answers every look-up that way (probe,
  2026-10-13). The message id is read from pyegeria's fields, not from text. "No element found" (singular)
  is a miss too.
- `catalogue_gateway.read_element` decided absence from exception text. A timeout naming "404" let an
  attach create and a leave-out write a "gone" proof. It now goes through `is_absent`.
- `egeria_identity._existing_guid` re-raises a look-up that could not be made, so `ensure_draft_zone_exists`
  reports an error and creates no zone.
- File-system catalog: one file that could not be checked is skipped and listed (`files_not_checked`).
  The run is not ended.
- Counts and not-checked lists now reach the result:
  - sub-resource proofs say "could not check, not created";
  - `lineage_not_checked` is in both database publish results;
  - annotation counts are what Egeria returned a GUID for, on the file-system catalog, the database step
    publish and the step words.
- Outbox: a 403 on the look-up is dead at once with the security words, and a 401 keeps the
  service-identity note. `is_security_refusal` and `_describe_refusal` read the look-up's cause
  (`OutboxLookupFailed`).
- Odd answers ({}, a non-list) are unreadable in `find_content_pack_component` and `ref_guid_exists`.
- Proposed reference sets are created as plain `ValidValueDefinition`. Egeria 6.2 does not recognise
  `ValidValueSet` (OMAG-COMMON-400-018), so the old `typeName: "ValidValueSet"` create failed on 6.2.

Round 3 (re-review of 4917afe4; tests in `tests/test_lookup_failure_round3.py` and
`frontend-build/test-harness/fs-publish-partial.test.mjs`):
- One shared reader, `egeria_absence.guid_from_answer`, for a by-name answer before a create. It returns a
  GUID, or "" for pyegeria's miss sentence. Anything else raises in the caller's vocabulary. Odd answers no
  longer read as "", in the identity, file-system, publisher, database and reference-catalog look-ups.
- `native_survey_run._is_not_found` uses `lookup_exception_is_absent`, so a view server that is down
  (OMAG-MULTI-TENANT-404) makes a pointer "unreadable", not "gone".
- A fresh report holding fewer annotations than were built is "failed", with "k of n annotations
  published" and a failure row, not a success proof.
- A file-system publish or hybrid survey that skipped files is `status: "partial"` and says "N file(s) not
  checked, not created" on the toast. `lineage_not_checked` is in the database publish step-run metrics.
- A sub-resource under a folder that could not be checked is itself "not checked", never "Egeria returned
  no GUID".
- The outbox reads an exception's cause for a refusal only when the cause is pyegeria's, or when the
  exception is the outbox's own look-up failure.
- `annotation_count` is RE's own count everywhere, and `annotations_in_egeria` is Egeria's.

## 2. ISSUE-129: relationship queries now send their arguments

### What 6.2.2 sends

`MetadataExpert._async_get_related_metadata_elements` (`metadata_expert.py:2901`):
- It puts `?startingAtEnd={starting_at_end}` on the URL.
- It merges `startFrom`, `pageSize`, `forLineage` and `forDuplicateProcessing` into the body through
  `_with_query_options` (`metadata_expert.py:30`).
- That helper adds only fields the body does not already set, and skips `None` values.
- `page_size` defaults to `max_paging_size`, which is 500 unless `egeria_max_page_size` is configured.
- `for_lineage` and `for_duplicate_processing` default to `None`, so they are still not sent unless the caller
  passes them.

So, for a caller that passes neither, the only new things on the wire are:
- `startFrom: 0` and `pageSize: 500` in the body. Before, the body had no paging, so the server's default
  page size applied.
- the `startingAtEnd` query parameter. Before, the server's default of 0, "either end", applied.

The two siblings, `get_all_metadata_element_relationships` and `get_metadata_element_relationships`, now put
paging in the body. Their `starting_at_end` argument is accepted and ignored.

### What `startingAtEnd` means (Egeria source)

In `MetadataElementHandler.getRelatedMetadataElements` (`omf-metadata-server .../handlers/MetadataElementHandler.java:478-485`):
- `startingAtEnd == 1` selects `selectionEnd = 2`. The starting element is at end 1, and the elements
  returned are at end 2.
- `startingAtEnd == 2` selects end 1.
- `0` means either end.

This matches the MetadataExpert javadoc at `MetadataExpertResource.java:774`. It also matches pyegeria's live
check under ISSUE-129: on a note log at end 1, `starting_at_end=2` returned 0.

### The call sites

| Site | Relationship and ends (Egeria type) | Written while the argument was ignored? | Now | Verdict |
|---|---|---|---|---|
| `native_survey_run.py:396` `report_guid_for_action` | `ReportOriginator` (`OpenMetadataTypesArchive1_2.java:5319`): end1 `reportOriginator` (Referenceable), end2 `generatedReports` (Report) | yes; `starting_at_end=0` was dropped, and the server default was also 0 | `?startingAtEnd=0` and `pageSize: 500` | Correct. The engine action can only be at end 1, because end 2 must be a Report, so "either end" returns exactly its reports. Nothing to fix. More than one report still raises rather than choosing. |
| `catalog_and_survey.py:373` `read_process` | `ActionRequester` (`1_2.java:861`; re-parented under `Lineage` in `OpenMetadataTypesArchive.java:234`): end1 `actionSources` (Referenceable), end2 `actions` (Action) | yes; `starting_at_end=1` was dropped, so the server returned both ends | `?startingAtEnd=1`: the process instance is end 1, and the engine actions it requested (end 2) come back | The direction is right, and it is now enforced. Before, a requester of the process instance (at end 2) could also have come back as `guids[0]`. Not a page loop; it takes the first engine action. No change. |
| `doc_source_egeria.py:397` `read_back_doc_sources` | `ExternalReferenceLink` (`1_2.java:1162`): end1 `consumingItems` (Referenceable), end2 `externalReference` (ExternalReference) | body only, so no argument was dropped except paging | `?startingAtEnd=0` and `pageSize: 500` | Correct. The asset is end 1, and either end returns its references. An asset with more than 500 references would be cut off silently, which is acceptable for a display read. No change. |
| `arch_recovery/blueprint_materializer.py:962-970` `_composition_children` | `SolutionComposition` (`1_7.java:824`): end1 `usedInSolutionComponents` (parent), end2 `nestedSolutionComponents` (child) | yes; paging was put in the body because the arguments were dropped | the body's `startFrom: 0, pageSize: 1000` win (`_with_query_options` never overrides), and `?startingAtEnd=0` | One page, no loop, so nothing can be skipped or duplicated. A full page reads as "could not tell". The `elementAtEnd1` filter keeps the children. The stale comment is corrected; there is no behaviour change. |

### Loops, siblings, other packages

- **Page loops.** None of the four sites pages. None follows, or needs, the rule that only an empty page ends
  a loop. The one full-page guard, in the blueprint read, compares against the page size it asked for in the
  body (1000), which is what Egeria now receives.
- **The two siblings.** Neither is called anywhere in trellis. The `grep` covered `packages/*`, excluding
  `data/repos`.
- **Other packages.** egeria-advisor and trellis-analytics do not call any of the three methods.

## 3. Notes for the branch

- **136 (the note reads now default to `Note`).** RE has one note reader:
  `feedback_egeria.py:494` (`find_note`). It already passes `metadata_element_type_name="Note"`, so its
  request is byte-identical on 6.2.2. RE never calls `get_note_by_guid` or `find_notes`. Note creation
  (`create_note`) is unchanged in 6.2.2. Brief F's publish passes the type explicitly. Nothing to do.
- **114 (`pyegeria.guid_or_none`).** It has the same semantics as RE's
  `surveyors/survey_definition_reader._as_guid`: a str, stripped, non-empty, with no whitespace. There are
  7 call sites. They are left alone; replacing them would be churn without a behaviour change.
- **132/127 (cascade delete still never cascades).** Nothing in RE relies on a cascade.
  - Every RE delete passes `cascade_delete=False` explicitly: `catalogue_gateway.py:887`, with its
    BEFORE/AFTER log flags at `catalogue_commit.py:1129`. These are pinned by
    `test_catalogue_commit.py:836,845` and `test_destructive_calls_logged.py`.
  - `grep` finds no `cascade=True`, `cascadeDelete` or `cascadedDelete` in `resource_explorer/`,
    trellis-analytics or egeria-advisor.
  - The one `cascade_delete=True` is `scripts/arch-spike/measure_wire_multi_link.py:151`. It is a throwaway
    spike's cleanup of the two components it created. Under ISSUE-132 the flag does not reach Egeria. The
    script is not on any runtime path.
- **137/141 (the trellis-analytics tile).** Both workarounds are gone.
  - `trellis_analytics/portal.py` builds the FormatSet with `ActionParameter(analytic_function=...)` and no
    `function`.
  - `tests/test_portal_tile.py` no longer fakes an `EgeriaTech`. It installs one whose constructor fails the
    test. That makes the test stronger: it proves that `exec_report_spec` builds no client for this routine.
  - trellis-analytics now pins `pyegeria>=6.2.2`, because it depends on both fixes.
  - The Advisor follow-up is done, in `packages/egeria-advisor/advisor/report_spec_parser.py`.
    `parse_report_spec_markdown` leaves `function` unset for an analytic-only spec, which keeps
    `examples/curation_coverage_tile.md` equal to `report_specs()`. `validate_report_spec` returns no
    warning when `analytic_function` is set, and still warns "No Action Function defined." when neither
    is set. Both are pinned in `tests/unit/test_report_spec_parser.py`.
- **135 (released in 6.2.2 too; the `*_by_name` first argument is optional).**
  `tests/test_actor_graph.py::test_round4_the_fake_rejects_what_real_pyegeria_rejects` pinned 6.2.1's
  required `name`. The guard now reads the real signature: under 6.2.2 the fake accepts a body-only call,
  and it still rejects a call the real method rejects. RE's by-name searches pass the name positionally,
  so their requests are unchanged.

## 4. Live read-only probe (2026-10-12/13 UTC)

The probe was a scratchpad script, run in two venvs:
- this worktree's venv, on pyegeria 6.2.2
- the 8813 worktree's venv, on pyegeria 6.2.1, used only to run the same raw reads and nothing else

In both, `dotenv -f packages/resource-explorer/.env run --` supplied the environment, and the environment was
never printed. The clients came from RE's `egeria_client(Daemon(SCHEDULER))`, the identity the native-survey
sweep uses. Every call was a read. There were no Egeria, registry or DDL writes. The engine-action and
process GUIDs came from `step_runs` through a read-only psycopg2 session.

| Read | 6.2.1 | 6.2.2 | RE on 6.2.2 |
|---|---|---|---|
| `ReportOriginator` from coco_pharma `f22c59f0` (in progress) | `startingAtEnd` 0/1/2 each return report `3717ed40` (argument ignored) | 0 and 1 return `3717ed40`; 2 returns "No elements found" | `report_guid_for_action` gives `3717ed40-d64b-...`, status IN_PROGRESS |
| `ReportOriginator` from coco_pharma `3ecb5dba` (finished) | 0/1/2 each return `c9104585` | 0 and 1 return `c9104585`; 2 returns none | gives `c9104585-fdbf-...`, COMPLETED |
| `ReportOriginator` from AdventureWorks `2660bbe6` | 0/1/2 each return `44153eed` | 0 and 1 return `44153eed`; 2 returns none | gives `44153eed-6c2c-...`, COMPLETED |
| `ActionRequester` from Catalog-and-Survey process `11429026` (laz_local_egeria_observations) | 0/1/2 each return engine action `705230b4` | 0 and 1 return `705230b4`; 2 returns none | `read_process` gives COMPLETED (from the process instance's own status, so the ActionRequester branch was not needed); the raw read that branch makes (`starting_at_end=1`) returns the one engine action |
| `ActionRequester` from process `06994d5d` (laz_local_usda) | 0/1/2 each return `eda20b32` | 0 and 1 return `eda20b32`; 2 returns none | `read_process` gives COMPLETED |
| `SolutionComposition` on egeria_git's 15 components (`SolutionComponent::repo::egeria_git*`) | 7 have relationships: six are children (n=1, related element at end 1) and `5ba8f5a9` is the parent (n=6, all at end 2) | identical | `_composition_children`: `5ba8f5a9` has 6 children (known); the six children have 0 children (known); total 6 |

What the probe shows:
- 6.2.2 really does send `startingAtEnd`, and Egeria honours it with "the starting element is at end N".
- At the end RE asks for (0 for ReportOriginator, 1 for ActionRequester), 6.2.2 returns exactly what 6.2.1
  returned. No result RE reads changed.
- The blueprint read is unchanged: the body's paging wins, and the end-1 filter still separates the parent
  from its children.

One probe artefact, kept here so nobody chases it. A raw `MetadataExpert` client used after a `port_session`
had closed its event loop failed, with "bound to a different event loop" or a connect-level
`CLIENT_ERROR_400`. This is ISSUE-96 (a client is bound to the loop that first drives it). The probe was
reordered to do the raw reads first. RE builds one client per port, so it is not affected.
