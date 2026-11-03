# Database/filesystem survey question coverage — audit and design input

**For:** a future design pass on closing the database/filesystem parity gap with repos.
**Date:** 2026-09-23 · **Read against:** `origin/main` at `85f20cb1`.
**Scope:** `database_questions` and `filesystem_questions` in
`resource_explorer/configdata/question_catalog.yaml`, cross-checked against
`analysis_catalog.yaml` and the surveyor code that backs it. Not a feature
build — no new analyses or surveyors were written for this audit; the only
code change is 11 corrected CSV rows (see §1).

---

## Summary — read this first

| Resource type | Total | Analysis/mixed/partial | Direct | Human | Gap | Gap % |
|---|---:|---:|---:|---:|---:|---:|
| repo | 57 | 31 | 11 | 6 | 8 | 14% |
| **database (was)** | 55 | 7 | 11 | 6 | **31** | **56%** |
| **database (corrected)** | 55 | **18** | 11 | 6 | **20** | **36%** |
| filesystem | 32 | 2 | 11 | 6 | 13 | 41% (unchanged — verified, not drift) |

**Eleven database rows were false gaps** — the catalog said "no mechanism
exists" for nine analyses (`db_activity_signals`, `db_resilience`,
`db_classification`, `db_relationship_graph`, `grain_determination`,
`db_fingerprint`, `schema_conventions`, `db_external_dependencies`,
`db_change_rates`) that are built, registered in `analysis_catalog.yaml`
with `action: survey`, and wired to a real results reader in
`survey_definition_adapter.py`'s `DATABASE_ANALYSIS_RESULTS_MAP`. Fixed in
this audit — see §1. **Filesystem's 13 gaps are not catalog drift**: every
one was checked against `analysis_catalog.yaml`'s single `filesystem_analyses`
entry (`filesystem_inventory`) and none of them is answerable by it.

**The three biggest open questions for a design pass**, detailed below:

1. **Two more database analyses exist but still can't answer their question
   as far as the UI is concerned** — `reference_data_match` and
   `nested_column_profile` are built and registered, but have no results
   reader wired (their output only ever becomes an Egeria annotation).
   Left as `gap`, not fixed, because "built and registered" isn't the same
   bar as "the answer-lookup path can show something." A precedent for
   fixing this the other way (`data_class_match`) already shipped — worth
   revisiting for consistency. See §2.4.
2. **Filesystem's 1-analysis catalog is genuinely unbuilt, not minimal by
   design.** `docs/multi-resource-questions-design.md` §6 names roughly a
   dozen filesystem-specific analyses as `(new)` — none of them exist yet.
   See §3.
3. **A handful of "gap" questions are repo-shaped concepts wearing a
   database/filesystem costume** — "who owns this" via contributor
   concentration is the clearest example. These may be candidates for
   *retirement* for non-repo types rather than ever being answered. See §2.1
   and §4.

---

## 1. What was corrected, and how

