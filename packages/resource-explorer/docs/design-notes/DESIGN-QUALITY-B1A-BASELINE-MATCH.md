# DESIGN — Quality B1a: baseline match on the measures RE already profiles, and the TestGen-only import (2026-10-27)

**Status:** built, and merged to main as PR #887 (2026-10-27) from branch `claude/re-quality-b1a-baseline-67dba5`. This is Q4 port order step 2
(`DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md` §4 B1a row, §5 row 2), plus the TestGen-only import of Q4 §6.5,
which §7 question 6 assigns to this slice. **Decision (project owner, 2026-10-26):** all seven §7 recommendations
accepted, so B1a is opt-in per table (question 2) and this slice builds the import (question 6).

No DDL. No write to Egeria, and none to the shared registry: every test runs against a temp SQLite registry and a
fake target connection. Calibration against a TestGen run was not done (§5).

## 1. What was built

| Piece | Where |
|---|---|
| Six types vendored unchanged: `Match_Row_Ct`, `Match_Distinct_Ct`, `Match_Null_Ct`, `Match_Zero_Length_Ct`, `Match_Min_Date`, `Match_Max_Date` | `configdata/quality/testgen/test_types/` |
| `gen_Match_Row_Ct.sql` vendored unchanged: Match_Row_Ct's selection is this template (`selection_criteria: TEMPLATE`) | `configdata/quality/testgen/gen_query_tests/` |
| Five selection fragments ported, each pinned to its SHA-256; Match_Row_Ct's port pinned to the template's hash | `quality/catalogue.py` `PREDICATES` |
| Defaults: four by column name; the two date baselines (`TO_CHAR(min_date, 'YYYY-MM-DD HH24:MI:SS')`) as pinned ports; Match_Row_Ct's `threshold_value` from the template's `s.record_ct` | `catalogue.default_params`, `DEFAULT_PORTS` |
| The opt-in per table, append-only | `quality/table_comparison.py` |
| Propose considers the six only for an opted-in table (or a key with a verdict) | `quality/propose.py` |
| Run: conditions other than `{THRESHOLD_VALUE}`, text measures, `null_measure_fails` | `catalogue.threshold`, `quality/run_checks.py` |
| The table card's "Watch for drift" button, read back like a verdict | `quality-checks.js`, `POST /api/quality-checks/{slug}/table-comparison` |
| CLI: `database quality-watch <slug> <schema.table> [--off]` | `cli/main.py` |
| TestGen-only import (§6.5) and its CLI `database quality-import-testgen <slug> --export <file> [--dry-run]` | `quality/testgen_import.py`, `cli/main.py` |
| `sync_quality_catalog.py --check` covers the generation template; in step with the clone at `e8a5a906` (`testgen/` identical to `3606859c`) | `scripts/` |

Tests: `tests/test_quality_b1a_baseline_match.py`.

## 2. Selection as ported

| Type | Upstream rule | Port note |
|---|---|---|
| Match_Row_Ct | `gen_Match_Row_Ct.sql`: every table of the profile run, `threshold_value = MAX(record_ct)` | No condition upstream, so an empty table is selected too (threshold 0). A table RE could not profile has no `record_ct`, so its default is NULL: not_established |
| Match_Distinct_Ct | `record_ct > 0 AND general_type <> 'X'` | baseline in `baseline_unique_ct`, a new params key and placeholder |
| Match_Null_Ct | `record_ct > 0 AND NOT (general_type = 'X' AND query_error IS NOT NULL)` | RE has no `query_error`. A column whose profile read failed is a row whose `state` is not `measured`, so that is the port of `query_error IS NOT NULL`. In practice such a row has no `record_ct` and the rule is NULL first |
| Match_Zero_Length_Ct | `general_type = 'A' AND value_ct > 0 AND sample_ratio IS NULL` | as written |
| Match_Min_Date, Match_Max_Date | `general_type = 'D' AND value_ct > 0` | as written |

## 3. The opt-in

Q4 §7 question 2: proposals only for a table a person opted in, from the table's card. TestGen opts in a **table
group**; RE opts in a **table**. That is a granularity difference the calibration evidence must list as expected.

Stored with no new table: each opt-in or opt-out is its own `app_settings` entry under
`quality_table_comparison::<slug>::<time_ns>-<random>`, written once (`add_setting_once`), the append-only trail
`dependency_table.py` already keeps for confirmations. Latest per table wins; the trail keeps who, when and a note.
The table must be in the stored inventory.

Verdicts win over the opt-in: a key with any verdict is still considered after an opt-out, so an accepted check
stays visible and keeps running until it is retired. An opt-out only stops new proposals.

## 4. Where the design did not hold against the code

Q4 §4 says B1a needs "nothing new" and §5 says "no DDL". Both hold for the proposals. Four things in the run step
did not, and one in the import:

1. **The date matches measure text; the results table stores a number.** Match_Min_Date and Match_Max_Date measure
   `TO_CHAR(..., 'YYYY-MM-DD HH24:MI:SS')` and fail on `<> {BASELINE_VALUE_LITERAL}`.
   `database_quality_results.measure` is `DOUBLE PRECISION`. Storing the text faithfully needs a column (DDL), which
   this slice is not cleared for. Built instead: the comparison is on the text, as upstream; `measure` holds the same
   wall-clock text as seconds from 1970-01-01 (`catalogue.text_measure_number`, lossless to the second TO_CHAR
   prints); the text is in `reason` and in the annotation (`measureText`), and `outcome_words` renders it back.
   Owner question 1.
