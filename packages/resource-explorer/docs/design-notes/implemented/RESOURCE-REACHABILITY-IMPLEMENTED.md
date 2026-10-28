# Resource reachability probe — implemented (Phase 1 slice #13)

**Status: built, 2026-09-22.** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`'s slice
#13 row: "Reachability probe (`finalAnalysisStep=CHECK_ASSET` via
`initiate_gov_action_type` directly), `resource_reachability` table,
launcher sentence in the classic UI | Sonnet | probe 7".

## The deferral, and its reversal

`egeria-support-for-multi-resource.md` §5/§10 filed this as:

> **Decision (project owner, 2026-09-21):** defer the `resource_reachability`
> table until further tests — do not build it yet.

**Decision (project owner, 2026-09-22): reversed the 2026-09-21 deferral and
asked for this slice to proceed.** The §10 filing table and §5 have been
updated in place with a dated correction that preserves the original
callout rather than overwriting it — see the diff on those sections and
`docs/Backlog.md`'s matching note. This document, and probe 7/8's live run
(`PROBES-2026-09-21.md`'s "Probes 7 and 8, run live" section), are the
"further tests" the original deferral was waiting for.

## What probe 7/8 actually showed (read this before the schema)

The design doc's premise — "that probe is cheap (`finalAnalysisStep=
CHECK_ASSET`)" — is confirmed, but the live run also surfaced something the
design doc could not have known without running it: **RE's own filesystem
cataloging path attaches no Connection to the folder Asset it creates**, so
this check reports `no_connection` for essentially every filesystem RE has
cataloged today, not a genuine reachability answer. This is a real,
previously-unconfirmed finding (the surveyor code already suspected it in a
comment, but flagged it as unverified) — full detail, including the raw
completion messages for all three outcomes tested live, is in
`PROBES-2026-09-21.md`.

Three live-confirmed outcomes, all fast (single-digit seconds):

| Scenario | Terminal status | Completion message (verbatim prefix) | Mapped outcome |
|---|---|---|---|
| No Connection on the Asset | `INVALID` | `OPEN-SURVEY-0009 ... has no connection` | `no_connection` |
| Connection present, path not visible from engine host | `FAILED` | `OMES-SURVEY-ACTION-0018 ... BASIC-FILE-CONNECTOR-404-001 ... does not exist` | `network_unreachable` |
| Connection present, path visible from engine host | `COMPLETED` | `OMES-SURVEY-ACTION-0019 ... completed the analysis ...` | `reachable` |

## What was built

### 1. `resource_reachability` table (`registry.py`)

```sql
CREATE TABLE IF NOT EXISTS resource_reachability (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type      TEXT NOT NULL DEFAULT 'filesystem',
    filesystem_slug    TEXT NOT NULL,
    probed_at          TEXT NOT NULL,
    probed_from        TEXT NOT NULL DEFAULT '',
    outcome            TEXT NOT NULL,
    error_code         TEXT DEFAULT '',
    error_detail       TEXT DEFAULT '',
    latency_ms         INTEGER DEFAULT NULL,
    engine_action_guid TEXT DEFAULT '',
    FOREIGN KEY (filesystem_slug) REFERENCES file_systems(slug)
)
```

A history table, not a single "current status" row — the same choice
`database_settings`/`database_grants` etc. make, for the same reason: a
change in reachability over time is itself a fact worth keeping.

**Deliberately NOT a fully polymorphic `(slug, ...)` table** as §5's "Gap
and ask" sketch first proposed, even though the outcome vocabulary is taken
from there verbatim. It carries a real `filesystem_slug` FK to
`file_systems(slug)`, matching every other per-resource-type detail table
in this file (`filesystem_survey_coverage`, `database_survey_coverage`,
...) rather than the un-FK-able polymorphic-slug shape `docs/registry.py`'s
own `_DetailTableSpec` machinery and `tests/test_no_orphaned_slugs.py`
exist specifically to avoid. `resource_type` is kept as a column (currently
always `'filesystem'`) so a future database-reachability extension has
somewhere to land without a schema change, but it does not participate in
any FK today. `remove_filesystem()` cleans up this table's rows explicitly
(it's excluded from the generic `_DETAIL_TABLE_SPECS` auto-detection
because it has no `surveyed_at`/`source`/`state` columns — see
`_parse_detail_table_spec`'s docstring in `registry.py`).

Accessors: `record_reachability_check()`, `get_latest_reachability()`,
`list_reachability_history()`.

### 2. The check (`resource_explorer/reachability.py`)

`check_filesystem_reachability(fs_slug, registry)`:

1. Looks up the filesystem's `egeria_asset_guid`. If it has never been
   cataloged in Egeria, records `outcome='unknown'`/`error_code=
   'NOT_CATALOGED'` — there is no Asset to target, which is itself a
   legitimate answer, not a bug.
2. Otherwise calls `AutomatedCuration.initiate_gov_action_type(
   action_type_qualified_name="FileSurvey::survey-folder",
   action_targets=[...], request_parameters={"finalAnalysisStep":
   "CHECK_ASSET"})` directly (per the coordinator brief and probe 7 — not
   `_async_initiate_survey`/its convenience wrappers, which
   `egeria-support-for-multi-resource.md` §8.1 already found drop
   `requestParameters` silently), then polls
   `MetadataExpert.get_metadata_element_by_guid` the same way
   `surveyors/egeria_delegated_step.py`'s `initiate_action_type_and_wait`
   does, to a terminal `activityStatus`.
3. Classifies the result via `classify_check_asset_result()` (pure,
   unit-tested — see below) and persists it via
   `registry.record_reachability_check()`.
4. **Never raises for an Egeria-side failure.** A raised exception,
   a failed initiation, or a timeout all become `outcome='unknown'` and are
   recorded like any other result — the check itself is a normal read
   operation, not something that should 500 the request that asked for it.

**Outcome vocabulary — reused, not reinvented.** `egeria-support-for-
multi-resource.md` §5 already specified `reachable`, `no_connection`,
`unresolvable_secret`, `network_unreachable`, `auth_rejected`, `unknown` —
a peer review during this slice's build flagged that this vocabulary should
be used verbatim rather than a fresh three-value scheme, and it is: the
task's own "three-state absence discipline" requirement is satisfied by
treating `unknown` as the third state ("checked, could not be determined")
alongside "checked, determinate outcome" (the other five values) and "never
checked" (absence of any row) — not by inventing a separate enum.

**Scope: filesystem only.** Database reachability is explicitly NOT
attempted by this check. `survey-postgres-database` is a different
governance action type with a different failure shape (secrets-store
resolution, per `PROBES-2026-09-21.md`'s probe 9 write-up) — extending this
mechanism to databases needs its own probe pass, which this slice does not
do. `check_filesystem_reachability` only accepts a filesystem slug;
`ReachabilityCheckScopeError` is reserved for a future caller that tries to
route a non-filesystem resource through this same entry point.

### 3. Classic-UI launcher sentence (`index.html`)

Per the coordinator brief: minimal, not a new page or dashboard. On a
cataloged filesystem's survey detail view (`renderFilesystemSurveyReport`):

- A **"🔌 Check Reachability"** button next to the existing Survey/Publish
  buttons, shown only when the filesystem has an `egeria_asset_guid`
  (nothing to check against otherwise).
- A rendered sentence below "Last surveyed": `Reachability: ✅ Reachable —
  checked <time>`, or the equivalent for each outcome (⚠️ for the four
  determinate-unreachable outcomes, ❓ for `unknown`, and a plain "not
  checked yet" when no row exists at all).

Backed by two new endpoints in `web/routes/filesystems.py`:
`GET /api/filesystems/{slug}/reachability` (latest, or `null`),
`GET /api/filesystems/{slug}/reachability/history`, and
`POST /api/filesystems/{slug}/reachability` (triggers a live check).

Stays in the classic UI (`index.html`), not `/next`, per `CLAUDE.md`'s note
that DB/FS work stays there.

## What's scoped out (not built)

- **Database reachability.** See "Scope: filesystem only" above.
- **Attaching a Connection to RE's own cataloged folders.** This would make
  `reachable`/`network_unreachable` the common case instead of
  `no_connection` — a real, separate design decision (does every folder
  need one? at catalog time or lazily?), filed to `docs/Backlog.md` rather
  than decided here.
- **`ConnectorActivityReport`/`ReachabilityAnnotation` upstream ask** (§5's
  "ask later, small"). Still not worth filing until this check has run for
  a while and the value is demonstrated, unchanged from the original
  design doc's own recommendation.
- Probe 8's "folder-depth control" claim was run but against an empty
  folder on both request-parameter shapes, so it doesn't yet distinguish
  "CHECK_ASSET skips recursion" from "nothing to recurse into" — flagged in
  `PROBES-2026-09-21.md`, not re-tested here.

## Tests

`tests/test_reachability.py` — 19 tests, all offline (no live Egeria
needed): `classify_check_asset_result()` against the exact live-confirmed
completion messages from probe 7/8 (not paraphrased), the three-state
discipline (never-checked / determinate outcome / could-not-determine), and
the registry's persistence + cleanup-on-remove behavior. Confirmed the
existing `test_db_fs_structured_tables.py::test_...` table-count assertion
(`len(_DETAIL_TABLE_SPECS) == 10`) is unaffected — `resource_reachability`
is deliberately excluded from that generic-detail-table auto-detection
(see `_parse_detail_table_spec`'s docstring).

Full suite: `1865 passed, 1 unrelated pre-existing failure` (a live-Egeria
`requires_egeria` test, `test_egeria_live_smoke.py::
test_a_cataloged_database_is_findable_by_name`, failing on a stale/missing
guid from an earlier session's Prefect-Postgres probe work — unrelated to
filesystems or this slice's changes, not investigated further here).
