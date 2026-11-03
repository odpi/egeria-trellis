# Table-count rename — "relation_count"/"relation_total" — implemented

**Dispatch:** a follow-on item from the "Resource Explorer expansion architecture" session's
G-series dispatch, after G3 (`re/empty-state-split`) — the "table count" rename named in
`REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md` §2.3.
**Branch:** `re/table-count-rename`, off `origin/main` `9bda025d`, built in a separate worktree
(`/Users/dwolfson/localGit/egeria-v6/trellis-re-table-count-rename`).
**Coordination:** file list confirmed with the "Resource Explorer expansion architecture" session
before writing. That session added two hard requirements after reviewing the plan (both addressed
below) and a conflict-check list against four other in-flight branches (also below, all clean).

## The bug

> "table count" means two populations on one screen. The evidence rail (`schema_inventory`) says
> **157**, which counts base tables, views and matviews. The Relationship Graph card says **68**,
> which is base tables only. [...] rename at the source: "tables and views" or "relations" for 157.

`_schema_inventory_results` and `_row_count_snapshot_results` (both in
`survey_definition_adapter.py`) each built `"table_count": len(tables)` from every stored
`database_tables` row for a database — base tables, views, materialized views, and foreign tables,
all combined. Nothing in the field name said so. The credential-capability probe
(`connection.py`'s `get_credential_capability()`) has the identical shape: its whole-database
`table_total`/`table_select` also count every relation kind, feeding the persistent
"connected as X ... SELECT on N of M table(s)" banner and several RFA/shortfall messages with the
same silently-wrong word.

`db_relationship_graph`'s own `table_count` (the "68") is a genuinely different, correct field —
it counts base tables only, by design (`_attach_coverage_status`'s own comment already documents
this). **Left unchanged.**

## The fix

Renamed at the source, in the whole-database (not per-schema) scope only:

| old key | new key | where |
|---|---|---|
| `table_count` | `relation_count` | `_schema_inventory_results` |
| `table_count` | `relation_count` | `_row_count_snapshot_results` |
| `table_total` | `relation_total` | `connection.py`'s `get_credential_capability()` (whole-database) |
| `table_select` | `relation_select` | `connection.py`'s `get_credential_capability()` (whole-database) |

**Deliberately NOT renamed** — these already mean base tables only, or are a legitimately
schema-scoped count the reply's "two populations on one screen" example was never about:

- `db_relationship_graph`'s own `table_count`/`unmeasured_table_count` (`_attach_coverage_status`,
  `_db_relationship_graph_container_headline`) — base tables only, correct as named.
- `grain_determination`'s `table_count` (`_grain_determination_headline`) — out of the assigned
  scope (only `_schema_inventory_results` and the `credential_capability` reader were named); its
  semantics weren't audited here and it wasn't part of the reply's worked example.
