# ASK — Designer: the catalogue scope tree in use, round 2 (2026-10-04)

From the design session, for the designer session, after the owner's first
use of slice A (the scope record and tree from
`REPLY-DESIGNER-CURATE-CATALOGUE-SCOPE-DATABASES.md`) on coco_pharma.
Reply as `REPLY-DESIGNER-CURATE-SCOPE-ROUND-2.md` in this folder; an update
to `wireframes/CatalogueScope.dc.html` is welcome. Four defects from the
same walk are being fixed without you (the tree read RE's credential-scoped
local survey instead of Egeria's full one, no select-all, a column header,
a cut-off column); these five points are design.

## What the owner saw

coco_pharma has 29 schemas and 266 tables since the Egeria lead extended it
on 2026-10-03. The tree offered 29 individual choices and proposed leaving
out two schemas on the strength of "no writes since <date>", where the
window was two surveys three days apart and the write counters had reset
with the server. RE and Egeria had already measured most of what the tree
needed, and the tree asked Postgres again.

## The five points

1. **Reuse what was measured, and date every number.** The tree's facts
   (table size, row estimate, write counters, data classes) should come
   from the stored survey annotations, RE's and Egeria's, each with its
   as-of date and source on the row, not from new catalogue queries.
   Postgres statistics lag: row estimates move on analyze and vacuum, and
   the write counters reset on crash recovery. On coco_pharma the old
   schemas show zero live rows and zero writes in `pg_stat` while RE's own
   survey estimated about 3,400 rows in coco_sus. So a "no writes since
   <date>" proposal must carry the counter window it rests on, and when
   the window is short or reset it says "can't tell · counters reset
   <date>", proposing nothing. How is the window drawn on the row, and
   when does "can't tell" become a proposal?
2. **The choice needs the facts beside it.** Row count, size, and a plain
   activity word (active / dormant / can't tell, with its window) belong in
   the tree's columns, not only structure. Which columns, in what order,
   and what does a row with no measurement show (never a blank or a zero)?
3. **Hundreds of tables: filter, sort, and rules.** The person needs
   filter and sort over tables (and columns), and decisions on groups by
   criterion: "catalogue every table with Sales in its name", "leave out
   every schema with no writes in 90 days". The design session's position,
   for you to confirm or change: a rule is a **stored, signed, dated object
   in the scope record**, like a node choice, so a table that arrives later
   and matches it is picked up and shown as "matched by rule <name> · new
   since <date>", and the rule is compiled to plain names at commit because
   Egeria's lists match plain names only. How are rules written, listed,
   and shown on the rows they decide, and how does a rule's match differ
   on screen from a hand choice?
4. **Cataloguing is incremental and the scope is a living record.** The
   reply's §3 already makes every commit a diff; the owner wants the
   drawing to say so: schemas and tables are added over time as needs
   grow, "new since your scope" arrives on its own between visits, and the
   tree's header carries the history ("declared 10-04 · 3 commits · last
   10-11"). What does the tree look like on the fifth visit rather than
   the first?
5. **Decide earlier.** Could Scouting, Discovery or Assessment already
   propose or record "worth cataloguing" at schema and table level, so
   Curate finalises and adjusts instead of deciding from scratch? The
   standing rule is that surveys propose observations, never judgements,
   and that verdicts gate but never propose; "worth cataloguing" sits near
   that line. Your call where it falls, and if an earlier stage may record
   it, which stage, from which measured facts, and how Curate shows a
   choice that was pre-recorded elsewhere.

## Constraints

Honesty rules hold: every number carries its source and as-of; absence is a
word; a proposal names the fact that decided it alone. The accent colour is
for controls, never states. No new glyph. Egeria's lists match plain names,
so whatever a rule means on screen, the commit compiles names.

## What we do with the reply

Slice B (the commit workflow) is waiting on a scratch-database test and is
unaffected. Points 1 and 2 become slice A2's follow-on; points 3 and 4 a
slice of their own; point 5 a design decision recorded in the catalogue.
Gate on 8810 by the owner on coco_pharma: filter to tables named like
Sales, write one rule, see the matched rows marked by it, revisit after a
new table appears and see it picked up and flagged.
