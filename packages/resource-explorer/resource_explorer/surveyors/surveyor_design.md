# Resource Explorer → Egeria Surveyor: Design Document

## Background

Egeria Area 6 defines a survey/annotation model for recording the results of automated analysis of resources. A **SurveyReport** groups all findings from one analysis run and is linked to an asset via a `ReportSubject` relationship. Individual findings are **Annotation** entities hanging off the report via `ReportedAnnotation`.

Resource Explorer already ingests GitHub repos and extracts rich metadata (file structure, language breakdown, dependencies, GitHub stats, module/API surface, documentation signals). This design extends it to produce Egeria-compatible survey reports from that data.

---

## What We Learned from Egeria Area 6

### Core Types

| Type | Purpose |
|---|---|
| `SurveyReport` | Top-level container for one analysis run; linked to asset via `ReportSubject` |
| `Annotation` | Base type for a single finding; linked to report via `ReportedAnnotation` |
| `AnnotationReview` | Steward assessment: approve, invalidate, or convert an annotation |

### Annotation Subtypes

| Subtype | Use |
|---|---|
| `ResourceMeasureAnnotation` | Counts, sizes, metrics |
| `ClassificationAnnotation` | Category/label assignments |
| `SchemaAnalysis` | Structure / API surface |
| `DataClassAnnotation` | Data class / dependency classification |
| `QualityScoreAnnotation` | Health and quality scores |
| `RelationshipAnnotation` | Discovered relationships between components |
| `RequestForAction` | Flags for human review |

### Base Annotation Fields (all subtypes inherit)

- `annotationType` — identifies the subclass
- `summary` — human-readable description
- `confidence` — certainty indicator (int)
- `expression` — detail about the relationship to the asset
- `explanation` — additional context
- `analysisStep` — which step of the survey produced this
- `jsonProperties` — structured properties (free-form JSON)
- `additionalProperties` — service-generated extras

### pyegeria API (DataDiscovery client)

```python
from pyegeria.omvs.data_discovery import DataDiscovery

discovery = DataDiscovery(view_server, platform_url, user_id)
guid = discovery.create_annotation(body)
```

Body structure:
```json
{
  "class": "NewElementRequestBody",
  "parentGUID": "<survey_report_guid>",
  "parentRelationshipTypeName": "ReportedAnnotation",
  "properties": {
    "class": "AnnotationProperties",
    "qualifiedName": "...",
    "summary": "...",
    "annotationType": "ResourceMeasureAnnotation",
    "jsonProperties": "{...}"
  }
}
```

---

## What Resource Explorer Already Knows

From the existing ingestion pipeline and SQLite registry, for any indexed repo we have:

- Full file tree with sizes and languages (from GitHub tree traversal)
- Dependency graph (`requirements.txt`, `pyproject.toml`, `package.json`, etc.)
- Python module/class/function structure (from `query_code_symbols`)
- GitHub stats: stars, forks, commit frequency, contributor counts (from `project_stats` + `project_commits` tables)
- Documentation quality signals (README, CHANGELOG, examples presence)
- License, CI config, security file presence

---

## Proposed Architecture

```
explorer/surveyors/
├── __init__.py
├── survey_report.py          # SurveyResult + per-annotation dataclasses (no Egeria coupling)
├── base_surveyor.py          # Abstract BaseSurveyor interface
├── file_classifier/          # ← already exists; wired in as a sub-surveyor (see Q3)
├── sub_surveyors/
│   ├── file_structure.py     # → ResourceMeasureAnnotation  (file counts, sizes, lang breakdown)
│   ├── language.py           # → ClassificationAnnotation   (primary lang, secondary langs)
│   ├── dependency.py         # → DataClassAnnotation        (deps, licenses)
│   ├── api_structure.py      # → SchemaAnalysis             (modules, public API surface)
│   ├── health.py             # → QualityScoreAnnotation     (stars, commit freq, contributors)
│   ├── documentation.py      # → ClassificationAnnotation   (docs type, quality signals)
│   └── security.py           # → RequestForAction           (missing SECURITY.md, stale deps)
├── survey_orchestrator.py    # Runs all sub-surveyors → SurveyResult
└── egeria_publisher.py       # SurveyResult → pyegeria API calls
```

### Key Design Principles