Every `kind: gap` row in `database_questions` and `filesystem_questions` was
checked against `analysis_catalog.yaml` and the surveyor/adapter code that
implements it, using this bar for "actually implemented" (the same one the
"Who owns this resource" fix in PR #226/#227 used):

- a real `analysis_catalog.yaml` entry, `resource_types` including the type
  in question, `action: survey` (not `publish`-only, and not absent), **and**
- a real results-reader function wired into the relevant
  `*_ANALYSIS_RESULTS_MAP` or adapter — not merely named in a docstring or
  design-doc comment.

### 1.1 False gaps — fixed (9 analyses, 11 rows)

| Question(s) | Analysis | Where the reader lives |
|---|---|---|
| Is this database alive… / …maintained and healthy… | `db_activity_signals` | `_operations_section_reader("activity_signals")`, `database_surveyor.py` ~L979 |
| …primary or a replica… / …resilient… | `db_resilience` | `_operations_section_reader("resilience")`, `database_surveyor.py` ~L1066 |
| What kind of database is this… | `db_classification` | `_db_derived_field_reader("db_classification")`, `db_derived.py` |
| Is there a data model here… | `db_relationship_graph` | `_db_derived_field_reader("db_relationship_graph")`, `db_derived.py` |
| What is the grain of each table… (database side only) | `grain_determination` | `_db_derived_field_reader("grain_determination")`, `db_derived.py` |
| Does this look like a copy or subset… | `db_fingerprint` | `_db_derived_field_reader("db_fingerprint")`, `db_derived.py` |
| Which tables have no primary key… | `schema_conventions` | `_db_derived_field_reader("schema_conventions")`, `db_derived.py` |
| What does this database depend on outside itself… | `db_external_dependencies` | `_operations_section_reader("external_dependencies")`, `database_surveyor.py` ~L1158 |
| How is this database changing… | `db_change_rates` | `_db_derived_field_reader("db_change_rates")`, `db_derived.py` |

Fixed by rewriting each CSV row's `Answering Analysis` cell from
`GAP: <id> (proposed) — <prose>` to the pure `<id> (<parenthetical>)` form
the generator (`csv_to_question_catalog_yaml.py`'s `_is_pure_analysis_list`)
recognizes as `kind: analysis`, then regenerating
`resource_explorer/configdata/question_catalog.yaml` with the real
generator script. No hand-edit of the YAML.

**Why this didn't need a filesystem-side correction too**, even though two
of these rows (`grain_determination`, and separately `reference_data_match`
below) are shared `database;filesystem` rows: `analysis_catalog.yaml`
registers both only under `resource_types: ["database"]`. The generator's
own per-type restriction (`_restrict_answering_to_type`, added 2026-09-20)
already drops an id that isn't valid for a given type and re-derives `kind`
accordingly — so the filesystem side of those rows was already correctly
`gap` before this fix and stayed that way after it. Verified directly
against `filesystem_analyses` (one entry, `filesystem_inventory`), not just
trusted from the generator's behavior.

Result: `database_questions` gap count 31 → 20; `analysis`-kind count
(including the two already-`mixed`/`partial` rows) rose to 18. Confirmed by
regenerating and reading the YAML, not just by counting the diff.

### 1.2 Not fixed, logged instead (see §2)

Two more built-and-registered analyses were found but are **not** classified
as false gaps here, because they fail the second half of the bar above —
`reference_data_match` and `nested_column_profile` have no results reader in
`DATABASE_ANALYSIS_RESULTS_MAP`. `survey_definition_adapter.py` documents
this explicitly (a comment block above `_db_derived_field_reader`): their
verdicts are real (`column_matching.py`, `nested_columns_step.py`) but only
ever turned into Egeria annotations, because there is no local detail table
for per-column match results and `upsert_finding()` requires a registered
repo `Project`. They stay `results=None` — "no results view yet," the same
disposition as repo's own `repository_health`. See §2.4 for why this is
still worth a design decision rather than a quiet leave-alone.

### 1.3 Verification

```
uv run pytest tests/ -k question_catalog -q   → 62 passed
uv run pytest tests/ -q                        → full suite (see PR for exact count;
                                                   only the known pre-existing flake,
                                                   test_egeria_live_smoke.py::
                                                   TestTheByNameFallbackWorks::
                                                   test_a_cataloged_database_is_findable_by_name,
                                                   fails)
```

---

## 2. Inventory of remaining true/partial gaps

Grouped by what a design pass would actually need to decide, not by
resource type. Each entry names the question, its perspectives, what would
need to exist, and a rough size.

### 2.1 Retirement candidates — repo-shaped concepts, no db/fs equivalent

| Question | Perspectives | Why it may not translate |
|---|---|---|
| Who owns this resource (accountable owner/administrator)? | Financial, Governance, Steward, Data Owner | The named analysis (`repository_health` + `chaoss_metrics`, contributor identities and concentration) reads **git commit history**. A database or filesystem has no commit graph — "who commits most" has no database/filesystem analogue. Ownership for these types is either an Egeria `Ownership`/`ProjectCharter` classification (Enrichment, human-supplied) or nothing. |

This is exactly the case the task background flagged as a possible-retire
shape: *contributor concentration* is a repo-native concept. A design pass
should decide whether this question is (a) retired for database/filesystem
in favor of the Enrichment-supplied ownership field that already exists, or
(b) kept as a permanent, honest `gap` because the question itself ("who is
accountable") is still meaningful even without a machine-computable answer.
Recommend (a) — the current `note` names an analysis that structurally
cannot apply, which reads worse than an honest "human-supplied only" `kind`.

### 2.2 Needs Egeria-native surveying or a new Egeria element, not a local analysis

| Question | Perspectives | What would need to exist |
|---|---|---|
| Can Egeria's survey engine reach this resource, or must RE survey it locally? | Admin, Architecture | A `CHECK_ASSET`-only engine action plus the cost comparison `step_cost_observer` already collects. Cross-type (also gapped for repo — wait, repo lists this as its own `discovery` item; here it's shared). Egeria-side build, not RE-side. |
| Where is this resource physically located / jurisdiction / residency? | Governance, Privacy | Nothing populates `DataScope` from a survey today; needs endpoint geolocation plus the Enrichment section that captures explicit jurisdiction. |
| What is the scope of the data in time (collection period, validity, coverage)? | Governance, Data Expert | Same `DataScope` element, populated from date-range extraction over column profiling that is itself unbuilt (ties to §2.3's `column_profile`). |
| Are there restrictions beyond the licence (zone, classification, non-standard terms)? | Privacy, Governance, Security | Zone/classification exist in Egeria but aren't read back into the question layer; non-standard-terms interpretation needs an agent step over the licence text. |
| Is there a defined need or market for this resource (subscriptions, requests, feedback)? | Financial, Consumer | `DigitalSubscription` isn't read; RFAs aren't searchable by the kind of data they request. |
| Is this resource ready to be offered as a data product? | Data Owner, Governance | A readiness composite over several of the above — genuinely blocked on them, not just unbuilt itself. |

These six are the same six that top both the database and filesystem gap
lists (cross-type, `Resource Types: *` or `database;filesystem;dataset`
rows) — none of them is database- or filesystem-specific work; all six sit
on Egeria-side elements (`DataScope`, `DigitalSubscription`,
`DigitalProduct`) that don't yet have a convenient write/read path from RE,
per the pattern already documented for Automate's `NotificationType` gap
(`docs/automate-notification-manager-pyegeria-spec.md`). A design pass
covering database/filesystem parity will hit these regardless of resource
type — they belong in whatever tracks the DataScope/DigitalProduct work,
not as a database- or filesystem-specific backlog item.

### 2.3 Needs a genuinely new local analysis — database

| Question | Perspectives | What would need to exist | Size |
|---|---|---|---|
| Is this database documented (comment coverage)? | Steward, Data Expert | A new comment-coverage analysis reading `pg_description` across tables/columns (design §5.2 proposes it; deliberately not named to avoid the stale-gap guard matching a substring of a real id — see the CSV note on this row). | Small — the read (`obj_description()`/`col_description()`, already used elsewhere per CLAUDE.md rule 13) is simple; the new part is just aggregating and publishing it as its own analysis. |
| What engine/version, extensions, is the version still supported? | Admin, Architecture | `db_server_profile` (proposed, server-level) — reads `pg_settings`/`pg_extension` and a version-support-window table. No analysis reads this today. | Small–medium. Server-level (not per-database) is a new `target_shape`, worth checking against the existing shape vocabulary before building. |
| Which tables would a consumer start with? | Consumer | `db_hub_tables` (proposed) — every input (FK graph, row counts, access frequency) already exists via `db_relationship_graph`/`db_activity_signals`; nothing combines them into a ranking. | Small — a combiner over already-derived fields, same shape as `db_derived.py`'s other combiners. |
| Which columns hold semi-structured data, and how much of the table? | Data Expert, App/AI Builder | `schema_inventory` records column types but derives no semi-structured-column check from them — a small derivation step, not a new fetch. | Small. |
| What do the columns actually contain (nulls, distinct counts, common values, ranges)? | Data Expert | A basic `column_profile` analysis reading `pg_stats` directly. Distinct from `reference_data_match`/`nested_column_profile`, which consume this kind of data but don't expose it as its own question-answering analysis. | Small–medium — `pg_stats` reads already happen as an input to other analyses (see `column_profile_step.py`); this is packaging them as their own answer, not new acquisition. |
| Which glossary terms do these columns probably mean? | Data Expert, Consumer | `semantic_suggestions` (proposed) — `SemanticAnnotation` exists as an Egeria type; nothing produces one. Likely needs an embedding/LLM step over column names + a sample, closer in shape to `data_class_match` than to a pure catalog read. | Medium. |

### 2.4 Built but not wired to a results reader — a plumbing gap, not a missing analysis

| Question(s) | Analysis | What's missing |
|---|---|---|
| Which low-cardinality columns conform to a known reference-data set? / How well-governed is this database's reference data? | `reference_data_match` | A results reader in `DATABASE_ANALYSIS_RESULTS_MAP`, plus (per `survey_definition_adapter.py`'s own comment) a local table to read per-column match results from — `upsert_finding()` currently requires a registered repo `Project`, which a database survey doesn't have. Logged already in `docs/Backlog.md` ("Database per-column match results have no local store"). |
| What is inside the JSON/JSONB/XML columns? | `nested_column_profile` | Same shape of gap as above. |

**Worth a design decision, not just a backlog note:** `data_class_match`
(the third analysis in this same family) was already reclassified from
`gap` to `analysis` in an earlier PR despite having the identical
`results=None` disposition. That's an inconsistency across three siblings
built the same way, for the same reason, at the same time — not a
correctness bug (the UI's "no results view yet" disposition for
`data_class_match` is honest, matching repo's `repository_health`
precedent), but a design pass should pick one bar and apply it to all
three: either all three surface as `analysis` (accepting "ran, no stored
view" as good enough to leave `gap` behind), or `data_class_match` should be
downgraded to match its two siblings. This audit did not resolve that
inconsistency in either direction — flagging it is deliberate, not an
oversight.

### 2.5 Composite/combiner measures — building blocks now exist, no combiner does

Two of the eleven fixes in §1.1 close half of what these three questions
need; the other half (a scoring/combining function) still doesn't exist:

| Question | Building blocks now available | Missing |
|---|---|---|
| How complete and consistent is this database's documentation? | `schema_conventions` (fixed §1.1) | The comment-coverage analysis from §2.3 — still fully unbuilt. |
| How well-modelled is this database (keys, constraints, FK coverage, orphan tables)? | `schema_conventions` + `grain_determination` (both fixed §1.1) | A combiner that turns two independent measures into one modelling score. Small — same shape as `db_hub_tables` above. |
| How well-governed is this database's reference data (bound share)? | `reference_data_match` (built, no reader — §2.4) | An aggregate "what fraction of eligible columns are bound" measure over `reference_data_match`'s per-column output — blocked on §2.4 first. |

### 2.6 Bigger, cross-type design questions

- **Similarity search** ("what are similar resources, and how does this
  differ?") — needs a pgvector embedding comparison generalized beyond
  repos. Not small: repos embed on source text; a database/filesystem
  equivalent embedding space (schema shape? column names? file-type
  histogram?) is itself a design question, not an implementation detail.
- **Licence classification** — `license_classification` exists for repos
  (reads `LICENSE` files) and has no database/filesystem equivalent
  (databases and filesystems don't carry a `LICENSE` file convention).
  Whether this is answerable at all for these types — versus something only
  Enrichment can supply — is worth settling before building anything.

---

## 3. The filesystem analysis-catalog gap — genuinely unbuilt, not minimal by design

`filesystem_analyses` in `analysis_catalog.yaml` has exactly one entry,
`filesystem_inventory` (file walk, format/size/timestamp classification,
tabular data-file schema profiling — added 2026-09-22 along with the first
two filesystem questions that actually cite it by name, per the CSV's
`Catalog History` column). Against that, `docs/multi-resource-questions-
design.md` §6 lays out roughly a dozen filesystem-specific analyses,
explicitly marked `(new)`:

`file_kind_breakdown`, `filesystem_classification`, `path_conventions`,
`descriptor_detection`, `file_fingerprint`, `hub_files`, a filesystem
variant of `data_file_profiling`, `nested_schema_profile`,
`schema_consistency`, `fs_change_rates` — **none of these exist in
`analysis_catalog.yaml` today.**

This is not "filesystems are a simpler resource so fewer analyses are
needed" — the design doc's own §6.1–6.4 tables are the same shape and
comparable length to §5's database tables. It reads as **the filesystem
side of the design simply hasn't been built yet**, one analysis
(`filesystem_inventory`, itself covering what would otherwise be three or
four separate Scouting-tier rows: structure, kind breakdown, tabular-schema
detection) having absorbed the cheapest, most obviously first slice. The
remaining dozen are real, scoped, and — per the design doc's own effort
signals (mostly `Analysis` mechanism, cost class C, no new fetch beyond
what a file walk or bounded content read already does) — mostly comparable
in size to the database analyses fixed in §1.1, not a fundamentally harder
problem. A design pass allocating build effort should treat filesystem as
"database, roughly one build-cycle behind," not as a separately-scoped
smaller problem.

One caveat: three of filesystem's 13 gaps (`grain_determination`,
`data_class_match`, `reference_data_match`) are **shared** analyses already
built for database but not yet registered for filesystem in
`analysis_catalog.yaml`. Extending an existing analysis's `resource_types`
list to include `filesystem` (once someone confirms the underlying reads
generalize — e.g. `grain_determination`'s primary-key logic needs a
filesystem-appropriate substitute for "declared primary key") may be
cheaper than building filesystem's dozen `(new)` analyses from scratch, and
would close 3 of the 13 filesystem gaps without new domain logic if the
generalization holds.

---

## 4. Architectural observations

**Grain- and lineage-shaped questions are disproportionately gapped across
both types**, more than any other category. `grain_determination`, the
generic column-content questions (`column_profile`, `nested_column_profile`
reader gap), and the composite "how well-modelled" questions all sit in
this family. For database this is now partly closed (§1.1), but the
composite layer (§2.5) and the results-reader plumbing (§2.4) both still sit
on top of it. For filesystem, essentially none of it exists (§3). This
looks like a systematic next investment rather than one-off additions: a
"grain and structural quality" pass across both types, closing the
composite/plumbing gaps in §2.4–2.5 for database and porting the
`db_derived.py` combiner pattern to a filesystem equivalent, would move
more questions from `gap` to answered than any single new analysis would.

**The cross-type Egeria-element questions (§2.2) are a second cluster worth
naming as one investment, not six.** All six sit on `DataScope`,
`DigitalSubscription`, or `DigitalProduct` — Egeria elements RE doesn't yet
have a write/read convenience path for, the same shape of gap already
documented for Automate's `NotificationType`
(`docs/automate-notification-manager-pyegeria-spec.md`). A single design
pass scoping "RE's convenience API surface over these three Egeria element
families" would address all six at once, for both database and filesystem
(and likely dataset/model, going forward), rather than being rediscovered
per resource type as each one's question catalog is audited.

**The false-gap pattern itself (§1) is a process gap, not a one-time
mistake.** `resource_questions.csv` rows were authored ahead of the
analyses that would answer them (`GAP: <id> (proposed) …`), and — per this
repo's stream-ownership convention — the implementation slices that later
built those analyses were correctly forbidden from editing the CSV, so
nobody's individual mistake produced the drift; the review step that would
have caught it (checking a newly-shipped analysis against its own
originating `GAP:` row) doesn't exist as a standing step, only as an
occasional audit like this one. A guard test that fails when a `gap`
question names an id present in `analysis_catalog.yaml` would catch this
mechanically going forward — worth checking whether one has already landed
before adding a second, since a review pass was independently working the
same seam concurrently with this audit.

---

## 5. What this audit did not do

- No new analyses, surveyors, or combiner functions were built — out of
  scope per the task brief. §2 and §3 are input to a future design/build
  pass, not a completed one.
- Dataset and model question catalogs were not audited — the task scope was
  database and filesystem only. `dataset_questions` has at least one
  `GAP: data_class_match` row that a companion review flagged as likely
  stale for the same reason as this audit's database fixes; worth folding
  into whoever's stream owns dataset/model coverage next.
- The `data_class_match`/`reference_data_match`/`nested_column_profile`
  results-reader inconsistency (§2.4) was surfaced, not resolved — it needs
  a project-owner or design-pass ruling on which bar to hold all three to.
