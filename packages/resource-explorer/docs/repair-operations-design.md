# Repair operations — design note

**Status:** implemented. Four repo-repair operations (2026-08-28), built.

RE's repo inventory had two operations before this: `add` and `remove`. This
adds four repair operations that came up in practice this week — see the
originating brief for the incident that motivated each one. This note covers
what each has to guarantee atomically and what it refuses rather than risk,
which is the part that mattered more than the CRUD plumbing.

Code: `resource_explorer/repair.py` (orchestration), `ProjectRegistry.
rename_project_slug`/`update_github_url`/`invalidate_indexing`/
`find_entity_investigations` (registry.py), `MultiCollectionStore.
rename_collection` (vector_store_pg.py → trellis_vectorstore/pg.py). CLI:
`resource-explorer repair *`. Web: `POST/GET/DELETE /api/admin/repair/*`,
Admin → 🔧 Repair.

Vocabulary note, per the brief this was built from: "project" in this
codebase's existing identifiers (`ProjectRegistry`, `project_slug`, the
`projects` table) means what current vocabulary calls a **repo**. This work
does not rename those symbols — that's a separate, larger decision — but
everything new here (this doc, `repair.py`, the CLI/API surface, the Admin
panel) says repo, not project.

## 1. Rename (change a repo's slug)

The hard one. A slug appears in five different shapes across the schema, and
missing any one of them leaves a silent orphan — data that still exists but
is no longer reachable through the renamed repo.

**What has to move, enumerated (not trusted from memory — verified against
`registry.py`'s actual `CREATE TABLE` statements, and cross-checked live
against Postgres's own `information_schema` in
`tests/test_integration_registry_pg.py::test_rename_project_slug_covers_every_table_with_a_fk`,
the same structural-guard pattern `test_remove_deletes_from_every_table_with_a_fk`
already used for `remove()`):**

1. **`projects.slug` itself** — the primary key everything else is keyed off.
2. **20 tables with a `project_slug` column** (`ProjectRegistry.
   _PROJECT_SLUG_TABLES`) — 17 with a declared `FOREIGN KEY ... REFERENCES
   projects(slug)`, plus 3 that carry the same column without a declared
   constraint (`project_published_annotation_types`,
   `project_published_analyses`, `repo_dispositions`) and would silently
   orphan their rows on rename if skipped just because nothing enforces it.
3. **12 tables keyed by `(entity_type, entity_slug)`** where `entity_type =
   'repo'` (`ProjectRegistry._ENTITY_SLUG_TABLES`) — tags, feedback, curator
   notes, schedules, activity log, egeria linkage status, investigation
   working-set membership, notification subscriptions, and others. These
   have no FK to `projects` at all (`entity_type` also covers `'database'`/
   `'filesystem'`), so they can be updated in any order relative to the
   `projects` row swap.
4. **`sub_resources`** — keyed by `(resource_type, resource_slug)`, same
   shape as #3 but its own table.
5. **`projects.parent_slug`** on *other* rows — a monorepo's sub-projects
   (registered via `--subpath`) point back at their parent by slug.
6. **pgvector collection tables** whose name embeds the slug
   (`{slug}_{collection_type}`, `ingestion/pipeline.py:134`).

**What is deliberately NOT renamed: shared collections.**
`collection_drift.py`'s own docstring already established this shape: a
collection name isn't uniformly `{slug}_{type}` — some are shared across
several repos (`web_docs_egeria_project_org`), matched by prefix/suffix
rather than exact equality. Renaming has to tell these apart precisely
because the failure mode is worse than in drift detection: `detect_drift()`
matching too loosely produces a wrong report; rename matching too loosely
renames or drops a table other repos still reference by its old name. So
`repair._owned_collection_rename_map()` renames only `name ==
f"{old_slug}_{ctype}"` exactly — anything else stays untouched, and the
result (`RenameResult.unchanged_shared_collections`) says so explicitly
rather than silently doing nothing.

**Ordering, and why.** `rename_project_slug()`'s SQL step goes: insert a new
`projects` row under the new slug (copying every column) → update every
child table from old to new → delete the old `projects` row. Never delete
first, and never update children before the new parent row exists — Postgres
enforces the FK in both directions (a child row can't point at a
nonexistent parent, and a parent row can't be removed while children still
point at it), so the insert-then-repoint-then-delete order is the only one
that never has an invalid intermediate state. `remove()`'s own history
(documented in its comment) is two incidents of exactly this class of bug —
an ordering that worked on SQLite (no FK enforcement) and broke on Postgres
— which is why this got the same real-Postgres integration test tier rather
than trusting SQLite's newer `PRAGMA foreign_keys=ON` to be an equivalent
check.

At the `repair.rename_repo()` level, pgvector renames happen **before** the
SQL rename, not after: a `rename_collection()` (`ALTER TABLE ... RENAME
TO`) is cheap and reversible (rename it back), where re-deriving "what
should the registry state have been" after a partial SQL failure is not. If
a pgvector rename fails partway through the owned-collection list, every
rename that already succeeded is rolled back (renamed back to its old name)
before raising, and the registry is never touched — so a failed
`rename_repo()` call leaves the system in the state it started in, not a
mix of old and new names. If the rollback itself fails, both errors are
named in the raised exception rather than one swallowing the other.

**What it refuses:** the repo isn't registered; the new slug collides with
an existing repo; the new slug normalizes to the same value as the current
one (would look like a successful no-op rename instead of surfacing that
nothing happened).

