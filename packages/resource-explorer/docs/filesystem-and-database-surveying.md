# Filesystem and database surveying

**Status:** consolidated design and reference. Current as of 2026-09-02.
**Work on this is deliberately deferred until the repository path is finished** —
a direct maintainer decision, 2026-09-02. This document is the design input for
when it resumes, not an active plan.

> **This document consolidates five.** Part I is the grounding — what Egeria
> already surveys natively, and how much of RE's repo machinery does *not* carry
> over. Part II merges the database surveyor design, the Postgres exploration,
> the filesystem analytics plan, and the operator quickstart. Part III is the
> settled register.

## 1. Egeria already surveys filesystems and databases

**17 native survey action services** in the source tree:

| resource | services |
|---|---|
| **filesystem** | `FileSurveyService`, `FolderSurveyService`, `CSVSurveyService` |
| **database** | `PostgresDatabaseSurveyActionService`, `PostgresServerSurveyActionService`, plus Oracle, MSSQL, DB2 LUW and DuckDB variants (server + database for each) |
| **catalog** | `OSSUnityCatalogServerSurveyService`, `…InsideCatalogSurveyService`, `…InsideSchemaSurveyService` |
| **platform** | `OMAGServerPlatformSurveyService` |

**There is no repo survey service in that list.** That asymmetry explains the
whole shape of RE's repo work — 40 steps, its own surveyors, its own
persistence — and it does not carry over.

So the first design question for FS and DB is not "what steps do we write"
but **which surveys does each side orchestrate** — and a direct maintainer decision
(2026-09-02) is that this is not a choice between two answers:

> "Egeria may be orchestrating its own surveys for some of them — and others
> may be orchestrated by RE. Egeria does not have all the Survey Types we will
> want for FS/DB."

So the target is a **mixed model**, and the design work is drawing the line,
not picking a side:

- **Egeria-orchestrated**, where a native service already does the job —
  `FolderSurveyService`, `PostgresDatabaseSurveyActionService` and friends.
  RE triggers or consumes; it does not reimplement.
- **RE-orchestrated**, for the survey types Egeria does not have. That set is
  not yet enumerated, and enumerating it is the first concrete task: what do
  we want to know about a filesystem or a database that no native service
  produces?
- **Both, over one resource.** A database asset can carry findings from a
  native Postgres survey *and* from an RE-only survey. Which means the two
  must produce output that sits together coherently — same asset, same
  `SurveyReport` conventions, compatible annotation types. That is a
  requirement on RE's side, not a nice-to-have, and it is what makes §2 below
  load-bearing rather than tidy-up.

`docs/survey-execution.md (§7)` already frames the mechanics as four cases;
the mixed model uses several of them at once rather than selecting one.

**Blocked today either way by ISSUE-79**, not by design: a native survey
against a template-created asset fails server-side (`assetConnector` null).
That plan is ON HOLD for exactly this reason. See
`docs/egeria-integration.md (§12)`.

---

## 2. RE uses 7 annotation types; Egeria defines 18

RE's `ANNOTATION_TYPES_REGISTRY` covers seven. Egeria's type model defines
eighteen. **Twelve are unused**, and several are not obscure — they are
precise fits for work RE already does, or is about to.

**Used today:** `ClassificationAnnotation`, `ResourceMeasureAnnotation`,
`SchemaAnalysisAnnotation`, `DataClassAnnotation`, `QualityAnnotation`,
`RelationshipAdviceAnnotation`, `RequestForAction`.

**Unused, and directly relevant:**

