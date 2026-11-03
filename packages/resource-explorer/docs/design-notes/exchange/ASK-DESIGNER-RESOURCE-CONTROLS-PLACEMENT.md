# ASK — Designer: where a resource's hide, remove and disposition controls live (2026-10-01)

From the coordinator, for the designer session, at the owner's request.
Reply as `REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md` in this folder; a
drawing under `wireframes/` is welcome. Background: the `#407` header-remove
fix (`DB-REMOVE-BUTTON-IMPLEMENTED.md`) and the owner's 8810 checks of
2026-10-01.

## Why now

Checking `#407` on 8810, the owner could not find where to remove a
resource, twice, in two different places:

1. First they looked in the **left pane**, where Classic had it. They found
   hide and delete there, inside **Select** mode, and did not see the header
   control at all.
2. Pointed at the header, they then looked in the **page's top bar**, not
   inside the white content area where the header link is, and needed it
   shown to them. The `remove` link is at the right end of the resource's
   title line, next to the disposition dropdown.

That is two independent misses by the person who uses this screen every
day. The control works (the `#407` check passed), so this is placement and
discoverability, not a defect.

## What exists today

Two places, with overlapping but not identical controls:

| Where | Controls | Scope |
|---|---|---|
| **Header**, right end of the title line | disposition dropdown, `hide`, `remove` | the one selected resource |
| **Left pane**, behind a `☐ Select` text toggle above the list | checkboxes, then an action bar: `＋ scope`, `− scope`, `hide`, `mark as…`, `save as work list`, `delete…` | the ticked resources |

Facts checked in the code (`next/app.js` on main `09eeb010`):

- The two paths are separate code. Bulk `delete…` dispatches by resource kind
  through `removeEntity`, as the header now does. Bulk `hide` writes the
  working-set flag per resource. Neither was affected by the `#407` bug,
  which was only the header's handler.
- Wording differs. The bulk button's tooltip says "Unregister entirely and
  delete all local survey data"; the header's confirmation is worded per
  kind, and says what it does **not** touch (the source database, files on
  disk, Egeria publications). The two should not disagree about what
  "remove" does.
- The sidebar's **Find** action is a bare **⊕ circle-plus icon** at the right end of the Repos / DBs / FS
  button row, next to the **?** icon, with no text label; only the tooltip says what it does, and the
  tooltip changes by kind ("Find and import candidate repos", "Find databases", "Register a filesystem
  path"). The owner (2026-10-02) could not find the database Find dialog or its Saved sources tab and had
  to be told where it was. It is the same kind of miss as the remove control.
- The Select toggle is a small text control at the top of the list. Nothing
  in the list suggests rows can be selected until it is on.
- `hide` means two things on screen: a view preference in both places, and
  "Show hidden" brings it back. `remove` and `delete…` mean unregister plus
  delete local survey data, which is not reversible here.

## Questions

1. **Where is the primary home for removing one resource?** The header (next
   to what you are looking at), the left pane (next to the list, like
   Classic), or both? If both stay, say what each is for, so the two never
   read as duplicates.
2. **Should a destructive control sit at the right end of the title line,
   in the same row as the disposition dropdown?** The owner overlooked it
   there. Does it want its own place, a "more" menu or a confirmed danger
   area, or is the fix that it looks like a control?
3. **Is Select mode discoverable enough?** It hides the bulk actions behind a
   text toggle. Should the checkboxes or a hint be visible without it, or is
   a toggle right for a list this size?
4. **One wording for "remove" and "delete".** Which word, and does the bulk
   tooltip need the same per-kind "does not touch" sentence as the header's
   confirmation?
5. **Do `hide` and `remove` belong on the same row as `mark as…`** (the
   disposition)? Disposition is a judgement about the resource; hide is a
   view preference; remove is an unregister. Are three different weights of
   action sharing one line right?

6. **Should Find carry a word, not only an icon?** The Find dialog (databases: Saved sources, Discover on
   a server, From a file) is where servers and sources live, and it is behind an unlabeled ⊕. Does it want
   a text label ("Find"), a place nearer the list's heading, or is the icon right once people know it?
   The same icon opens a different dialog per kind, so should the kind show on the control?

## Out of scope

The remove behaviour itself (settled and live) and the Classic parity rows
for it. This ask is only about where the controls are and how they are
named.
