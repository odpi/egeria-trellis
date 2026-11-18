"""Sub-surveyor: Data File Profiling → ResourceMeasure + SchemaAnalysis.

Two-tier operation:

  Tier 1 — inventory only (always runs):
    Detects data files (CSV, XLSX, Parquet, etc.) from project_file_inventory,
    reports counts and sizes per format, and identifies which directories
    contain data files.

  Tier 2 — stored profiles (runs when project_data_profiles rows exist):
    During add/refresh the ingestion pipeline profiles each readable data file
    while the repo is on disk and stores results in project_data_profiles.
    The surveyor reads these pre-computed profiles to emit one
    SchemaAnalysisAnnotation per file with column names, dtypes, row count,
    and null-rate summary — no local clone required at survey time.

    Tier 2 also activates when local_path= is passed to the constructor
    (e.g. via --data-path on the CLI) to force a fresh profile without a
    full re-ingest.
"""
from __future__ import annotations

import json as _json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import UNVERIFIED, StepOutcome, from_upstream_table
from resource_explorer.survey_time import now_utc_iso
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.scoping import path_matches_scope
from resource_explorer.surveyors.survey_report import (
    Annotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)

log = logging.getLogger(__name__)

STEP = "DataProfiling"

# Extensions treated as data files for Tier 1 reporting
_DATA_EXTENSIONS: dict[str, str] = {
    # Tabular text
    "csv":     "CSV",
    "tsv":     "CSV",
    "tab":     "CSV",
    "psv":     "CSV",
    # Excel
    "xlsx":    "Excel",
    "xls":     "Excel",
    "xlsm":    "Excel",
    "xlsb":    "Excel",
    "ods":     "Excel",
    # Columnar / binary
    "parquet": "Parquet",
    "avro":    "Avro",
    "orc":     "ORC",
    "arrow":   "Arrow",
    "feather": "Arrow",
    # HDF5 / NumPy
    "h5":      "HDF5",
    "hdf5":    "HDF5",
    "hdf":     "HDF5",
    "npy":     "NumPy",
    "npz":     "NumPy",
    # JSON variants
    "jsonl":   "JSON Lines",
    "ndjson":  "JSON Lines",
    # SQLite
    "db":      "SQLite",
    "sqlite":  "SQLite",
    "sqlite3": "SQLite",
    "duckdb":  "DuckDB",
    # Pickle
    "pkl":     "Pickle",
    "pickle":  "Pickle",
}

# Extensions that pandas can profile
_PANDAS_READABLE: set[str] = {"csv", "tsv", "tab", "psv", "xlsx", "xls", "parquet", "feather", "arrow"}

# Columnar formats where pyarrow can read schema + row count from file metadata
# without loading any row data — no size limit needed for these.
_SCHEMA_ONLY: set[str] = {"parquet", "feather", "arrow"}

# Skip profiling CSV/Excel files larger than this (pandas must read bytes to infer types)
_MAX_PROFILE_SIZE_MB = 50


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    if size_bytes >= 1_024:
        return f"{size_bytes / 1_024:.1f} KB"
    return f"{size_bytes} B"


