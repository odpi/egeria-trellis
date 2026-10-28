# `is_published` honours linkage staleness — implemented

**Coordinator brief:** pulled forward from slice 18 (identity/read-back) —
raised as a Slice 12 finding (2026-09-26) during the database Survey
Definition gate, then split into its own branch per the coordinator's
ruling B.
**PR:** #TBD (`re/is-published-honours-linkage`).
**Replying to:** a Slice-12 diagnosis request — why `coco_pharma`'s header
read "surveyed just now · published to Egeria" while the Survey Definition
publish step failed with "Database 'coco_pharma' is not yet cataloged in
Egeria."

## The bug

Both statements were about the same database, and only one was current.
`registry.get_database('localhost_docker_coco_pharma').egeria_asset_guid`
was still set (a real GUID, cached from a prior publish), but
`registry.get_egeria_linkage('database', slug)` showed
`{'status': 'stale', 'stale_guid': '4dd8d5ee-...', 'detected_at':
'2026-09-22T00:30:50Z', 'detail': '...404 OMAG-REPOSITORY-HANDLER-404-007,
entity not found...'}` — RE had already detected and recorded, four days
earlier, that this specific cached GUID no longer resolves in Egeria
(almost certainly a platform reset). `databases.py`'s
`is_published=bool(getattr(db, "egeria_asset_guid", "") or "")` — and the
identical pattern in `filesystems.py` (2 call sites) and `projects.py`
(2 call sites) — never consulted that linkage record at all: a cached GUID
read as "published", full stop, regardless of whether RE itself had
already flagged it unreachable.

The mechanism to detect this already existed
(`resource_explorer/egeria_linkage.py`'s `note_divergence`/
`mark_egeria_linkage_stale`, and even a frontend rendering path for it —
`app.js`'s `resourceHeaderHtml` already had a `ov?.egeria_link_stale`
branch rendering "published, but the Egeria link is stale"). But that
frontend branch reads `state.overview`, populated only by
`GET /api/projects/{slug}/scouting-overview` — a REPO-ONLY endpoint
(`getScoutingOverview` in `re-api.js` always calls `/api/projects/...`
regardless of `state.resourceType`), fetched lazily and best-effort ("its
absence is survivable — the header renders without it"). So even for
repos the staleness caveat could silently not show if the fetch hadn't
landed yet or failed, and for databases and filesystems it could never
show at all — there is no equivalent overview endpoint for them.

## Fix

**`resource_explorer/egeria_linkage.py`** gained
`describe_publish_status(registry, entity_type, entity_slug, guid) ->
{"is_published": bool, "note": str}`:

- No GUID at all → `{"is_published": False, "note": ""}` (never published).
- GUID present, linkage not `stale` (including no linkage row at all —
  absence means healthy, the same convention
  `clear_egeria_linkage_status` already uses) →
  `{"is_published": True, "note": ""}`.
- GUID present, linkage `stale` →
  `{"is_published": False, "note": "published to Egeria · link stale
  since <date> — element not found"}`. The GUID history is itself
  information a plain "not published" would discard, so the note keeps
  the word "published" rather than flipping to a bare negative — the
  coordinator's own wording for the rule.

All 5 `is_published=bool(...)` call sites replaced with this helper, on the
already-loaded `registry` in each function:

- `databases.py`: `DatabaseSummary` gained `egeria_publish_note: str = ""`;
  `_to_summary`'s one construction site.
- `filesystems.py`: `FileSystemSummary` gained the same field; both
  `list_filesystems` and `get_filesystem`.
- `projects.py`: `ProjectSummary` (the list/summary row) gained the field;
  `_to_summary`'s one site (guarded for `registry=None`, an existing
  optional parameter — falls back to the plain GUID-only boolean with no
  note in that case, since there is no registry to check linkage against).
  `ScoutingOverview` (the repo detail page's separate endpoint) ALSO
  gained the field and now computes `is_published` the same way, since it
  had the identical bug independently of the summary row's.

**`app.js`**'s `resourceHeaderHtml`: `p?.egeria_publish_note` (the summary
row, present for every resource type, no separate fetch) is now the
primary source for a stale-link caveat, checked before the pre-existing
`ov?.egeria_link_stale` (repo's own scouting-overview, kept for its
`egeria_link_stale_guid` detail and left as the more specific signal for
repos when both happen to be present).

Not touched: the two worklist rows at lines ~1057/~2058 that read
`w.egeria_guid` directly (a different data shape entirely,
`state.workLists`, a corpus-level grouping rather than a per-resource
summary) — out of this branch's scope; flagged as a possible follow-up,
not the bug this branch was asked to fix.

## Tests

- `tests/test_describe_publish_status.py` (new, 5 tests): no GUID → never
  published; GUID + no linkage row → published, no note; GUID + stale
  linkage → not published, note names the exact date; a cleared
  (`clear_egeria_linkage_status`) linkage stops showing the note; a stale
  linkage under a DIFFERENT entity_type for the same slug doesn't leak
  into this one.