1. **Separation of concerns** — Sub-surveyors produce plain Python dataclasses (`SurveyResult`), no Egeria dependency. `EgeriaPublisher` handles all API calls. Surveys are useful standalone even without Egeria running.
2. **Reuse existing data** — Sub-surveyors read from the SQLite registry and pgvector (already indexed). They do not re-download from GitHub. The `project-explorer add` / `refresh` pipeline feeds the survey.
3. **Asset auto-registration** — `EgeriaPublisher` finds or creates the GitHub repo as an `Asset` in Egeria before attaching the `SurveyReport` (see Q2). **Historical note (2026-09-14):** originally a `SourceControlLibrary`, corrected once that was found to be a type error — see `egeria_publisher.py`'s module docstring.
4. **CLI integration** — New `project-explorer survey <project> [--publish]` command. Without `--publish` the survey prints as markdown. With `--publish` it also pushes to Egeria (see Q4).
5. **Governance action integration** — `--publish` also exposes an option to trigger a defined Egeria governance action process (via pyegeria) to catalog the asset, not just record the survey. This keeps cataloguing as a deliberate user choice rather than automatic.

---

## Annotation Mapping

| What we know | Egeria Annotation Type | Key fields populated |
|---|---|---|
| File counts by language/type, total size | `ResourceMeasureAnnotation` | `resourceProperties` JSON |
| Primary language, project category (library/CLI/service) | `ClassificationAnnotation` | `candidateClassifications` |
| `requirements.txt` / `pyproject.toml` / `package.json` deps | `DataClassAnnotation` | `dataClassGUIDs`, `jsonProperties` |
| Python module tree, public functions/classes | `SchemaAnalysis` | links to schema elements |
| Stars, commit frequency, contributor count, last commit age | `QualityScoreAnnotation` | `qualityScores` map |
| README / CHANGELOG / examples / contributing guide presence | `ClassificationAnnotation` | `summary`, `jsonProperties` |
| Missing SECURITY.md, no CI, stale dependencies | `RequestForAction` | `actionRequested`, `actionTargetName` |

---

## Decisions

### Q1: Data source for survey ✓
**Decision:** Read from local SQLite/pgvector (fast, offline-capable). Add `--refresh` flag to force a fresh pull from GitHub before surveying.

---

### Q2: Egeria asset type for GitHub repos ✓
**Decision:** Use `SourceControlLibrary` — a subtype of `ResourceManager` (software capability), not `SoftwarePackageManifest` (which is a classification, not an asset type). pyegeria support for `SourceControlLibrary` is in progress; `EgeriaPublisher` will use a **placeholder** until the new `AssetMaker` methods land. The placeholder will log a warning and skip asset registration gracefully rather than failing.

---

### Q3: File classifier integration ✓
**Decision:** Wire `file_classifier/` into the main survey as a sub-surveyor, producing `ClassificationAnnotation` entries per file (or per file-type group). It also remains usable standalone.

The classifier runs in **all cases** — even without Egeria — using a local cache of file-type mappings. When Egeria credentials are present, the cache is refreshed from `ValidMetadataValues`; when offline, the last-known cache is used. This makes Egeria an enhancement rather than a hard dependency, and gives improved classifications even in air-gapped or local-only deployments. The ingestion pipeline will eventually migrate to this same classifier, replacing its simpler extension-based lookup in a phased way.
---

### Q4: Default behavior of `--publish` ✓
**Decision:** Opt-in. Default behavior is always to print the survey as markdown (no Egeria required). `--publish` activates the Egeria push.

Cataloguing is explicitly decoupled from surveying: it happens at a different time, may require additional review, and may be partial (only some surveyed assets are worth cataloguing). After publishing, if Egeria credentials are present, the CLI offers an interactive prompt — *"Also trigger governance action process to catalog this asset? [y/N]"* — so the user decides with full context of the survey results in hand, not as an automatic side effect of pushing the survey.
---

### Q5: Egeria connection configuration ✓
**Decision:** Use the standard pyegeria environment variables — no new config block needed. Add the following to `.env.example` as an optional section:

```bash
# Egeria integration (optional — required only for --publish)
EGERIA_PLATFORM_URL=https://localhost:9443
EGERIA_VIEW_SERVER=qs-view-server
EGERIA_USER=erinoverview
EGERIA_USER_PASSWORD=secret
PYEGERIA_TIMEOUT_SECONDS=30
```

If none are set, pyegeria falls back to its own `config/config.json`.

> **Superseded 2026-11-18 (TR-39):** the RE server signs in as its own service account, `RE_DAEMON_USER_ID` / `RE_DAEMON_USER_PASSWORD` (`resourceexplorernpa`), never `EGERIA_USER*`. Those are for the CLI only. See `docs/deploying-trellis-with-quickstart.md` §1.3. `EgeriaPublisher` checks for `EGERIA_PLATFORM_URL` at startup and raises a clear error if `--publish` is requested but credentials are missing, rather than failing mid-run.

