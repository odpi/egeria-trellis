# W1-C implemented: vocabulary, sidebar, stage click

Slice W1-C of `REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md` (§1 and §5).

Environment proof: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-w1-vocab/packages/resource-explorer/resource_explorer/__init__.py`.
Built on origin/main 346770d4. Branch `re/w1-vocab-sidebar-stage-click`.

## What changed

- **Vocabulary.** The investigation page's "Members (N)" heading is "Scope · N" (section id `inv-scope`);
  the investigation list's "Members" column is "Scope". The list of work lists (the ▦ front door) opens
  with the one-sentence definition, in the empty state too. "Working set" appears nowhere as a label.
- **Sidebar** (`workListsSidebarHtml()`, directly under the investigation selector). With an investigation
  current: "Scope · N" (opens the In scope chip), "Work lists for <name> · N" with its lists, "Other work
  lists · N" folded in a closed `<details>`. With none current: "Work lists · N" listing every bench.
  "Suggestions · N" (`suggested-to-*`) is its own folded line, with a sentence saying they are inboxes.
  Counts are `.length` of arrays split from the real `state.workLists` rows; the scope count is
  `listInvestigationMembers().length` across every kind (`state.scopeCount`, `null` shown as "–" when the
  read failed). No route argument was needed: every row already carries `investigation`, so filtering
  client-side returns exactly what `list_all(investigation=)` would.
- **Stage click.** One check in front of the work-list branches in `loadPane()`, reading `STAGES[].class`
  through `stageClassOf()`. Run stages keep the list open; the title line reads
  "Sales databases · 9 databases · Assessment’s questions". Investigation, Understanding and Automate
  clear `workListSlug` into `lastWorkListSlug`, so the existing "↩ <list>" link appears. Because
  `loadPane()` now closes a list on a non-run stage, every place that opens a list
  (sidebar row, index row, the back link, save-as-work-list) first moves to `state.lastRunStage`
  (`ensureRunStageForList()`); otherwise the list would open and close at once.
- **Special case.** Investigation clicked while the open list's `work_lists.investigation` names an
  investigation that exists: that investigation's detail opens with its Scope section scrolled into view,
  even if it is not current (the current investigation is not changed).
- The nav's "▦ Work lists N" count and the index list exclude suggestion inboxes, since they are not benches.

## Tests

`frontend-build/test-harness/work-lists-vocab-sidebar-stage-click.test.mjs` (19 tests, all red on the old
code). `find-dialog-fixes.test.mjs` line 202 pinned the old "Members (3)" and now pins "Scope · 3".

## Left for W1-B and later

- The work-list pane's two actions ("Add these N to <investigation>", "Start an investigation from this list…").
- The linked header "7 of 9 in scope for <investigation> · 2 not added" and the per-row scope column.
- The header link from an investigation's Scope section back to its work lists (§3); the way back today is the
  "↩ <list>" link.
- Nothing writes `work_lists.investigation` after save (no route yet), so the special case only fires for lists
  saved while an investigation was current.
- The Scope line opens the In scope chip, which filters repos only (pre-existing); its count is every kind.
- Not verified in a real browser.
