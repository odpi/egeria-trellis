# DB remove button — implemented (parity D-10 / F-03)

Environment line: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-db-remove-button/packages/resource-explorer/resource_explorer/__init__.py`.

## What changed (`web/static/next/app.js`)
- `bindResourceHeader()` remove handler now calls `removeEntity(apiEntityType(state.resourceType), slug)`
  (re-api.js dispatch: `DELETE /api/projects/{slug}`, `/api/databases/{slug}`, `/api/filesystems/{slug}/`)
  instead of `removeProject` for every kind. It prunes the matching list (`projects`/`databases`/`filesystems`),
  clears the slug from `state.selected`, and picks the next selection from that same list.
- `loadSchemaInventoryPane()` now calls `bindResourceHeader()` (it drew the header and never bound it, so
  remove, hide and disposition were dead there).
- New exported `removeConfirmationHtml(entityType, slug)`: per-kind wording, from the backend code.
  Database: deletes registry row + survey records (surveys, detail tables, coverage); not touched: the source
  database, anything already published to Egeria, the server registration. Filesystem: files on disk and Egeria
  untouched. Repo: wording kept (pgvector collections + registry row), now also says what is not touched.
- Every kind has an existing backend route, so no button is hidden or disabled.
- `tests/test_next_rail_states.py`: one source anchor updated (`state.projects[0]` -> `state[listKey][0]`).

## Evidence
- New `tests/test_next_header_remove_by_kind.py` (11 tests): against unfixed app.js 9 failed, 2 passed
  (the 2 are known-negatives that hold either way); with the fix 11 pass.
- All `tests/test_next_*.py`: green. `node --check app.js` OK on Node v26.0.0 (default node here is 18.16.1).
- Run with PGVECTOR_PORT=1 and a SQLite REGISTRY_DATABASE_URL.

## Not verified
- No live browser check of the button on a database, on Schema Inventory, or a filesystem.
- Tests are source-level pins (no Node runner in CI), not behaviour.
- "Not touched" claims rest on reading `remove_database`/`remove_filesystem`/`remove_project`; rows keyed by slug
  in other tables (dispositions, working set, investigation membership) were not audited, so the text makes
  no claim about them.
- Classic's silent swallow of a non-OK delete (CL:6402) is untouched.