- `connection.py`'s **per-schema** `by_schema[name]["table_total"]`/`["table_select"]`, and every
  reader of it (`schema_scope.container_scope_states()`'s per-schema state dicts,
  `_schema_inventory_container_rows`'s own per-schema `table_count`,
  `_attach_container_credential_scope`'s per-container fraction). A schema's own table count is a
  single population already, not two different figures competing for the same screen — renaming it
  would have touched `_schema_inventory_container_headline`/`_schema_inventory_container_measurements`
  (G3's own territory) and a large, already-tested, already-shipped surface for no reader-facing
  gain the reply asked for.
- `database_surveys.table_count` (the SQL column `record_database_survey()` writes, read back by
  `databases.py`'s summary/diff endpoints and `cli/main.py`) — a distinct, separately-computed
  figure (`schema_info.get("total_tables", 0)` at write time), not read from either renamed
  reader's dict. A candidate for a *separate* future audit, not touched here.

### Text changed alongside the keys

- `_schema_inventory_headline`: `"{relation_count} relation(s) (68 base, 87 view, ...)"` (was
  `"{table_count} table(s) (...)"`).
- `_row_count_snapshot_headline`: `"No row counts recorded for any of {total} relation(s)."` /
  `"({measured} of {total} relations measured)"`.
- `_credential_scope_status`'s `fraction`: `"{select} of {total} relation(s) in {visible} of
  {total} schemas"`.
- `schema_scope.credential_shortfall()`'s `phrase`: `"{reachable} of {n} {level}s readable; {select}
  of {total} relation(s)"` — this is the same string the RFA summary and `_credential_scope_status`'s
  own `schema_fraction` both reuse, so all three updated together by construction.
- `credential_capability.assess()`'s `READ` detail: `"...SELECT on {got} of {total} relation(s)..."`
- `database_surveyor.py`'s `_create_credential_capability_annotations()`: both the
  `ResourceMeasureAnnotation.summary` and the RFA fallback fraction text.
- `app.js`'s persistent credential-visibility banner (`~line 2888`): `"SELECT on N of M relation(s)"`.
- `facts.py`'s `_note_for()` MEASURED_WITHIN_CREDENTIAL_SCOPE catalog-recovery note reads
  `relation_count` now (this was a fifth consumer of `_schema_inventory_results`'s field, found only
  by chasing test failures — not caught in the original file-list message).

### Also fixed while in there (architecture session's instruction)

`app.js`'s evidence rail (`showEvidence`) printed `"${f.state} · ${f.provenance} · run 2m ago"`.
`Fact.provenance` defaults to `PROVENANCE_MEASURED = "measured"`, the same string as
`result_status.MEASURED`, so a plain local measurement printed **"measured · measured · run 2m
ago"** — the state twice. Provenance is now shown only when it differs from state (i.e. when it
actually says ☁ Egeria / 🏠 local / ⏳ pending, per this file's own "Survey source display" table,
rather than repeating "measured").

## Backward compatibility (architecture session's hard requirement)

Every `credential_capability` probe already stored inside a survey's `survey_data` blob — every
database ever surveyed before this change, `coco_pharma` and `adventureworks` included — carries
the OLD key names (`table_total`/`table_select`) forever, since nothing rewrites historical rows.
`database_surveyor.py`'s own `_store_results` even carries a PRIOR run's stored
`credential_capability` forward verbatim when the current run's own steps didn't include the probe
(`prior_credential_capability`), so old- and new-shaped blobs will coexist in the same registry
for a long time, not just at the moment of this rename.

Every reader that consumes this probe reads the new key with the old key as a fallback:

- `credential_capability.py`'s `assess()`: `probe.get("relation_total", probe.get("table_total")) or 0`
- `schema_scope.py`'s `credential_shortfall()`: same pattern on `cap`
- `survey_definition_adapter.py`'s `_credential_scope_status()`: same pattern on `cap`
- `database_surveyor.py`'s `_create_credential_capability_annotations()`: same pattern on `info`
  (defends `publish_step_annotations`'s stored-step-output call path, even though the more common
  call site always has a fresh probe)
- `app.js`'s credential banner: `cap?.relation_total ?? cap?.table_total`

`_schema_inventory_results`/`_row_count_snapshot_results`'s own `relation_count` needs **no**
fallback: both build their value dict fresh from the current `database_tables` rows on every call
— they never read a `table_count`/`relation_count` field back out of stored JSON. (`facts.py`'s
`_note_for()` still reads with a fallback anyway, for defense in depth, since it receives whatever
`value` a Fact happens to carry.)

**Verification:** `tests/test_relation_total_rename_backward_compat.py` feeds a fixture built from
the exact pre-rename shape (old keys only, no `relation_*` keys at all) through all four renamed
readers and asserts each still produces the correct fraction/summary/RFA — not "0 of 0". One test
also stores that fixture through `registry.record_database_survey()` and reads it back through
`_credential_scope_status()`, the real end-to-end path a pre-rename stored survey takes.

## Conflict check (architecture session's requirement)

`git merge-tree --write-tree <branch> HEAD`, all four clean (a tree SHA printed, exit 0, no
`CONFLICT` markers):

- `origin/re/column-profile-savepoint-fix` (touches `connection.py`, `survey_definition_adapter.py`)
- `origin/re/empty-state-split` (G3's own branch — touches `_schema_inventory_container_*` in
  `survey_definition_adapter.py`; disjoint from this branch's `_schema_inventory_results`/
  `_schema_inventory_headline`/`_credential_scope_status` edits)
- `origin/re/structured-table-clobber` (`database_surveyor.py`'s `_store_results`)
- `origin/re/g1-glyph-consolidation` (`app.js`)

No rebase needed; staying off `9bda025d` as instructed.

## Testing

Existing tests updated for the rename (all were asserting the OLD key names / OLD wording, so a
full run was the actual finder — two whole passes of the full suite surfaced these, not a
targeted search):

- `test_postgres_catalog_fallback.py`, `test_schema_enumeration_floor.py`,
  `test_schema_containment_grain.py`, `test_schema_inventory_headline.py`, `test_stage_page.py`,
  `test_db_fs_results_and_questions.py`, `test_fact_layer_resource_type_dispatch.py`,
  `test_requires_capability_gate.py` — top-level `table_count`/`table_total`/`table_select`
  fixtures and assertions renamed; every `by_schema[...]` per-schema fixture left untouched.

```bash
uv run pytest tests/test_credential_capability_step.py tests/test_postgres_catalog_fallback.py \
  tests/test_schema_inventory_container_headline.py tests/test_schema_enumeration_floor.py \
  tests/test_schema_containment_grain.py tests/test_schema_inventory_headline.py \
  tests/test_schema_inventory_results_evidence_fields.py tests/test_slice17_question_level_gate.py \
  tests/test_stage_page.py tests/test_store_results_preserves_prior_operations.py \
  tests/test_db_fs_results_and_questions.py tests/test_fact_layer_resource_type_dispatch.py \
  tests/test_requires_capability_gate.py tests/test_relation_total_rename_backward_compat.py -q
# 257 passed

uv run pytest tests/ -q   # full suite, 6609 passed, 103 skipped, exit 0
```

## Status

Pushed to `origin/re/table-count-rename`, on top of `4e02abd2`. No PR opened, per the standing
dispatch instruction (project owner away for the evening; `gh` PR creation hangs on an unattended
1Password prompt). Committed the morning after 1Password was confirmed unlocked, after a
`git merge-tree --write-tree origin/main HEAD` check came back clean; reported branch/tip to the
"Resource Explorer expansion architecture" session on push.
