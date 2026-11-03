# REPLY — Designer: Curate and Understanding for every resource kind (2026-10-01)

To ASK-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md, read against main at
524f1c7f (Classic `index.html`, `web/routes/curate.py`, `web/routes/stats.py`,
`web/routes/databases.py`, `registry.py`, `next/stages/curate.js` and
`understanding.js`). Drawing: `wireframes/CurateAndUnderstanding.dc.html`,
canvas page 16.

## Two things to fix before anything is drawn

**1. The three Curate controls record no author.** `resource_tags`,
`resource_feedback` and `resource_curator_notes` have no column for who
wrote the row, and none of the routes in `curate.py` reads the signed-in
user. `DELETE /api/curate/notes/{note_id}` deletes any note by id for
anyone. Classic hides this because it shows nobody's name. /next can't: a
rating or a note with no author is testimony nobody can question, which is
the same defect already ruled out for the data lens (work-lists reply §6)
and recorded for Context's human-question answers. So "the work is the UI"
isn't quite true. Each of the three needs an `author` column, filled from
the session on write, with a 401 like the report acts when nobody is signed
in. Existing rows render as **"unsigned · from before authors were
recorded"**, never blank.

**2. The database chart routes turn "not measured" into zero.**
`schema_distribution`, `table_sizes` and `column_types` read the latest
run's `survey_data` blob. The diff route stopped reading that blob on
2026-09-20, because the structured `database_tables` rows replaced it.
Reading the blob loses the distinction those rows keep:

- `table_sizes` does `table.get("row_count", 0)`. A table whose row count
  was never established (no `ANALYZE`, the pg_stats case) comes back as 0
  rows, which is a confident wrong answer. In `database_tables`,
  `row_count` is NULL in that case.
- Classic's chart switches from rows to megabytes only when *every* row
  count is 0, and its title says so. In the mixed case (some counts
  established, some not), the unestablished tables are ranked as 0 rows
  and fall out of the top 15 without a word.
- `survey_history` defaults `schema_count`, `table_count` and
  `column_count` to 0 and cuts `surveyed_at` to the day, so two runs on one
  day draw as one date, and a run that didn't measure tables draws as a
  drop to zero.
- `column_types` buckets a missing type as "unknown", counted like a real
  type.
- None of the four says which run it read, or whether that run was partial
  (◐ within credential scope).

Wrapping these routes as they are would carry all of this into /next. The
Understanding slice should rebuild them on `database_tables` /
`database_columns` and return what the repo routes already return: a
figure, plus the run it came from, plus a state. That's backend work, not
only UI.

## 1. Curate on a database or a file system

**Curate is "make it findable and reusable", so it is the same three bands on
every kind:**

1. **Findable**: a single compact band at the top. **Group** (the current
   group, with the change control inline; Admin keeps the full group
   manager) and **tags** (chips, add with the existing autocomplete from
   `GET /api/curate/tags`). This adds two lines, not a section.
2. **The kind's own curation work**:
   - **Repository:** the component tree, verdicts, plan and Catalogue commit,
     unchanged (§2).
   - **Database:** **glossary terms on tables and columns**, then **logical
     schema match**, both proposed-then-confirmed rows with the four
     observation states (proposed, confirmed, overridden,
     survey-now-disagrees). Neither has a reader yet, so each section is
     present with one sentence naming what it waits for: "No proposals yet:
     needs data classes per column (`data_class_match`) and a glossary to
     match against." No empty table.
   - **File system:** one sentence: "Nothing to review for file systems yet.
     Group, tags, ratings and notes above and below apply." Being honest
     doesn't need a placeholder section here.
3. **What people say**: **ratings** and **notes** (below).

What stays off Curate:

- **Context (Enrichment) keeps facts about the resource:** owner, licence,
  purpose, doc sources. A tag isn't a fact; it's a label for finding
  things. A rating isn't a fact about the resource; it's a reader's verdict
  on how well it served them. Both go on Curate, as the CLAUDE.md
  distinction says.
- **The population rule** ("only `tracking` or `using` get curated") gates
  the **Catalogue commit**, not labeling. Anyone can tag or rate any
  resource. The commit keeps its rule, and says so where it is.

**Ratings.** Classic shows stars per entry and nothing in aggregate. Keep
it that way: **never an average and never a star score in a header.** The
band's headline is counts: "4 ratings · ★★★★★ 2 · ★★★ 1 · ★★ 1". Each entry
is signed, dated and has its category. The category list is unchanged.

**Notes: one notes surface, not two.** /next already has a signed,
append-only notes surface for every kind: the **journal**, on
Discovery → Disposition, "why it matters, and to whom". Classic's curator
notes are the same kind of thing but unsigned and deletable, and Classic
labels them as "ongoing commentary about discoverability, quality, or
readiness". Building a second notes list beside the journal would leave a
person guessing which one to write in. So:

- Curate's notes band **is the journal**: the same component and the same
  data as on Disposition, with new notes written there.
- Existing curator notes show beneath it as **"Curator notes from
  Classic · N · unsigned"**, read-only, newest first. Deleting from /next
  is offered only for those, because they have no author to protect. Once
  Classic retires, nothing writes there again.
- The parity row C-04 closes on "notes are written and read in /next",
  not "curator notes are rebuilt".

