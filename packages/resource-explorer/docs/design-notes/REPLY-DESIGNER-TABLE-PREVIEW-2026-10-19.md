# REPLY — Designer: previewing a table's rows (2026-10-19)

To ASK-DESIGNER-TABLE-PREVIEW-2026-10-19.md (on
`re/ask-designer-preview-and-scheduling-2026-10-19`), read against main at
55c5c6ea: `app.js` `SUB_TABS` (Schema Inventory, database-only),
`credential-change.js`, `glyphs.js`, and the owner's decisions recorded in the
ask. The scheduling ask has its own reply. Drawing:
`wireframes/PreviewAndScheduling.dc.html`, canvas page 23, panel A.

**The rule this rests on: a preview is an act, not a view.** Every other tab
reads RE's stored records. A preview queries the live database as the person
and shows data. So nothing runs when the tab opens. The person picks a table
and presses, and before the press the tab says what will happen: whose
credential, how many rows, shown and never stored, and recorded with their
name.

## 1. The tab, and how a table is picked

**"Table preview", database-only, beside Schema Inventory**, gated the same
way (`resourceTypes`). On every stage, because what a table holds is useful
at Discovery as much as at Curate. "Rows" alone would be read as row counts,
which the Schema Inventory already shows.

**Two doors to one tab:**

- **From Schema Inventory:** each table row gets "preview rows ›", which
  opens the tab with that table picked. This is the common path: a person
  sees a table in the inventory and wants to look inside.
- **On the tab:** a table picker with a filter box ("name contains…"),
  listing schema.table flat, because a database can have hundreds of tables.
  It isn't a schema-then-table pair of menus.

**Then one press:** "Show 20 rows". The count is a choice, 20 or 100. The
default is 20, which is enough to see what a column holds and small enough
to read. 100 is TestGen's precedent, for when 20 isn't telling.

**No paging.** Without an ORDER BY, "the next 20" isn't a defined set, and
adding an order would turn a preview into a query design. The header says
what it is:

> **sales.orders** · first 20 rows as the database returned them · not a
> sample, not ordered · as you (dan_ro) · 14:03

"as you (dan_ro)" names the database user, so the person sees which
credential's view of the table this is.

## 2. Masked cells

**The reason belongs to the column, not the cell, so the cue goes in the
column header.** Every cell of a masked column looks the same: a muted
"••••••" in place of the value, at a fixed width so its length leaks nothing.
That's text, not a new glyph. The header carries a small bordered tag with
the reason in words. Each of the three reads differently, and each names its
source:

| Reason | Header tag | On hover |
|---|---|---|
| matched a PII data class | `masked · email` | "column profile matched the PII class email · run 10-06" |
| classified confidential | `masked · confidential` | "Confidentiality classification · set by dwolfson 10-12 in Curate" |
| never profiled | `masked · not profiled` | "no column profile has read this column, so RE can't say it's safe" |

"not profiled" is in muted ink and the other two in ink. The first two are
findings, and the third is an absence, so the reader can tell a known risk
from an unknown one at a glance.

**One line above the grid counts them** (and each count opens the columns
it counted): "4 of 12 columns masked · 2 PII matches · 1 confidential · 1 not
profiled".

**Reveal: one column at a time, one confirm, one audit row.** The owner's
ruling settles the cost: whoever holds the credential can read the column
anyway, so asking for a reason would only slow them down. But revealing is a
recorded act, and the confirm says so. In place, in the header:

> Show email's values? This is recorded with your name in RE and Egeria.
> **Show** · keep masked

After the press, the header tag reads `revealed by you · recorded`, in ink.
A reveal lasts for this preview only. The next preview of the table starts
masked again, because masking is the default, not a preference that drifts
off. "Mask all again" sits in the count line while anything is revealed.

## 3. No credential

**The same in-place pattern as the Survey run line, with one difference
that must be said: this is the person's own credential, not the surveyor's.**
Otherwise someone will put their credential into the surveyor's slot, or
assume the surveyor's is being used. In place of the "Show 20 rows" button:

> Previews run as you, with your own credential for coco_pharma, not the
> surveyor's. **Enter your credential ›**

The form is `credential-change.js`'s test-before-save, retitled "Your
credential for coco_pharma".

**Decision (project owner, 2026-10-19):** RE keeps the person's credential
**for the session only**. The form says so in one line under its fields:
"kept for this session only · never stored". After sign-out or a new
session, the next preview asks again, in the same place.

## 4. Refusals: three different results

Each replaces the grid, keeps the header, and offers the next press:

| Result | What it shows | Offers |
|---|---|---|
| **the credential can't read the table** | "your credential can't read sales.orders · ‹Postgres's first sentence, e.g. permission denied for table orders›" | "use another credential ›" |
| **timed out** | "no rows shown · the query ran longer than 10 s and was stopped · nothing was read" | "try again" |
| **empty** | "∅ 0 rows · sales.orders is empty, as of 14:03" | nothing |

∅ is `glyphs.js`'s "measured nothing", which is exactly an empty table. The
other two take no glyph: nothing was measured, and their words say why. "No
rows" is never shown for the first two.

## 5. Views

**Yes, previewable, with the cost said before the press.** A view's rows are
what the view is *for*, so leaving them out would hide the most useful thing.
But a view runs its query, even with a limit. So on a view the button reads
"Run the view, 20 rows", and the line above it says "a view runs its query
on every read · stopped after 10 s". Where RE knows the view's definition,
it adds what the view reads ("reads 4 tables"). A materialized view stores its
rows, so it's previewed like a table.

Masking applies to a view's columns the same way. A view column RE can trace
to a profiled base column takes that column's reason. Every other view column
is "not profiled", which means most view columns start masked. That's the
honest result of "unknown is not safe", and the count line will say so.

## 6. The Egeria audit

**Yes, the person sees it, before and after, as a proof, not a promise.**

- **Before the press**, on the line under the button: "shown, never stored ·
  each preview is recorded with your name in RE and Egeria".
- **After**, in the header, from the proof rows: "recorded · RE 14:03 ·
  Egeria 14:03". A reveal adds its own row and changes nothing else on screen.
- **If Egeria's record fails**, the header says so and keeps RE's: "recorded
  in RE · not yet in Egeria · ‹Egeria's first sentence›", with the outbox
  retrying.

**Decision (project owner, 2026-10-19):** a preview runs when Egeria can't
take the record. RE's own audit row is written first, the Egeria record is
queued through the outbox, and the header says "recorded in RE · not yet in
Egeria" until the read-back proves it. The same holds for a reveal.

## Slices

| Piece | Kind |
|---|---|
| Table preview tab (database-only), picker with filter, "preview rows ›" on Schema Inventory, Show 20/100, the header | UI + route |
| Typed preview query (quote identifier, limit, statement timeout) behind the engine seam; nothing stored | route |
| Column masking from data-class matches, Confidentiality, and never-profiled; header tags; count line | route + UI |
| Reveal one column: in-place confirm, audit row, per-preview only, Mask all again | UI + route |
| Person's own credential, in place; three refusal states; views with the cost line | UI + route |
| Audit rows in RE, Egeria record via the outbox, "recorded" from proof rows | route |

## Left open

1. **Architect** (as the ask lists): the Egeria element for the audit record.

Settled since the first draft (project owner, 2026-10-19): a preview runs
when Egeria can't take its record (§6), and a person's own credential is kept
for the session only (§3).