class DataProfilerSurveyor(BaseSurveyor):
    """
    Detects and profiles data files in the project.

    Parameters
    ----------
    project    : Project from registry
    registry   : open ProjectRegistry
    local_path : path to a local clone of the repo root.  When set, forces
                 fresh pandas profiling for each readable data file regardless
                 of whether stored profiles already exist.
    """

    def __init__(
        self,
        project: Project,
        registry: ProjectRegistry,
        local_path: str | Path | None = None,
        surveyed_at: str | None = None,
        scope_locator: str = "",
    ) -> None:
        super().__init__(project, registry)
        self.local_path: Path | None = Path(local_path) if local_path else None
        # See SecurityHygieneSurveyor's identical constructor comment — shared
        # run-timestamp from SurveyOrchestrator (Phase B, D1). Previously
        # unused — this surveyor never persisted a snapshot at all (found
        # while wiring the generic upsert_metric call below).
        self._surveyed_at = surveyed_at or now_utc_iso()
        # Repo scope-narrowing funnel plan (docs/repo-scope-narrowing-funnel.md),
        # D5/D6 — "" (default) = whole-repo, unchanged from every existing
        # caller; a non-empty locator narrows to one cataloged sub-resource
        # (this kind is target_shape="corpus" — a path-prefix filter).
        self._scope_locator = scope_locator

    @property
    def step_name(self) -> str:
        return STEP

    def _record_outcome(self, outcome: StepOutcome, *, total_files: int,
                        total_bytes: int, formats: dict | None = None) -> None:
        """One metric row per run, on every terminal path — including the zeros.

        Before this, the snapshot was written only when data files were found,
        so the trend series simply had no point for a run that found none. A
        gap in a trend is unreadable: it means "no data files", "step not
        selected" and "inventory empty" all at once. Writing the zero with its
        outcome attached distinguishes them.
        """
        try:
            self.registry.upsert_metric(
                self.project.slug, "data_profile",
                {"total_files": total_files, "total_size_bytes": total_bytes},
                detail={"formats": formats or {}, **outcome.as_row()},
                surveyed_at=self._surveyed_at,
                scope_locator=self._scope_locator,
            )
        except Exception as exc:
            log.warning("Could not persist data profile snapshot for %s: %s",
                        self.project.slug, exc)

    # ── public entry point ────────────────────────────────────────────────────

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            rows = self.registry.get_file_inventory_with_sizes(self.project.slug)
            data_files = self._filter_data_files(rows) if rows else []
            if self._scope_locator:
                data_files = [
                    f for f in data_files
                    if path_matches_scope(f["file_path"], self._scope_locator)
                ]

            # Two zeros that used to look identical from the outside: an
            # inventory nobody populated (this step could not run) and a repo
            # that genuinely ships no CSV/Parquet/XLSX (it ran, and the answer
            # is none). Only the second is a fact about the repository.
            outcome = from_upstream_table(
                len(rows), len(data_files),
                empty_table_cause="empty_file_inventory",
                no_match_cause="no_data_files",
                scope_locator=self._scope_locator,
            )
            if not data_files:
                self._record_outcome(outcome, total_files=0, total_bytes=0)
                if outcome.outcome == UNVERIFIED:
                    results.append(
                        RequestForActionAnnotation(
                            check_name="data_profiling",
                            summary="No file inventory found — data profiling skipped",
                            analysis_step=STEP,
                            confidence=100,
                            explanation=(
                                "project_file_inventory is empty, so this run cannot tell "
                                "whether the repo has data files or whether the inventory "
                                "was never built. Run repo_file_inventory (or a Profile "
                                "refresh) first, then re-run."
                            ),
                            action_requested="Run repo_file_inventory to build the file inventory",
                            action_target_name=self.project.slug,
                        )
                    )
                else:
                    # Provable zero: the inventory had rows and none of them were
                    # data files. Said out loud rather than returning nothing,
                    # which reads as "never run".
                    results.append(
                        ResourceMeasureAnnotation(
                            check_name="data_profiling",
                            summary=f"No data files among {len(rows):,} inventoried files",
                            analysis_step=STEP,
                            confidence=100,
                            explanation=(
                                "The repository was scanned and contains no CSV, Parquet, "
                                "XLSX or other recognised data files"
                                + (f" under scope '{self._scope_locator}'."
                                   if self._scope_locator else ".")
                            ),
                            resource_properties={"total_files": 0, "total_size_bytes": 0},
                            json_properties={"source": "project_file_inventory",
                                             **outcome.as_row()},
                        )
                    )
                return results

            # Tier 1: inventory-level format summary
            self._tier1_summary(data_files, results, outcome)

            # Tier 2a: fresh profiling from local clone (explicit --data-path)
            if self.local_path:
                self._tier2_local(data_files, results)
                return results

            # Tier 2b: read pre-computed profiles stored during ingestion
            stored = self.registry.get_data_profiles(self.project.slug)
            if self._scope_locator:
                stored = [
                    p for p in stored
                    if path_matches_scope(p["file_path"], self._scope_locator)
                ]
            if stored:
                self._tier2_stored(stored, results)
            else:
                profilable = [
                    f for f in data_files
                    if Path(f["file_path"]).suffix.lstrip(".").lower() in _PANDAS_READABLE
                ]
                if profilable:
                    results.append(
                        RequestForActionAnnotation(
                            check_name="profiles_not_captured",
                            summary=(
                                f"{len(profilable)} data file(s) could be profiled — "
                                "re-ingest to capture column schemas"
                            ),
                            analysis_step=STEP,
                            confidence=70,
                            explanation=(
                                "No stored profiles found. Column-level schema profiling "
                                "runs automatically during add/refresh while the repo is "
                                "on disk. Run 'project-explorer refresh <slug>' to populate."
                            ),
                            action_requested="Run refresh to populate data profiles",
                            action_target_name=self.project.slug,
                        )
                    )

        except Exception as exc:
            log.exception("DataProfilerSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results

    # ── tier 1 ───────────────────────────────────────────────────────────────

    def _filter_data_files(self, rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            ext = Path(r["file_path"]).suffix.lstrip(".").lower()
            if ext in _DATA_EXTENSIONS:
                out.append({**r, "_format": _DATA_EXTENSIONS[ext], "_ext": ext})
        return out

    def _tier1_summary(self, data_files: list[dict], results: list[Annotation],
                       outcome: StepOutcome) -> None:
        count_by_fmt: dict[str, int] = defaultdict(int)
        size_by_fmt: dict[str, int] = defaultdict(int)
        dirs_by_fmt: dict[str, set] = defaultdict(set)
        total_bytes = 0

        for f in data_files:
            fmt = f["_format"]
            count_by_fmt[fmt] += 1
            size_by_fmt[fmt] += f["file_size_bytes"]
            total_bytes += f["file_size_bytes"]
            parts = Path(f["file_path"]).parts
            dirs_by_fmt[fmt].add(parts[0] if len(parts) > 1 else "(root)")

        fmt_summary = {
            fmt: {
                "count": count_by_fmt[fmt],
                "total_size": _fmt_size(size_by_fmt[fmt]),
                "directories": sorted(dirs_by_fmt[fmt]),
            }
            for fmt in sorted(count_by_fmt, key=lambda k: -count_by_fmt[k])
        }

        results.append(
            ResourceMeasureAnnotation(
                check_name="data_file_summary",
                summary=(
                    f"{len(data_files)} data file(s) across "
                    f"{len(count_by_fmt)} format(s), "
                    f"total {_fmt_size(total_bytes)}"
                ),
                analysis_step=STEP,
                confidence=95,
                resource_properties={
                    "total_data_files": len(data_files),
                    "total_data_size": _fmt_size(total_bytes),
                    "total_data_size_bytes": total_bytes,
                    "formats": fmt_summary,
                },
                json_properties={"source": "project_file_inventory"},
            )
        )

        # Same recorder every terminal path uses, so a run that profiled
        # nothing writes the same row shape as one that profiled thousands —
        # differing only in the numbers and the outcome label.
        self._record_outcome(outcome, total_files=len(data_files),
                             total_bytes=total_bytes, formats=fmt_summary)

    # ── tier 2b: stored profiles ──────────────────────────────────────────────

    def _tier2_stored(self, stored: list[dict], results: list[Annotation]) -> None:
        profiled = [p for p in stored if p.get("row_count") is not None]
        unprofiled = [p for p in stored if p.get("row_count") is None]

        for p in profiled:
            cols: list[dict] = []
            if p.get("schema_json"):
                try:
                    cols = _json.loads(p["schema_json"])
                except Exception:
                    pass

            results.append(
                SchemaAnalysisAnnotation(
                    check_name="data_file_schema",
                    item_key=p["file_path"],
                    summary=(
                        f"{p['file_path']}: "
                        f"{p['row_count']:,} rows × {p['col_count']} columns"
                    ),
                    analysis_step=STEP,
                    confidence=95,
                    schema_name=p["file_path"],
                    schema_type=p["format"],
                    explanation=p.get("null_summary", ""),
                    json_properties={
                        "row_count": p["row_count"],
                        "col_count": p["col_count"],
                        "columns": cols,
                        "file_size": _fmt_size(p["file_size_bytes"]),
                        "profiled_at": p["profiled_at"],
                    },
                    # annotation-linking-plan Phase 2: this per-file schema
                    # annotation is evidence for the Tier-1 aggregate
                    # ResourceMeasureAnnotation `_tier1_summary` appends at
                    # index 0 of THIS run's results list — always index 0,
                    # since `run()` calls `_tier1_summary` before either tier-2
                    # path. Safe as a same-run, list-index signal (see
                    # `Annotation.evidence_of`'s own docstring for why this is
                    # NOT safe to reconstruct cross-run).
                    evidence_of=0,
                )
            )

        if unprofiled:
            log.debug(
                "DataProfilerSurveyor: %d data file(s) stored but not profiled "
                "(too large or unsupported format)", len(unprofiled),
            )

    # ── tier 2a: fresh local profiling ───────────────────────────────────────

    def _tier2_local(self, data_files: list[dict], results: list[Annotation]) -> None:
        try:
            import pandas as pd
        except ImportError:
            self._warn(
                results,
                "pandas not installed — install with: uv add pandas openpyxl pyarrow",
            )
            return

        limit_bytes = _MAX_PROFILE_SIZE_MB * 1_048_576
        for f in data_files:
            if f["_ext"] not in _PANDAS_READABLE:
                continue
            # Columnar formats use pyarrow metadata — no size limit needed.
            # Only CSV/Excel require the size gate (pandas reads bytes to infer types).
            if f["_ext"] not in _SCHEMA_ONLY and f["file_size_bytes"] > limit_bytes:
                results.append(
                    RequestForActionAnnotation(
                        check_name="data_file_too_large",
                        item_key=f["file_path"],
                        summary=f"Data file too large to profile: {f['file_path']} ({_fmt_size(f['file_size_bytes'])})",
                        analysis_step=STEP,
                        confidence=90,
                        explanation=f"Exceeds the {_MAX_PROFILE_SIZE_MB} MB profiling limit for text formats.",
                        action_requested="Profile large data file manually or reduce size",
                        action_target_name=f["file_path"],
                    )
                )
                continue

            local_file = self.local_path / f["file_path"]  # type: ignore[operator]
            if not local_file.exists():
                continue
            try:
                profile = self._profile_file(local_file, f["_ext"], pd)
                if profile:
                    results.append(
                        SchemaAnalysisAnnotation(
                            check_name="data_file_profile",
                            item_key=f["file_path"],
                            summary=f"{f['file_path']}: {profile['row_count']:,} rows × {profile['col_count']} columns",
                            analysis_step=STEP,
                            confidence=95,
                            schema_name=f["file_path"],
                            schema_type=f["_format"],
                            explanation=profile.get("null_summary", ""),
                            json_properties=profile,
                            # Same basis as _tier2_stored above — the aggregate
                            # is always index 0 of this run's results.
                            evidence_of=0,
                        )
                    )
            except Exception as exc:
                log.warning("DataProfilerSurveyor: could not profile %s: %s", f["file_path"], exc)

    # ── shared profiling logic ────────────────────────────────────────────────

    @staticmethod
    def _profile_file(path: Path, ext: str, pd: Any) -> dict | None:
        # Columnar formats: read schema + row count from file metadata only.
        # pyarrow never loads row data for these paths, so no size limit applies.
        if ext == "parquet":
            result = DataProfilerSurveyor._profile_parquet(path)
            if result is not None:
                return result
            # pyarrow not available — fall through to pandas below
        if ext in {"feather", "arrow"}:
            result = DataProfilerSurveyor._profile_arrow(path)
            if result is not None:
                return result

        # Text / binary formats that require pandas to read rows
        if ext in {"csv", "tsv", "tab", "psv"}:
            sep = "\t" if ext in {"tsv", "tab"} else (";" if ext == "psv" else ",")
            try:
                df = pd.read_csv(path, sep=sep, nrows=100_000, low_memory=False)
            except Exception:
                df = pd.read_csv(path, sep=sep, nrows=10_000, encoding="latin-1", low_memory=False)
        elif ext in {"xlsx", "xls"}:
            df = pd.read_excel(path, nrows=100_000)
        elif ext == "parquet":
            df = pd.read_parquet(path)
        elif ext in {"feather", "arrow"}:
            df = pd.read_feather(path)
        else:
            return None

        return DataProfilerSurveyor._summarize_df(df, path)

    @staticmethod
    def _profile_parquet(path: Path) -> dict | None:
        """Read Parquet schema + row count from file metadata — no row data loaded."""
        try:
            import pyarrow.parquet as pq
        except ImportError:
            return None
        try:
            meta = pq.read_metadata(path)
            schema = pq.read_schema(path)
            columns = [
                # null_pct is None, not 0.0. Reading it would mean loading the
                # column data, which is exactly what these metadata-only paths
                # avoid. 0.0 claimed "measured, no nulls" for every column of
                # every parquet/feather file ever profiled -- an unmeasured
                # value rendered as a confident measurement.
                {"name": schema.field(i).name, "dtype": str(schema.field(i).type), "null_pct": None}
                for i in range(len(schema))
            ]
            return {
                "row_count": meta.num_rows,
                "col_count": len(schema),
                "columns": columns[:50],
                "null_summary": "Null rates not read — schema and row count come "
                                "from file metadata, which does not carry them.",
                "file_size": _fmt_size(path.stat().st_size),
            }
        except Exception as exc:
            log.debug("_profile_parquet failed for %s: %s", path, exc)
            return None

    @staticmethod
    def _profile_arrow(path: Path) -> dict | None:
        """Read Arrow/Feather IPC schema + row count — only one column loaded for row count."""
        try:
            import pyarrow as pa
        except ImportError:
            return None
        try:
            with pa.memory_map(str(path), "r") as source:
                reader = pa.ipc.open_file(source)
                schema = reader.schema_arrow
                # Read one column to get row count without loading all data
                first_col = schema.names[0] if schema.names else None
                table = reader.read_all([first_col] if first_col else [])
                row_count = len(table)
            columns = [
                # null_pct is None, not 0.0. Reading it would mean loading the
                # column data, which is exactly what these metadata-only paths
                # avoid. 0.0 claimed "measured, no nulls" for every column of
                # every parquet/feather file ever profiled -- an unmeasured
                # value rendered as a confident measurement.
                {"name": schema.field(i).name, "dtype": str(schema.field(i).type), "null_pct": None}
                for i in range(len(schema))
            ]
            return {
                "row_count": row_count,
                "col_count": len(schema),
                "columns": columns[:50],
                "null_summary": "Null rates not read — schema and row count come "
                                "from file metadata, which does not carry them.",
                "file_size": _fmt_size(path.stat().st_size),
            }
        except Exception as exc:
            log.debug("_profile_arrow failed for %s: %s", path, exc)
            return None

    @staticmethod
    def _summarize_df(df: Any, path: Path) -> dict:
        """Build a profile dict from a loaded pandas DataFrame."""
        columns: list[dict] = []
        high_null_cols: list[str] = []
        for col in df.columns:
            # .mean() on an empty Series is NaN, not 0 — and NaN isn't valid
            # JSON (json.dumps allows it by default, but Starlette's
            # JSONResponse does not, so it would 500 when read back via the API).
            null_rate = df[col].isna().mean() if len(df) else 0.0
            columns.append({
                "name": str(col),
                "dtype": str(df[col].dtype),
                "null_pct": round(null_rate * 100, 1),
            })
            if null_rate > 0.5:
                high_null_cols.append(str(col))
        null_summary = ""
        if high_null_cols:
            null_summary = (
                f"{len(high_null_cols)} column(s) >50% null: "
                + ", ".join(high_null_cols[:5])
            )
        return {
            "row_count": len(df),
            "col_count": len(df.columns),
            "columns": columns[:50],
            "null_summary": null_summary,
            "file_size": _fmt_size(path.stat().st_size),
        }
