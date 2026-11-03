# `/next` render harness — implemented

**Coordinator brief:** build a node+jsdom based test harness so `/next`'s
vanilla-JS frontend can actually be loaded and rendered in a test
environment, and use it to write two regression tests that neither of the
package's existing test styles (Python, or JS source-text assertions)
could have caught: the engine-note-persistence bug and the Schema
Inventory filter-then-expand bug (Slice 22).

**Decision (design session, 2026-09-28):** every /next fix from here on adds its regression to the harness, not only a source-text test.

## Why this had no coverage before

Every JS-related test in this package before this branch (`grep tests/
test_next_*.py`) asserts against app.js's **source text** — does the code
call the right function, contain the right string, avoid a banned pattern.
That style genuinely works for a large class of bugs, and has caught real
ones. It cannot catch a bug about what the DOM looks like **after a second
render** — a pane switch, a reload, a filter-then-expand — because it
never actually renders anything; it reads the code that would render.

Two real bugs shipped this exact class of defect in one week
(2026-09-27/28):

1. **Engine-note persistence** (`re/engine-note-persist`,
   `ENGINE-NOTE-PERSISTENCE-IMPLEMENTED.md`):
   `launchSurvey()` appended the "which engine ran this" line as a
   transient DOM write, then called `loadSurveyPane()` a few lines later —
   which re-renders the whole pane and wipes it. The code called the right
   functions in the right order; the bug was still real.
2. **Schema Inventory filter-then-expand** (Slice 22 follow-up, commit
   `93e50e27`): a matched table's `<details>` opened, but its column rows
   stayed `display: none` because `data-tree-text` conflated "this node's
   own name" with "this node OR any descendant's name," so `filter
   SchemaTree`'s per-node matching hid a column row that was itself
   invisible to the query, even though its parent table was visibly open.

A separate class of bug caught the same week (Tailwind class staleness,
`TAILWIND-NEXT-FRESHNESS-CHECK-IMPLEMENTED.md`) is a
different failure mode — a *build* artifact going stale, not a render
bug — and already has its own CI check; this harness is not trying to
cover that too.

## What was built

**`frontend-build/test-harness/static-loader.mjs`** — a Node ESM loader
hook (`node:module`'s `register()`, stable from Node ≥ 20.6) that resolves
`/next`'s own root-relative import specifiers (`'/static/next/
envelope.js'`, `'/static/re-api.js'`, etc.) to the real files under
`resource_explorer/web/static/`. Confirmed empirically (not assumed) that
without this, Node treats a specifier starting with `/` as an absolute
filesystem path and fails looking for it at the OS root — `import('/
static/next/a.js')` errors `Cannot find module '/static/next/a.js'`, not
a project-relative resolution.

**`frontend-build/test-harness/dom-harness.mjs`** — `makeDomEnvironment()`
builds a fresh jsdom `window`/`document` per test and installs the small
set of globals app.js's own module top level needs to import cleanly:
- `Auth` (a global, not an ES import — loaded via a classic `<script>` tag
  in `index.html` in the real app) — stubbed with a no-op `init` that
  **never invokes its callback**. That callback is `start()`, app.js's
  full page bootstrap (a dozen parallel network fetches). Not calling it
  is deliberate: this harness has no server, and neither regression test
  needs the bootstrap path — both call a specific exported render function
  directly with fixture data.
- `fetch` — stubbed to **throw loudly** (`_UNSTUBBED_FETCH: ...`) rather
  than hang or silently return nothing, so a test that accidentally
  exercises a network path fails with a clear message.
- `sessionStorage`/`localStorage`/`navigator`/etc. from jsdom's own
  `window`.

`loadAppModule()` imports the real, unmodified (except for the four
`export` additions below) `app.js` — its actual dependency graph:
`worklist.js`, `format.js`, `glyphs.js`, `envelope.js`, `re-api.js`, and
the `stages/*`/`admin/*` modules it pulls in — with a cache-busting query
string so each test gets its own top-level `state` object rather than
silently sharing app.js's module-singleton state across tests.

