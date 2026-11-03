> **Spike closed 2026-10-31 (owner); recommendation "convert as touched" accepted; twins not merged; branch `re/spike-analyses-as-library-steps` @ 41feb550 kept for reference.** The harness that produced these figures is `scripts/spike_ls5_timing.py`; it runs on main without the twins and times the native side alone.

# SPIKE RESULT — two RE analyses as Analytic Library steps (LS5, 2026-10-31)

Branch `re/spike-analyses-as-library-steps`, tip `41feb550` (cut from `b0c17965`). Nothing native changed or removed; no DDL; no Egeria write.
Flag `RE_SPIKE_LIBRARY_TWINS=1`. Medians of 3 runs, load average 10–14, no gate running. Harness: `packages/resource-explorer/scripts/spike_ls5_timing.py`.

## The numbers

| Measure | coco_pharma (263 tables, 1,916 columns) | re_reproof_postreset_1025 (4 tables, 13 columns) | Pass? |
|---|---|---|---|
| Load of the library's manifests (engine-host line), with 2 extra packages vs without | about 1.66 s vs 1.55 s (noise ±70 ms) | same process | yes: about +100 ms for both, under +200 ms per step |
| Statistics freshness, native **as the Questions row pays it today** (`run_db_derived`, recomputes all derived checks) | 824 ms | 440 ms | — |
| Statistics freshness, native function alone (`derive_statistics_freshness`) | 0.9 ms | 0.1 ms | — |
| Statistics freshness, twin run (read + shape + store 267 results) | 137 ms (wrapper run 89 ms) | 25 ms (11 ms) | against the alone-function: no (+88 ms); against what the screen pays today: the twin is about 6× cheaper |
| Value profile, native reader (`_value_profile_results`) | 45 ms | 3 ms | — |
| Value profile, twin run (read + group 1,916 columns + store 266 tables) | 159 ms (+114 ms, +250%) | 21 ms | no, if "run" is compared with a read; see below |
| Value profile, **reading the stored twin result** (what a screen does with the flag on) | 10 ms | 1.6 ms | yes: faster than the native read (45 ms) |
| SQL statements | twin counted: freshness 1 typed read, value profile 2. Native **not counted** (the registry's raw connections bypass the engine hook the counter listens to); the native value-profile reader's 2 statements (latest `surveyed_at`, then its rows) are read from `query_detail_rows`, not measured | same | unproven by measurement: the twin's count is measured, the native's is read from the code |
| First import of a step's code (`first_load_ms`) | 0.0 ms (already imported by discovery) | | yes |

**Equivalence.** Value profile: column count (1,916), measured (1,846) and state counts identical; Value profile screen header and 266-table list identical flag off vs on (after fixing `ran_as`, `41feb550`). Statistics freshness: never analyzed 226, stale 0, current 37 identical; **the denominators differ**, 266 tables in the twin vs 263 native, because native keeps base tables only and the twin counts the 3 other rows in the snapshot as "not read" (a real gap: the typed read does not say what kind of table a row is). Publish table with the flag on: rows come from the twin's declaration (2 measures per analysis) rather than the native 4 (stale / never analyzed / current / not read), so the words and the measure list differ by design; the per-table measures show no single value (member scope), the summaries do (226; 1,846).

## What was awkward

- **A native read with no typed-read equivalent:** table kind (base table vs view) for freshness; the twin cannot drop views without a second read or a wider `table_activity_snapshots`.
- **A measure the declaration cannot say:** per-column cells of the Value profile tab (about 40 measures × 1,916 columns). The twin carries state, type, record count and four counts per column in one text field, so the tab's table detail still reads native rows. A declaration describes numbers about a result; it is not a place to store the table.
- **A screen that needed native code:** the Value profile screen needed a branch (`_profile`) to read the twin; the Questions row, publish table and (by the fact layer) the Chat compile needed none beyond the fact-layer alias and the declared-list alias. The Chat compile itself was not exercised against live data in this spike.
- **Registration cost:** each step touched the generated catalog entries, the engine-host declaration documents, four tests' expected lists and the typed-read allow-list (`STEP_ODS_METHODS`). A conversion is more than a code move.
- **The native cost is not where it looked:** the freshness function takes under 1 ms; the 824 ms is the whole derived pass recomputed on every read. The twin is faster only because a stored result is read, not recomputed.

## Recommendation

**Convert as touched**: the stored-result read is faster than the native one and the Questions row, publish table and compile needed no per-analysis renderer, but a run costs more than a native read (value profile +114 ms on 1,916 columns) and two gaps (table kind, per-column detail) need typed-read and declaration work first, so a mass conversion would buy nothing the owner can see yet.

Not measured: Chat compile against live data; the 8813 boot itself (the start-up figure is the manifest load inside the engine-host line, in a fresh process).
