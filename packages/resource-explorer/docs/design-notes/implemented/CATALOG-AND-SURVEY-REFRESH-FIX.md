# catalog_and_survey never refreshes an existing element's connection — Fixed for fresh catalogs (2026-09-20)

Closes the investigation opened by `docs/Backlog.md`'s "TIER 1 — `catalog_and_survey`
never refreshes an existing element's credentials/connection" entry. Records what was
actually found (the root cause was not what the entry assumed), what got fixed, what
deliberately did not, and why.

## Starting point

The Backlog entry found `coco_ods` (a real Coco Pharmaceuticals sample database)
stuck: its Egeria asset had no `Connection`, so any native PostgreSQL survey
against it failed with

> `OPEN-SURVEY-0009 ... has no connection, so there is no way to reach the
> resource it describes`

and re-running `EgeriaDatabaseSurveyor.catalog_and_survey` with corrected
credentials produced the identical error — proving the re-run changed nothing.
The entry's working theory: `_catalog_and_survey` only calls
`create_postgres_server_element_from_template`/
`create_postgres_database_element_from_template` (the calls carrying
`db_user`/`db_pwd`) when no element is found by name, so an existing element's
connection can never be refreshed. Two things were flagged as unconfirmed:
whether pyegeria has a separate "update connection" call, and whether the
create-from-template calls are themselves upsert-safe (in which case the
simplest fix would be removing the `if not <guid>` guard).

## What was actually found, live against qs-view-server

**1. The create-from-template calls ARE upsert-safe by qualifiedName — confirmed live.**
Calling pyegeria's `create_elem_from_template` again with the same
`serverName`/`databaseName` placeholder values that `coco_ods` was already
cataloged under returned the **same GUID** (`8f239316-8773-44da-9e8e-760243226a10`)
rather than creating a duplicate. So the "simplest fix" the Backlog entry
floated — remove the guard, always call, let Egeria decide create-vs-update —
is safe from a duplication standpoint. It is not, however, sufficient (see #3).

**2. The real, deeper defect: pyegeria's template calls never request a deep copy.**
`AutomatedCuration.create_postgres_server_element_from_template`/
`create_postgres_database_element_from_template` (pyegeria 6.1.15,
`pyegeria/omvs/automated_curation.py`) build their `TemplateRequestBody` by
hand and never set `"deepCopy": True`. `TemplateRequestBody.deep_copy` (see
`pyegeria/models/models.py`) defaults to `False`. Confirmed live: fetching
`coco_ods`'s asset graph (`AssetMaker.get_asset_by_guid(...)['mermaidGraph']`)
showed **no Connection/Endpoint/ConnectorType node at all** — only a
`SourcedFrom` link to the template, Engine Action nodes, and Survey Reports.
This means the bug is not specific to *repeat* runs — a first-time,
never-before-cataloged database would hit the same "no connection" failure,
because the template's attached Connection subgraph is never copied over
regardless of whether the top-level element already existed.

This is a second instance of the pattern already documented at the top of
`egeria_database_surveyor.py` for `initiate_postgres_server_survey`/
`initiate_postgres_database_survey` (wrong hardcoded separator): a pyegeria
convenience wrapper that doesn't do what its own docstring/RE's assumption
believed. Per this repo's pyegeria-gaps-tracking convention, this is **not**
patched in pyegeria itself — RE bypasses the broken wrapper the same way it
already does for the survey-initiation bug, calling the lower-level
`create_elem_from_template` directly with a hand-built body that adds
`deepCopy: True`.

**3. The reuse-by-qualifiedName path does NOT re-run deepCopy's child-copying — also confirmed live.**
After adding `deepCopy: True` and re-issuing the create-from-template call
against `coco_ods` (already-existing, matched by qualifiedName), the returned
GUID was again the same, but the asset graph *still* showed no Connection.
Three independent direct lookups (not mermaid-graph traversal, to rule out a
relationship-visibility delay a peer was separately investigating under
ISSUE-108) confirmed no Connection element exists anywhere for `coco_ods` by
any name:  `AutomatedCuration.get_guid_for_name()` on the connection's exact
qualifiedName, `ConnectionMaker.get_connections_by_name()` on the same name,
and `ConnectionMaker.find_connections('coco_ods')`. All three: "No elements
found."

Conclusion: Egeria's template engine, when it matches an **existing** element
by qualifiedName and reuses its GUID, never runs the "copy the template's
attached children" step that `deepCopy` controls — that only happens on a
genuine new-anchor instantiation. So **the guard-removal fix helps only fresh
catalog runs.** It cannot repair an already-broken existing element, no matter
how many times it's re-run, with or without `deepCopy`.

**4. Repairing an existing broken element is not a small follow-up — investigated and deliberately not automated.**
The Connection attached to the "PostgreSQL Relational Database" template is
not a plain Connection with `userId`/password fields sitting on it. Fetched
live: it's a `VirtualConnection` (qualifiedName
`PostgreSQL Relational Database::~{serverName}~::~{databaseName}~::Connection`)
with an **embedded child Connection** whose own qualifiedName is
`...::SecretsStoreConnection`, wired to a `YAMLSecretsStoreProvider` connector
(`configurationProperties: {"secretsCollectionName": ...}`), plus its own
`Endpoint` and `ConnectorType`. `databaseUserId`/`databasePassword` flow into
this multi-node graph via Egeria's own template placeholder substitution —
not as plain properties on a single Connection.