**Four `export` additions in `app.js`, no logic changed**: `surveyRowHtml`,
`schemaTreeHtml`, `tableHtml`, `filterSchemaTree` were module-private;
they now carry `export` so the harness can call the REAL functions
directly, rather than re-extracting or re-typing their source into a test
fixture (the pattern the existing `test_next_*.py` files use for
functions they can't import). This is the same precedent app.js already
sets for `readEnvelope`/`esc`/`$`/30-plus others — see `envelope.js`'s own
header comment on splitting render logic out specifically so a node-run
test could import it without the file's top-level DOM/auth side effects.

## The two tests

**`frontend-build/test-harness/engine-note-persistence.test.mjs`** (3
cases) — renders `surveyRowHtml(candidate)` into a container **twice**,
simulating exactly what `loadSurveyPane()`'s re-render does
(`container.innerHTML = candidates.map(surveyRowHtml).join('')`), and
asserts the engine-note line is present and correct **after the second
render**. Also covers the fallback (local-execution) note's distinct
styling, and that no note renders when `last_run_engine_note` is empty
(never run).

**`frontend-build/test-harness/schema-inventory-filter.test.mjs`** (4
cases) — builds a real schema tree via `schemaTreeHtml()` (two tables,
one matching a filter query, one not), calls the real `filterSchemaTree()`
against it, and reads back each row's actual `.style.display`/`.open` —
not just the returned HTML string, which a source-text test could already
inspect. Covers: a table-name match opens the table AND makes its column
rows visible (the exact bug); a non-matching sibling table is fully
hidden; a column-name match opens its owning table and hides the other
table; clearing the filter restores every node to visible.

**Gap flagged, not guessed past:** a related-but-different bug from the
same gate (`filterTreeNode` force-opens every matched table's `<details>`
unconditionally, so two simultaneous table matches both dump their full
column lists at once) is documented as deferred in commit `b033a6f9` and
is **not** fixed on `main` as of this branch. It is deliberately **not**
tested here — only the original, fixed filter-then-expand bug is covered.
Picking that follow-up up later should add its own test to this same
file, per the rule `b033a6f9` already recorded (auto-open only when a
table is the sole match).

## CI wiring

`.github/workflows/resource-explorer.yml`, alongside the existing
Tailwind-freshness `npm ci` step:
- new **"Set up Node"** step (`actions/setup-node@v4`, pinned to Node 20)
  — added because the loader hook needs Node ≥ 20.6 and the runner image's
  preinstalled Node version isn't something this workflow should depend on
  by chance (the existing hookless `node -e`/`--check` calls elsewhere in
  this suite got away without pinning; a loader hook should not gamble on
  the same luck).
- the existing **"Install frontend-build JS dependencies"** step (`npm
  ci`) now also installs `jsdom` (added to `frontend-build/package.json`
  devDependencies) alongside `tailwindcss`, since both live in the same
  `package.json`.
- new **"Run the /next render harness tests"** step (`npm run
  test:harness`, i.e. `node --test test-harness/*.test.mjs`) — runs
  immediately after, before the Postgres-dependent Python steps.

## Red/green verification

Per the design session's explicit gate: both tests were checked against
the code as it existed **before** their respective fix, to confirm they
actually fail there, not just that they pass now.

**Schema Inventory filter test**, against `93e50e27^` (the commit
immediately before the Slice 22 filter-recursion fix), with the same four
`export` additions applied to that older source (needed only so the test
file can import the functions — no other change):

```
node --test test-harness/schema-inventory-filter.test.mjs
# RED (pre-fix): 1 pass, 3 fail
#   "filtering by a TABLE name..." / "a sibling table..." /
#   "filtering by a COLUMN name..." all failed — the old concatenated
#   data-tree-text meant a table's own `data-tree-text` was never equal to
#   just its own name, so the test's own node lookups came back empty.
#   ("clearing the filter" passed both before and after — it exercises no
#   matching logic, so it isn't expected to distinguish the two versions.)
```

Restored to post-fix `app.js`:

```
# GREEN (post-fix): 4 pass, 0 fail
```

**Engine-note persistence test**, against `origin/main` as of this
branch's base (i.e. before `re/engine-note-persist` — `engineNoteHtml`
does not exist on that commit at all), with the same four `export`
additions applied:

