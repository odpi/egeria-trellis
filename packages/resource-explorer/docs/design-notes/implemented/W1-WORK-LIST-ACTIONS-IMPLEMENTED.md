# W1-B implemented: the work list's two actions into an investigation

Slice W1-B of `REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md` §2.

Environment proof: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-w1-actions/packages/resource-explorer/resource_explorer/__init__.py`.
Built on origin/main a33ed0d9 (after #459). Branch `re/w1-work-list-actions`.

## What changed

- **Add action.** `worklist-actions.js` (new): "Add these N to <name>", "Add N selected to <name>",
  "Add to an investigation…" (none current: the W1-A picker opens first, the chosen investigation becomes
  current). Names cut at 24 characters, full name in the title. The note says "Added 2 to Customer 360. 7 were
  already in scope; their reasons were kept." Sits beside "publish to Egeria" on the pane and on every row of the
  list of work lists (the index row is now a div holding the open button and the two action buttons; a button
  inside a button is invalid).
- **Start action.** "Start an investigation from this list…": a dialog prefilled with the list's name and
  description, purpose chips unselected, "Where it lives" shown with the model's default (Egeria). Start creates
  the investigation (default classification), adds the members, tags the list, makes it current. Each step after
  the create reports its own failure in the note. The list stays and moves to "Work lists for <name>".
- **Routes** (`web/routes/work_lists.py`, `work_lists.py`):
  - `PUT /api/work-lists/{slug}/investigation` `{investigation}` links a saved list; `''` unlinks; 404 for an
    unknown list or investigation. `WorkLists.set_investigation`.
  - `POST /api/work-lists/{slug}/add-to-investigation` `{investigation, entity_slugs?}`: each member's
    `rationale` becomes `membership_rationale` ("from work list <name>" when empty); a member already in scope
    is skipped and reported (`added`, `already_in_scope`), `keep_existing=True` as a second guard; 404 / 409
    (not open) / 422 (none named, or a slug not on the list). It does not tag the list; the PUT does.
- **Linked header and scope column** (`worklist.js`): "7 of 9 in scope for Customer 360 · 2 not added", read
  live from `list_investigation_members` (same kind only) every time the pane opens or an add lands; a scope
  column ("in scope" / "not in scope") in ink, no glyph, no accent; the phone view says it in its sub-line.
  An unreadable scope says so ("could not be read", column "scope unknown"), never "0 in scope".
- **▦ index** shows the investigation's name, not its slug.
- `stages/investigation.js`: `vocab()` is exported as `investigationVocab()` (the Start dialog reuses it).
- `re-api.js`: `linkWorkListInvestigation`, `addWorkListToInvestigation`.

## Tests

`frontend-build/test-harness/work-list-actions.test.mjs` (18 tests; 17 red on the old code, the 18th is a
source-scan guard that is green either way). `tests/test_work_list_investigation_routes.py` (8 tests; 6 red
without the route code, the two 404 cases pass either way). `tailwind-next.css` needed no regeneration.

## Left for W2

- The investigation page's grid and its "work lists: …" link (§3).
- Linking a list to a second investigation is one-at-a-time: `work_lists.investigation` is a single value.
- "Add" does not tag an unlinked list; only Start (and the PUT) does. There is no UI yet for tagging an
  existing list by hand.
- Not verified in a real browser, against live Egeria, or against Postgres.
