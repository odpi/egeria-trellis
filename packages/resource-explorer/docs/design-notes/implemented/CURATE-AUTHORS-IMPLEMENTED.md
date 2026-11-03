# Curate authors: implemented (backend slice only, no /next UI)

Source: REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md, "Two things to fix before anything is drawn" item 1, plus owner rulings of 2026-10-01 (journal-style notes are append-only; Egeria publish later, not now).

Verified import path: `/Users/dwolfson/localGit/egeria-v6/trellis-re-curate-authors/packages/resource-explorer/resource_explorer/__init__.py`

## What changed
- `registry.py`: `author TEXT DEFAULT NULL` on `resource_tags`, `resource_feedback`, `resource_curator_notes` via `_add_author_column` (read the column list in the same transaction, ALTER only if absent; same SQL on both dialects). Writers take `author=` (None writes a legacy unsigned row). Readers normalise every row (see contract).
- `web/routes/curate.py`: `_require_author(request, action)` (401 when nobody is signed in) on tag add/delete, feedback, note add/delete. Author comes from the session (`get_current_user`, resolved in the request thread and passed explicitly; never a ContextVar read in a bare thread, never `''`). Write models have no `author` field, so a body `author` is dropped.
- `DELETE /api/curate/notes/{id}`: needs sign-in; 404 unknown; **409** for a signed note ("append-only, add a later note to amend it"); legacy unsigned notes are deletable. The registry's `delete_curator_note` also carries `author IS NULL` in its SQL, so a caller that forgets the route check cannot delete a signed note. No edit endpoint.
- Tag removal leaves no row, so who removed it goes to `activity_log` as operation `curate_tag_removed` (summary and annotations carry the user). Decision made inside the brief's "both record author"; flag if a different home is wanted.
- Seam: `_publish_curation_to_egeria(entity_type, slug, kind, payload)` in curate.py, a deliberate no-op. Nothing publishes to Egeria.

## Migration (runs on first start after deploy, when `ProjectRegistry._init_schema` runs)
Both dialects, each only if the column is absent:
```
ALTER TABLE resource_tags          ADD COLUMN author TEXT DEFAULT NULL
ALTER TABLE resource_feedback      ADD COLUMN author TEXT DEFAULT NULL
ALTER TABLE resource_curator_notes ADD COLUMN author TEXT DEFAULT NULL
```
Postgres detects absence with `SELECT column_name FROM information_schema.columns WHERE table_name = ? AND table_schema = current_schema()`; SQLite with `PRAGMA table_info`. A nullable column with NULL default is metadata-only on Postgres (no rewrite). Idempotent; existing rows keep NULL (unsigned).
**The owner must peer-check before the deploy: first start migrates the shared registry (localhost:5442).** I did not run it against that database.

## API contract
Every row from `GET /api/curate/feedback/{type}/{slug}`, `GET /api/curate/notes/{type}/{slug}`, `GET /api/curate/feedback` (admin list, resource rows) and the new `GET /api/curate/tags-detail/{type}/{slug}` carries:
- `author`: user id string, or `null` for rows from before authors were recorded
- `authored`: boolean (`author is not null`)
- `author_label`: the user id, or `"unsigned · from before authors were recorded"`; never blank
`GET /api/curate/tags/{type}/{slug}` still returns `list[str]` (Classic depends on it); `tags-detail` returns `[{tag, created_at, author, authored, author_label}]`.
Writes: tag add returns `author`; tag delete returns `removed_by`. Signed-out: 401 `Sign in to <action> — a tag, rating or note needs an author.` Signed note delete: 409.

## Tests
`tests/test_curate_authors.py` (19 tests). With source changes stashed (main behaviour): 16 fail, 3 pass (the three that assert behaviour main already has: legacy unsigned note deletable, no edit route, the guard-check known-negative). Fixed: 19 pass. Covers: 401 for each of the five write routes (and nothing written), author recorded from session, body `author` ignored, two users not mixed, tag-removal recorded, legacy rows unsigned, signed note undeletable (409, also at registry level), legacy note deletable, no edit route, guard-presence test over every POST/PUT/PATCH/DELETE route in curate.py (component/blueprint verdict routes are exempt by name: they have their own 403 authorization), SQLite migration from an old schema run twice, Postgres migration SQL via a fake connection.
`tests/test_web.py`: the live copy of the Curate router classes now signs in (the file contains two copies of those classes; the later one shadows the first, untouched).
Run with `PGVECTOR_PORT=1`, SQLite temp registry: test_curate_authors, test_registry, test_admin_feedback_view, test_compile_persistence, test_facts, test_repair, test_web, test_curate, test_curate_blueprints_route, test_next_false_capability_claims, test_next_admin_pane, test_check_registry, test_init_schema_takes_no_write_lock_when_idle, test_registry_pool_resilience, test_conftest_schema_isolation: all pass (414 + 83).

## Not verified
- Postgres: SQL reviewed and dialect-checked against a fake connection only; never run on a real Postgres.
- Classic `index.html` is untouched: its add/delete calls now 401 when signed out, and its Delete-note button on a signed note gets a 409 it does not message (it only reloads). Classic retires; /next UI is the next slice.
- `curate_tag_removed` activity rows were not checked against the Activity UI.
- Not exercised in a browser or against a running 8813.

## Test isolation (2026-10-01)
`tests/test_curate_authors.py` passed alone but 9 tests failed (five signed-out 401 cases, signed-out legacy-note delete, signed-in author recorded, body-author ignored, tag removal) when run after `tests/test_curate.py` or `tests/test_curate_blueprints_route.py` in the same process. Cause: `routes/curate.py` does `from resource_explorer.auth import get_current_user`, binding the name once at the app's first import. Those earlier files monkeypatch `resource_explorer.auth.get_current_user` to a fixed signed-in user *before* their first `from ...web.app import app`, so the app (and curate.py's bound name) is created under the stub; monkeypatch reverts the `auth` attribute but never the already-bound route name. In the new tests every request then resolved to that stub user: the guard never fired and authors were the stub's. Not a production bug (production never patches that name). Fix, in the test file only: the `client` fixture rebinds `routes.curate.get_current_user` to the real `auth.get_current_user` for the test and undoes it after. Other files that patch the same attribute before first app import (test_catalogue_depth_offer, test_component_tree, test_reports, test_promotion) were left alone; they do not break their own tests.
