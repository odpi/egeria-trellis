# Resource Explorer — Surveyor Reference Guide

**Status:** stale. Oldest unrevised reference (2026-08-07); predates the database surveyors and Prefect. Check against the code.

The survey framework produces an **Egeria-aligned annotation report** for any indexed project.
Surveys run entirely from SQLite (no Egeria connection needed); publishing to Egeria is optional.

---

## Quick Start

```bash
# Survey one project
resource-explorer survey egeria

# Survey several projects in one pass
resource-explorer survey egeria beeai_framework ml_llm_ops

# Survey every registered project
resource-explorer survey --all

# Survey only top-level projects (skip monorepo sub-projects)
resource-explorer survey --all --top-level

# Survey and push to Egeria in one step
resource-explorer survey egeria --publish

# Survey all and publish all
resource-explorer survey --all --publish

# View Egeria survey history for a project (no Egeria connection needed)
resource-explorer egeria-reports egeria
resource-explorer egeria-reports egeria --full    # also fetch annotation detail from Egeria
```

The **web UI** shows the survey report under the **"📊 Survey Report"** tab when a project is
selected. File type counts and health metrics are displayed as Plotly charts. A **Data Files**
section shows column schemas, row counts, and null rates for profiled CSV/Excel/Parquet files.
When data files are detected but no profiles exist, a hint shows the exact `refresh` command.
Individual file types can be selected and cataloged as Egeria `DataSet` assets from the same
tab using **"Catalog selected →"**.

The sidebar has per-project action buttons (visible on hover): **🔄 Refresh & profile** and
**📊 Survey**. 🔄 is useful to populate profiles on projects indexed before data profiling was
added — it runs synchronously and auto-reloads the report tab on completion.

---

## Typical Workflows

### First-time survey of a new project

```bash
# 1. Index the project (also builds file inventory and data profiles)
resource-explorer add https://github.com/apache/arrow

# 2. Survey — reads everything from SQLite, no clone needed
resource-explorer survey arrow

# 3. Optionally publish to Egeria
resource-explorer survey arrow --publish
```

### Re-survey after code changes

```bash
# Refresh re-indexes and rebuilds the file inventory and data profiles.
# If no new commits are found but profiles are missing, refresh still
# downloads and profiles automatically.
resource-explorer refresh arrow

# Survey picks up the updated inventory and profiles automatically
resource-explorer survey arrow
```

### Survey a data-heavy project and inspect schemas

```bash
# After add/refresh, data profiles are stored automatically
resource-explorer survey house_prices_global

# Example output (DataProfilerSurveyor):
#   data/train.csv: 1,460 rows × 81 columns
#   data/test.csv:  1,459 rows × 80 columns
#   5 column(s) >50% null: PoolQC, MiscFeature, Alley, Fence, FireplaceQu

# If you have an updated local clone with new data files:
resource-explorer survey house_prices_global --data-path ~/datasets/house-prices
```

### Surveying a monorepo

```bash
# Index two sub-projects from the same repo
resource-explorer add https://github.com/odpi/egeria \
    --subpath open-metadata-implementation/adapters --name egeria-adapters
resource-explorer add https://github.com/odpi/egeria \
    --subpath open-metadata-implementation/frameworks --name egeria-frameworks

# Survey both — they share one SourceControlLibrary asset in Egeria
resource-explorer survey egeria-adapters egeria-frameworks

# Publish both (creates separate SurveyReports linked to the shared asset)
resource-explorer survey egeria-adapters egeria-frameworks --publish
```

### Routine batch survey

```bash
# Re-index and survey everything weekly
resource-explorer refresh --all
resource-explorer survey --all --publish
```

---

## Survey Pipeline

`SurveyOrchestrator.run(slug)` executes surveyors in this order:

| # | Surveyor | Primary Source | Output |
|---|---|---|---|
| 1 | `FileClassifierSurveyor` | `project_file_inventory` | `ClassificationAnnotation` per file type group |
| 2 | `FileStructureSurveyor` | `project_stats`, `project_code_symbols` | `ResourceMeasureAnnotation` — counts, LOC, dir tree |
| 3 | `FileSizeSurveyor` | `project_file_inventory` | `ResourceMeasureAnnotation` — size-by-type, top-10 largest; `RequestForAction` for files >50 MB |
| 4 | `DataProfilerSurveyor` | `project_file_inventory`, `project_data_profiles` | `ResourceMeasureAnnotation` — data format summary; `SchemaAnalysisAnnotation` per profiled file |
| 5 | `LanguageSurveyor` | `project_stats`, `project_code_symbols` | `ClassificationAnnotation` — primary/secondary language, project type |
| 6 | `HealthSurveyor` | `project_stats`, `project_commits` | `QualityScoreAnnotation` — activity, community, release cadence, freshness |
| 7 | `DependencySurveyor` | `project_dependencies` | `DataClassAnnotation` per ecosystem + totals |
| 8 | `DocumentationSurveyor` | pgvector collections, `project_file_inventory` | `ClassificationAnnotation` — doc presence, hygiene files |
| 9 | `SecuritySurveyor` | `project_file_inventory` | `RequestForAction` for missing SECURITY.md, CI config, license |
| 10 | `ApiStructureSurveyor` | `project_code_symbols` | `SchemaAnalysisAnnotation` per language — module tree, public symbols |

Each surveyor is independent and failures are non-fatal — a failed surveyor adds an error to
`SurveyResult.errors` and the rest of the pipeline continues.

### Example survey output

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ Survey: egeria ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## File Classification
  Python Source          1,243 files
  Java Source            8,102 files
  Markdown               412 files
  YAML Config            289 files
  JSON                   156 files
  XML                    98 files
  Other                  341 files  (hover for breakdown)

## File Structure
  Total files: 10,641 · Repo size: 287 MB · Lines of code: ~2.1M
  Directories: open-metadata-implementation/ (72%), open-metadata-tests/ (18%), ...

## File Size
  Total disk footprint: 287 MB across 8 type groups
  Top largest: egeria-chassis-spring.jar (42 MB), ...
  ⚠ 3 file(s) exceed 50 MB — consider Git LFS

## Data Profiling
  No data files found in this project.

## Language
  Primary: Java · Secondary: Python
  Project type: Library / Framework

## Health  [score: 82/100]
  Activity:   ██████████  98   (commits last 90d: 847)
  Community:  ████████░░  78   (contributors: 34, stars: 1,842)
  Releases:   ████████░░  80   (last release: 12 days ago)
  Freshness:  ████████░░  72   (last commit: 2 days ago)

## Dependencies
  Maven: 312 dependencies
  PyPI:  28 dependencies

## Documentation  [Good]
  Collections: markdown_docs ✓  web_docs ✓  api_reference ✓
  Hygiene: README ✓  CHANGELOG ✓  CONTRIBUTING ✓  LICENSE ✓

## Security
  ✓ SECURITY.md present · ✓ CI configured · ✓ License: Apache-2.0

## API Structure
  Java: 1,203 classes · 8,941 public methods across 412 files
  Python: 47 classes · 289 functions across 31 files

Annotations: 18  ·  Errors: 0  ·  Elapsed: 2.3s
```

---

## Data Profiling

Data file profiling runs automatically during `add` and `refresh` while the repo is on disk.
Results are stored in `project_data_profiles` and read at survey time — no local clone is needed
when you run `survey`.

### How It Works

**At ingestion time** (`add` / `refresh`):
```
IngestionPipeline._profile_data_files()
  → walks code_root for _DATA_EXTENSIONS
  → Parquet: pyarrow.parquet.read_metadata() + read_schema() — no size limit
  → Arrow/Feather: pyarrow IPC metadata + single-column read for row count — no size limit
  → CSV/Excel: pandas.read_csv/read_excel (nrows=100_000) — skips files >50 MB
  → stores row_count, col_count, column schemas, null rates → project_data_profiles

IncrementalIndexer (used by `refresh`):
  → if new commits exist: downloads repo, re-indexes changed collections, then calls
    _store_file_inventory() and _profile_data_files() on the downloaded tree
  → if no new commits but project_data_profiles is empty: downloads repo for profiling only
    ("Downloading repository for data profiling…"), then updates inventory and profiles
  → either path ensures project_data_profiles is populated after any `refresh`
```

**At survey time** (`survey`):
```
DataProfilerSurveyor.run()
  → Tier 1: reads project_file_inventory → counts/sizes per format
  → Tier 2: reads project_data_profiles → SchemaAnalysisAnnotation per file