Hand-assembling that graph via generic `ConnectionMaker` calls (`create_connection`,
`create_endpoint`, `create_connector_type`, `link_connection_endpoint`, ...)
would mean reimplementing wiring Egeria's own template-deepCopy mechanism
already knows how to do correctly — with real risk of producing a Connection
that satisfies `OPEN-SURVEY-0009`'s bare "does this asset have *a* connection"
check while still being unable to actually authenticate (wrong secrets-store
wiring, wrong embedded-connection shape, etc.). Per the task this fix was
scoped under: *"do not invent a workaround that mutates Egeria state through
an unsupported path"* — this is exactly that unsupported path, so it was not
attempted.

The only mechanism confirmed to correctly build this graph is Egeria's own
template deep-copy on a **genuine new-anchor instantiation** — i.e.
delete-and-recatalog. That repairs the connection but changes the asset's
GUID, which orphans its existing `SurveyReport`s and annotations (they're
linked to the old GUID via `ReportSubject`). That's a real fix with a real
cost, and — per the project owner's decision on this investigation — is a
bigger, less-reversible call than this bug fix's scope: it is **not**
automated into RE's code, and `coco_ods` itself was deliberately left in its
current broken state rather than being repaired live today. It's documented
here and in the Backlog entry as the path forward, pending an explicit
decision to spend that cost.

## What was built

`resource_explorer/surveyors/database/egeria_database_surveyor.py`:

- **`_create_postgres_element_from_template(technology_type, placeholder_values)`**
  — new helper. Bypasses `create_postgres_server_element_from_template`/
  `create_postgres_database_element_from_template` entirely, calling
  `AutomatedCuration.get_template_guid_for_technology_type` +
  `create_elem_from_template` directly with `"deepCopy": True` added to the
  request body. `_catalog_and_survey`'s two "not found by name → create" branches
  now call this instead of the broken wrappers. This fixes deep-copying (and
  therefore the Connection subgraph) for **fresh catalog runs** — confirmed
  live that the resulting call shape and upsert-by-qualifiedName behavior are
  unchanged from before, only `deepCopy` is added.
- **`_warn_if_database_has_no_connection(db_entity, server_name, db_guid)`** —
  new, deliberately non-fixing, visibility-only check. When the database
  element is found by name (the case this fix cannot repair), this checks
  whether a Connection exists for it by the naming convention Egeria's own
  PostgreSQL templates use (`f"{qualifiedName}::Connection"`, confirmed live —
  not a guaranteed-stable API, called out as a heuristic in the docstring) and
  logs a `WARNING` naming the exact failure mode and pointing at this document
  if not. This is the same "non-fatal must not mean invisible" principle
  `_note_stale_guid_if_any` already applies to stale GUIDs elsewhere in this
  file — the goal is that the *next* database stuck this way surfaces here
  instead of only downstream as an opaque `OPEN-SURVEY-0009` from the native
  survey engine.

`resource_explorer/surveyors/filesystem/egeria_filesystem_surveyor.py`:

- No code change. Investigated whether the same bug applies (the task
  explicitly asked not to assume symmetry) and confirmed it does not:
  `catalog_and_survey` takes no credentials at all (a local filesystem mount
  has nothing to authenticate with), and `create_folder_element_from_template`
  accepts none either — there is no credentials-carrying create call being
  skipped on repeat runs. The DataFolder/DataFile templates it uses also have
  no attached Connection subgraph the way the PostgreSQL templates do, so the
  `deepCopy` gap doesn't apply either. Documented inline with a comment above
  `EgeriaFileSystemSurveyorError` so the next reader doesn't have to
  re-investigate. (`trigger_survey_by_guid`'s own docstring separately flags
  that the *native* FileDirectory survey's Connection requirements are
  unverified — a different, pre-existing, still-open question, not something
  this fix touches.)

## Live verification

Coordinated per `coordinate-shared-writes` (checked for live peers via
`list_sessions` — none running; the project owner separately identified an
active peer, `egeria-python-d3`, working on a related Egeria
relationship-visibility timing issue, ISSUE-108, who was looped in before
further writes). Performed against `qs-view-server`'s real `coco_ods` asset,
already in the broken state this fix targets:

