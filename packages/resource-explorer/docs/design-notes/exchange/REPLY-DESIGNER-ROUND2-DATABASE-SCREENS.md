# Reply — Designer round 2: the database screens

**Replying to:** `ASK-DESIGNER-ROUND2-DATABASE-SCREENS.md` (2026-09-27)
**Read against:** `main` at `019c2796` (#318), the five screenshots in
`screenshots/2026-09-27/`, `BRIEF-BY-ANALYSIS-PANEL-USABILITY.md` and
`SLICE-22-SCHEMA-INVENTORY-VIEW-IMPLEMENTED.md`
**Drawing:** `wireframes/DatabaseScreens.dc.html` (canvas page 12): the
contents board with one card open, the tree rows with their per-schema
measures, and the two header lines before and after.
**Date:** 2026-09-28

---

## 0 · Three things in the screenshots are already fixed

I read the code at HEAD before replying to the pictures. Since the screenshots
were taken:

- **The tree order** is now data → structure-only → views-only → staging →
  empty → no-access, and `_schema_inventory_container_rows`' comment names
  coco_pharma as the reason. That was going to be my first proposal. It's
  already right.
- **The kind words** (`schema`, `table`, `view`, `matview`, `foreign table`)
  have shipped.
- **The filter** no longer opens a matched table with nothing under it.

None of these is redrawn. Everything below is against what ships now.

---

## 1 · One glyph table, in one module (the question under all three items)

The ask proposes a single vocabulary for Questions, By-analysis and the tree.
**The code already has three, and they disagree:**

| where | `◐` means | `○` means |
|---|---|---|
| `GLYPH` (Questions, `app.js:603`) | ran, but not at this level | not run **and** no surveyor |
| `CELL` (work lists, `worklist.js:52`) | partial | not run **and** no surveyor, **told apart by colour alone** |
| the brief | *not established* | never run **and** *nothing found* |

`factGlyph` (`app.js:4968`) is a third table. `⚠` means *needs a person* on
Questions, *disagreement* on By-analysis, and *error* at `app.js:4878`.

So the brief's glyph list would give `◐` a second meaning and `○` a third, on
the surfaces it wants to "feel like one product". And the work list is already
breaking the palette's own rule: colour must never be the only channel.

**Proposal:** one table in `next/glyphs.js`, imported by all four surfaces. The
glyph names the **family**, meaning what the reader can do with this at a
glance. The word beside it names the exact state.

| glyph | family | covers | change from today |
|---|---|---|---|
| ✓ | measured | answered · measured · readable | — |
| ∅ | measured nothing | found nothing · empty (a counted zero) | already `CELL.nothing`; the brief had `○` |
| ◐ | measured, within a limit | at database level only · **within credential scope** · structure only · partly readable | adds the scoped case the ask says has no mark; the word names the limit |
| ○ | not run, and can be | a run control sits beside it | now means one thing only |
| ◌ | can't be answered here yet | no surveyor · no reader · containment undeclared | `no-surveyor` moves here from `○` |
| ? | tried, couldn't establish | not established · not measured · no access | already `CELL.unknown`; the brief had `◐` |
| ⚠ | needs a person | Enrichment · declare a lens | **only** this: disagreements use `≠`, errors use `✕` |
| ◔ ✕ ⏵ · | running · failed · waiting for your yes · unclassified | | as today |

Every state keeps its own word, so nothing is merged in the data or on screen.
Only the glyphs are grouped, and the glyph is never the only channel.

**Where the legend lives:** nowhere separate. The count line at the top of each
surface is the key, as it already is on Questions (`KEY ✓ answered 4 · ◐ …`).
The contents board gets *"7 analyses · ✓ 5 measured · ○ 1 not run · ◌ 1 no
reader yet"*. The tree gets *"8 schemas · ◐ 2 structure only · ∅ 3 empty · ? 2
no access"*. Each glyph carries its word in `title` and `aria-label`.

**Rendering:** Lora and Cormorant have no `◐`, `◌` or `∅`, so the browser falls
back to whatever symbol font it finds. On the owner's Mac they look fine. In a
headless Linux browser, `◐` came out as a sliver until I set the font
explicitly. The module should pin a symbol font stack for its glyphs, or draw
them as inline SVG. A vocabulary shouldn't depend on which fonts happen to be
installed.

---

## 2 · By-analysis: card anatomy and the contents board

The brief is right almost everywhere. Where I agree I haven't restated it;
these are the places I'd change it or add to it.

### 2.1 · Card boundary: a band, not a box

The brief asks for a bordered box. Every count row inside a card already has a
hairline, so a box around a ruled table produces a grid of lines, and a
collapsed box is a frame around a single line. **The boundary should be three
signals stacked:**

- **space**: s6 above a card, where rows get s1
- **weight**: a `rule-strong` top rule, where rows get a hairline
- **type**: the heading face at name size

It reads the same open or collapsed, so a page of collapsed cards becomes a
clean list of bands. No new tokens.

### 2.2 · The contents-board row truncates at a boundary in the sentence, not at a character count

A rollup headline shows its **whole-database lead**, then *"· 11 schemas ›"*,
which opens the list. Never cut mid-list. Cutting mid-list promotes whichever
schema sorts first. On AdventureWorks that's `hr`, which holds only views, so
Table Grain's row would read *"hr: not established (no_schema_rows)…"* and put
the least representative schema forward as the analysis's result.

**The server should return the headline already split, as `{lead, parts}`.**
The envelope already carries `is_rollup`, `container_count` and `containers`.
The client should never parse the sentence apart. If the lead alone is longer
than a line, ellipsis on the lead with the full text in `title`.

The parts also leak raw enum values: `(no_schema_rows)`, `(stats_not_populated)`.
Same fix as the #271 copy review: *"views only"*, *"no statistics — ANALYZE
hasn't run"*.

### 2.3 · Three corrections to the brief

- **`preliminary_fit`'s 0 isn't a confidence.** The Shared names block would
  list *"confidence · db_classification 46 · subject_signals 60 ·
  preliminary_fit 0"*. The 0 is *no requirement declared*: no lens was
  supplied, so there was nothing to be confident about. As a number it becomes
  a false disagreement. **→** show it as *"— (no lens declared)"* and leave it
  out of the comparison. That leaves two values that really are different
  measures, and that's the rename RFA the brief already proposes.
- **"table count" means two populations on one screen.** The evidence rail
  (`schema_inventory`) says **157**, which counts base tables, views and
  matviews. The Relationship Graph card says **68**, which is base tables only.
  The Shared names block won't catch this, because `schema_inventory` isn't in
  Discovery's set. **→** rename at the source: *"tables and views"* or
  *"relations"* for 157.
- **Small:** the rail prints *"measured · measured · run 2m ago"*, with the
  state twice.

### 2.4 · Fix the Questions headline before relying on the brief's backend test

The ask lists the Questions row anatomy as fixed (question, headline,
provenance footer), and I haven't touched it. **But the headline slot on
Questions doesn't currently hold a headline.** On AdventureWorks, *"Could this
be in scope for what I am looking for?"* holds about 200 words: the
explanations of `grain_determination`, `subject_signals`, `coverage_signals` and
`preliminary_fit` joined with `·`. The actual answer, *"NO REQUIREMENT DECLARED —
no lens was supplied, so fit is not a question that has an answer here"*, sits
about two-thirds of the way in.

And the row is marked **✓ answered**. That's a tick on an answer that says there
is no answer, which breaks the honesty rules the ask lists as fixed.

The brief's backend test asserts that *"the headline shown per analysis equals
the one the Questions tab computes."* Run today, that test would lock this
concatenation in as the standard for the By-analysis headline. **So the
Questions headline needs fixing first.** One sentence per analysis, with the
rest behind *"the numbers behind this"*. And the fit row becomes ⚠ *needs a
person: declare a lens*, not ✓. That fixes what fills the anatomy, not the
anatomy itself.

---

## 3 · The Schema Inventory tree

### 3.1 · Kind marking: say the level once; keep the table-kind word on every row

The owner's instinct is right, and it helps to separate two kinds of word:

- **"schema" on every top-level row is didactic.** Top position, semibold and
  indentation already say it. **→** state the levels once, as the tree's own
  heading, taken from the engine's declared containment: *SCHEMAS › TABLES ›
  COLUMNS*.
- **"view" / "matview" / "foreign table" is information.** Indentation can't
  carry it. AdventureWorks has 87 views and 68 base tables, so views are the
  majority and neither kind can be treated as the unmarked default. **→** keep
  the kind word on every table row, as it ships now.

### 3.2 · The per-schema overview goes in the rows, not in a separate chart

The owner asked for a bar chart above the tree. A separate chart would be a
second list of the same schemas in the same order. Two lists mean two things
to keep in step, and the filter would have to narrow both. **→** put the
measures **in the schema rows**, as right-hand columns. The collapsed tree
becomes the overview: eight rows, eight bars. It answers "which schema holds
the data" at a glance, and filtering narrows the tree and the overview
together.

**Two measures, because they need different privileges:**

| measure | needs | on coco_pharma |
|---|---|---|
| rows | `SELECT` | words, mostly: *"rows not readable"* |
| columns | nothing: the catalog (`pg_attribute`) is readable without it, the path #257's catalog fallback already uses | **a real bar for every schema** |

That's the answer to *"what does it do for a credential that sees no rows"*. It
draws the measure the credential **can** see, and says in words which one it
can't. On coco_pharma the column bars carry the finding: `coco_sus` and
`coco_ods` hold nearly all the structure, and their rows aren't readable. A row
of zero-height bars would say the opposite.

**Three bar states, all distinct:**

| state | drawn as |
|---|---|
| measured | solid bar, number always printed; linear scale |
| catalog estimate | the same bar, hatched, with *"~"* on the number |
| measured zero | no bar; a baseline tick and *"0"* (∅) |
| not measured / not readable | no bar; the word (*"rows not readable"*, *"not measured"*) with ◐ or ? |

### 3.3 · Prerequisite: "empty" covers four conditions today, one of them never measured

`_schema_inventory_container_rows` classifies a schema as `empty` when

```python
table_count == 0 or scope_state == SCOPE_EMPTY or row_total == 0 or row_total is None
```

and then stores `"row_total": row_total or 0`.

So **never measured** becomes a stored zero, and the renderer prints it as *"1
table(s) — empty"*. The comment justifies this with the owner's ruling, but the
quote has *"[or, as here, no row data at all]"* **added inside the quote
marks**. The owner ruled on schemas with zero rows, not on schemas with no
measurement.

Any row-count bar built on this field would draw *not measured* at the same
height as *counted, and zero*. **This has to be split before the overview
ships:**

- `row_total is None` → ? *"rows not measured"*
- `row_total == 0` → ∅ *"0"*
- `table_count == 0` → *"no tables"*

Whether coco_pharma's three sales schemas are real zeros or were never measured
is one registry query away. A Docker database that has never been ANALYZEd is
the usual way to get `reltuples = -1`, and so `NULL` rows. The drawing assumes
they're real zeros and says so.

### 3.4 · Engines with more or fewer levels

The heading from 3.1 handles this: *TABLES › COLUMNS* for SQLite, *DATABASES ›
SCHEMAS › TABLES › COLUMNS* for Snowflake. The depth is stated as the engine's
own, so a shallower tree reads as a property of the engine, not as missing
data. For an engine with no declared containment: one line, *"{engine}'s
grouping isn't declared yet — tables shown flat"*, per #271's rewrite of
`undeclared_envelope`.

### 3.5 · Two small things

- `key_role` (PK/FK) renders in `text-accent-ink`, which is the action colour,
  on text you can't click. **→** `text-ink`. This is the same finding as the
  native-processes block in #271.
- *"comments not captured"* repeats on every column row of an uncommented
  table. **→** say it once per table (*"no column comments captured"*, or
  *"comments on 3 of 25 columns"*), and leave the per-row cell as a dash.

---

## 4 · The header lines, as two states

### 4.1 · What "element not found" means

RE stored Egeria's GUID when it published coco_pharma. When it next checked,
Egeria's repository returned *not found* for that GUID. `egeria_linkage.py`'s
docstring records the cause: a platform reset on 2026-09-22 wiped the entry.
The note reports what was observed (a failed lookup), never why, which is
right, because a reset and a deletion look identical from RE's side. But
*"element not found"* is Egeria's wording, not the reader's.

### 4.2 · The proposal

Each line **leads with its state**. The glyph carries the state, so the text
returns to ink. **Only the remedy is in the accent colour, because it's the
only thing on the line you can click.** Today both lines are accent from end to
end, and neither is clickable. Dates and the GUID go behind a hover on the
date: provenance belongs in the evidence, not the sentence.

> ✕ Egeria no longer has this database's entry — published once, missing since
> 26 Sep · **publish again in the current UI ↗**
>
> ◐ Within `surveyor`'s access — reads 3 of 61 tables in 6 of 8 schemas; every
> count here stops there · **access request open ›**

A healthy header folds to one quiet line:

> ○ not published to Egeria · ✓ reads all 157 tables in 11 schemas as
> `dwolfson` · surveyed 12m ago

Why the order changes: today's note **begins "published to Egeria"** while
`is_published` is false (`describe_publish_status`), and a reader stops at the
first clause.

### 4.3 · Neither remedy exists in `/next`

- **Publish:** `/next` has no database publish action. Classic has one, at
  `POST /api/databases/{slug}/publish`. So the remedy links to classic, as a
  deferred affordance that's named rather than left out, until `/next` gets its
  own. That's one small slice.
- **"Pick a broader account":** there's no connections UI anywhere, classic
  included, and no route for one. **→** the remedy links to the RFA the
  capability probe already raises, whose shape is *"connected as X: SELECT on N
  of M tables — grant SELECT on `schema.*`"*. That's the actual way forward: a
  DBA grants it. A connections UI is a separate, larger piece of work, not a
  header change.

### 4.4 · "Never surveyed" above answered rows

The Questions screenshot's header says *"never surveyed"*, while rows below it
say *"run just now"*. `last_surveyed_at` counts only full survey runs, not
individually run analyses. **→** *"no full survey yet · 4 analyses run
individually, latest 2m ago"*. The data is there (`get_analysis_last_run`).

---

## 5 · Order

1. **Now, and small:** the glyph module (§1), including pinning the glyph
   font. Then the By-analysis slice can use it from the start instead of
   adding a fourth table.
2. **Before the By-analysis slice's headline test:** the Questions headline
   and the ✓ on the fit row (§2.4).
3. **Before the tree overview:** split `empty` (§3.3).
4. **The rest as briefed:** the By-analysis slice with §2's changes, the tree
   slice with §3, and the header slice with §4. Plus two small follow-ons that
   are engineering, not design: a `/next` database publish action, and the
   `table count` rename.
