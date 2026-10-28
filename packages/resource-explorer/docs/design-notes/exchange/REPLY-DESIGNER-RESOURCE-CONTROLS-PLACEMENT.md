# REPLY — Designer: where a resource's hide, remove and disposition controls live (2026-10-03)

To ASK-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md, read against main at
cf52f4a9 (`next/app.js` `resourceHeaderHtml`, `selectActionsHtml`,
`confirmBulkDelete`, `removeConfirmationHtml`, the sidebar's kind row;
`next/index.html`'s header). Drawing: `wireframes/ResourceControls.dc.html`,
canvas page 17.

## Why the owner looked where he did

Both misses point at something on screen, not at the owner:

- **The top bar's resource name looks like a control and isn't one.**
  `#scope-slug` is drawn with a border in the accent-deep color, in accent
  text: the exact treatment controls get. It's the one bordered, accent
  thing in the bar, sitting by the resource's name. Looking there for the
  resource's actions was the right instinct. The screen promised something
  it didn't deliver.
- **The header's `remove` is styled as a link**, the same underlined accent
  text as `GitHub ↗` and `hide`, at the same size, on a line that's mostly
  links. A destructive act that reads as navigation gets passed over.
- **The left pane is where Classic put it**, and where the bulk version
  still is, behind `☐ Select`.

## 1. The primary home for removing one resource

**The resource's own menu, on its name in the top bar.** `#scope-slug`
becomes what it already looks like: a button, `laz_local_adventureworks ▾`.
It opens a small menu for the resource:

- **Hide from my list** / **Unhide**, with "a view preference: only your
  list, nothing is deleted"
- *(a rule)*
- **Remove from Resource Explorer…**

The menu goes there because the top bar is the one place that names the
current resource on **every stage and every sub-tab**. The content header
is redrawn per pane. The owner went there second, and the border already
told him to.

**The left pane keeps removal for many, not for one.** Select mode is "act
on these N". It keeps `remove…` as its last action, separated from the rest
(§5). The two never read as duplicates because they differ in number: the
menu acts on the resource you're looking at, the bar on the ones you
ticked. With exactly one row ticked, the bar still says "Remove 1
database…". It doesn't change wording to look like the single-resource
control.

**The content header loses `hide` and `remove`.** It keeps the name, the
links, the provenance line and the **disposition pill**. A verdict is a
judgement about what's on the page, and it belongs beside the content being
judged (§5).

## 2. A destructive control on the title line

**No: its own place, and it looks like a control.** In the resource menu,
`Remove from Resource Explorer…` sits below a rule, last. It's in ink, not
accent, because it's an ordinary menu item and the accent is reserved for
the control that commits. Choosing it opens the per-kind confirmation, which
is already well worded (`removeConfirmationHtml`), **as a panel under the
top bar**. It doesn't go in the content pane's `#resource-action` slot,
which can be off-screen. The panel's commit button is the only accent in it,
and it names what goes: **"Remove the database laz_local_adventureworks"**,
not "Remove".

A "more" menu would usually hide things. The difference here is that this
menu sits on the one element the owner already tried to click.

One fix that comes with it: the header confirmation's sentence is drawn in
`text-accent-ink`. That's a warning shown in the control color, which the
house rule forbids. The sentence goes to ink. The irreversibility is said
in words ("This cannot be undone"), which is enough.

## 3. Is Select mode discoverable?

**Keep the toggle, because checkboxes on several hundred rows would compete
with every row's marks. Make it a visible control, and give it two other
ways in:**

- `☐ Select` becomes a bordered button reading **"Select several…"**. When
  it's on, it reads **"Done selecting"**, and the bar's first line says
  "Tick resources, then act on all of them."
- **Shift-click or ⌘/Ctrl-click a row** turns Select mode on with that row
  ticked, which is the convention every list teaches.
- When the list is filtered to a facet (a disposition chip, "In scope"),
  the count line offers **"select these N"**. This is the most common
  reason to bulk-act: "everything I marked ignored".

## 4. One word

**"Remove", always, with its object: "Remove from Resource Explorer".**
"Delete" suggests the database or the files are deleted, which is the one
thing this never does. The tooltip's "delete all local survey data" is the
same misreading in small print.

- The bulk button: **`remove…`**, tooltip "Remove from Resource Explorer:
  unregisters these and deletes RE's own records about them. The sources
  themselves are not touched."
- The bulk confirmation uses **the same per-kind sentence** as the single
  one. `removeConfirmationHtml` takes a list. The sidebar is scoped to one
  kind, so a bulk remove is always one kind and the sentence fits. Its
  commit button reads **"Remove 3 databases"**.
- The bulk `delete…` button is currently styled as a primary action (accent
  border), the same weight as `＋ scope`. It becomes a quiet bordered
  control. Only the confirmation's commit button carries the accent.

"Ignored" stays distinct, as the confirmation already says: an ignored
resource is still registered.

## 5. Three weights on one line

**No. Each weight gets its own place:**

| Act | Weight | Single resource | Several |
|---|---|---|---|
| disposition | a judgement about the resource | header pill, beside the content | "mark as…" |
| hide | a view preference, yours only | resource menu | "hide" |
| remove | unregister, not reversible here | resource menu, last, after a rule | "remove…", last, after a rule |

**The Select bar is grouped the same way**, each group on its own line, in
this order:

1. **Scope:** "＋ add to Customer 360" / "− remove from Customer 360", the
   wording ruled in `REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md` §4,
   replacing "＋ scope / − scope".
2. **Judgement and lists:** "mark as…", "save as work list…".
3. **View:** "hide".
4. *(a rule)* **"remove…"**.

"− remove from Customer 360" and "remove…" now share a verb. The object
always follows it ("from Customer 360", "from Resource Explorer"), so they
don't read alike. The scope one is the frequent, harmless act, and it's
first. The other is last, after a rule.

## 6. Find carries a word

**Yes. Replace the bare ⊕ with a text button that names the kind:**
**"＋ Find repos"**, **"＋ Find databases"**, and for file shares
**"＋ Add a file share"**. File shares have no discovery yet
(`FIND_TITLE.filesystem` is "Register a filesystem path"), so the button
says what it does today. It sits at the right end of the kind row, where
the icon is, and fits beside three short chips at sidebar width. The `?`
icon stays an icon: marks-help is a lookup, not an act.

An icon is fine for something people learn once and use daily. Find is
used rarely and per kind, and its meaning changes with the chip beside it.
That's the case where a label is needed. The list's empty state ("No
databases registered.") gets the same control inline:
"No databases registered. **＋ Find databases**".

## What the slice changes

| Change | Where |
|---|---|
| `#scope-slug` becomes a button with the resource menu (hide, remove) and the remove panel under the top bar | `next/index.html`, `renderTopBar`, header bindings |
| Header drops `hide` and `remove`; keeps the disposition pill | `resourceHeaderHtml` |
| Remove confirmation sentence in ink; commit button names the resource | `removeConfirmationHtml` |
| Select: labeled toggle, shift/⌘-click entry, "select these N" on a filtered list | sidebar |
| Bulk bar grouped by weight; `delete…` → `remove…`, quiet; per-kind sentence for N | `selectActionsHtml`, `confirmBulkDelete` |
| ⊕ → "＋ Find <kind>" | sidebar kind row; empty states |

No behavior changes. Every act does what it does today, from a different
place and under clearer words.

## Left open

1. On narrow screens the top bar wraps. Does the resource menu need a
   second entry point in the content header there? I'd say no, since the
   crumb is still shown, but it's worth checking on a phone-width window
   during the gate.
2. Should the resource menu also carry "Open in Classic" while Classic
   exists? It's a navigation act, not management, so I've left it out.