```

### Supported Formats

| Format | Extensions | Profiled (row/col schema) | Size limit |
|---|---|---|---|
| CSV | csv, tsv, tab, psv | Yes | 50 MB |
| Excel | xlsx, xls | Yes | 50 MB |
| Parquet | parquet | Yes — schema + row count from file metadata, no data loaded | None |
| Arrow / Feather | arrow, feather | Yes — schema from IPC metadata, one column for row count | None |
| Avro | avro | No (size/count only) | — |
| ORC | orc | No (size/count only) | — |
| HDF5 | h5, hdf5, hdf | No (size/count only) | — |
| NumPy | npy, npz | No (size/count only) | — |
| JSON Lines | jsonl, ndjson | No (size/count only) | — |
| SQLite | db, sqlite, sqlite3 | No (size/count only) | — |
| DuckDB | duckdb | No (size/count only) | — |
| Pickle | pkl, pickle | No (size/count only) | — |

Parquet and Arrow/Feather use pyarrow to read schema and row count from file metadata — no row
data is loaded regardless of file size. CSV and Excel require pandas to parse bytes to infer
types; files larger than **50 MB** are skipped and a `RequestForAction` annotation is emitted.

### Example: data-rich project

```
resource-explorer add https://github.com/datasciencedojo/datasets
resource-explorer survey datasets
```

```
## Data Profiling
  6 data file(s) across 1 format(s), total 18.3 MB

  titanic.csv:              891 rows × 12 columns
    Cabin: 77.1% null  Age: 19.9% null
    Columns: PassengerId(int64) Survived(int64) Pclass(int64) Name(object)
             Sex(object) Age(float64) SibSp(int64) Parch(int64)
             Ticket(object) Fare(float64) Cabin(object) Embarked(object)

  house-prices/train.csv:  1,460 rows × 81 columns
    PoolQC: 99.5% null  MiscFeature: 96.3% null  Alley: 93.8% null
    ⚠ 5 column(s) >50% null

  iris.csv:                 150 rows × 5 columns
    No null values.
```

### Pandas Dependency

Profiling requires `pandas` plus optional readers:

```bash
uv add pandas openpyxl pyarrow   # Excel + Parquet support
```

If `pandas` is not installed, Tier 1 (inventory counts) still runs; Tier 2 is silently skipped
during ingestion and a note is printed.

### When You Need `--data-path`

Profiling runs automatically during `add` and `refresh` — you do **not** need `--data-path`
for normal use. The flag exists for one specific case: you want updated profiles without running
a full re-ingest (e.g., the data files changed but the code did not).

```bash
# Force fresh profiling from a local clone without re-downloading the repo
resource-explorer survey house_prices_global --data-path ~/datasets/house-prices
```

This runs `DataProfilerSurveyor` Tier 2 directly from the local path and updates
`project_data_profiles`.

---

## File Type Classification

All file type lookups go through `FileTypeCache` with this priority:

1. Egeria `ValidMetadataValues` by filename (highest priority)
2. Egeria `ValidMetadataValues` by extension
3. Built-in defaults by filename (e.g., `Dockerfile`, `pyproject.toml`)
4. Built-in defaults by extension (lowest priority)

Unrecognized files land in the **"Other"** bucket. The extension breakdown is stored in
`details_json` and shown as a hover tooltip in the web UI File Types chart.

### Built-in Extension Coverage

**Source code:** py, pyi, ipynb, js, mjs, ts, tsx, jsx, html, css, java, kt, scala, go, rs, c, cpp, h, hpp

**Data — tabular:** csv, tsv, tab, psv, xlsx, xls, xlsm, xlsb, ods

**Data — columnar/binary:** parquet, avro, orc, arrow, feather, h5, hdf5, npy, npz

**Data — serialization:** pkl, pickle, joblib, msgpack, jsonl, ndjson, geojson

**Data — databases:** db, sqlite, sqlite3, duckdb

**Config:** toml, yaml, yml, json, xml, cfg, ini, env, lock

**Docs:** md, mdx, rst, txt, pdf

**Scripts:** sh, bash, zsh, bat, ps1

**SQL:** sql

**Archives:** gz, bz2, xz, zst, tar, tgz, zip, rar, 7z

**ML models:** pt, pth, ckpt, safetensors, onnx, pb, mlmodel, bin

**Images:** png, jpg, jpeg, gif, svg, ico

**Well-known filenames:** Dockerfile, Makefile, LICENSE, .gitignore, requirements.txt, pyproject.toml, package.json, go.mod, Cargo.toml, pom.xml, and more

To add a new type, append to `_BUILTIN_BY_EXTENSION` in
`explorer/surveyors/file_classifier/type_cache.py` **and** to `_DATA_EXTENSIONS` in
`explorer/surveyors/sub_surveyors/data_profiler.py` if it is a data format.

---

## File Size Analysis

`FileSizeSurveyor` reads `project_file_inventory.file_size_bytes` to produce:

- **Total repo disk footprint** with size-by-type breakdown and average file size
- **Top-10 largest files** — useful for spotting accidentally committed binaries
- **`RequestForAction`** for any file exceeding 50 MB recommending Git LFS or external storage

The threshold constants are in `file_size.py`:

```python
_LARGE_FILE_THRESHOLD_MB = 50
_VERY_LARGE_FILE_THRESHOLD_MB = 200
```

### Example output

```
## File Size
  Total: 287.4 MB across 8 type groups
  Java Source:    198.2 MB  (68.9%)  avg 24.4 KB
  Markdown:        12.1 MB  ( 4.2%)  avg 29.4 KB
  Archives:        48.3 MB  (16.8%)  avg 16.1 MB
  ...

  Top 10 largest files:
    egeria-chassis-spring.jar              42.1 MB
    open-metadata-distribution.zip        38.7 MB
    ...

  ⚠ RequestForAction: egeria-chassis-spring.jar (42.1 MB)
    Recommendation: Move to Git LFS or external artifact storage
