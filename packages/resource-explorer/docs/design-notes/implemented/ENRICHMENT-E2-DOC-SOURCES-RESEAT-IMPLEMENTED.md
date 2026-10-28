# Implemented: Enrichment E2 — doc sources on Context, corrected, and extended to repos

**Replies to:** `REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` §3, plus the coordinator's
relayed design answers of 2026-09-29 (signature placement, probe-state split,
repo scope, remove confirmation).
**Builds on:** E1 (#363), which had already mounted `renderDocSources()` on
Context's last section unchanged; E2 corrects it in place.

## What changed

1. **Glyphs through `glyphs.js`.** The local `PROBE_GLYPH` table (with its
   off-vocabulary `●`) is deleted; each probe state renders with `glyphSpan()`:
   reachable→`measured` ✓, needs_sign_in→`scoped` ◐, not_found→`error` ✕,
   blocked / timed_out / unreachable→`not_established` ?, not probed→`unrun` ○.
   The wording moved with the glyph (§3: "reachable behind a sign-in",
   "not found · HTTP 404 — the link is broken, not the site").
2. **Remove is an ordinary control** (`text-accent-ink underline`, same class
   string as re-check), asks once via `window.confirm`, and the row is gone after
   the re-render. Declining sends no request.
3. **Signed row.** `personRowLineHtml({verb:'added by', author, whenIso:added_at})`
   on its own line, separate from the probe line. An unsigned legacy row reads
   "added by unknown · <time>" rather than nothing.
4. **Repo support.** Same block, same facts, same signing.
   - API: `repo` added to `_ENTITY_TABLES`; `resolve_entity_for_doc_source`
     gained a repo branch.
   - Repos have no per-entity Egeria credentials, so the branch returns a
     `RepoEgeriaConnectionView` (attribute names the call sites already read,
     filled from `EGERIA_*` env values, the repo's asset guid and display name).
     Its `repr`/`str` never include the password (tested).
   - Context: `DOC_SOURCE_KINDS` includes `repo`.
   - Publish hook: `EgeriaPublisher.publish` now calls `publish_local_doc_sources`
     for the repo (best-effort), like the database/filesystem routes.
5. **Probe states split three ways.** `timed_out` (httpx.TimeoutException, reads
   "timed out · 4.0s · re-check"), `unreachable` (DNS/TLS/connection: the site
   never answered), `blocked` (reserved for a real 4xx/5xx refusal, "blocked by
   the site · HTTP 503"). Stored as plain text; no migration.
6. **Ingest fact** on every row reads "ingestion not built yet", the words
   Survey & analyses uses; the disabled "ingest — coming soon" button is gone.

## Lesson worth naming: configured for two kinds, never called for the third

Doc sources were wired for database and filesystem, with the publish hook in
each of those routes' publish paths. The repo path (`EgeriaPublisher.publish`)
never called it, and nothing failed: a repo would simply have kept its sources
at "local" forever. This is a recurring shape in this project: a path
parameterised for two kinds that is never invoked for the third. When adding a
kind, grep every place the existing kinds are enumerated (entity-table tuples,
resolvers, publish hooks, UI kind lists), not just the obvious one.

## Wording note for review

An unpublished resource's source reads the existing `local_only` wording
("local only — resource not published"); the "local — not catalogued (publish
needed)" wording is the existing `not_catalogued` state for a published
resource with nothing linked. Both are unchanged and shared by all three kinds.

## Tests

Harness: `doc-sources-e2.test.mjs` (gates 1–6, red/green checked by mutating
the confirm, the remove class and the timed_out branch); the old ingest-button
and sign-in-wording assertions in `doc-sources-enrichment.test.mjs` were
replaced. Python: `tests/test_doc_sources_repo.py`, probe tests for the new
states, route parametrisation. `tailwind-next.css` regenerated.