| type | Egeria's own description | where it fits |
|---|---|---|
| **`CodeAnalysisAnnotation`** | *"A set of properties describing the content of the code in a code repository like GitHub."* | **exactly what RE does for repos today**, published as ResourceMeasure/SchemaAnalysis instead |
| **`ContributorAnalysisAnnotation`** | (0680 code-analysis model) | RE's CHAOSS metrics, community support, contributor concentration |
| **`ResourceProfileAnnotation`** | *"A collection of properties that characterize an aspect of a resource."* | data profiling — RE has its own shape for this |
| **`ResourceProfileLogAnnotation`** | — | profile results too large to inline |
| **`ResourcePhysicalStatusAnnotation`** | — | **filesystem**: size, timestamps, physical state |
| **`DataFieldAnnotation`** | *"A collection of properties about a data field, or number of data fields, in an Asset."* | **database**: column-level findings |
| **`DataGrainAnnotation`** | — | **database**: granularity of a table |
| **`FingerprintAnnotation`** | *"An annotation capturing digital resource fingerprint information."* | duplicate detection across files/tables |
| **`SemanticAnnotation`** | *"A recommendation of likely mappings to Glossary Terms for all or part of an Asset."* | glossary linkage — RE has no equivalent |

Also unused: `AssociatedAnnotation`, `ReportedAnnotation`, `DataFieldAnnotation`'s
siblings, and the base `Annotation`.

### Why this matters more than it looks

**`CodeAnalysisAnnotation` is the tell.** Egeria has a typed annotation whose
own description is "the content of the code in a code repository like GitHub",
and RE — which analyses code in GitHub repositories — does not use it. RE's
code findings are published as `ResourceMeasureAnnotation` and
`SchemaAnalysisAnnotation`, which are the generic shapes.

That is not a bug today; those annotations carry the information. But it means
**a consumer querying Egeria for code analysis will not find RE's** — the
publishing is correct and the type is generic, so it is invisible to a
type-scoped query. Same argument for contributor analysis.

For FS and DB the equivalent mistake would be publishing column-level findings
as generic measures when `DataFieldAnnotation` exists, and physical file state
as measures when `ResourcePhysicalStatusAnnotation` exists — with the added
cost that Egeria's *own* FS/DB survey services presumably emit the specific
types, so RE's output would not sit alongside theirs.

### Rectifying this is a task, not an observation

the maintainer, 2026-09-02, on the type mismatch: **"we may need to rectify this."**

Two pieces, and the second is the one with a deadline attached:

1. **Retroactive, for repos.** `CodeAnalysisAnnotation` and
   `ContributorAnalysisAnnotation` are the clear cases. Needs the migration
   question answered rather than assumed: what happens to annotations already
   published under the generic types? Re-publishing under a new type without
   retracting the old one leaves both in the catalog.
2. **Before FS/DB steps are written.** This is the one that gets harder with
   time. Under the mixed model above, RE's output has to sit alongside output
   from Egeria's own services on the same asset — and those services
   presumably emit the specific types. Choosing generic types for RE's FS/DB
   findings would produce a catalog where two surveys of one database
   disagree about how to describe the same kind of finding.

### The check to run before designing FS/DB steps

For each planned finding, ask **"does Egeria already have a type for this
shape?"** before reaching for `ResourceMeasureAnnotation`. The generic types
are a reasonable default only where nothing specific exists.

Worth doing retroactively for repos too — at least `CodeAnalysisAnnotation`
and `ContributorAnalysisAnnotation` — but as its own change, with the
migration question (what happens to already-published annotations of the
generic type) answered rather than assumed.

---

## 3. What is not yet established

- **What the native FS/DB services actually emit.** This document establishes
  that they exist and that Egeria has richer annotation types; it does *not*
  establish which types each service produces. Reading
  `PostgresDatabaseSurveyActionService` and `FolderSurveyService` would say,
  and would show how much RE would be duplicating under case 3.
- **Whether RE can author every relevant annotation type over REST.**
  `create_annotation` exists in pyegeria's `data_discovery.py`; whether a
  `SurveyReport` can be authored over REST at all is flagged as unverified in
  `survey-execution.md (§7)`.
- **Property shapes.** Each unused type has its own `…Properties` class; none
  were read here. Adopting one means matching its properties, not just its
  name.
- **Whether ISSUE-79 still reproduces** on the current deployment. The tracker
  records an Egeria-side investigation that disproved the original root-cause
  theory and an FVT suite now passing; whether this deployment still hits it
  is open.

## 4. Suggested sequence

1. Settle **surveyor vs orchestrator** for FS and DB first. Everything else
   depends on it, and it is a question about duplication and ownership, not
   about code.
2. Read what the native services emit, so the duplication cost is a measured
   number rather than an impression.