- `tests/test_next_resource_header_publish_note.py` (new, 3 tests,
  source-level per this codebase's established no-JS-runner pattern): the
  summary row's `egeria_publish_note` is read and rendered; the pre-existing
  repo-only `ov?.egeria_link_stale` branch is still present.
- Existing suites re-run clean: `test_api_structure_surveyor.py`,
  `test_arch_proposal.py`, `test_database_update_credentials_route.py`,
  `test_db_derived_step.py`, `test_next_sidebar_list_db_fs.py`,
  `test_no_confident_placeholders.py`, `test_scouting_overview_routes.py`,
  `test_facts.py`, `test_web.py` — 293 passed, 0 failed.
- First full suite (before the fix below): 6452 passed, 103 skipped,
  1 failed (`test_egeria_live_smoke.py::TestTheByNameFallbackWorks::
  test_a_cataloged_database_is_findable_by_name`) — see next section.
- Full suite after the fix below: 6452 passed, 104 skipped, 0 failed.

## A second, real defect: the by-name smoke test picks a database nobody ever checked

`test_a_cataloged_database_is_findable_by_name` had failed on every run for
days, excused each time as "pre-existing, hits the live platform." Diagnosed
from this branch's own live gate, and fixed here rather than on Slice 12
(where it was first found) per the coordinator's ruling — this branch is
what makes the resulting `stale` records user-visible, so it owns the fix.

This deployment has TWO databases with a cached `egeria_asset_guid`:
`localhost_docker_coco_pharma` (linkage correctly recorded `stale` since
2026-09-22) and `egeria_optional_prefect_db` (linkage: no row AT ALL — never
probed). The test's selection filter (`... .get("status") != "stale"`)
treats "never checked" the same as "confirmed healthy," so it picked the
second one — whose cached GUID `45a75724-...` also 404s when resolved
directly (confirmed live: `_get_element_by_guid_` raises
`PyegeriaNotFoundException`), just never detected, because nothing had
recently surveyed/published through this rarely-used database to trip the
reactive `guard_linkage` check.

The test's own skip message already said "run `resource-explorer
egeria-recheck`" first — it just never did so itself. Fixed by calling
`egeria_linkage.recheck_all_linkages(registry, entity_types=["database"])`
at the top of the test, wrapped in a try/except that skips (not errors) if
the registry can't be written or Egeria is unreachable — this test is about
the by-name mechanism, not connectivity. This WRITES to the shared registry
from inside a test, called out explicitly in the code comment: acceptable
only because it records what is actually true and this whole file already
talks to the live platform, not a pattern for an isolated/unit test.

Live result: both databases are now correctly recorded `stale` in the
shared dev registry, `cataloged` is empty, and the test SKIPS with its own
honest reason instead of failing on a stale cache — a genuinely broken
dev-platform state (both database links are dead), not a defect in the
by-name mechanism itself. `tests/test_egeria_live_smoke.py` re-run in full:
24 passed, 1 skipped.

## A third defect, found in review of the second: "correct number, wrong label"

The note above (`"link stale since <date>"`) read `detected_at` straight
off the linkage row — but `mark_egeria_linkage_stale`'s `ON CONFLICT`
clause overwrote `detected_at` on every call, including a `recheck_all_linkages`
call that only RE-CONFIRMED an already-known staleness. `coco_pharma`'s
linkage had genuinely been stale since 2026-09-22, but the smoke-test fix's
own `recheck_all_linkages` call re-confirmed it today and silently bumped
`detected_at` to today — so the just-fixed header would have read "link
stale since 2026-09-26" for a link four days older than that. Caught in
review before merge, not live.

**Fix:** `egeria_linkage_status` gained a `last_checked_at` column
(migrated via `ALTER TABLE ... ADD COLUMN` for existing databases, same
pattern every other column addition in `registry.py` uses).
`mark_egeria_linkage_stale`'s `ON CONFLICT` now preserves `detected_at`
when the row is ALREADY `stale` (`CASE WHEN ... status='stale' THEN
<old value> ELSE <new value> END`), and always advances `last_checked_at`
to now. `describe_publish_status`'s note now reads: `"published to Egeria
· link stale since <detected_at> · last checked <last_checked_at> —
element not found"` when the two dates differ, collapsing to just
`"... since <date> ..."` when a link has never been rechecked since first
detected (the common case). A recovery (`clear_egeria_linkage_status`)
deletes the row, clearing both dates together — there's nothing to
preserve once the divergence itself is resolved.

**Data-loss disclosure, as asked rather than silently corrected:** this
bug already ran live against the shared dev registry before it was found —
my own earlier diagnostic and recheck calls (documented above) overwrote
`coco_pharma`'s and `egeria_optional_prefect_db`'s real `detected_at` to
2026-09-26 before this fix existed. The original 2026-09-22 first-detection
date for `coco_pharma` is genuinely gone from that row; it is not
back-dated here, per instruction — the fix stops it from happening again,
but does not fabricate history the row no longer has. Both rows currently
read `detected_at: 2026-09-26T23:47:29...`, `last_checked_at: ''` (empty,
written by the pre-migration code path); the next recheck against either
will populate `last_checked_at` correctly and `detected_at` will finally
hold from that point on.

Tests: `TestRepeatedDetectionPreservesFirstDetectedAt` (new, 2 tests) —
two calls to `mark_egeria_linkage_stale` keep `detected_at` and advance
`last_checked_at`; a fresh detection after `clear_egeria_linkage_status`
gets a genuinely new `detected_at`. `test_not_published_and_note_names_the_date`
updated to set both dates explicitly (equal, the never-rechecked case);
new `test_note_names_both_dates_when_rechecked_after_first_detection`
pins the two-date wording. `test_egeria_linkage_divergence.py` (existing,
31 tests) re-run clean — unaffected by the new column.

Full suite after this fix: [pending — background run in progress at time
of writing].

## Not attempted here

- A database/filesystem equivalent of the repo `scouting-overview`
  endpoint (`last_published_at`, `homepage`, etc.) — this branch only
  closes the `is_published`/staleness gap, on the summary row every
  resource type already has, rather than building the larger detail
  endpoint repos have and the other two don't.
- The two worklist rows' own `w.egeria_guid` checks (see above).
