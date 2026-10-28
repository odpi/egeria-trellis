# ASK — Designer round 2: the database screens, against real rows (2026-09-27)

From the design session, for the designer session. Reply as a
`REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md` in this folder; drawings, if
any, under `wireframes/` as self-contained HTML like
`wireframes/ExposureHeatmap.dc.html`. A review reply is more useful than a
finished drawing; a drawing of one card is more useful than a page.

## Why now

The coordinator brief (`COORDINATOR-BRIEF-MULTI-RESOURCE.md`, stream 15)
held designer round 2 until there were real database rows to draw against.
There are now two:

- `localhost_docker_coco_pharma` — 8 schemas, mostly empty or
  structure-only, surveyed with a scoped credential (`surveyor`: 6 of 8
  schemas, SELECT on 3 of 61 tables). Every count on its pages is
  qualified.
- `laz_local_adventureworks` — Microsoft's AdventureWorks OLTP on Postgres:
  11 schemas, 68 tables, 87 views, 91 foreign keys, 427 comments, full
  visibility. The Relationship Graph finds 2 components, the largest
  spanning 67 of 68 tables.

Today's screenshots, taken by the project owner on the current build, are
in `screenshots/2026-09-27/`:

| file | screen |
|---|---|
| `adventureworks-discovery-questions.webp` | Questions tab, Discovery stage, all cards |
| `adventureworks-by-analysis-discovery.webp` | By-analysis tab, Discovery stage |
| `adventureworks-survey-and-analyses-scouting.webp` | Survey & analyses tab, Scouting |
| `adventureworks-schema-inventory-filtered.webp` | Schema Inventory tab with a filter typed |
| `coco-pharma-schema-inventory.webp` | Schema Inventory tab, scoped credential |

The owner's verdicts on them, verbatim: *"a lot to do on usability"*;
*"hard to distinguish one card from another, no table of contents at the
top, large blocks of details"*; on the tree: *"should probably indicate that
something is a schema or table? (perhaps too didactic)"*; *"might be nice to
show a bar chart showing estimated rows / columns per schema at the top"*.

## What is fixed and what is open

Fixed, do not redesign: the stage funnel and perspective chips; the honesty
rules (every count says what it is scoped to; "not established" is a state,
never blank; a rollup names its parts); the Questions tab's row anatomy
(question, headline, provenance footer with `evidence · copy as evidence ·
re-run · the numbers behind this · notify me`).

Open, and yours: the three items below. Each has an engineering brief
already; your reply shapes how they look, not whether they happen.

### 1. The By-analysis panel: card anatomy and the contents board

Brief: `BRIEF-BY-ANALYSIS-PANEL-USABILITY.md`. It specifies a table of
contents at the top that doubles as the status board (state glyph, one-line
headline, run time per analysis, sticky, click-to-jump), headline-first
cards with the catalog description behind a closed disclosure, counts
grouped into result / coverage / diagnostics, and shared-name disagreements
rendered once.

Questions for you:

- One state-glyph vocabulary for Questions, By-analysis and the tree:
  measured, nothing found, not established, never run, no reader,
  measured-within-credential-scope. Today `✓ ○ ◐ ◌` carry some of these;
  the scoped case has no mark of its own. Propose the set and where the
  legend lives.
- Card boundary and header band: how strong, given the page already has
  rules between rows. The current screen has none, and that is the
  complaint.
- The contents-board row: how much headline fits, and what happens to a
  headline like *"Keys captured for 68 of 68 tables — by schema: hr: not
  established (no_schema_rows); humanresources: data_model (6 table(s), 5
  edge(s), 1 leaving); …"* when it must be one line. Our rule is that the
  whole-database rollup leads and the per-schema list follows; say how the
  row should truncate.

### 2. The Schema Inventory tree

Implemented this week (`SLICE-22-SCHEMA-INVENTORY-VIEW-IMPLEMENTED.md`),
gated by the owner on both databases: schemas → tables → columns, filter
across all three levels with a clear control, quiet kind words after names
(schema / table / view / matview), container states per schema row
(readable, structure only, empty, views only, no access), ordered data →
structure-only → views-only → empty → no-access with system schemas folded.

Questions for you:

- Kind marking without a legend: the owner's "perhaps too didactic" worry.
  Row styling per level, a glyph, or the quiet word we have now.
- The per-schema overview the owner asked for: estimated rows and column
  count per schema above the tree, so "which schema holds the data" is a
  glance. Bar, dot, or inline sparkline; what it does for a credential that
  sees no rows (coco_pharma: every bar would be zero or unknown, and that
  is itself the finding).
- Engines without a schema level (MySQL, SQLite) or with more (Snowflake:
  database above schema). The tree renders whatever containment levels the
  engine declares; how should a two-level or four-level tree read next to
  the three-level one, without a person noticing the difference as an
  error.

### 3. The header lines

Two sentences now sit under every database title:

> surveyed 9h ago · published to Egeria · link stale since 2026-09-26 ·
> last checked 2026-09-27 — element not found
>
> connected as surveyor — sees 6 of 8 schema(s), SELECT on 3 of 61
> table(s) · every count on this page is scoped to this credential, not
> the whole database

Both are true and both are needed; the owner asked what "element not
found" meant. Propose how these read as two states (a publish state with a
remedy, a credential scope with a consequence) rather than two long
sentences, and where the remedy ("publish again from the Analysis pane";
"pick a broader account") goes.

## Constraints

- Every sentence on screen must survive the honesty rules above; a design
  that hides a qualification to look cleaner is a regression.
- Phone width matters less than a 13-inch laptop at default zoom; the
  screenshots are from a large display and still ran long.
- No new colours beyond the existing tokens; state is carried by glyph and
  weight, colour is secondary.

## What we do with the reply

The By-analysis slice is queued behind the structured-table clobber work
(brief section B) and will start within days. If your reply arrives before
it starts, the coordinator builds to it; if after, item 1 lands as a small
follow-up. Items 2 and 3 become their own small slices. Anything you
disagree with in the engineering briefs, say so in the reply; the briefs
are not sacred, the gates are.