**Not published.** Tags and ratings are local only: nothing in the publish
path writes them, and Egeria's `InformalTag` isn't used. The Findable band
says "local · not in Egeria" once, beside the tags. Whether tags should
publish as InformalTags is an owner call (left open below).

## 2. Curate on a repository

**The three bands, with the component work as band 2.** The Findable band
sits above the existing section nav as two lines, so the review's columns
move down by about 50 px, not a screen. Ratings and the journal go below
the Catalogue commit, where a person goes after the decision is made.

One person, one page, every kind. Someone who tagged a database doesn't have
to learn that tags on a repository live somewhere else, which meets the
parity goal (never go to Classic for tags or notes).

The false sentence in `nonRepoCurateHtml` is already being removed. The
new non-repo body replaces it entirely.

## 3. Understanding on a database

**Two sections, "Now" and "Over time", in the order a person asks.**

**Now** (as of one run, named in the section header: "from the survey of
09-28 14:02 · local · 3 of 5 schemas readable" when that run was ◐ partial):

1. **Tables and columns per schema.** Two small multiples side by side,
   not one grouped bar chart. Classic puts tables and columns on one axis,
   and columns (thousands) flatten tables (tens).
2. **Largest tables, by rows.** It always counts rows, never switches
   measure on its own. Tables whose row count isn't established are **not
   drawn as zero bars**. A line under the chart counts them: "? 12 tables'
   row counts not established — statistics not gathered on the server ·
   by size instead ›". The "by size" view is a second, labeled measure the
   person chooses, not a fallback.
3. **Column types.** It counts columns per declared type. A missing type is
   "? type not recorded · N", never a bar named "unknown".

**Over time:**

4. **Since the last run**: **the first row of this section, as a
   sentence, not a banner and not a chart.** "Since 09-21: 2 tables added,
   1 removed, 14 columns added" with each count opening the names (counts
   open what they counted). Two points don't make a chart, and a banner
   is chrome that claims urgency for what is a fact about time. It uses
   `GET /api/databases/{slug}/diff` and its existing states: with fewer
   than two runs it says "Only one run so far — nothing to compare"; with
   `not_materialized` it gives the backfill sentence the route already
   returns.
5. **Structure over runs**: schemas, tables, columns per run. One point
   per run with its full timestamp, so two runs on one day are two points.
   A run that didn't measure a count leaves a gap in the line, not a drop
   to zero.
6. **Activity per table**: the busiest tables by rows inserted, updated and
   deleted per day, from `db_change_rates`. Its three states keep their
   own words: "insufficient history", "idle", "counters reset between
   runs — not comparable".

**A chart whose step never ran** says it in the chart's own slot, at the
chart's size, in the not-run family: **"○ not run: needs a database
survey · Run survey ›"** (an accent control). If the survey ran but this
part of it couldn't be read, the slot says which: "◐ structure only — row
counts weren't readable with this credential". "No data" is never the
wording: it's the phrase that can't distinguish the two.

Every chart has a provenance line under it: run, source (local or Egeria),
and what is counted ("rows, as estimated by the server's statistics").

## 4. The chart-kind list

**Per kind.** The "tabs never disappear" ruling is about navigation:
the sub-tab rail is the same on every stage so a person's muscle memory
holds. A chart kind isn't navigation. It's content, and "GitHub stars have
no database analogue" is a permanent truth that a database owner reads
every time and learns nothing from.

So a database sees the six database charts above. Any it can't draw yet
are **present and explained** in its own list ("Activity per table · not
wired up yet"), because those are gaps in *its* catalog. A file system
gets one sentence until its charts are designed: "No charts for file
shares yet. Their survey isn't charted anywhere today." Which file-share
charts to build is a separate ask.

`CHART_NO_EQUIVALENT_REASON` and the repo-kinds-for-a-database list go
away.

## 5. Export

**One control per chart, not one for the stage.** A "Save image" link sits
on each chart's provenance line, using Plotly's own `toImage`. It's the
same component as the PNG export queued for the Scouting chart: one
implementation.

**The saved image carries its provenance burned in**: resource, chart
title, run and source, as a footer line in the PNG. An exported chart
leaves the page and its evidence behind. Without that line it becomes a
picture of numbers nobody can trace. Export everything at once isn't
needed, and nobody asked for it.

## What the two slices change

| Slice | Change | Kind |
|---|---|---|
| Curate, all kinds | `author` on tags, feedback and curator notes; 401 when signed out; existing rows "unsigned" | route + migration |
| | Findable band (group, tags); ratings band (counts, no average); journal as the notes band; Classic notes read-only beneath | UI |
| | Database: glossary-term and schema-match sections, present and explained | UI (sentences only until readers exist) |
| Understanding, databases | Rebuild the four chart routes on `database_tables`/`database_columns`: figure + run + state, NULL kept | route |
| | Six charts in two sections; "since the last run" as a sentence; per-kind list | UI |
| | Per-chart "Save image" with provenance footer, shared with Scouting | UI |

## Left open, for the owner

1. Publish tags to Egeria as `InformalTag`s, or keep them local? Today they
   are local, and the band says so.
2. Should deleting a signed journal entry ever be possible? It isn't now
   ("there is no delete"). I've kept it that way. Classic's unsigned notes
   stay deletable.
3. File-share charts: what would a steward want to see? File types, sizes
   and ages are the obvious candidates, but no route exists, so it needs its
   own ask.