```

---

## Annotation Types

All annotations inherit from `Annotation` and are defined in `survey_report.py`:

| Type | Egeria class | Produced by |
|---|---|---|
| `ClassificationAnnotation` | `ClassificationAnnotationProperties` | FileClassifier, Language, Documentation |
| `ResourceMeasureAnnotation` | `ResourceMeasureAnnotationProperties` | FileStructure, FileSize, DataProfiler, Dependency |
| `QualityScoreAnnotation` | `QualityAnnotationProperties` | Health, FOSS scorecard, SQL view analysis (databases) |
| `DataClassAnnotation` | `DataClassAnnotationProperties` | Dependency, database column profiling (`data_class_match`, one per column checked) |
| `SchemaAnalysisAnnotation` | `SchemaAnalysisAnnotationProperties` | ApiStructure, DataProfiler |
| `RelationshipAnnotation` | `RelationshipAdviceAnnotationProperties` | (reserved) |
| `RequestForActionAnnotation` | `RequestForActionProperties` | Security, FileSize, DataProfiler, any via `_warn()` |

`RequestForActionProperties` has **no "Annotation" suffix** — this is the correct Egeria class name.

**A `QualityScoreAnnotation`'s scores are in `jsonProperties`, under `qualityScores`, as numbers.** Egeria's
`QualityAnnotationProperties` has a single int `qualityScore` (which RE leaves unset, so Egeria shows `0`) and
ignores unknown fields. RE used to send the map as a `qualityScores` field, which Egeria discarded without an
error; reports published before 2026-10-19 hold no scores. `egeria_reader` restores the map to
`subtype_data["qualityScores"]` when it reads an annotation back.

---

## Publishing to Egeria

`resource-explorer survey <slug> --publish` runs the full survey then:

1. Finds or creates a `SourceControlLibrary` asset for the GitHub URL
2. Creates a `SurveyReport` asset linked via `ReportSubject`
3. Creates one `Annotation` per finding using the correct Egeria subtype class names
4. Persists the asset GUID and report GUID to SQLite for future runs

The asset GUID is cached in `projects.egeria_asset_guid` so repeated publishes skip the
`find_software_capabilities` search call.

### Example publish session

```bash
$ resource-explorer survey egeria --publish

