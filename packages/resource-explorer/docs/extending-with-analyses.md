# Reference: what a library step declares, and what Resource Explorer does with it

> **Vocabulary note (2026-11-07):** the absence word `measured_nothing` was renamed `nothing_found` (trellis-analytics 0.3); this document now uses the new word. Results stored with the old word are still read.


**Status:** live reference. Written 2026-10-28, brought up to date 2026-10-30 against `origin/main` at `aacdfaff`.

**Want to add a step? Start with the tutorial:**
[Your first analysis: adding a measurement to Resource Explorer](../../trellis-analytics/docs/TUTORIAL-ADD-AN-ANALYTIC-STEP-2026-10-30.md).
It takes about an hour. It walks you through one working step, from a copy of an example to a result on screen.
This page is **not** a second tutorial. It is the reference you come back to for the details: every field a
step can declare, what a curator sees, and how Chat uses a step's results.

A **library step** is a small Python package that adds a measurement to Resource Explorer (RE) without
changing RE. Changing RE's own built-in analyses is a different job, covered in
[extending-resource-explorer.md](extending-resource-explorer.md). The library's own reference for
authors is [the egeria-trellis-analytics README](https://github.com/odpi/egeria-trellis-analytics#readme). Where this page and that
README differ, this page was checked against the code. How steps are installed, versioned and retired is in
[`DESIGN-ANALYTIC-LIFECYCLE-2026-10-30.md`](design-notes/DESIGN-ANALYTIC-LIFECYCLE-2026-10-30.md).

**The same states, under different names.** The tutorial teaches five states in plain words. This page
uses the names the code uses.

| In the tutorial | In the code |
|---|---|
| measured | `measured` |
| nothing found | `nothing_found` |
| not measured, or not permitted | `not_established` (the `reason` says which) |
| not applicable | `not_applicable` |

---

## What you are building

A **library step** is a function that reads what RE has already stored about a resource and returns
one result per thing it measured, such as one per table. It ships as its own Python package. The
package's **manifest** says what the step reads, what it measures, and, in its **declaration**, what
each result means: its unit, when it was measured, what an absence means, and how confident it is.

**Installing the package registers the step.** There is no registration call and no config file to
edit. RE finds the step through a Python entry point.

What RE does with a declared step today:

| RE surface | What your step gets | where it stands |
|---|---|---|
| Analysis card on a database | runs through the run queue; results stored with their envelope | on main |
| Results in Understanding | shown through the generic annotation reader | on main |
| Curate's publish table | each declared measure is a row a curator can **Publish** or **Keep in RE** | on main (PR #886) |
| "already in Egeria via …", native SchemaAnalysisAnnotation | the publish table checks Egeria before it offers Publish | on main |
| Chat compiles your results into its context | answers to the questions your manifest names | on main |
| Egeria starts your step (engine host) | an Egeria governance action runs it in RE | on main, after the owner runs the generated declaration |

**Limits on main today:**

- **Steps attach to databases only.** File systems and repositories are not offered yet.
- **A step reads, and does nothing else.** It reads through typed, read-only methods. It gets no SQL,
  no password and no Egeria identity, and RE does not yet open a connection to the database for it.
- **Declare a `result_kind`, but expect no special rendering from it yet.** Understanding has no
  renderer per kind; results show through the generic reader.
- **This is not a sandbox.** The step runs inside RE. Install only steps you would trust to run as
  RE itself.

---

## 1. The package

To build one, follow the tutorial: it copies
[`examples/steps/database_staleness/`](../../trellis-analytics/examples/steps/database_staleness/)
and renames it. A step package has three files that matter:

```
my_step/
  pyproject.toml
  src/my_step/__init__.py     # __version__ = "0.1.0"
  src/my_step/manifest.py     # MANIFEST = StepManifest(...)
  src/my_step/step.py         # def run(inputs) -> list[dict]
```

### The entry point: how RE finds the step

The entry-point group is **`pyegeria.analytics`**. Installing the package registers the step: Resource Explorer and
the Egeria Portal both read this group, so there is nothing else to configure. A report spec names the step by its
registered name (the manifest's `name`), never by its Python path. Steps built before 2026-10-31 declare
`trellis.analytics`, which is still read as an alias for now; write `pyegeria.analytics` in anything new.

Its value names the manifest object:

```toml
[project]
name = "my-step"
dynamic = ["version"]
dependencies = ["trellis-analytics>=0.1.0"]

# Installation is registration: Resource Explorer finds the step through this entry point.
[project.entry-points."pyegeria.analytics"]
my_step = "my_step.manifest:MANIFEST"
```

The entry point's value may be a manifest, or a callable that returns one manifest or a list of
them. **A broken entry point is an error, never skipped:** discovery raises, naming the entry point.
RE logs `user steps not loaded for database: …` and starts with no user steps, so one bad package
cannot leave the others half-loaded.

### The function

```python
def run(inputs) -> list[dict]:
    ...
```

`inputs` carries only what your manifest's `inputs` list asks for. Every other field is `None`, and
there is no field that could carry a password:

| input | what it is |
|---|---|
| `ods` | a typed, read-only reader of what RE stored: `row_count_snapshots`, `commit_proofs`, `catalogue_scope_events`, `resource_scope_events`, `outbox_status_counts`. To read something else, ask for a new method in RE |
| `source_report` | in an Egeria process, the survey report the previous step wrote, as plain data (`{guid, at, annotations}`) |
| `resource` | the resource being surveyed |
| `connection` | declared but **not provided by RE yet**. RE refuses a step that asks for it |
| `slug`, `resource_type`, `parameters` | always set. `parameters` is a copy of the manifest's `parameters` |

Return **one dict per scope**: one per table, or one for the whole database when there is nothing
finer to say:

| key | required | meaning |
|---|---|---|
| `scope` | yes | what the result is about, for example `"public.orders"` |
| `state` | yes | `measured`, `nothing_found`, `not_established` or `not_applicable` |
| `reason` | for `not_established` and `not_applicable` | why there is no measurement |
| `value` | no | a dict of the measured values. Its keys are what the declaration's `value_field`, `as_of` and extent `fields` name |
| `summary` | no | one sentence. RE writes one from the state and reason if you leave it out |
| `measure` | no | which declared measure this row reports. Leave it out for the step's own measure |
| `candidate_classifications` | no | at most one, and only for a measure published as `ClassificationAnnotation` |

**The four states are the point.** "We looked and found nothing" (`nothing_found`) must never read
like "we could not look" (`not_established`). Every path through your code returns a result,
including the path where there is no data yet.

**Any refused result drops the whole run.** That covers an unknown state, a missing scope, a
`not_established` with no reason, and an undeclared measure. A partial store would read as the whole
answer, so RE stores nothing from that run.

---

## 2. The manifest and its declaration

`StepManifest` (in `trellis_analytics.manifest`) describes the step. Its `declaration` (in
`trellis_analytics.declaration`) describes the results. **A misspelt field is an error, not
ignored:** every declaration model forbids extra keys.

### StepManifest: the fields you will set

| field | notes |
|---|---|
| `name` | the step key. It is also the name of the step's primary measure |
| `function` | dotted path to `run`, for example `"my_step.step.run"`. Resolved at each run, so the installed version is the one that runs. Only the manifest holds this path; everything else, report specs included, names the step by `name` |
| `version` | stamped on every result. Read it from `__version__` |
| `source` | `"ods"`, `"egeria"` or `"resource"`: where the data comes from |
| `resource_types` | `["database"]`. RE accepts nothing else yet |
| `inputs` | a subset of `ods`, `source_report`, `resource`, `connection`. Default `["ods"]` |
| `credential_tier` | `"none"` (the default) or `"read_only"`. Anything higher is refused (owner ruling 2026-10-07) |
| `annotation_type` | the Egeria type the step writes. Default `ResourceMeasureAnnotation`. A measure's `annotation` in the declaration is the same fact: write it in one place and the other follows; write both and they must agree, or the error names both fields |
| `measures` | extra measures beyond the step's own. If you list any, include the step's own name too |
| `measure_annotation_types` | per-measure Egeria type, where it differs from `annotation_type` |
| `question_defs` | new questions the step brings, each `{id, question}` with an id under the step's own name (`my_step:Q1`); their ids join `questions` |
| `parameters` | tunable values the code reads as `inputs.parameters` |
| `display_name`, `description`, `returns` | the card's name and text. Say in the description what "not measured" means for this step |
| `stage` | RE's stage for the card, default `analysis` |
| `perspectives`, `purposes`, `family`, `tags` | how the card is filtered and grouped |
| `questions` | question **ids** from RE's question catalog (`Q-0041`), never their wording. An unknown id warns and is kept |
| `request_type` | the engine-host request type. Default `re-<name>`. It may not take a built-in step's |
| `nothing_to_derive_guard` | what an Egeria run completes with when there is nothing to derive. Use `survey-completed` when an honest absence is a complete answer |
| `declaration` | see below. A manifest without one still loads and runs, but gets no publish table and no Chat |

### The declaration

```python
Declaration(
    result_kind=...,   # measure | series | distribution | list | graph | finding | proposal | table
    measures=[MeasureDeclaration(...), ...],
    absence=AbsenceSentences(...),
    extents=[ExtentDeclaration(...)],
    confidence=ConfidenceDeclaration(...),
    scope=...,         # resource | container | member | field
)
```

`result_kind`, `measures` and `absence` are the **minimum**. **Full** also has `extents`,
`confidence` and `scope`, plus `answers`, which is filled from the manifest's `questions`.
`Declaration.missing()` names what a declaration still lacks. The example steps are required to
declare in full (`test_every_library_step_declares_in_full`). Choose `table` only when none of the
other seven kinds fits; it requires a `result_kind_note` saying why.

**Measures: a number never travels without its unit and its as-of.** Each `MeasureDeclaration`
needs four non-blank fields:

| field | meaning |
|---|---|
| `name` | the measure, as the manifest names it |
| `unit` | in words, plural where it counts: `"tables"`, `"rows per day"`, `"days"` |
| `as_of` | the result field that says when it was measured. A dotted path reaches into a nested value; `run.surveyed_at` names a field of the producing run |
| `value_field` | the result field holding the value. A trailing `[]` means "the number of items in this list" |
| `scope` | optional: `resource`, `container`, `member` or `field`. Absent means the declaration's scope. Use it for a summary in a member-scoped step (a step whose rows are per table and which also writes one value for the whole database): mark the summary `scope="resource"`, and the publish table reads it from its own record and offers Publish, while the per-table measure stays "one value per member" |
| `annotation` | optional. `AnnotationDeclaration(type=..., properties={result_field: egeria_property})` names the Egeria type this measure writes as. Leave it out and the publish table says `generic measure · no native type declared` |

A measure published as anything other than `ResourceMeasureAnnotation` must declare its
`annotation`, and the type must match what the manifest says the step writes.

**Absence: declared, never improvised.** `AbsenceSentences` needs all four sentences. They are what a
person reads in place of a value, everywhere RE shows one:

| sentence | used for the state |
|---|---|
| `not_measured` | `not_established`: RE could not measure |
| `not_permitted` | `not_established`: RE was not allowed to read it. The reason tells the two apart |
| `not_applicable` | `not_applicable` |
| `nothing_found` (written `measured_nothing` before 0.3; still read) | `nothing_found` |

**Confidence: a basis written for a person.** `ConfidenceDeclaration(form=..., method=..., basis=...)`:

- `form` is one of `measured`, `estimated`, `inferred`, `judged` or `generated`.
- `method` says in words how the value was arrived at. It is what a person reads.
- `basis` is a result field carrying a per-result basis. If you give `basis`, give `method` too:
  "a person reads the method, never a field path".
- `inferred` and `judged` need a `basis`. `estimated` and `generated` need a `basis` or a `method`.
- `measured` takes no `score` and no `calibrated`, because an exact read has none.
- `inferred` must say `calibrated`: `true` if its score is a probability, `false` if it is a rule count.
- A `generated` result is never published, so it may not declare an `annotation`.

**Confidence per measure.** A `MeasureDeclaration` may carry its own `confidence`. The declaration's own confidence must then be the **weakest** of its
measures', in the order measured, judged, estimated, inferred, generated (owner ruling 2026-10-28).

**Extents: the shapes Chat may quote, and no other.** Each `ExtentDeclaration` names a list the
step's results can be read as:

| field | meaning |
|---|---|
| `name` | the extent's name |
| `fields` | which result fields to show, in order |
| `rows` | how many rows to show at full detail |
| `order` | the order, in words, for example `"by rows changed, largest change first"` |
| `truncation` | must contain `{shown}` and `{total}`, for example `"{shown} of {total} tables shown"` |
| `token_cost` | the extent's estimated cost in a Chat context |
| `citation` | for example `"{analysis} · {scope} · as of {as_of}"` |

**`sort`.** An extent can say its order as sort keys: `sort=["rowsChanged desc"]`, each key `"<field> asc"` or `"<field> desc"` on one of its
`fields`. RE derives the words from the keys and sorts before it leaves rows out. Declare `sort` or
`order`, never both. Keep `order` for an order no key can express, such as "PUBLIC grantees first,
then by table". The owner chose this on 2026-10-28.

**Tables: rows about the members of a resource (B1, 2026-11-02).** When a result is one row per
column, file or dataset, declare `result_kind="table"`, `scope="member"` and a `TableDeclaration`.
The design is `~/.re-gate/DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02.md`.

| field | meaning |
|---|---|
| `columns` | `TableColumn(name, type, unit, absence, states, measure_ref)`. A bare string is a `text` column with no absence word yet |
| `key_fields` | the columns that make a row one member. Required at `member` and `field` scope |
| `max_rows` | how many rows are **shown**. The default and the ceiling is 1,000 (owner's cap, 2026-11-02). Every row is stored |
| `order` | the order rows are shown in, in words |
| `truncation` | must contain `{shown}` and `{total}`; it is the table's summary line |
| `summary` | the measures, declared beside the table, that say the whole of it. Required at `member` scope |

A column's `type` is one of `text`, `integer`, `number`, `boolean`, `date`, `datetime`, `bytes`,
`duration` or `state`. A `number`, `bytes` or `duration` column needs a `unit`. A `state` column
lists its `states`. Every column that is not a key needs an `absence` word, the word an empty cell
reads as: "not counted" is not "none found" is not "not permitted". `measure_ref` names the summary
measure a column is the per-member value of, so the rows and the summary agree.

**The gate is the scope.** A table declared at `member` or `field` scope is about the members of a
resource, and owes its `key_fields`, a typed column with an `absence` word for every non-key column,
its `truncation`, its `order` and its `summary`. **Decision (Architect, 2026-11-02):** for one release
a lack is reported by `Declaration.missing()` (`table.key_fields`, `table.columns.<name>.absence`,
`table.truncation`, `table.order`, `table.summary`), not refused, so a declaration written before B1
keeps loading until it converts (convert-as-touched). A table that owes nothing is a table result
(`Declaration.is_table_result()`): only a table result stores its rows, draws the table block,
publishes its summary alone and quotes its stored rows. A `resource`-scoped or unscoped table keeps
bare columns and hand-written extents.

**A table result's extent is its table.** Do not declare `extents` beside one: Chat's extent is
derived from the table (its key and columns, `max_rows`, `order`, `truncation`), priced at about 8
tokens a cell (`TOKENS_PER_CELL`), with `token_cost_per_row` beside the whole. An answer quotes about
2,000 tokens of rows per table (2,000 ÷ `token_cost_per_row`, in the declared order, never fewer than
5), then "N of M rows quoted; the table lists the rest"; the whole-table cost is never spent
(Architect, 2026-11-02). A declaration that writes an extent different
from the derived one is refused. The summary measures are computed by the step over **all** rows,
never added up from the shown slice. Only the summary goes to Egeria: no row leaves Resource Explorer.

**Reading the rows.** A step or renderer that needs the stored rows asks the library's seam,
`trellis_analytics.readers.table_rows.table_rows_reader().table_rows(run, declaration, limit)`; Resource
Explorer registers its read (`resource_explorer.table_result.RegistryTableRows`) behind it.

```python
Declaration(
    result_kind="table",
    result_kind_note="one row per column: about forty facts for each of up to thousands of columns",
    scope="member",
    table=TableDeclaration(
        key_fields=["schema", "table", "column"],
        columns=["schema", "table", "column",
                 TableColumn(name="state", type="state", absence="not profiled",
                             states=["measured", "not_measured", "not_permitted", "nothing_found"]),
                 TableColumn(name="declared_type", absence="type not recorded"),
                 TableColumn(name="null_count", type="integer", absence="not counted", measure_ref="nulls_total"),
                 TableColumn(name="distinct_estimate", type="integer", absence="not estimated")],
        order="schema, table, column as the catalog lists them",
        truncation="the first {shown} of {total} columns; every column is stored and the summary counts them all",
        summary=["columns_total", "columns_measured", "nulls_total"]),
    measures=[...],    # columns_total, columns_measured, nulls_total, each scope="resource"
    ...
)
```

**`answers`: one list of questions, not two.** For a library step, leave `answers` out. It is filled
from the manifest's `questions`, and an `answers` list that differs from `questions` is refused.
For RE's own built-in analyses, `answers` is generated from the question catalog's `analysis_ids`. That
changes nothing for a library step.

**Not built yet.** `built: false` marks an analysis that is declared but has no runner yet. Chat then says "Planned, not built yet: not measured." It is applied to three of
RE's own entries, and a library step with code has no use for it.

---

## 3. The catalog entry: merged for you, no commit in RE

RE shows an analysis card only for an analysis in its catalog. For library steps **you do not write or commit that
entry any more.** When RE reads `analysis_catalog.yaml` it also reads every installed `pyegeria.analytics` manifest and
merges it in, so installing the package is enough: the card, the run, the declaration, Chat's compile and the publish
table all see the step after RE restarts. The reasoning is in
[`DESIGN-STEP-MANIFESTS-MERGED-AT-READ-TIME.md`](design-notes/DESIGN-STEP-MANIFESTS-MERGED-AT-READ-TIME.md).

**The committed entries are a fallback, and regenerating them is optional.** The block between
`# BEGIN GENERATED user steps (database)` and `# END GENERATED user steps (database)` still holds the four example
steps, so an environment that lacks their packages still lists them and says "step not installed here: ... comes from
package P". When a package is installed its manifest replaces the committed entry, so the two cannot disagree in what
RE shows. To refresh the fallback for a step you want package-less environments to know about:

```bash
uv run --with-editable path/to/my_step python packages/resource-explorer/scripts/generate_external_step_entries.py
```

Name every library step package, one `--with-editable` each, or the block is regenerated without the ones you left out.
`--check` writes nothing and exits 1 if the block is stale. Neither generator talks to Egeria.

**The staleness test** is
`packages/trellis-analytics/tests/test_database_staleness_step.py::test_the_catalog_entries_in_analysis_catalog_yaml_are_the_generated_ones`.
It checks the committed fallback block against the generator, and each entry against its manifest. It guards the
fallback, not the card: a stale fallback no longer changes what an installed step shows.

**A question your step declares.** Name a question the catalog already holds by its id in `questions`, and Chat
routes by it with no configdata row. A **new** question goes in the manifest's `question_defs`, each with its text and an id under
your step's own prefix (`my_step:Q1`): `question_defs=[QuestionDeclaration(id="my_step:Q1", question="…")]`. RE merges it into
its question catalog while the package is installed (owner, 2026-10-29), with no central id and no configdata row; the id
is added to your `questions` for you. An id outside your prefix, or repeated, makes the manifest fail to load, with that sentence.

**On the Questions tab.** Every question your manifest names (`questions`, or `question_defs`) lists your step among the
analyses that answer it, after RE's own, in catalog order: a question RE's analysis and your step both answer shows both.
The row's line for your step is your step's own sentence, the same `state · summary` the Survey tab shows for the same
stored rows (a step with one result says it; one with a resource-scoped summary measure says that; only several plain
results are counted). "The numbers behind this" lists each stored result, and each further declared measure on its own row
with its own state, so a measure that could not be measured reads "not established" and its reason, never a zero.

---

Ways to check that RE sees the step. RE has no admin page or CLI verb that lists library steps yet.

- `python -c "from trellis_analytics.discovery import discover; print(sorted(discover()))"` lists
  every installed manifest. A broken entry point raises here, naming itself.
- Your step's card appears on a database, under the stage its manifest names. A card that fails with
  "step not installed here" means the catalog has the entry but this environment lacks the package.
- If the engine host runs, its start-up log names every installed step per host, with a missing list for each:
  `library steps · Egeria host: change_rates, hub_tables, … (none missing) · Files host: fs_activity, fs_permissions, … (none missing)`.
  Database steps are the Egeria host's; file-system steps run under the Files host. A step the engine host
  cannot publish logs `not offered to Egeria: <why>`, listing everything the step emits and which of those types block
  the offer. The types we want Egeria to support are tracked in
  [`EGERIA-ANNOTATION-TYPES-WANTED.md`](design-notes/EGERIA-ANNOTATION-TYPES-WANTED.md).
- An installed step with no catalog entry (its manifest was refused, for instance a built-in's id) logs a warning.
  It can run from a Survey Definition, but has no card.
- Once a survey has run, the step's declared measures appear as rows in Curate's publish table (§4).

---

## 4. What a curator sees: Curate's publish table

> **Two reports after a database Contents commit.** A commit publishes RE's measures into RE's own survey report (the
> step names it: *RE's measures are in report fb6d96f4*), and also starts an Egeria survey, whose engine action writes a
> separate report that can read "0 annotations" until that survey finishes. RE says both on the survey step; it does not
> change what is sent.
>
> **A derived row's dates.** A step RE derives on read (not a stored record) has no measurement time of its own: its row
> says *derived on read from the survey of <date>* beside the *as of*, never a second identical date.

On a database's **Curate → Describe and publish** tab, the table headed **Publish · declared
measures** lists every declared measure with a value. Its columns are Analysis, Measure, Value,
As of, Confidence, Scope, Writes as, Choice and State. The As of column names both dates: the date the statistic is **as of**, and, under it, when Resource Explorer **measured** it.

- **Each row has two buttons: Publish | Keep in RE.** Nothing publishes by default. A row nobody has
  chosen reads `not chosen · stays in RE`.
- **The table has no publish press of its own.** Chosen rows go with the page's one Publish, the
  commit on **Contents**. That commit carries them inside RE's own survey report, reads each one back,
  and records a proof. The State column then reads `published · read back <time>`.
- **A choice is a signed, dated record**, newest first: `will publish · <who> · <when>`. **It lasts
  across surveys.** A row chosen for Publish is sent by every later commit until someone chooses Keep
  in RE. If the value changes, the row reads `changed since published`.
- **Writes as** shows the measure's declared Egeria type, or `generic measure · no native type
  declared`. On main, RE writes `ResourceMeasureAnnotation` natively. A generic measure goes as a
  ResourceMeasureAnnotation, with its value under the measure's name and its unit, as-of, confidence
  and scope alongside.
- **A row that cannot publish says why**, and offers Keep in RE only. The reasons are:
  - no value: the declared absence sentence is shown, never a zero;
  - the value is not one number or word;
  - there is no as-of;
  - the confidence is `generated`;
  - the declared type is not one RE writes yet.
- **`proposal` results are not on this table.** They are judged on Structure with the verdict selector.

**Also on the table, from the owner's answers of 2026-10-28:**

- **No number written twice.** A measure already in Egeria shows
  `already in Egeria via Resource Explorer's survey report (the commit on Contents)`,
  `already in Egeria via the engine host · report <time> · engine action run <id>` (the id is the engine host's own run record, the one this check reads), or
  `already in Egeria via Egeria's own survey report`. Publish is disabled, and Keep in RE is still
  offered. This covers a library step the engine host has already run and published.
- **SchemaAnalysisAnnotation is written natively.** `schemaName` and `schemaType` are set when the
  declaration's `annotation.properties` maps them, and the value and its envelope go in
  `jsonProperties`. `ResourcePhysicalStatusAnnotation` stays for a later slice.
- **Choice lifetime is spelled out on the row**: `will publish · <who> · <when> · sent by every commit
  until Keep in RE`, and `changed since published · … · the next commit sends the new value`.
- **File systems** now publish too (2026-10-29), as described next.

**File systems publish their chosen measures (2026-10-29).** The owner ruled on 2026-10-28 that a file system's
measures wait for a survey report of RE's own for file systems; the Files steps built it (#905), and the wait is lifted.

- **The table is on the file system's Curate page**, with the same columns and the same Publish | Keep in RE choice per
  row (sign in to choose). It names its press: *chosen measures go inside Resource Explorer's own file-system survey
  report: the folder survey's Publish sends them, each read back by name.* The root-folder Publish still attaches the
  root folder only.
- **The press is the folder survey's Publish** (and the walk-extensions Publish). The chosen measures ride in the same
  SurveyReport as the walk's annotations and the derived steps' results, after them, so no earlier annotation's name
  moves.
- **Same words as a database.** *Writes as* is the declared type or the generic measure; the report is then read back by
  name, one proof row is left per measure, and the row says `published · read back <time>`, or `sent · not read back
  <time>` when the read did not show it. The word comes from the proof row, never from the press.
- **A measure the step could not establish** (for example `fs_activity`'s access times where the mount records none) is
  listed as `not measured this run`, with the step's own reason, and offers Keep in RE only. A secondary measure is read
  from its own stored key, so it shows its own state, never its sibling's.

---

## 5. How Chat uses the declaration

Chat compiles a step's declared results into its context:

- **Routed by question.** An analysis enters a Chat answer only if its `answers` include the question
  being asked. `answers=[]` means it runs and renders but never enters a Chat answer.
- **Built from the declaration alone.**
  - Each measure appears as `name: value unit (as of …)`.
  - A confidence line gives the form and the method.
  - Each extent shows its declared fields in their declared order, sorted by `sort`: the declared
    `rows` at full detail, three in a summary. The truncation sentence is filled in, and at full
    detail a `cite as:` line follows.
  - A field a row lacks says `(none)`. An extent with no rows in the result says so, rather than
    showing an empty list.
- **Absences stay honest.** With no result, the declared absence sentence goes into the
  instructions, so the model is told "not measured", never shown a zero. The owner chose to keep this
  as built on 2026-10-28.
- **What was left out is named.** Sections dropped for space are listed with their declared
  `token_cost`. The compile's manifest records the weakest confidence the answer rests on.

The design note records the known gaps (it was checked with a stubbed fact layer, and some
analyses have no results reader): `DECLARED-EXTENSIBILITY-SLICE2-CHAT-COMPILE.md`.

---

## 6. The worked example, and the other example steps

`packages/trellis-analytics/examples/steps/database_staleness/` is a real, installable step: rows changed per
table since the last survey. It reads only RE's stored row-count snapshots and opens no connection. The
[tutorial](../../trellis-analytics/docs/TUTORIAL-ADD-AN-ANALYTIC-STEP-2026-10-30.md) copies it and walks
through its code, its manifest and its tests, so they are not repeated here. Its manifest is the best
example of a declaration made in full: one measure with a unit and an as-of, the four absence sentences, a
written confidence method, one extent and a question named by id.

### The other example steps

| step | result kind | what it shows you |
|---|---|---|
| `statistics_freshness` | `list` | two measures, one a resource-scoped summary whose headline counts the tables that *need attention* (never analysed + stale), because a count of "stale" alone reads 0 for a database whose tables were never analysed and so reads as all current; confidence `measured` |
| `hub_tables` | `proposal` | a measure published as `ClassificationAnnotation` (`measure_annotation_types`), `candidate_classifications`, and `parameters` (`hub_min_referencing: 3`). A proposal is judged on Structure, not on the publish table |
| `change_rates` | `distribution` | rows written per day; one extent |

---

## Checklist (the tutorial has the steps in full)

1. Write the package: `pyproject.toml` with the `pyegeria.analytics` entry point, `run()`, and `MANIFEST`.
2. Make every path return a result with a state, and every absence carry a reason.
3. Declare in full: units, as-ofs, value fields, four absence sentences, confidence with a written
   method, extents, scope, and `questions` by id.
4. Test the four states against a scratch registry, never the shared one.
5. Nothing to commit in RE for the card: installed manifests merge into the catalog. Optionally run
   `generate_external_step_entries.py` to refresh the fallback entry, and the engine-host generator if Egeria
   should start the step.
6. Install the package where RE and any Prefect worker run, then restart RE.
7. Check that the card appears, run it on a database, and look at the publish table.

## See also

- [the egeria-trellis-analytics README](https://github.com/odpi/egeria-trellis-analytics#readme): the library's reference for authors
- [The tutorial](../../trellis-analytics/docs/TUTORIAL-ADD-AN-ANALYTIC-STEP-2026-10-30.md): build your first step, start to finish
- [`DESIGN-ANALYTIC-LIFECYCLE-2026-10-30.md`](design-notes/DESIGN-ANALYTIC-LIFECYCLE-2026-10-30.md): where steps live, how they are installed, versioned and retired
- [user-guide.md](user-guide.md), "The Analytic Library": what users see
- [extending-resource-explorer.md](extending-resource-explorer.md): changing RE's own built-in analyses
- Design notes, for the reasoning: `DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md`,
  `DECLARED-EXTENSIBILITY-SLICE1-PR1-GAPS.md`, `DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md`
  (indexed in [design-notes/README.md](design-notes/README.md))