**What it does NOT attempt:** renaming is not exposed as a bulk/batch
operation, and there's no dry-run mode that reports the plan without
executing it — `RenameResult` reports what was done, after the fact, which
is enough for a repair operation invoked deliberately on one repo at a time
but would not be enough for renaming many repos unattended.

## 2. Correct a repo's github_url

The narrower problem: pgvector holds content embedded from the OLD url, and
once the url changes that content no longer describes what the repo points
at. The two honest options are "refuse" or "force a re-index" — leaving the
stale content silently reachable under the new url was the one option ruled
out by the brief, and rightly: it produces confident, wrong answers with
nothing marking them as such.

This implements "force a re-index" as two steps, not one: `change_github_url`
always drops every existing collection and resets the repo to the same
"not yet indexed" state a freshly-added repo starts in
(`collections=[]`, `last_indexed_at=""` — `ProjectRegistry.
invalidate_indexing`), but does **not** itself run the ingestion pipeline.
Re-indexing is a separate, slow, network-bound operation
(`resource-explorer refresh <slug>`) that the caller runs deliberately as
its own step. Folding it into `change_github_url` would make one call do
both "make the stale state unreachable" (fast, local) and "re-embed a
possibly-large repo" (slow, network) — bundling a guaranteed-safe operation
with one that can fail or take minutes is worse than making the second step
explicit.

Refuses unless `confirm=True` (CLI: `--yes`; Admin UI: a JS `confirm()`
dialog before the request) — the refusal message itself names how many
collections would be dropped, so the caller sees the cost before confirming
rather than after.

## 3. Enable a drift-flagged collection

The one operation that already had almost all its machinery:
`IngestionPipeline._ingest_collection()` already supported a
`local_root=None` "incremental single-collection" fallback path (downloads
the repo zipball itself, ingests one collection type) — added previously for
the incremental-reindex path, reused here as-is rather than duplicated.
`enable_collection()` is a thin wrapper: look up the collection type, refuse
if already enabled or unknown, run the single-collection ingest, and only
append the new collection name to the registry's `collections` list if the
ingest actually produced chunks.

**Known limitation, inherited rather than introduced:** the fallback path
always downloads the *full* repo, ignoring `subproject_path`/
`extra_docs_paths`. For a plain repo this is exactly right; for one
registered with `--subpath` (a monorepo sub-project), enabling a collection
this way scans more of the tree than the repo's other collections do. Not
fixed here — fixing it means either threading subproject_path through the
fallback path (a real change to shared ingestion code, used by other
callers) or accepting a second, narrower download path just for this repair
operation. Left as a known gap rather than silently accepted or
hand-waved.

**What it refuses:** unregistered repo; unknown collection type; already
enabled; a real ingest pass that produces zero chunks despite drift
detection's file-count signal claiming eligibility (the two disagreeing is
itself worth surfacing, not swallowing into "nothing happened").

## 4. Repoint or drop an investigation member

The smallest gap: `add_working_set_member`/`remove_working_set_member` and
`investigation_working_set_slug()` already existed and already worked —
`investigations.py`'s own `/members` endpoints already exposed add/remove.
What was missing was **entity-centric discovery**: every existing surface
answers "what's in this investigation's Folio", nothing answered "which
investigations is this repo in" — the question an admin actually has when
Scouting scoped a repo into the wrong investigation.

`ProjectRegistry.find_entity_investigations()` is the new primitive (a join
across `working_set_members` → `working_sets` [`collection_kind='folio'`]
→ `investigation_resource_lists` → `investigations`). `repair.py`'s
`list_repo_investigation_memberships`/`repoint_investigation_member`/
`drop_investigation_member` are thin wrappers over that plus the existing
add/remove primitives — repoint is drop-then-add, not a single UPDATE,
because membership rows have no identity of their own to move (the PK is
`(working_set_slug, entity_type, entity_slug)`).

Folio-only, matching `list_investigation_members()`'s own existing
reasoning: a resource can be in scope without anyone having judged it yet
(no disposition WorkingSet entry), and that's the normal post-Scouting
state, not a gap to route around.

**What it refuses:** unregistered repo; either investigation slug not
found; repointing to the same investigation; repointing away from an
investigation the repo isn't actually a member of (silently creating a
membership under a "repoint" label when the premise was wrong would hide
that the caller's assumption didn't hold).

## What was deliberately left out

- **No bulk rename / batch repair.** Every operation here is invoked on one
  repo at a time, matching `add`/`remove`'s own granularity. A batch mode
  (e.g. "rename every repo matching X") would need its own dry-run/rollback
  story and wasn't asked for.
- **No dry-run mode.** `RenameResult` and the other return values report
  what happened, not what *would* happen. For rename specifically this is a
  real gap for a repo with a large owned-collection set — there is no way
  to preview the collection-rename plan without executing it (short of
  reading `_owned_collection_rename_map()`'s output directly in a REPL).
- **No re-run of onboarding's wizard prompts for enable_collection.** The
  wizard prompts (`accept_all` etc.) are onboarding-specific UX around a
  choice; `enable_collection()` is a direct, unprompted action — the
  Admin UI's confirm-before-drift-enable is the only gate.
- **`sub_resources`, `repo_dispositions`, and `parent_slug` are covered by
  rename but have no dedicated repair operation of their own** (e.g. no
  "re-catalog a sub-resource under a new locator"). Out of scope — nothing
  in the brief's four gaps touched them beyond rename needing to carry them
  along.
