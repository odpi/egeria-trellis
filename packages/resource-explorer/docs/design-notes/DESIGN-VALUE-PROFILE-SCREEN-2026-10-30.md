# Design — a profile-results screen for a database's value profile (2026-10-30)

Owner's words (via the coordinator, 2026-10-30): "yes to both - send the fixes and design the screen". Item 7 of the
quality-screens read (READ-QUALITY-SCREENS-ADVENTUREWORKS-2026-10-30.md): run 0f5d3c4d measured 545 columns of
adventureworks and nothing on screen shows one of them. Q-0079's "the numbers behind this" opens data_class_match's
four scalars from 9 days ago; the value_profile route returns four scalars (surveyed_at, column_count,
measured_count, ran_as) because `_value_profile_results` nests its columns under `columns`, which the measurements
route skips by design. Design only: no code, no DDL. **No DDL is needed** (§7).

## 1. What exists to build on (read from main 16fa5676)

- Storage: `database_value_profiles`, one row per column per batch (schema, table, column, general_type, ~45 measure
  columns, state ∈ measured | not_measured | not_permitted | not_applicable, reason, ran_as; hash_state and
  frequency_state for the optional families), read by `registry.query_detail_rows("database_value_profiles", slug)`
  = the latest batch; `database_value_profile_frequent_values` holds a column's most common values when that family
  was on. A column that was not profiled has every measure NULL, never zeros; an empty table is measured with
  record_ct = 0 (real zeros).
- Reader: `_value_profile_results` (adapter :3058) already returns surveyed_at, column_count, measured_count,
  state_counts, ran_as and `columns[]` with column_path, state, reason and every measure. It is the one source.
- Routes: `/api/projects/<slug>/analyses/value_profile/measurements?entity_type=database&level=<level>`: at
  level ≠ resource it consults `adapter.analysis_container_results_map[analysis_id]` and returns that reader's rows
  as they are (slice 21a); value_profile has no container reader, so every level falls through to the four scalars.
- Screens: per-stage sub-tab strips with db-only tabs (`resourceTypes: ['db']`: Schema Inventory, Table preview);
  Table preview's "door" pattern (`setPreviewPick` from a Schema Inventory row); Curate › Structure's quality
  checks (quality-checks.js) grouped by table with colresize tables; the Questions row's "the numbers behind this"
  (`toggleMeasurementsInPlace(i, analysisId, btn, level)`), which takes the question's FIRST analysis id
  (`data_class_match` for Q-0079) and the question's level (`field`).
- Q-0079 is a field-level question answered by data_class_match, value_profile, reference_data_match (kind partial).

## 2. Where it sits

**A db-only sub-tab "Value profile" on every stage strip, between Schema Inventory and Table preview**, the same
gate as those two (`resourceTypes: ['db']`). The profile is a reading of the data, like the inventory and the
preview, not a stage's own content, so it belongs with them rather than under one stage. URL
`?type=db&resource=<slug>&tab=value_profile[&table=schema.table]`.

Two doors into it besides the tab:
- Schema Inventory: a table row gains "profile ›" beside "preview rows ›", carrying the table (the `setPreviewPick`
  pattern). A table with no profile row says "not profiled" instead of the link.
- Q-0079's row on the Questions tab: "the numbers behind this" lists the per-TABLE rows (§5), and its footer carries
  "open the value profile ›" to the tab. Curate › Structure's proposal rows gain nothing in this slice (a proposal
  already names the profile it came from); a later slice can link "Profile of <date>" to the column's row.

Not chosen: a card on Analysis › By analysis (design §10 said "a Value profile card: one row per column"): 1,236 rows
is not a card; and a column inside Schema Inventory (the inventory is estimates from the catalogue, the profile is
counts from the data; mixing them in one row hides which is which).

## 3. The screen

**Header line** (one sentence, two dates where they differ): "Value profile · 456 of 1,236 columns measured · 68
tables · profiled 2026-10-30 11:12 as postgres · 780 columns not measured: 52 tables over the 5-minute budget, 3
views". Counts come from state_counts and the per-table reasons; the reasons are grouped into at most three phrases,
the rest "and N more (see the tables)". Never "0 columns" for a state that did not arise: a state with no rows is
not mentioned.

**Filters**, the Curate pattern (select with counts): State (measured / not measured / not permitted / not
applicable), Type (text / number / date / boolean / time / other, from general_type), and a text filter on
schema.table. Default: every table, collapsed.

**Table cards**, one per table in schema order, collapsed by default, with colresize tables inside (owner rule
2026-10-20). A card's line:

`production.product · 504 rows · 25 columns measured · profiled 11:12`
or, not measured: `sales.salesorderdetail · not measured · profile incomplete · exceeded 5 min · counts not measured`
or: `person.vemployee · not applicable · a view, not a table: only tables are read`