3. Decide the **annotation-type mapping** before writing steps — retrofitting
   published annotations is the expensive direction.
4. Only then design steps, and only for whatever RE is actually going to
   survey itself.

---

# Part II — Database and filesystem designs

*Merged 2026-09-02 from four notes.*

## 5. Filesystem: two steps, drawn at the cost boundary

*(from `filesystem-survey-analytics-plan.md`; design agreed 2026-07-13)*

**Do not fragment into one step per annotation type.** If the code is already
asking the OS for a file's `stat()`, it should take everything that call offers in
the same pass rather than walking the tree again per annotation.

> **The real boundary is cost, not annotation shape:** a cheap metadata-only pass
> (one `stat()` and one extension lookup per file) versus an expensive
> content-reading pass (opening and parsing every data file's bytes).

That boundary is also exactly where the "hang" came from — the parsing errors and
multi-minute runtimes all lived in the second pass — so it is a natural split
regardless of Egeria parity.

- **Step 1, `filesystem_structure`** — one walk, one `stat()` per file. Produces
  the counts measure, per-group classifications, an extension breakdown, and two
  requests for action: **unclassified files** and **inaccessible files**. That
  second one directly fixes the silent half of the hang bug — permission errors
  were being swallowed during the walk and reaching only the server console.
- **Step 2, `filesystem_data_profiling`** — the existing content-reading pass,
  unchanged in kind, now separable and skippable.

## 6. Database: what Egeria's Postgres model already provides

*(from `egeria-postgresql-exploration.md`, `database-surveyor-design.md`)*

Egeria defines **seven PostgreSQL technology types** as valid metadata values,
separating concerns RE would otherwise have conflated: the *server software*, the
*server instance*, the *database*, the *schema* (as a tabular-data-set
collection), the *table*, and the *RDBMS capability* that manages them.

The design consequence is that RE's database surveyor should map onto that
existing hierarchy rather than inventing an asset shape — the same conclusion the
repository path reached about Area 6, arrived at independently.

## 7. Running a database survey

*(from `database-surveyor-quickstart.md`)*

The operator path — install, connect, run, and read the survey output — is
unchanged by the consolidation and lives here so the design and the instructions
for using it are not in separate files. The survey produces schema, table and
column structure plus profiling statistics, published as annotations against the
catalogued database asset.

**Credentials (2026-09-25):** register the *catalog identity* only; the
survey's structure and estimate steps run with it, and the steps that read
rows bind to a separate *data identity* requested at the point of use. What
each identity holds per engine, how the connected account's visibility is
probed and reported, and how Egeria's own surveys choose a connection are in
`docs/security-model.md`, which supersedes anything this document implies
about a single `db_user`/`db_password` pair.

---

# Part III — Settled — do not reopen without re-measuring

| Question | Settled | On what basis |
|---|---|---|
| Does RE's repo machinery carry over to FS and DB? | **No** | 17 native survey services exist for FS/DB/catalog/platform, and **none for repositories** — the asymmetry is why the repo path looks the way it does |
| Is orchestration a choice between Egeria and RE? | **No** — mixed | Direct maintainer decision: Egeria orchestrates where a native service does the job, RE orchestrates the survey types Egeria lacks |
| Should steps be split per annotation type? | **No** — per cost | One `stat()` pass yields several annotations; the meaningful boundary is metadata-only versus content-reading |
| Should RE define its own database asset shape? | **No** | Egeria already separates server software, server instance, database, schema, table and RDBMS capability |
| Are RE's 7 annotation types sufficient for FS/DB? | **Unresolved** | Egeria defines 18; which of the remaining 11 apply is open, and is design input rather than a settled answer |
| Is a database credential one account with more or fewer grants? | **No** — two identities | Project owner, 2026-09-25: a catalog identity (registration) and a data identity (at the gate); `docs/security-model.md` |
| Are catalog statistics actuals? | **No** — estimates as of the last utility run | `pg_stats` is refreshed only by `ANALYZE`/`VACUUM` and is gated by column `SELECT` (live probe 2026-09-25); `multi-resource-questions-design.md` §5.1a |
