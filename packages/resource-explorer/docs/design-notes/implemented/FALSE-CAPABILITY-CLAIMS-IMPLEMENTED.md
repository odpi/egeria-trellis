# False capability claims in /next: implemented

Source: CLASSIC-VS-NEXT-PARITY-2026-09-30.md s.7c (C-02..C-04, R-42).

Verified import path: `/Users/dwolfson/localGit/egeria-v6/trellis-re-false-capability-claims/packages/resource-explorer/resource_explorer/__init__.py`

## Claim 1: `stages/curate.js` `nonRepoCurateHtml`
- Before: "tags, feedback and curator notes ... are reachable from the resource header regardless of type".
- Reality: backend routes exist (`routes/curate.py` 111-282) but nothing in `static/next` calls `/api/curate/tags|notes|feedback/{type}/{slug}` (only `admin/feedback.js`, a cross-resource list). The header has no such control. FALSE.
- After: says this view has no controls for tags, resource feedback or curator notes, for any type.

## Claim 2: `app.js` `publish_stale` sentence
- Before: "publish again from the Analysis pane".
- Reality: `/next` has no publish control in the Analysis pane (only `worklist.js` publishes, a work list, a different thing); the code comment above it already said so. FALSE.
- After: "this view has no publish control, so they are not republished from here". Comment updated.

## Tests
`tests/test_next_false_capability_claims.py` (source-level pins + known-negative that /next has no tag/note/feedback caller outside admin). Unfixed: 2 FAIL, 1 pass. Fixed: 3 pass. `-k "next or curate"`: 786 passed, 2 skipped. PGVECTOR_PORT=1, SQLite registry.

## Not verified
Not exercised in a browser. `node --check` ran on Node v26 (copied as .mjs). Wording does not point users to Classic, since that path was not part of the brief.