A not-measured table has no column rows to open and no selector; its reason is its whole card (quality-checks.js's
not-established pattern). An EMPTY table reads `0 rows · empty table` with its columns openable and every count a
true 0.

**Column rows** inside a card. Columns, in this order, each a measured fact in number:

| Column | From | Words |
|---|---|---|
| Column | column_name | monospace, breakLong |
| Type | general_type | words: text, number, date, boolean, time, other (the letter is TestGen's) |
| Filled | value_ct of record_ct | "504 of 504" with a share "100%"; nulls are the complement and are NOT a second column unless `null_value_ct ≠ record_ct − value_ct` (then both, and a title) |
| Distinct | distinct_value_ct | "504 · all distinct" when = value_ct; "1 · one value" when 1; the number alone otherwise |
| Range | min/max by type | date: "2008-04-30 → 2014-02-08"; number: "0 → 3,578.27"; text: "lengths 1 → 50 (avg 12.3)"; boolean: "true 312 · false 192" (boolean_true_ct and the complement) |
| Most common | frequent values table | top 3 as "value ×count", "+N more" opening the full list in place; this cell is the one that may be wide |
| Flags | the relative-to-now and placeholder counts | only non-zero ones, as short words: "12 future dates", "3 empty strings", "7 before 1990"; nothing when all are 0 |
| Checks | proposals for this column | "4 proposed · 1 not established" linking to Curate › Structure filtered to the table; "—" when none |

A column that was not profiled (state ≠ measured) is one row: its name, a state cue (◌ not measured / ⊘ not
permitted / — not applicable, the glyph table's) and the reason, no numbers.

## 4. Never measured vs measured empty — the rules, per cell

Four different facts share the look of an empty cell today; each gets its own rendering:

1. **The column was not profiled** (row state ≠ measured): one sentence row, §3. No cell is drawn.
2. **The measure does not apply to this type** (a length on a number, a date range on text; the measure column is
   NULL because upstream takes no such measure): the cell is a muted "—" with title "not a measure of a <type>
   column". Decided from general_type and the measure family, not from the NULL alone.
3. **The measure applies but was not taken**: optional families with their own state: frequent values
   (`frequency_state`: NULL = "not asked for · off in Admin", "not_measured" = asked, could not be read, with the
   run summary's reason), hashes (`hash_state`, not shown on this screen), text shape (sampled; NULL on a text
   column = "not sampled"), percentiles (the batch's `percentiles_not_measured`). The cell says the short absence
   words with the reason as title, and the header counts them once ("most common values not collected: off").
4. **Measured and zero/empty**: "0" in number, plain ("0 nulls", "0 distinct" on an empty table, "no future
   dates" is only the Flags column's silence). An empty table's row says "0 rows · empty table" once; its columns'
   zeros are not repeated as flags.

The rows that reach the screen are rows of the latest batch only (`query_detail_rows` returns it), so a column
profiled in an earlier batch but not the latest is case 1 with the latest batch's reason, never the old numbers. A
later slice may offer "compare with the profile of <date>" (record_ct series exist: `value_profile_record_counts`).

## 5. How Q-0079's "numbers behind this" reaches it

Register a **container reader for value_profile** in `DATABASE_ANALYSIS_CONTAINER_RESULTS_MAP` (the slice 21a
mechanism, used by schema_inventory for the schema-level question). At level `field` (Q-0079's level) it returns one
row per TABLE: `{name: "production.product", value: "504 rows · 25 of 25 columns measured", opens: <tab URL with
table=>, note: "" | "not measured · <reason>"}`, grouped with not-measured tables last. 68 rows fit the in-place
panel; 1,236 column rows do not, and the tab is where they live. The panel's footer gains "open the value profile ›".

The row's own answer line (the headline) stays `_value_profile_headline`'s sentence, with two word fixes already
queued (W: "column(s)" → in number; the states in words). The envelope's choice of the FIRST analysis id for the
numbers button is the remaining defect: Q-0079's first analysis is data_class_match. The fix is in the question
row, not here: when a question has several analyses and the clicked row's level has a container reader for one of
them, offer "the numbers behind this" PER analysis (the step-line pattern from #924 already draws one line per
library step; the same line for RE's own secondary analyses), so data_class_match's four scalars and the profile's
68 table rows are each reachable. Until then the container reader alone does not change what the button opens, and
the brief must include that envelope change.

## 6. The route

One new read-only route, no new table:

`GET /api/databases/<slug>/value-profile` → the header (surveyed_at, ran_as, counts by state, reasons grouped) and
the table cards' lines (per table: record_ct, columns measured of total, state, reason), from one pass over
`_value_profile_results` (or a grouped SQL over the same table when the batch is large; the reader is the one
source either way).

`GET /api/databases/<slug>/value-profile/<schema>/<table>` → that table's column rows, with the frequent values
joined (top 3 + count of the rest) and the proposal counts per column from `database_quality_proposals`. One table
at a time keeps the payload under a few hundred rows; the tab fetches a table when its card opens (the Curate
proposals payload does the same: `groups[].rows` is null until a card opens).

Can an existing route serve it? The measurements route can serve the per-table rows (§5) once the container reader
exists, but not the column rows: its row shape is `{name, value, opens, note}`, a sentence per row, and the screen
needs typed cells. The proposals route reads only proposals. So: the container reader for §5 (existing route) plus
the two reads above. Both reads are reads of RE's own tables with the signed-in person's RE session; no database
credential and no Egeria call.

## 7. DDL

None. Every fact above is a column that exists on `database_value_profiles`, `database_value_profile_frequent_values`
or `database_quality_proposals`. Two things a later slice might want and would need DDL: a per-table outcome row
(today a not-measured table is known only through its columns' identical reasons; the reader groups them) and a
kept index on (database_slug, surveyed_at, schema_name, table_name) if the per-table read is slow on a big batch.
Neither is needed for adventureworks' 1,236 rows; measure first.

## 8. The screenshot it should give the owner

Desktop width (1440). The Value profile tab for adventureworks with the header line, the State filter showing
"measured (456) · not measured (…) · not applicable (…)", and ONE card open: `production.product · 504 rows · 25
columns measured · profiled 2026-10-30 11:12`, its 25 column rows visible with Filled, Distinct, Range and Most
common filled in words and numbers, two or three Flags ("12 future dates"), and a not-applicable table's one-line
card below it for contrast. No `null`, no identifier with underscores except the column names themselves, no raw
ISO timestamp.

## 9. Gate for the builder's PR

- e2e: the two reads on a registry with one measured table (one empty table, one not-measured table, one view) assert
  the header counts, the per-table lines, and the four cell cases of §4 (a NULL length on a number column renders
  "—" with the type title; a frequency_state NULL renders "not asked for"; a 0 renders "0").
- the container reader: Q-0079 at level field returns one row per table, not-measured last, each with `opens`.
- render harness for the tab at 800 px and 1440 px; colresize on the column table; tailwind build; boot.
- status-words: no "column(s)", US spelling, two dates only where they differ.
- the architect's live read on adventureworks after the restart: header, one card, Q-0079's panel.

Not in this brief: hashes and text-shape measures on screen (Admin-gated families; a later slice), comparing two
batches, publishing from this tab, and the Curate "Profile of" link.

## Addendum (2026-10-30, after #930/#931 shipped) — two statements of this note that the build made stale


**A1 — §1 "value_profile has no container reader".** True on main 16fa5676 when the note was read; #930 added one.
`DATABASE_ANALYSIS_CONTAINER_RESULTS_MAP["value_profile"]` now returns one row per table at level `field`
(`/api/projects/<slug>/analyses/value_profile/measurements?entity_type=database&level=field`), and Q-0079's step line
carries its own "the numbers behind this N ›" (`data-numbers-for=value_profile data-numbers-level=field`), so §5's
two halves (the reader and the per-analysis button) are both built. §1 should read: "value_profile's container reader
returns one row per table; before #930 every level fell through to the four scalars."

**A2 — §4.3 "frequency_state NULL = 'not asked for · off in Admin'".** Too narrow. In
`surveyors/database/quality/frequent_values.py` a NULL `frequency_state` means either (a) the column was not eligible —
eligibility is TestGen's rule: a measured text column with 2 to 70 distinct values of at most 70 characters — or (b) the
row predates the analysis. The screen (`value_profile_screen.py`) therefore reads a NULL in two ways, deciding
eligibility from the column's own measured counts:

| frequency_state | eligible? | cell | title |
|---|---|---|---|
| NULL | yes | "not asked for" | most common values were not collected on this run |
| NULL | no | "not collected" | read only for text columns with 2 to 70 distinct values of at most 70 characters |
| not_measured | — | "not read" | asked for, but could not be read inside its time budget |
| read, no child rows | — | "only blank values" | the read found no value other than blanks |
| non-text column | — | "—" (muted) | most common values are read for text columns only |

So §4's four cell cases hold, but case 3 ("applies but not taken") splits into "not asked for" (eligible, run did
not ask) and "not read" (asked, budget), and the ineligible column is a case-2 sibling ("not collected": the measure
does not apply to this column's shape) rather than a case-3 absence. The "off in Admin" words were a guess at the
cause and are not on screen; the cause of a NULL on an eligible column is not recorded, so "not asked for" is the
honest extent. If the run summary later records why frequent values were skipped, the title can say it.

Live read of both on adventureworks production.product (2026-10-30): "not collected" on name, productnumber
(all distinct, so > 70) and sizeunitmeasurecode (one value, so < 2); no "not asked for" case exists on that database.
