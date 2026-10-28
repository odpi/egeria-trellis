# db_hub_tables and the owner-question wording: implemented

**Implements:** slices D2 and D3 of `PLAN-ANALYSIS-GAPS-REPOS-AND-DATABASES.md`.
**Branch:** `re/db-hub-tables-and-owner-text`, cut from `origin/main` at `2d44c2f9`.
**Source:** `RULING-DB-QUESTION-CATALOG-CONSISTENCY.md` section 1, the Backlog
entry "`db_hub_tables` is most of the way there for free", and design 5.3.

Setup proof (before any test): `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"`
printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-db-hub-owner/packages/resource-explorer/resource_explorer/__init__.py`.

## D2: `db_hub_tables`

### What it reads

Stored rows only, through the same `load_inputs` every `db_derived` check uses.
No connection is opened (the existing `TestZeroFetch` runs the whole step with
`database_connection` booby-trapped; `test_db_hub_tables.py` repeats that for
this check). Four signals per base table (views are excluded, as in every other
structural check):

| signal | read from | weight | established when |
|---|---|---|---|
| `fk_in_degree` | `database_columns.foreign_key_json`: distinct OTHER tables that reference the table | 3 | keys were captured for EVERY base table |
| `reads` | `database_table_activity.seq_scan + idx_scan` | 2 | the table has a counter row with at least one non-NULL counter |
| `rows` | `database_tables.row_count` | 2 | not NULL |
| `comment` | `database_tables.description` | 1 | some table or column anywhere carries a description |

A table's score is the weighted mean of its normalised signals (in-degree as a
share of the maximum, rows and reads log-scaled against the maximum). The list
is the top ten tables with a score above zero.

### NULL stays NULL

Tables are only comparable on the same evidence, so a signal takes part in the
ranking only when it is established for ALL base tables. The signals left out
are named in `signals_not_used`, each with how many tables lacked it and why,
and the explanation says they are "never counted as zero". A value that was
never established shows as `None` on the table's entry, not `0`. Specific
cases pinned by tests: all counters NULL, one table with no activity row, one
NULL `row_count`, keys never captured, one table with uncaptured keys (which
makes no table's zero in-degree a finding), no comments anywhere. A real zero
(keys captured for every table, nothing references the table) reads `0`. If no
signal is established for every table the state is `not_measured` with reason
`no_comparable_signal` and no list; if there are no table rows it is
`not_measured` with `no_schema_rows`.

The payload also carries `provenance` (`read_snapshot` and the per-table
`table_surveyed_at` map), which is what "carries the check's provenance" means
on the answer.

### Where it is declared (copied from the other checks)

- `db_derived.py`: `derive_hub_tables`, an entry in `DB_DERIVED_ANALYSES`, a
  key in `run_db_derived`'s `derived`, `_hub_annotations` in `build_annotations`
  (one `ResourceMeasureAnnotation`, `check_name` spelled as a literal).
- `analysis_catalog.yaml`: `db_hub_tables`, database only, intent `discovery`,
  `whole_resource_only`.
- `survey_definition_adapter.py`: a `DATABASE_ANALYSIS_RESULTS_MAP` entry (the
  shared `_db_derived_field_reader`), a `DATABASE_ANALYSIS_HEADLINE_MAP` entry
  and a `DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP` entry. The last is needed
  because the question is asked at member level and the level gate only accepts
  a level-specific headline; the relayed explanation names the top tables, so
  it is a genuine member-level reading. `DATABASE_ANALYSIS_RE_STEP_MAP`, the
  run route, the scheduler and the workflow dispatch all derive from
  `DB_DERIVED_ANALYSES`, so no edit was needed there.
- `apply_container_grain`'s `targets` list was NOT extended: the ranking is
  whole-database, and no per-schema breakdown is claimed.

The brief said "seventh `db_derived` check". The step already lists eleven ids
before this one (the six originals, `schema_diff`, `grant_change`, and the three
section-16 checks), so this is the twelfth. Behaviour is unaffected.

### The question it closes

"Which tables would a consumer start with?" (database, member level). Before:
`kind: gap`, note "GAP: db_hub_tables (proposed) ...". After: `kind: analysis`,
`analysis_ids: [db_hub_tables]`. Changed in `resource_questions.csv` (the
Answering Analysis cell is now the bare id, and a Catalog History entry was
added); the YAML is regenerated.

## D3: the owner question and its siblings

### Cause

One CSV row, authored for repositories (`repository_health + chaoss_metrics`,
mechanism `Git Statistics`, rationale about `project_commits`), is stamped into
every resource type. The generator's `_restrict_answering_to_type` then
downgraded it to `kind: gap` with the repo note appended, for the four
non-repo types.

### Fix and the schema decision

The ruling forbids splitting the row (the writer assumes one row per question
text) and leaves the shape to the implementer. The shape chosen:
`PER_TYPE_ANSWERING_OVERRIDES` in `scripts/csv_to_question_catalog_yaml.py`, keyed
by question text then resource type, using the CSV's own column names. The CSV
row is untouched and stays the repository's answer. The override is parsed by
the same `_parse_answering` (so the gap guard and check-ref validation still
apply) and applied after the automatic downgrade. `tests/test_db_question_wording.py`
fails if an override names a question the CSV lacks or a type the row does not
apply to. The cost: the per-type answer lives in the script, not the CSV, so
someone editing only the CSV will not see it.

`facts.py`: for kind `mixed` or `partial` with no analysis ids, the envelope's
`blocked_reason` used to be the generic "declares no analysis that answers it,
so there is nothing to read" and dropped the note. It is now "No single analysis
answers this: part of it is not an analysis result." followed by the note, as
`gap` and `human` already do. Without it the new database wording never reaches
the screen. `kind: analysis` with no ids keeps the old sentence.

### Rows changed (before / after, generated YAML)

All rows are in `resource_explorer/configdata/question_catalog.yaml`.

**"Who owns this resource (accountable owner), and who administers it?"**

| type | before | after |
|---|---|---|
| database | kind `gap`; note "GAP: repository_health + chaoss_metrics (contributor identities and concentration) -- repository_health + chaoss_metrics are not a real analysis for database resources (absent from analysis_catalog.yaml's database_analyses section)."; mechanism `Git Statistics`; rationale about `project_commits` | kind `mixed`; analysis_ids `[]`; note "MIXED: two halves, and neither is an analysis result on its own. Who administers the database is measured: a survey reads the owner role Postgres records for it (pg_database.datdba, the database_owner fact shown in the Context tab). Who is accountable for it is human-supplied: it is recorded in Enrichment, not read from any survey."; mechanism `Database Catalog Query + Human-Supplied`; rationale and history reworded for databases |
| filesystem, dataset, model | same repo `gap` text with the type name | kind `human`; note "Human-supplied: who owns it and who administers it are recorded in Enrichment; no survey reads either for a <type>."; mechanism `Human-Supplied`; rationale and history reworded |
| repo | unchanged | unchanged (`analysis`, `repository_health` + `chaoss_metrics`, `Git Statistics`) |

**"What explicit license does this resource use, and are there non-standard or copyleft terms?"**
(the ruling says licence is "the same case" and gets the same ruling; it showed
the same defect, "license_classification is not a real analysis for database
resources" and a GitHub API rationale)

| type | before | after |
|---|---|---|
| database, filesystem, dataset, model | kind `gap`, repo note, mechanism `Code Analysis`, GitHub rationale | kind `human`; note "Human-supplied: the license is recorded in Enrichment; no survey reads it for a <type>."; mechanism `Human-Supplied`; rationale and history reworded |
| repo | unchanged | unchanged |

**"Which tables would a consumer start with?"** (database): see D2 above.

The Context text a person now reads on a database for the owner question: "No
single analysis answers this: part of it is not an analysis result. (MIXED: two
halves ... pg_database.datdba ... Enrichment ...)", with no "No mechanism
exists" and no repo vocabulary.

### Tests that pin the old text

No harness test and no existing pytest pinned the owner or license wording.
Existing tests changed only for the new check: `test_annotation_check_names.py`
(`KNOWN_EXCLUSIVE` gains `db_hub_tables`), `test_db_fs_analysis_last_activity.py`
(the `db_derived` id set gains it), `test_db_fs_results_and_questions.py` (the
results map count 19 to 20).

### New tests

- `tests/test_db_hub_tables.py` (30): declaration, the fixture database's
  ranking and per-table numbers, every NULL case above, and the question
  answering through `FactLayer` and through the `answer_question` route with
  the check's provenance.
- `tests/test_db_question_wording.py` (20): the owner and license rows per type,
  the repo rows unchanged, the facts text for a database, a scan of every
  non-retired database row for repo-only vocabulary, and the override anchoring.

The scan uses the 39 analysis ids that are repo-only in `analysis_catalog.yaml`
plus `GitHub`, `Git Statistics` and `project_commits`, over each row's note,
mechanism and rationale. It found four more repo words on database rows that
this slice did not touch (below), which the test pins as `KNOWN_LEFTOVERS` so
the set cannot grow, and fails when one is fixed so the list gets trimmed.

## Test runs

All with `PGVECTOR_PORT=1` and `REGISTRY_DATABASE_URL` pointing at a temp SQLite
file. Pytest from the package dir over every test file mentioning
`question_catalog`, `db_derived`, `analysis_catalog`, `DB_DERIVED` or `FactLayer`,
the two new files, `tests/test_next_*.py`, `tests/test_tailwind*.py` and
`tests/test_static_js_syntax.py`: 2168 passed, 146 skipped, 0 failed. Harness
on Node 20.11.0 (`node --test test-harness/*.test.mjs`): 261 of 261. Red on
`main`: `test_db_hub_tables.py` fails at import (`derive_hub_tables` does not
exist) and 15 of 20 tests in `test_db_question_wording.py` fail; the other 5 are
the known negatives (repo rows unchanged, CSV row unsplit).

## Not verified, and left open

- Nothing was run against a live database, Egeria, or the shared Postgres. The
  ranking was exercised on fixture rows in a temp SQLite registry only. How the
  ranking reads on a real database (for example `coco_pharma` or AdventureWorks)
  is not seen.
- The screen itself was not looked at in a browser. The text was verified at the
  `FactLayer` and `answer_question` route level and the harness (261 of 261
  on Node 20.11.0) does not pin it.
- The license `human` ruling is my reading of "licence is the same case, gets
  the same ruling". The ruling's table names ownership only. There is no
  measured half for a database license (the inventory lists it as unresolved),
  so I chose `human`; if the project owner wants something else, it is one
  override entry.
- That the Enrichment form actually records a license is taken from the ruling
  (`curate_plan.py` reads `enrichment["licence"]`); I did not open the form.
- Repo vocabulary still shown on database rows, not touched: "What is this
  resource, and what is it for?" and "Under what license or agreement may this
  resource be used?" (both rationale text about GitHub), "Does it fit into our
  security infrastructure?" (`security_scan`), "Does it fit into our governance
  frameworks?" (`egeria_publish`). The "Is this database documented" gap note
  also talks about a "readable repo analysis id" as a substring; it is only in
  the note, and a `gap` shows only its first 200 characters.
- Not done, and not part of this slice: the ruling's admin half (`relowner` read
  from `pg_class`, `datdba` kept at registration) and a results reader for the
  `database_owner` fact as an analysis-catalog id. The `mixed` row therefore
  claims no analysis id.
- Sibling D1 conflict surface: this branch also edits
  `DATABASE_ANALYSIS_RESULTS_MAP` (one entry) and the count assertion in
  `test_db_fs_results_and_questions.py`; see the merge-tree result in the report.
