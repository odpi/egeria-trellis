# Resource controls placement -- implemented (branch `re/resource-controls`, 2026-10-03)

Implements `REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md` (wireframe `ResourceControls.dc.html`, canvas page 17; both read from
origin/re/design-resource-controls @ dcd9609e). Base: origin/main cf52f4a9.

Environment line (before any test): `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-resource-controls/packages/resource-explorer/resource_explorer/__init__.py`.

## What changed, per control

Files: `web/static/next/app.js`, `index.html`, `feedback.js`, `tailwind-next.css` (rebuilt, Node 20.11). Nothing else.

- **Top-bar name -> the resource's menu.** `#scope-slug` is now a `<button>` reading `<slug> ▾` (disabled, "no resource selected",
  when nothing is selected). It opens `#resource-menu`: **Hide from my list** / **Unhide** with a one-line explanation, a rule,
  then **Remove from Resource Explorer…** last, in ink (no accent anywhere in the menu). Escape, an outside click, or the name
  again closes it. `renderTopBar()` is exported (harness precedent: `export` only) and binds the button itself, so the menu
  works on every stage, as the bar is not redrawn per pane. `feedback.js` read the slug from the element's text, which now has
  the `▾`; it reads `data-slug` first (falls back to the text for old markup).
- **Remove -> a panel under the top bar.** `#resource-controls-panel` is inserted directly after `<header>`, never in the content
  pane's `#resource-action`. It carries the existing per-kind sentence (`removeConfirmationHtml`) in ink, "This cannot be undone"
  in words, and a commit button that names the object: "Remove the database laz_local_adventureworks" / "the repo X" /
  "the file system X". The commit is the only accent. Commit still dispatches by kind via `removeEntity`
  (`DELETE /api/projects/{slug}`, `/api/databases/{slug}`, `/api/filesystems/{slug}/`), prunes that kind's list, clears the
  selection, moves to the next resource, and re-renders sidebar/top bar/rail scope/pane (the stale-rail clear in `loadPane`
  fires from there). The outcome ("Removed the database X from Resource Explorer.") and any refusal ("Not removed: ...", in
  the warn tone, not accent) land in the same panel. A panel stamped for resource A is dropped when the selection moves to B.
- **Hide / Unhide** moved to the menu unchanged underneath (`POST /api/discovery/working-set` with the kind's entity type); its
  one-line outcome shows in the same panel instead of the content slot.
- **Content header** lost hide and remove; it keeps name, slug, links, provenance, credential banner and the disposition pill
  (the pill's picker still opens in the header's own `#resource-action` slot).
- **Select mode.** `☐ Select` -> bordered **Select several…** / **Done selecting**; when on, the bar's first line is "Tick
  resources, then act on all of them." Shift-click or ⌘/Ctrl-click a row enters the mode with that row ticked (and, once on,
  toggles that row) without opening it. When the list is narrowed by a disposition facet, a text filter, or (repos) a scope
  chip, the count line offers **select these N**, which enters the mode with exactly those ticked.
- **Select bar** is grouped on separate lines: scope (＋ scope, − scope); judgement and lists (mark as…, save as work list); view
  (hide); then a rule and the quiet bordered **remove…** (+ muted "from Resource Explorer"). With resources ticked the button
  reads "Remove 1 database…" / "Remove 2 databases…" (see deviations). Never primary.
- **Bulk confirmation** uses `removeConfirmationHtml(kind, [slugs])` (it now takes a list; the single-resource text is byte-for-byte
  what it was apart from "filesystem" -> "file system"), in ink, plus the list of slugs; commit reads "Remove 3 databases" /
  "Remove 1 database", the only accent. Handlers renamed `confirmBulkRemove` / `bulkRemove`, actions `sel-remove*`.
- **One word.** No label or tooltip in `static/next` says "delete" for removing a resource. The sentences that describe what
  happens to RE's own records ("delete its local survey records") are kept, as in the wireframe.
- **Find** is a word: **＋ Find repos / ＋ Find databases / ＋ Add a file system** (bordered accent, right end of the kind row).
  The `?` stays an icon. An empty db or file-system list says "No databases registered. ＋ Find databases" with the same control
  inline. `data-act="find-repos"` is unchanged, so the existing Find dialogs and their tests are untouched.

## Deviations from the reply, and from the brief

1. **Bulk bar button wording.** The reply says the button is `remove…` and also that with one ticked the bar "still says 'Remove 1
   database…'". Both are honoured: `remove…` (disabled) with nothing ticked, "Remove N <kind>…" once something is, still quiet
   and bordered. The confirmation's commit has no ellipsis ("Remove 3 databases").
