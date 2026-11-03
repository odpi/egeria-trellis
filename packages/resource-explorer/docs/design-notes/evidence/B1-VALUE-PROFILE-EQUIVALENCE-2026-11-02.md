# B1: value_profile as a table result, native read vs typed read — 2026-11-02

**Status:** branch `claude/re-b1-table-result`, commit 3. This is the equivalence proof that gates commit 3
(BRIEF-B1-TABLE-RESULT-AND-VALUE-PROFILE-2026-11-02, "Commit 3"). The live press of both runs on 8813, or on the gate's
isolated stack, is still the coordinator's call; this proof runs on the profiles already stored.

## How it was run, and what it did not touch

`scripts/b1_value_profile_equivalence.py`. It read the latest value-profile batch of each database, that batch's
frequent values and the latest proposals from the shared registry (5442) through a **read-only** psycopg2 session
(`set_session(readonly=True)`; it never constructs `ProjectRegistry` against 5442). It copied them into a scratch SQLite
registry, stored the typed table exactly as the step does (`value_profile.write_typed_rows`), and compared the two reads
there. No write to 5442, 8813 or Egeria.

The pre-B1 publish rows come from the catalogue at `cbab28be` (origin/main before this branch), so "identical" means
identical to what the publish table said before the conversion.

## Result

| | coco_pharma (`localhost_docker_coco_pharma`) | adventureworks (`laz_local_adventureworks`) |
|---|---|---|
| batch | 2026-10-24T21:18:04.067402 | 2026-10-30T11:12:58.965452 |
| native rows / typed rows stored | 1,916 / 1,916 | 1,236 / 1,236 |
| column count, typed = native | 1,916 = 1,916 | 1,236 = 1,236 |
| measured, typed = native | 1,846 = 1,846 | 456 = 456 |
| not permitted, typed = native | 52 = 52 | 0 = 0 |
| nulls_total, typed = native | 12,781 = 12,781 | 352,109 = 352,109 |
| `_value_profile_results` identical (every column, every field) | yes | yes |
| tab summary identical (header, filters, per-table lines) | yes | yes |
| every table card identical (266 / 157 tables, every cell) | yes | yes |
| first 1,000 rows identical in order | yes | yes |
| first 1,000 rows: field values differing | 0 | 0 |
| Questions row words | "1,846 of 1,916 columns profiled · 18 not applicable · 52 not permitted." both | "456 of 1,236 columns profiled · 780 not applicable." both |
| publish rows identical to pre-B1 (the new band aside) | yes: profiled_columns 1,846, columns 1,916 | yes: profiled_columns 456, columns 1,236 |
| publish band | "rows stay in Resource Explorer · summary published" | same |
| truncation sentence | "the first 1,000 of 1,916 columns; every column is stored and the summary counts them all" | "the first 1,000 of 1,236 columns; …" |

**Cost.** The step's added work is `write_typed_rows`: read this run's native rows back (so the typed order is the
registry's own on either backend) and store them. On 1,916 columns, SQLite, seven runs: median **81 ms** (read back
39 ms, store 43 ms), fastest 67 ms. Within the spike's +114 ms. Not yet measured on Postgres; the live press measures it.

## What changed in the declaration, and why not more

- `result_kind: table`, `key_fields: [schema, table, column]`, 28 typed columns, each with its absence word (every
  measure the native read hands the screen is a declared column: a test pins that nothing the tab shows is lost),
  `max_rows: 1000`, the truncation sentence above, order "by schema, table and column name, as the registry sorts them".
- **Columns keep the native names** (`record_ct`, `null_value_ct`, ...), not the brief's illustrative `record_count`,
  `null_count`: the typed read then compares with the native read field for field, which is what made the proof exact.
- **The summary is the two existing measures** (`profiled_columns` with its basis of `columns`, and `columns`), not the
  brief's five new names. Renaming them changes their publish locators (`value_profile:<measure>`), so saved
  Publish/Keep choices would be lost; adding three more adds three publish rows. Either breaks "publish table rows
  identical". `nulls_total`, `columns_not_permitted` and `tables_profiled` are a later, owner-approved addition.
- **Scope stays `field`**, not the brief's `member`: the publish table's scope column reads the declaration's scope, so
  `member` would change every publish row. A column is a field; B1's keyed-scope rules (key, absences, order,
  truncation) apply to both.
- The order is the registry's, not the catalogue's ordinal order: the tab has always listed columns by name, and the
  proof requires the same order.

## What still reads natively

`database_value_profiles` keeps every row it had: the quality checks, the proposals and the profile publish reshape read
it there (convert-as-touched moves only what the Value profile tab reads). A profile stored before this commit has no
typed rows; the tab reads it natively and draws no table block until the next run.
