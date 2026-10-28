# ASK — Designer: cataloguing at scale: rules, increments and earlier decisions (2026-10-14)

From the design session, for the designer session, at the owner's request.
Reply as `REPLY-DESIGNER-CATALOGUING-AT-SCALE.md` in this folder; a
drawing of the Contents tab with a rule row and an arrival row under
`wireframes/` is welcome. Background: your split-by-job reply (the
Contents tab), `RULING-PUBLISH-NOT-CATALOG.md` (parts, depth, detail),
`REPLY-DESIGNER-CURATE-CATALOGUE-SCOPE-DATABASES.md` and the scope
round 2, and four backlog entries of 2026-10-05 from the owner's first
look at coco_pharma's 29 schemas and hundreds of tables.

## The owner's direction, in his words

- "With hundreds of tables, and columns, a per-row choice does not
  scale": filters and sorts for tables and columns, and decisions on a
  group that fits a criterion, for example "publish every table with
  Sales in its name".
- "Cataloguing is incremental over time, not a one-time operation":
  the scope is a living record and every commit is a diff; "new since
  your scope was declared" arrives on its own.
- "Some of the decisions about what to publish could be made in earlier
  stages (Scouting, Discovery, Assessment, Analysis), with Curate
  finalising and adjusting them instead of deciding from scratch."
- "Add these N to an investigation" is hard to find from a finding's
  numbers (the work-lists reply's control, logged 2026-10-04).

## What the code says

- The scope tree (`stages/curate-scope.js`) is one Include | Leave out
  selector per schema and per table, with select-all-shown, clear, three
  bulk choices and "include all N schemas"; a filter box narrows the
  rows. There is no sort, no column-level choice, and no stored rule: a
  bulk choice writes one decision per row, signed and dated, and a table
  that arrives later is "new since your scope was declared · undecided".
- Egeria's cataloguer takes include and exclude lists of exact names
  (`includeTableNames`, `excludeTableNames`, same for schemas, views and
  columns), nothing pattern-shaped. A rule must therefore be compiled to
  plain names at commit time, and recompiled when new names arrive.
- Earlier stages record measurements and a person's verdicts on
  components (repositories) but nothing on tables; the design's rule is
  that only a measurement that decides alone may propose.
- The scope record (`resource_scope_events`) stores one event per row
  decision with who, when and why; it has no row kind for a rule.

## The questions for you

1. **A rule as a signed object.** Is a rule ("every table whose name
   contains Sales", "every table in schema X with more than N rows",
   "all except these three") a stored, signed, dated object in the scope
   record, shown as its own row above the tables it matches, so a table
   that arrives later and matches is picked up and shown as "included by
   rule <name>" rather than "undecided"? How does a person see the
   difference between a row decided by hand and a row decided by a
   rule, and override one row without breaking the rule?
2. **Filter, sort, decide.** Which filters and sorts does the scope tree
   need for hundreds of tables (name, schema, row count, size, last
   activity, has keys, matched a data class, already published), and is
   "decide for everything currently shown" the control, with the shown
   set becoming the rule's definition, or does a rule need its own
   editor?
3. **Arrivals.** On a later survey, a new table that matches a rule, a
   new table that matches nothing, a table that vanished, and a table
   whose match changed: four states on one tree. What does each look
   like, what is the one-line summary at the top ("3 new · 2 matched by
   rule · 1 gone"), and does an arrival ever change what is published
   without a person pressing Publish? (Design position: never; the rule
   proposes, the commit publishes.)
4. **Earlier decisions.** Where does Scouting, Discovery, Assessment or
   Analysis record "worth publishing" for a table: as a proposal from a
   measurement (a data-class match, a size, a usage figure), or as a
   person's verdict made early? How does the Contents tab show its
   provenance ("proposed by data-class match on 10-06", "chosen in
   Assessment by Dan on 10-05") and let Curate finalise or adjust
   without re-deciding?
5. **Columns.** The same selector at column level, when a person wants
   tables but not their columns, or all columns except a few: a depth
   setting is already in the publish module; is a per-column choice ever
   needed beyond "leave these out", and where does it live?
6. **"Add these to an investigation".** From a finding's numbers, the
   path to the work-lists control is not obvious. Where does it go on a
   finding row, and is it the same control as "include these by rule"?

## Constraints

Status words derive from proof rows; a rule compiles to exact names at
commit and the manifest says how many names it compiled to; a rule never
publishes on its own; honesty rules for arrivals (a table that vanished
is "gone from the source · <date>", never silently dropped); the cue
vocabulary of your 2026-10-06 reply applies; no new glyph.

## What we do with the reply

One slice for rules and arrivals on the Contents tab, gated by use on
coco_pharma: the owner declares one rule, sees the matched rows, a later
survey brings a new matching table, and the commit publishes it only
after he presses Publish. Earlier-stage decisions are a second slice
once the first stage that proposes is chosen.