Surveying egeria...
  ✓ FileClassifier      12 annotations
  ✓ FileStructure        1 annotation
  ✓ FileSize             4 annotations (incl. 3 RFAs)
  ✓ DataProfiler         0 annotations
  ✓ Language             1 annotation
  ✓ Health               1 annotation
  ✓ Dependency           3 annotations
  ✓ Documentation        1 annotation
  ✓ Security             1 annotation
  ✓ ApiStructure         2 annotations

Publishing to Egeria (https://localhost:9443)...
  Asset GUID (cached): 7f3a1b2c-...
  SurveyReport GUID:   9e4d2f1a-...
  Annotations created: 26

Survey report written to Egeria. Trigger governance action? [y/N]:
```

### Viewing published surveys

```bash
# List all survey runs for a project (reads local SQLite — no Egeria needed)
$ resource-explorer egeria-reports egeria

  Surveyed at           Annotations  Report GUID
  ─────────────────────────────────────────────────
  2026-05-21 09:14:33        26      9e4d2f1a-...
  2026-05-14 08:52:17        24      8c3b1e0d-...
  2026-05-07 11:03:44        24      7b2a0d9c-...

# Fetch full annotation detail from Egeria
$ resource-explorer egeria-reports egeria --full

  [FileClassification] Python Source — 1,243 files
  [FileClassification] Java Source — 8,102 files
  [ResourceMeasure] Total files: 10,641 · 287 MB
  [QualityScore] Health: 82/100
  ...
```

### Egeria Environment Variables

```bash
EGERIA_PLATFORM_URL        # default: https://localhost:9443
EGERIA_VIEW_SERVER         # default: qs-view-server
EGERIA_USER                # default: erinoverview
EGERIA_USER_PASSWORD       # default: secret
PYEGERIA_TIMEOUT_SECONDS   # default: 30
```

---

## Cataloging File Types as DataSet Assets

From the web UI **Survey Report** tab, check any file type rows and click **"Catalog selected →"**.
This calls `POST /api/egeria/{slug}/catalog-elements` which:

1. Verifies the project is registered in Egeria (GUID must be cached — publish a survey first)
2. Creates a `DataSet` asset per selected type with `qualifiedName = DataSet::{slug}::{label}`
3. Links each `DataSet` to the `SourceControlLibrary` via a `CapabilityAssetUse` (useType: GOVERNS) relationship

**Requires a live Egeria connection** (`EGERIA_PLATFORM_URL` must point to a running server).
If Egeria is not running, the endpoint returns `{"status": "error", "error": "..."}` with the
connection details. The `AssetMaker` constructor takes `(view_server, platform_url, user, pwd)` —
note the argument order differs from some pyegeria examples.

---

## SQLite Tables Reference

| Table | Populated by | Read by |
|---|---|---|
| `project_file_inventory` | `IngestionPipeline._store_file_inventory()` on every `add`/`refresh` | FileClassifier, FileSize, DataProfiler surveyors |
| `project_data_profiles` | `IngestionPipeline._profile_data_files()` on every `add`/`refresh` (including no-commit `refresh` when table is empty) | DataProfilerSurveyor (Tier 2), web Survey Report tab |
| `project_file_type_counts` | `FileClassifierSurveyor` (per survey run) | Web File Types chart, StatsAgent |
| `project_egeria_surveys` | `EgeriaPublisher._create_survey_report()` | EgeriaReader, web Egeria tab |
| `projects.egeria_asset_guid` | `EgeriaPublisher._find_or_create_asset()` | EgeriaPublisher (cache), web status |

`project_file_type_counts` is **appended** on each survey run (not replaced), so you can track
how file composition changes over time. The web File Types chart shows the most recent run;
`query_file_type_history()` returns one row per run for trending.

## Web API Reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/projects/` | — | List all registered projects |
| `GET` | `/api/projects/{slug}` | — | Get one project's metadata |
| `POST` | `/api/projects/{slug}/refresh` | — | Incremental re-index + profiling (synchronous; returns `{status, slug, message, error}`) |
| `DELETE` | `/api/projects/{slug}` | — | Remove project and all collections |
| `GET` | `/api/egeria/{slug}/status` | — | Registration status + local survey history |
| `GET` | `/api/egeria/{slug}/survey-report` | — | Full survey report from SQLite (no Egeria needed) |
| `GET` | `/api/egeria/{slug}/annotations` | Egeria | All annotations for a specific survey run |
| `POST` | `/api/egeria/{slug}/survey` | — | Run survey pipeline only (no publish); returns `{status, annotation_count, surveyed_at, errors}` |
| `POST` | `/api/egeria/{slug}/publish` | Egeria | Full survey + publish; returns `{status, report_guid, annotation_count, surveyed_at}` |
| `POST` | `/api/egeria/{slug}/catalog-elements` | Egeria | Create `DataSet` assets for selected file types |

---

## Adding a New Sub-Surveyor

1. Create `explorer/surveyors/sub_surveyors/my_surveyor.py` extending `BaseSurveyor`
2. Implement `step_name` property and `run() → list[Annotation]`
3. Use `self._warn(results, msg)` for non-fatal issues (creates a `RequestForAction`)
4. Export from `sub_surveyors/__init__.py`
5. Add to the `surveyors` list in `SurveyOrchestrator.run()`

```python
from explorer.surveyors.base_surveyor import BaseSurveyor
from explorer.surveyors.survey_report import (
    Annotation,
    ResourceMeasureAnnotation,
)


class LicenseDetailSurveyor(BaseSurveyor):
    """Example: emit a ResourceMeasureAnnotation with license metadata."""

    @property
    def step_name(self) -> str:
        return "LicenseDetail"

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            rows = self.registry.get_file_inventory_with_sizes(self.project.slug)
            license_files = [
                r for r in rows
                if Path(r["file_path"]).name.upper().startswith("LICENSE")
            ]
            results.append(
                ResourceMeasureAnnotation(
                    summary=f"{len(license_files)} license file(s) found",
                    analysis_step=self.step_name,
                    confidence=95,
                    resource_properties={
                        "license_files": [r["file_path"] for r in license_files],
                    },
                )
            )
        except Exception as exc:
            self._warn(results, str(exc))
        return results
```

---

## Batch Operations

Both `survey` and `refresh` accept multiple slugs or `--all`:

```bash
# Named list — runs in sequence, one summary table at the end
resource-explorer survey egeria beeai_framework docling_git ml_llm_ops

# All registered projects
resource-explorer survey --all

# All top-level projects (skip sub-projects of monorepos)
resource-explorer survey --all --top-level

# All projects, publish each to Egeria
resource-explorer survey --all --publish

# Re-index all, skip GitHub API calls (faster, no rate limit risk)
resource-explorer refresh --all --no-stats

# Re-index top-level only, then survey top-level only
resource-explorer refresh --all --top-level
resource-explorer survey --all --top-level --publish
```

**Batch survey output** is condensed — one line per project showing annotation count and any
errors, followed by a Rich summary table:

```
  egeria              ✓  26 annotations   2.3s
  beeai_framework     ✓  18 annotations   1.1s
  docling_git         ✓  14 annotations   0.8s
  ml_llm_ops          ✗   0 annotations   0.2s  FileClassifier: no inventory — run refresh

  Surveyed: 3  Failed: 1  Total time: 4.4s
```

Full per-annotation listings only appear for single-project runs. `--publish` in batch mode
creates a shared `EgeriaPublisher` instance (one Egeria connection) and publishes each
project's result in sequence; the governance action prompt is suppressed.

---

## Sub-Projects and Monorepos

When a monorepo contains multiple independently indexable sub-projects, each is registered
with `--subpath`:

```bash
resource-explorer add https://github.com/odpi/egeria \
    --subpath open-metadata-implementation/adapters --name egeria-adapters
resource-explorer add https://github.com/odpi/egeria \
    --subpath open-metadata-implementation/frameworks --name egeria-frameworks

# Use --from-local if you already have a clone (avoids re-downloading)
resource-explorer add https://github.com/odpi/egeria \
    --subpath open-metadata-implementation/view-services --name egeria-views \
    --from-local ~/repos/egeria
```

### File inventory and data profiling scope

Each sub-project's inventory and data profiles are scoped to its `code_root`:

```
full_root = /tmp/extracted/egeria
code_root = full_root / open-metadata-implementation/adapters
```

File paths in `project_file_inventory` and `project_data_profiles` are **relative to
`code_root`**, so:

- `egeria-adapters` sees: `src/main/java/...`, `pom.xml`
- `egeria-frameworks` sees: `src/main/java/...`, `pom.xml`
- Neither sees files from the other sub-project's directory

To index the full monorepo as a single project add it without `--subpath`:
```bash
resource-explorer add https://github.com/odpi/egeria --name egeria-full
```

### Egeria asset sharing

All sub-projects from the same GitHub URL share one **`SourceControlLibrary`** asset in Egeria
(identified by `SourceControlLibrary::{github_url}`). The first sub-project to publish creates
the asset; subsequent ones find and reuse it via the cached `egeria_asset_guid`.

Each sub-project still gets its **own `SurveyReport`** linked to the shared asset via
`ReportSubject`, so survey histories are independent per sub-project.

| Egeria object | Scope |
|---|---|
| `SourceControlLibrary` asset | Shared across all sub-projects of the same URL |
| `SurveyReport` | One per sub-project per survey run |
| `Annotation` objects | Scoped to the sub-project's survey results |
| `egeria_asset_guid` (SQLite) | Stored per sub-project slug; set from first publish |

### Surveying sub-projects

```bash
# Survey one sub-project
resource-explorer survey egeria-adapters

# Survey several sub-projects together (one Egeria connection for --publish)
resource-explorer survey egeria-adapters egeria-frameworks egeria-views --publish

# Survey everything except sub-projects (parent repos only)
resource-explorer survey --all --top-level

# Survey absolutely everything — parents and sub-projects
resource-explorer survey --all
```

`--top-level` filters to projects where `parent_slug` is empty — i.e., projects registered
without `--subpath`.

---

## Troubleshooting

### "No inventory — run refresh"

`FileClassifierSurveyor` found no rows in `project_file_inventory`. This means the project
was indexed before the file inventory feature was added.

```bash
resource-explorer refresh <slug>
```

### "N data file(s) could be profiled — re-ingest to capture column schemas"

`DataProfilerSurveyor` found data files in the inventory but no stored profiles. This happens
for projects indexed before profiling was added. Fix:

```bash
resource-explorer refresh <slug>
```

`refresh` detects the empty `project_data_profiles` table and downloads the repo for profiling
even if no new commits are present. You can also use the 🔄 sidebar button in the web UI.

### Survey runs but Egeria publish fails

Check that the Egeria platform is reachable and credentials are set:

```bash
echo $EGERIA_PLATFORM_URL   # should be https://your-host:9443
resource-explorer survey <slug> --publish
# Look for "stage: survey" vs "stage: publish" in the error to isolate which step failed
```

The web UI publish button (`POST /api/egeria/{slug}/publish`) returns
`{"status": "error", "stage": "survey"|"publish", "error": "..."}` so you can tell
which step failed without reading logs.

### Parquet profiling returns no schema

`pyarrow` is not installed. Install it:

```bash
uv add pyarrow
```

Without pyarrow, Parquet files fall back to pandas, which respects the 50 MB limit and
may skip large files. With pyarrow, schema and row count are always available regardless
of file size.

---

## Filesystem Surveyor

The Filesystem Surveyor scans directory structures on the local host (or mounted volumes) and catalogs them in Egeria's metadata server.

### Local vs. Hybrid Survey Mode
*   **Local mode (`LocalFileSystemSurveyor`)**: Iterates through the files on disk starting from `local_mount_point`. It identifies data files and triggers `DataProfilerSurveyor` to extract schema columns and row/column counts. The results are stored in the local SQLite database.
*   **Hybrid mode (`run_hybrid_filesystem_survey`)**: First executes the local mode, then initializes `EgeriaFileSystemSurveyor` to catalog the folders and data files in Egeria using the calculated canonical path names. Finally, it publishes the generated `SurveyReport` and `Annotations` to Egeria.

### Filesystem API Endpoints
The web server provides endpoints under `/api/filesystems/` for managing filesystems:
*   `GET /api/filesystems/` - Lists registered filesystems.
*   `POST /api/filesystems/` - Registers a new filesystem.
*   `GET /api/filesystems/{slug}/` - Gets details of a filesystem.
*   `DELETE /api/filesystems/{slug}/` - Deletes a filesystem registration.
*   `POST /api/filesystems/{slug}/survey` - Executes a local or hybrid walk survey.
*   `POST /api/filesystems/{slug}/publish` - Manually publishes local walk stats to Egeria.