1. Confirmed real Postgres credentials (`postgres`/`egeria`, port 5442,
   `docker-entrypoint-initdb.d`/shared-infra compose config) work directly
   against the database via `psql`.
2. Confirmed `create_elem_from_template` reuses `coco_ods`'s existing GUID
   rather than duplicating it (see finding #1 above) — read-only in effect
   (no new element, no property change observed) but a real write attempt.
3. Confirmed, with `deepCopy: True` added, the reused-element path still
   produces no Connection (finding #3) — via three independent direct-lookup
   reads, ruling out the relationship-visibility-delay explanation a peer's
   concurrent investigation raised as a real alternative.
4. Did **not** attempt to hand-assemble the VirtualConnection/SecretsStoreConnection
   graph, and did **not** delete-and-recatalog `coco_ods` — both out of scope
   per the project owner's explicit decision (see finding #4). `coco_ods`
   remains in the same broken state the Backlog entry originally found it in;
   this fix prevents new databases from ending up there, and makes the state
   visible via a warning if one already is.

No fresh database was cataloged live to verify the `deepCopy: True` path
produces a real Connection end-to-end — doing so would mean cataloging a new
throwaway resource against shared dev Egeria, which wasn't judged worth the
extra write for this fix's scope. This is the one open item: **the `deepCopy`
fix is verified by code/request-shape inspection and by pyegeria's own model
field semantics, not by observing a fresh Connection actually appear.** Flagged
here rather than assumed.

## Testing

`tests/test_catalog_and_survey_refresh.py` (new, mocked, no live Egeria):

- `TestCreatePostgresElementFromTemplate` — pins that the new helper's request
  body always sets `deepCopy: True`, and that it never calls either of the two
  broken pyegeria wrappers.
- `TestCatalogAndSurveyFreshCatalog` — the case this fix repairs: nothing found
  by name yet, both server and database creation go through the new
  `deepCopy`-enabled path, and current (possibly corrected) credentials are
  the ones actually sent.
- `TestCatalogAndSurveyExistingElement` — the case this fix does NOT repair:
  an element already found by name must not trigger another create call
  (Egeria's reuse behavior is safe but doesn't help, so there's no reason to
  make the extra network call), and the new connection-presence warning fires
  exactly when no Connection is found and not otherwise.

Full suite: `uv run pytest tests/ -q` — see commit for the pass/fail count run
at the end of this work.

## Follow-ups deliberately left open

1. **Delete-and-recatalog for already-broken existing elements** — needs an
   explicit product decision (data-loss tradeoff: GUID change orphans existing
   Survey Reports/annotations) before it's worth building, per finding #4.
2. **pyegeria gap: `deepCopy` never set.** This should be logged in
   `PYEGERIA_ISSUES.md` per this repo's pyegeria-gaps-tracking convention —
   not done as part of this change because that file lives in the
   `egeria-python` checkout, outside this worktree's scope (`trellis` only).
   Flagged for whoever picks this up next.
3. **The `deepCopy` fix's actual effect on a fresh catalog was not observed
   live** (see "Live verification" above) — worth confirming the next time a
   genuinely new database is cataloged through this path.

4. **Resolved 2026-09-21** — `PROBES-2026-09-21.md` found
   why even a fresh, deepCopy'd catalog run still failed its native survey
   with a SCRAM/no-password error: the templated `SecretsStoreConnection`'s
   `secretsCollectionName`/`secretsStorePathName` configuration properties
   were themselves left as Egeria's own literal, unsubstituted placeholder
   text (`~{secretsCollectionName}~` / `~{secretsStorePathName}~`) — nothing
   had ever supplied real values for either at template-instantiation time.
   `EgeriaDatabaseSurveyor._catalog_and_survey` now passes both as
   `placeholderPropertyValues`, sourced from a new
   `_save_database_secret`/`_ensure_own_secrets_store_guid` pair.

   **This does not revisit finding #4's "unsupported workaround" ruling.**
   That ruling was specifically about hand-assembling *the database asset's
   own* `VirtualConnection`/`SecretsStoreConnection`/`Endpoint`/`ConnectorType`
   subgraph via generic `ConnectionMaker` calls — Egeria's `deepCopy` template
   instantiation already builds that graph correctly; the gap was only that
   two of its placeholders were never bound. The new code uses
   `ConnectionMaker` for something else entirely: a single, generic,
   RE-owned `Connection`/`Endpoint`/`ConnectorType` graph representing RE's
   *own* secrets store (found-or-created once, independent of any
   individual database) — the documented, supported "client-side secret"
   pattern (https://egeria-project.org/concepts/client-side-secret), not a
   per-database workaround. **Decision (project owner, 2026-09-21):** Egeria
   does not share secrets across clients, so RE keeps its own secrets store
   rather than writing into Egeria's bundled `*.omsecrets` files (those hold
   Egeria's own bootstrap credentials for unrelated purposes — see the
   PROBES doc's item 2).
