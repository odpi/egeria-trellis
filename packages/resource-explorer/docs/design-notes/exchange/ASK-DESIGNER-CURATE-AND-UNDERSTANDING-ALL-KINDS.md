# ASK — Designer: Curate and Understanding for every resource kind (2026-10-01)

From the design session, for the designer session, at the owner's request.
Reply as `REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md` in this
folder; a drawing of each stage on a database under `wireframes/` is
welcome. Background: `CLASSIC-VS-NEXT-PARITY-2026-09-30.md` rows C-01 to
C-04, D-24, D-28, D-31, R-39, R-40 and X-11, and the Curate blank-pane fix
of 2026-10-01.

## Why now

The owner's loose-ends pass found two stages that /next promises and does
not deliver for a database: Curate rendered nothing (fixed today: it now
says "Curate isn't available for databases yet" with the reason), and
Understanding lists the repository chart kinds each marked "does not
apply". Both are honest now. Neither is useful. Classic has real content on
both stages, so the parity table lists these as the largest absent block
after Publish.

## What the code says

**Curate.** Three things exist for every kind in the backend and in Classic,
and nowhere in /next for any resource: tags (`POST/DELETE
/api/curate/tags/{type}/{slug}`), resource-level feedback (rating and
category), and curator notes (add, delete). /next's Curate is the
repository-only component tree with verdicts, the curation plan and the
"Catalogue" commit (X-11), which Classic lacks. A sentence in /next that
claimed the three controls were "reachable from the resource header" was
false and is being removed. Group assignment (C-01) lives in Admin for all
kinds, with no control on the resource.

Two things from this week's design work also land in Curate for databases:
semantic assignment of glossary terms to columns and tables, and logical
schema matching against schemas Egeria knows, both as proposed-then-confirmed
rows with the four observation states (`DESIGN-FIND-AND-INTEGRATE…` §6).

**Understanding.** For a repository /next draws stars, commits, languages,
health, file types, weekly commits, top committers and survey history. For a
database, three server routes exist and return plain arrays, not figures
(schema distribution, table sizes, column types), and the survey-history
trend route exists; /next wraps none of them, so the stage lists the
repository kinds marked "does not apply". Classic draws the three database
charts and a schema-metric trend. Neither UI has the "changes since last
run" banner for a database (D-24), which the multi-resource spec puts on
Understanding as a schema-diff timeline.

## The questions for you

1. **Curate on a database (and a file system).** With no component tree to
   curate, what is the stage? Candidates: the three generic controls (tags,
   feedback, curator notes) as the stage's body for every kind; the
   semantic-assignment and schema-matching rows for databases; group
   assignment on the resource. Which belong on Curate, which on Context,
   and in what order? The CLAUDE.md distinction holds: Enrichment records
   facts about the resource, Curate is ongoing work to make it findable and
   reusable.
2. **Curate on a repository.** Does the component tree stay the whole
   stage, with the three generic controls added, or do the generic
   controls become a shared top section on every kind with the kind's
   specific work below? The owner's parity goal is that a person never has
   to go to Classic for tags or notes.
3. **Understanding on a database.** Which charts, in what order, and what
   does a chart say when its survey step never ran (not "no data")? The
   three existing routes plus the schema-metric trend are the material;
   row-count and size trends per table exist as comparators. Should
   "changes since last run" be a banner, a chart, or the first row of the
   trend?
4. **The chart-kind list.** Today the stage lists every chart kind for every
   resource and marks the inapplicable ones. Should the list be per kind,
   so a database sees only database charts, or stay universal with the
   marks, as the Enrichment "tabs never disappear" ruling might suggest?
   Those are different cases: a tab is navigation, a chart kind is content.
5. **Export.** Classic has none; the (a)+(c) follow-up queued a PNG export
   for the Scouting chart. Does Understanding get one export control for
   all charts, or none?

## Constraints

Honesty rules: a chart whose step never ran says so and offers to run it;
a count is labelled with what it counts. The accent colour is for
controls, never states. No new glyph. The three Curate controls already
have routes; the work is the UI.

## What we do with the reply

Two slices, each gated on 8813 on coco_pharma and amundsen: Curate for every
kind (the generic controls plus whatever you rule for databases), then
Understanding for databases. The parity rows C-02 to C-04 and D-28, D-31
move from absent to present on those gates.
