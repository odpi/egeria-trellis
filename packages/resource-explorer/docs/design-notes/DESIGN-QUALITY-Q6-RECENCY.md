# DESIGN — Quality slice Q6: Recency, the date branch of the functional-type inference, and Future_Date_1Y (2026-10-26)

**Status:** built, and merged to main as PR #880 (2026-10-27) from branch `claude/re-quality-q6-recency-d5e308`. Step 1 of the port order in
`DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md` (§5 row 1, §5.1), whose seven §7 recommendations the owner
accepted:

**Decision (project owner, 2026-10-26):** all seven recommendations of the Q4 note's §7 are accepted as written.

The gate, from `DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md` §3: *the eighth check proposes on a dated column and
nowhere else.* No Egeria write, no write to the shared registry, and no read of a surveyed database was made to
build or test this slice.

## 1. What was ported, and from where

Everything comes from the owner's TestGen clone, `~/localGit/egeria-v6/dataops-testgen`, whose HEAD is
`e8a5a906` ("docs: Trellis evaluation note…"), one docs commit on top of the vendored commit
**`3606859c`** (release 5.134.0). `git diff --stat 3606859c HEAD -- testgen/` is empty, so every file below is
as it is at `3606859c`, the commit `PROVENANCE.md` already names.

| Vendored file | Upstream file | What |
|---|---|---|
| `test_types/Recency.yaml` | `testgen/template/dbsetup_test_types/test_types_Recency.yaml` | unchanged |
| `test_types/Future_Date_1Y.yaml` | `testgen/template/dbsetup_test_types/test_types_Future_Date_1Y.yaml` | unchanged |
| `postgresql/profile_measures.sql` | `testgen/template/flavors/postgresql/profiling/project_profiling_query.sql` | one more measure line, verbatim: `before_20yr_date_ct` |
| `postgresql/templated_functions.yaml` | `testgen/template/flavors/postgresql/profiling/templated_functions.yaml` | `DATEDIFF_DAY` and `DATEDIFF_MONTH`, verbatim |
| `profiling/functional_datatype_date.sql` | **`testgen/template/profiling/functional_datatype.sql`** (629 lines) | the eight statements that can label a date column, verbatim, in upstream order, each headed by its line range |

The Python ports are pinned to what they were ported from, as slice 1's are: the two selection rules to their
`selection_criteria` SHA-256, Recency's default threshold to its `default_parm_values` SHA-256
(`catalogue.DEFAULT_PORTS`), and the inference to the SHA-256 of `functional_datatype_date.sql`
(`functional_type.VENDORED_SQL_SHA256`). `scripts/sync_quality_catalog.py --check` now also checks the
templated functions and each vendored statement still appear verbatim upstream; against the clone it reports
"in step with upstream".

## 2. The inference: what the whole file says about a date column

The Q4 note asked for the whole file to be read for anything that labels a date column by name before the
CASE. The file is 38 ordered UPDATEs. Twenty carry `AND functional_data_type IS NULL`, so the first label wins;
18 do not. Of those 18, the first clears every label, and several act only on a label another statement already
set (for example 'ID' to 'ID-Unique', 'Measurement' to 'Measurement Pct'); the rest can overwrite a date
column's label. For a column of `general_type 'D'`, in upstream order:

| Upstream lines | Statement | Guard | Ported as |
|---|---|---|---|
| 18-25 | `'TBD (Not enough data)'`: `record_ct = 0`, or `value_ct < 5`, or every value zero-length | none (first) | `date_case_label` |
| 28-36 | `'Constant'`: one distinct value filling > 75 % of the records | IS NULL | `date_case_label` |
| 39-49 | **by name**: `column_name ILIKE '%_sk'` 'ID-SK', then `ILIKE '%_id'` 'ID' (the table group's masks; TestGen's defaults) | IS NULL | `date_case_label` |
| 72-116 | **the date CASE**: `before_20yr_date_ct / value_ct >= 75 %` 'Historical Date', then `future_date_ct / value_ct >= 95 %` 'Future Date', then `>= 50 %` 'Schedule Date', then the later branches | IS NULL | `date_case_label`; the later branches are one outcome, `LATER_DATE_BRANCH` |
| 136-142 | **by name**: `'Process ' \|\| functional_data_type` for names like `update_date`, `modified` | **none** | `recency_excluded` |
| 228-250 | Period Month / Period Week overrides, by min/max date alignment and the date-days/weeks/months measures | **none** | `recency_excluded`, partly (§3) |
| 252-262 | **by name**: `month`, `week` -> 'Period Month' / 'Period Week'; `period` -> 'Period' | **none** | `recency_excluded` |
| 356-366 | **by name**: 'Process User' (`^(last_?\|)(create\|update\|…)(.*?(by\|id\|…))$`), 'System User' (`(user\|usr)_?(name\|nm)?$`) | **none**, and no type filter | `recency_excluded` |
| 472-517 | everything still NULL -> 'UNKNOWN' (for a date column, every branch's numeric inputs are NULL) | IS NULL | not needed: it means a date column never ends NULL |

Two consequences shape the port:

- **Recency's exclusion is never NULL upstream.** Every date column ends with some label (the last statement
  sets 'UNKNOWN'), so `NOT functional_data_type IN ('Future Date', 'Schedule Date')` is true or false. Recency
  needs only one bit: *is the final label exactly 'Future Date' or 'Schedule Date'?* A label that is not one of
  those two after the date CASE and the Process prefix cannot become one later, since every later unguarded
  statement sets some other label. `recency_excluded` is that bit, three-valued.