---

### Q6: Granularity of annotations ✓
**Decision:** Start fine-grained — one annotation per distinct finding or per file-type group. Each `ResourceMeasureAnnotation` covers a single language/type; each `RequestForAction` covers a single missing artifact. Composite summaries and roll-up reports (e.g., one top-level `QualityScoreAnnotation` aggregating all health signals) will be added in a later phase, likely as a separate report type or graph view.

---

## Implementation Notes

### File path collection — full inventory (primary) + fallback sources

`FileClassifierSurveyor._collect_file_paths()` uses **`project_file_inventory`** as its primary source when available. This table is populated during every `add` and `refresh` by `IngestionPipeline._store_file_inventory()`, which walks the downloaded repo directory and stores every file path and size. It gives the surveyor a complete view of the repo — including YAML CI configs, shell scripts, TOML manifests, and anything else that isn't vectorised.

For projects indexed before the inventory table was added, the surveyor falls back to three partial sources:

1. `project_code_symbols` (SQLite) — Python/JS/Java/Go source files only
2. `MultiCollectionStore.list_source_files(collections)` (pgvector) — markdown, examples, release notes, PDFs (stored as `file_path` in `metadata_json`)
3. `project_dependencies.source_file` (SQLite) — package manifest files (pyproject.toml, requirements.txt, package.json, …)

Web URLs (from `web_docs` collection) are excluded from classification since they are not local files.

**Action**: run `project-explorer refresh <slug>` to populate the inventory for projects indexed before this feature was added.

### FileTypeCache — built-in defaults

`FileTypeCache.lookup()` follows a four-level priority chain:

1. Egeria cache by filename (`by_name`) — highest specificity
2. Egeria cache by extension (`by_ext`)
3. Built-in defaults by filename (`_BUILTIN_BY_NAME`) — ~20 well-known files (Dockerfile, Makefile, pyproject.toml, requirements.txt, package.json, …)
4. Built-in defaults by extension (`_BUILTIN_BY_EXTENSION`) — ~55 extensions (.py, .md, .toml, .yaml, .js, .java, .go, .rs, .sh, …)

This means the classifier produces meaningful labels (e.g. "Python Source File", "TOML Configuration", "YAML Configuration") with no Egeria connection. Egeria `ValidMetadataValues` enrich or override the built-ins when credentials are present and the cache is refreshed.

### Unrecognized files — "Other" group

Files whose name and extension match nothing in the priority chain are consolidated into a single **"Other"** `ClassificationAnnotation` rather than creating a separate label per extension. The annotation's `json_properties.unrecognized_extensions` records the extension breakdown (e.g. `{".xyz": 12, ".bin": 3}`). This detail is also stored in `project_file_type_counts.details_json` and surfaced as a hover tooltip on the web File Types chart.

### FileTypeCache persistence

Survey results are written to `project_file_type_counts` (SQLite) and **appended**, not replaced, on each run. `query_file_type_counts()` returns only the latest run; `query_file_type_history()` returns totals per run for trending. The web File Types chart reads from this table, falling back to raw extension counts from `project_code_symbols` when no survey has been run.

The `details_json` column stores per-label breakdown data — currently used to capture the extension breakdown within the "Other" group.

### File count reported by StatsAgent

The file count shown in stats responses now prefers the survey total from `project_file_type_counts` (sum of all type groups, covering all file types). If no survey has been run, it falls back to `COUNT(DISTINCT file_path)` from `project_code_symbols` (code files only) with a note to the user to run a survey for the full count.

## Status — All phases complete

All items from the original Next Steps list have been implemented:

| Phase | Status |
|---|---|
| `survey_report.py` — SurveyResult + 7 Annotation dataclasses | ✓ |
| `base_surveyor.py` — Abstract BaseSurveyor | ✓ |
| `file_classifier/` — sub-surveyor with FileTypeCache (built-ins + Egeria) | ✓ |
| 7 sub-surveyors (file_structure, language, health, dependency, documentation, security, api_structure) | ✓ |
| `survey_orchestrator.py` | ✓ |
| `egeria_publisher.py` with SourceControlLibrary placeholder | ✓ |
| `project-explorer survey` CLI command | ✓ |
| Egeria env vars in `.env.example` | ✓ |
| `project_file_inventory` table + ingestion pipeline integration | ✓ |