2. **Menu text avoids "deleted".** The reply's menu line "nothing is deleted" and the bulk tooltip "deletes RE's own records"
   contradict its own "delete is retired from labels and tooltips". Followed the retirement: the menu line reads "A view
   preference: it changes only your list. The resource stays registered."; the tooltip reads "unregisters these and drops RE's own
   records about them. The sources themselves are not touched." The hide outcome says "nothing was lost" (was "nothing was deleted").
3. **File system, not filesystem**, in the new button/commit text and in the single-resource sentence ("Unregister the file
   system X"). Existing placeholders ("Filter filesystems…", "No filesystems registered.") were not touched.
4. **Select-bar scope wording not changed.** The reply says "＋ add to Customer 360" / "− remove from Customer 360" replace "＋ scope /
   − scope", but that wording (and "add to an investigation…" picker with no investigation) belongs to
   `REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md` §4, a separate slice that touches the same buttons. The brief's list for this
   slice did not include it. Also "save as work list" stays without "…". Both deferred to that slice.
5. The `<slug> ▾` button sits in the header, which gained `relative`; the menu is absolutely positioned from the button's offset.

## Deferred / left open

- Narrow-screen question (reply "Left open" 1): not checked on a phone-width window; no second entry point added.
- "Open in Classic" in the menu: left out, as the reply suggests.
- Scope-button wording (above).
- Keyboard: Escape closes, first item is focused; arrow-key movement inside the menu is not implemented (Tab works).

## Evidence

Tests: new `frontend-build/test-harness/resource-controls.test.mjs` (46). It parses the shipped `index.html` body into jsdom, runs the real
`app.js` and router, clicks real buttons; only fetch is stubbed.
- On main (+ only the `export` on `renderTopBar` so the harness can reach it): **42 fail, 4 pass**. The 4 passes are known-negatives or
  guards that hold either way (plain click on a row does not enter Select mode; the pill's picker opens in the header slot;
  bulk hide + "＋ scope" refresh the open investigation page; the known-negative that the source-scan pattern would flag a
  "delete…" label).
- With the change: 46/46. Full harness: **320/320** (was 274). Python: all of `tests/test_next_*.py`, `tests/test_tailwind*.py`,
  `tests/test_static_js_syntax.py`: **704 passed** (`PGVECTOR_PORT=1`, SQLite `REGISTRY_DATABASE_URL`, explicit globs of those files only).
- Pins updated, none deleted: `test_next_header_remove_by_kind.py` (remove handler is now `commitResourceRemoval` /
  `openRemovePanel`), `test_next_sidebar_list_db_fs.py` (`bulkDelete` -> `bulkRemove`; hide pin reads `toggleHiddenFromMenu`),
  `test_next_nav_grouping.py` (search window over `renderTopBar` widened 2000 -> 4000 chars). The tailwind tripwire on dynamic
  class interpolations was tripped (94 vs 75+15) by helper-variable class strings; fixed by writing the classes inline, not by
  raising the number.
- `node --check` on `.mjs` copies of `app.js`, `feedback.js` and the new test: ok (Node 20.11).
- Covered by the harness: menu on every stage x repo/db/file system; panel position (next sibling of `<header>`, outside `#content`,
  `#resource-action` untouched); commit names the object and is the only accent; warning is ink; header has no hide/remove and keeps
  the pill on every stage; three doors into Select; "select these N" (and its absence unfiltered); bar groups/order/rule/quiet remove;
  "Remove 3 <kinds>" / "Remove 1 <kind>" for all three kinds; per-kind DELETE routes and list pruning (single and bulk); a refused
  removal keeps the row; hide/unhide POST bodies; stale-rail clear after removal; open-investigation refresh after "＋ scope"; Find
  by kind and inline empty state; a rendered-text scan and a source scan for "delete" (allowlist documented in the test: admin
  panes, curation notes, schedules, JS identifiers, comments, effect sentences).

## Not verified

- **No real browser.** Menu placement (absolute, from `offsetLeft`/`offsetTop`; jsdom reports 0), stacking over the nav, the panel's
  look at width, the wrap of the bar's lines in the sidebar and phone-width behaviour are unseen. The Tailwind classes exist in the
  rebuilt CSS (coverage test green) but nothing has been rendered with them.
- ⌘-click and Ctrl-click are dispatched as synthetic events; on macOS a real Ctrl-click is a context-menu gesture in some browsers.
- Backend routes were not exercised (fetch stubbed); no Postgres, Prefect or Egeria was touched.