- **The order matters for real, but not where the Q4 note put it.** `before_20yr_date_ct` (more than 240 months
  before the run) and `future_date_ct` (after the run) count disjoint rows, so 'Historical Date' (>= 75 %) can
  never pre-empt 'Future Date' (>= 95 %) or 'Schedule Date' (>= 50 %); porting the Historical branch first
  changes the label, not Recency's selection. What does change Recency's selection is the earlier and later
  statements above: a future-dated column named `due_id` is 'ID', one named `update_date` is 'Process Future
  Date', and neither is excluded. Those are ported, and tested (`test_earlier_statements_label_…`,
  `test_recency_excludes_only_a_future_or_schedule_label_…`).

Not ported, with the reason each cannot change Recency's answer on what RE profiles:

- **The date CASE's later branches** (Transactional Date and its Wk/Mo/Qtr forms, 'Date (TBD)', 'Date Stamp',
  'DateTime Stamp'): they read `before_5yr_date_ct`, `date_days_present`, `date_weeks_present` and
  `date_months_present`. None is a label Recency excludes.
- **The character-date statements** (119-134) and the CASE's `value_ct = date_ct + zero_length_ct` arm: they
  label text columns, and Recency selects only `general_type = 'D'`.
- **'City'** (301-318 and 520-536) and **'ID-FK'** (569-579): cross-column. City needs the next two columns to
  be 'Zip' and 'State', and its second form reads text-only measures (NULL on a date column, upstream too).
  ID-FK needs the same column name to be a unique ID in another table, and a date column with such a name
  (`%_id`, `%_sk`) is already 'ID' / 'ID-SK' by the masks. Both are family C (port step 6).
- **The person-name statements** (330-347): they read `avg_embedded_spaces`, which upstream measures for text
  columns only, so on a date column they never fire.

## 3. Absent is not zero

- `before_20yr_date_ct` is a nullable column (`docs/ddl-value-profile-q6.md`). NULL means not measured: a row
  from before the column, a column that is not a date, a table not profiled, or an empty table.
- An inference input that is NULL makes the label unknown, never a label: `date_case_label` returns None, and
  Recency is `not_established` with the input named, unless another of its terms already says no. Upstream's
  own NULL behaviour is kept only where upstream gets the NULL itself (`zero_length_ct` and `filled_value_ct`
  are not measured on a date column there either, and the CASE's `NULLIF(value_ct, 0)`).
- **The Period Month and Period Week overrides** read `date_days_present`, `date_weeks_present` and
  `date_months_present`, which RE does not measure. Their date-bound part RE can evaluate (min and max both
  at midnight on the first, or both on the last, day of a month; min on Sun/Mon/Fri/Sat and on the same weekday
  as max). When that part fails the override cannot fire; when it holds and the label is Future or Schedule,
  Recency is `not_established` rather than a guess. In practice this is rare: a Future or Schedule Date has
  `max_date` after the run, and Recency's own `max_date <= run_date + 1 day` already says no to almost all of
  them.
- A measured column with no values says "the column had no values when profiled, so max_date is undefined",
  not "the profile did not measure".

## 4. The two checks

**Recency** (Timeliness, severity Warning). Selection, term for term: `general_type 'D'`, `max_date <= run_date +
1 day`, the name is not `filedate`/`file_date`, not excluded by the inference, and `DATEDIFF('DAY', max_date,
run_date) <= 62`. `run_date` is the profile's own instant (its `surveyed_at`), as upstream's `profile_results.run_date`.
`DATEDIFF('DAY', …)` here is TestGen's **metadata** function, `DATE_PART('day', second - first)`: whole 24-hour
periods, truncated toward zero, not a difference of calendar dates (`functional_type.datediff_day`). The default
threshold is the vendored CASE over that difference. The run-time measure is the vendored
`<%DATEDIFF_DAY;MAX({COLUMN_NAME});'{RUN_DATE}'::DATE%>`, which is a calendar-date difference on the surveyed
database. Upstream's templated functions are expanded in the vendored text **before** any identifier or value is
filled, so nothing a database names passes through the `;`-split.

**Future_Date_1Y** (Timeliness, severity Fail). Selection reads only `general_type`, `future_date_ct` and
`max_date`, plus `{AS_OF_DATE}`: the instant checks are generated, which in RE is the propose run's
`surveyed_at` (TestGen's generation run with no profiling delay). It is a plain B0 port: no new measure.

In the end-to-end test over a fake connection, Recency is proposed on a recent date column and on a
mostly-historical column whose latest value is recent (upstream selects that too: 'Historical Date' is not
excluded). It is not proposed on a stale date column, a Schedule Date column, a text column or a number.
Future_Date_1Y is proposed on the Schedule Date column only.

## 5. Open

- **`before_5yr_date_ct` is not ported.** It is read only by the date CASE's later branches, which also need
  the three date-days/weeks/months measures (profile 1b, port step 3). Adding it alone would cost a measure no
  rule reads. Recommendation: add it with profile 1b, together with the later branches, so the CASE is then
  ported whole.
- **Added profile time is not measured yet.** Q4 §5.2 asks each measure-adding slice to state the added time on
  the largest coco_pharma and adventureworks tables, from a static copy. One more `SUM(CASE …)` per date column
  in the existing single scan should be small next to `COUNT(DISTINCT …)`, but that is an expectation, not a
  measurement. It belongs to the calibration run on the static copy (Q4 §6.3, recommendation 5).
- **Calibration against TestGen** for these two types (Q4 §6.4's gate: zero unexplained disagreements on the
  static copy) has not been run.
