# `scouting-overview` 404 on database slugs — implemented

**Found:** Section D agent's live-render check, logged in `docs/Backlog.md`, 2026-09-28.
**Branch:** `re/scouting-overview-repo-only-gate`, off `origin/main` `4cbf8d06`.

## The bug

Every `/next` page load called `getScoutingOverview(slug)` (`app.js`'s `loadPane`, inside the
Questions-tab renderer) regardless of resource type. `GET /api/projects/{slug}/scouting-overview`
is a repo-only endpoint, so this 404'd every time for a database or filesystem slug — the same
class of bug as the earlier `'db'`/`'database'` resourceType mismatch: a call written against one
resource type and never gated for the others.

## Fix

One-line gate in `loadPane`, `app.js`:

```js
if (state.resourceType === 'repo' && state.overview?.slug !== slug) {
  state.overview = null;
  getScoutingOverview(slug)
    ...
```

No design question here, per the Backlog item's own note — purely "don't call an endpoint that
doesn't apply to this resource type."

## Tests

New `tests/test_next_scouting_overview_repo_only_gate.py`. `loadPane` isn't exported and is too
large/DOM-entangled to run standalone under node, so this follows the source-level regression
pattern `tests/test_next_resource_header_publish_note.py` already established for the same
limitation on a different function: extract the function's source text, assert the call site is
guarded by `state.resourceType === 'repo'` in its nearest enclosing `if`, and assert the call still
exists at all (guards against a future refactor silently deleting the block instead of gating it).

Full suite (no `-k`, `-rf`): 6684 passed, 103 skipped, 0 failed, 637.10s.

## Coordination

Confirmed with the "Resource Explorer expansion architecture" session before committing — G2
(`re/g2-questions-headline-slot`, PR #330) was already merged into `main` by the time this branch was
created, so no separate merge-tree check against it was needed; this branch's base already includes
it.

## Status

Pushed to `origin/re/scouting-overview-repo-only-gate`. No PR opened — the "Resource-explorer PR/CI
merge" session batches them.