2. **All six carry upstream's `null_measure_fails: true`.** RE's run step maps a NULL measure to `not_established`
   (slice 1, to avoid TestGen's old NULL-means-pass). Upstream's flag says these six **fail** on a NULL measure
   (`execute_tests_query.py`: a NULL measure with this flag turns a pass into a fail). That is the right reading for
   a baseline match: an emptied table or column has moved from its baseline. Ported: a type whose entry sets the flag
   fails at its severity on a NULL measure, with a reason that says so; every other type is unchanged (none of the
   nine earlier types sets the flag). Without this, calibration would show RE `not_established` against TestGen
   `Failed` on every emptied column.
3. **Conditions are not `{THRESHOLD_VALUE}`.** They are `{BASELINE_CT}`, `{BASELINE_UNIQUE_CT}` and
   `{BASELINE_VALUE_LITERAL}`. `catalogue.threshold` now reads the one placeholder each condition names; a condition
   that is anything else is refused. As before, RE compares in Python, so no baseline value is ever put into SQL; the
   date baseline must be exactly `YYYY-MM-DD HH:MM:SS` and a real date, or the check is not_established quoting it.
4. **The date baseline from a stored profile date.** The profile stores dates as the target returned them (ISO text,
   with an offset for `timestamptz`, in the reading session's zone). The port of `TO_CHAR(min_date, ...)` keeps the
   wall clock and drops the offset and fractional seconds, because the run step's `CAST(MAX(col) AS TIMESTAMP)` reads
   the same wall clock through the same account's session. If the profile and the run ever used sessions in
   different time zones, a `timestamptz` match would fail spuriously. Owner question 2.
5. **The import cannot name TestGen's test definition id.** Q4 §6.5 asks the proposal's `reason` to name it.
   `export_testgen.py` (in egeria-workspaces-fs) reads `td.id` and then pops it before writing the test. The import
   reads `id` or `test_definition_id` when present and otherwise says "(id not in the export)", and names the suite,
   test run and profile run. The fix is one line in `export_testgen.py` (keep `d["id"]`), in another repository, so
   it is a follow-up, not done here.

## 5. The TestGen-only import (§6.5)

`resource-explorer database quality-import-testgen <slug> --export testgen-export.json` reads the harness's export
file and nothing else; it never reaches TestGen or Egeria.

- Only a type in `PORTED_TEST_TYPES`. Others are counted by type for the calibration evidence, not proposed.
- An inactive TestGen test is not imported. A key with a live rejected verdict is not imported; a live accepted one
  is a match; a key RE's own latest batch proposes, accepts or suppresses is a match and RE's row stands.
- Parameters come from TestGen's definition (text there, numbers here) and are validated as RE's own are (condition
  and measure). One that does not validate is a `not_established` row that quotes it, so it can be seen and not
  accepted.
- `origin` is `testgen` (the column is free text: no DDL), `catalogue_ref` the vendored type's, `reason` the run
  facts.

**Why the import writes a batch, and why propose carries it forward.** Proposals are "latest batch wins". An import
written as its own batch would hide RE's rows, or be hidden by the next propose run. So the import writes the latest
batch with its rows merged in, and `run_propose` carries open imported rows into each later batch (their state set
from verdicts like RE's own). RE's own row replaces a carried import when RE now decides the same key itself (for
example once the table is opted in); an import replaces RE's `not_established` row for a key, since TestGen's
definition has the parameters RE's profile could not give.

The 10-21 gate (one TestGen-only proposal accepted in Curate and run by RE) is proven in a test against a temp
registry and a fake target (`test_imported_proposals_are_carried_forward_accepted_and_run`), not live.

## 6. Not done here

- **Calibration evidence** (Q4 §6.4): needs a static copy of coco_pharma, a TestGen run with a Table Comparison
  table group, and RE's run on the same copy. Expected asymmetries to list once: TestGen opts in a table group, RE a
  table; the date measures compare as text on both sides, while RE's `measure` column holds seconds (§4.1).
- **The harness's compared types** still default to slice 1's seven in `compare_re_testgen.py` (§6.2 item 4 asks it
  to read RE's `test_types/`). Another repository; follow-up.
- **`export_testgen.py` keeping the definition id** (§4.5). Another repository; follow-up.
- **Added run time** (Q4 §5.2): six more aggregates per opted-in table in the existing per-table SELECT; not measured
  on a static copy.

## 7. Questions for the project owner

1. **A text measure column?** The date matches store their measure as seconds from 1970-01-01 because
   `database_quality_results.measure` is numeric (§4.1). *Recommendation:* add a nullable `measure_text TEXT` with
   profile 1b's DDL (step 3 already brings DDL), write the TO_CHAR text there, and keep the number for charts.
2. **Session time zone for `timestamptz` date matches.** (§4.4) *Recommendation:* leave as built, and add
   `SET LOCAL TIME ZONE 'UTC'` to both the profile read and the run when profile 1b touches `begin_read`, so the
   wall clock cannot differ between them.
3. **Opt-in granularity.** RE opts in a table, TestGen a table group (§3). *Recommendation:* keep per table, as §7
   question 2 recommended; a "watch every table in this schema" convenience can come later if asked for.