```
node --test test-harness/engine-note-persistence.test.mjs
# RED (pre-fix): 1 pass, 2 fail
#   "engine note survives a pane re-render..." and "the fallback... note
#   also survives..." both failed on the FIRST render already (surveyRowHtml
#   pre-fix has no engine-note rendering at all — the note used to live
#   only in a transient #survey-note DOM write that surveyRowHtml never
#   participated in, so a direct call to surveyRowHtml can't reproduce it
#   either way). The property under test — "the row itself carries and
#   survives the note" — is absent pre-fix regardless of render count,
#   which is what the test is for.
#   ("no last_run_engine_note renders no note" passed both before and
#   after, for the same reason as the schema test's clear-filter case.)
```

Restored to post-fix `app.js` (`re/engine-note-persist` merged into this
branch — see Merge-order dependency below):

```
# GREEN (post-fix): 3 pass, 0 fail
```

**Full harness suite, post-fix, both files together:**

```
npm run test:harness
# tests 7, pass 7, fail 0
```

## Merge-order dependency on `re/engine-note-persist`

This branch (`re/next-render-harness`) has `origin/re/engine-note-persist`
(PR #345, commit `89904e0c`) merged into it — the engine-note test needs
`engineNoteHtml`/`c.last_run_engine_note` rendering in `surveyRowHtml`,
which does not exist on `main` yet as of this branch's creation. Per
coordination with the PR/CI session: **`re/engine-note-persist` should
land first (or at the same time as) this branch.** If this branch's own
PR is reviewed/merged before #345, its CI will show the engine-note test
failing (or the harness failing to import `engineNoteHtml`) until #345
lands — that is expected, not a regression in this branch, and resolves
itself once #345 merges and `origin/main` is merged back into this branch
to drop the duplicated diff.

## Fallout from the `export` additions: one pre-existing test's terminator

`tests/test_next_schema_inventory_filter.py`'s own source-text `_fn()`
helper slices `schemaTreeHtml`'s body by searching for the literal string
`"\n}\n\nfunction tableHtml"` as the end-of-function marker. Adding
`export` to `tableHtml`'s declaration changed that literal text to
`"\n}\n\nexport function tableHtml"`, so both call sites using that
terminator (`test_schema_search_text_is_the_schema_name_alone`,
`test_schema_row_also_names_its_own_kind`) stopped matching and failed
with `ValueError: substring not found` — caught by the full `pytest
tests/ -q -rf` run this branch's own report requires. Fixed by updating
the two terminator strings to match. `tests/test_engine_note_
persistence.py`'s own `_fn()` helper was unaffected — it searches for
`f"function {name}("` as a substring (which still matches inside
`"export function ..."`) and terminates at the next bare `"\n}\n"`
regardless of what follows, so it needed no change.

## What was NOT built

- No coverage of `launchSurvey()`'s real async flow (the actual
  `runSurveyDefinition`/`pollActivity` network sequence) — that would need
  stubbing most of `re-api.js`'s fetch surface for one test's sake, more
  than the acceptance property asked for. The regression property under
  test — the row's engine note survives a wholesale pane re-render — is
  fully exercised without it, since that re-render is exactly what
  `loadSurveyPane()` does with whatever candidate data it fetches.
- No coverage of the deferred multi-match auto-open follow-up (see Gap
  flagged above).
- `filterTreeNode`/`directTreeChildren` stay module-private; only the
  public `filterSchemaTree()` entry point (the same surface a real filter
  keystroke drives) is exported and exercised.
