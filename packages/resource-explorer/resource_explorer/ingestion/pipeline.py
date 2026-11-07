"""Orchestrates full ingestion for a project across all selected collection types."""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import Progress

from resource_explorer.configdata.collection_config import CollectionType
from resource_explorer.ingestion.vendored import is_vendored, is_vendored_abs
from resource_explorer.vector_store_pg import MultiCollectionStore
from resource_explorer.registry import ProjectRegistry, ProjectStatus
from resource_explorer.ingestion.vendored import is_vendored_abs

log = logging.getLogger(__name__)


@dataclass
class ProfileResult:
    """Return value of IngestionPipeline.refresh_profile()."""
    file_count: int = 0
    symbol_count: int = 0


class IngestionPipeline:
    """
    Full ingestion flow for a project:
      1. Download repo as a single zipball (1 GitHub API call)
      2. For each CollectionType: parse files from disk → chunk → filter → embed → insert
      3. Update registry with last_indexed_at and active collections

    Downloading via zipball avoids per-file API calls, which hit rate limits on
    large repos (e.g. ODPI/egeria has thousands of files).

    See IncrementalIndexer for the partial-update (changed files only) path.
    """

    def __init__(self) -> None:
        self.registry = ProjectRegistry()
        self.store = MultiCollectionStore()
        self.console = Console()

    # Collection types that can ingest from extra_docs_paths
    _DOC_CTYPES: frozenset[str] = frozenset({
        "markdown_docs", "web_docs", "api_reference", "examples", "pdfs",
    })

    def run(
        self,
        resource_slug: str,
        github_url: str,
        collection_types: list[CollectionType],
        subproject_path: str | None = None,
        extra_docs_paths: list[str] | None = None,
        local_path: str | None = None,
    ) -> None:
        from resource_explorer.github.client import GitHubClient

        self.registry.update_status(resource_slug, ProjectStatus.INDEXING)
        active_collections: list[str] = []
        extra_docs_paths = extra_docs_paths or []

        client = GitHubClient()
        repo = client.get_repo(github_url)

        try:
            if local_path:
                full_root = Path(local_path).expanduser().resolve()
                if not full_root.is_dir():
                    raise ValueError(f"--from-local path is not a directory: {full_root}")
                self.console.print(f"[cyan]Using local path {full_root}[/cyan]")
                code_root, resolved_extra = self._setup_roots(full_root, subproject_path, extra_docs_paths)
                file_count, loc = self._ingest_from_root(
                    resource_slug, repo, collection_types, code_root, resolved_extra, active_collections
                )
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    self.console.print("[cyan]Downloading repository...[/cyan]")
                    if extra_docs_paths:
                        # Must download full repo to reach paths outside the subproject
                        extracted = client.download_zipball(repo, Path(tmp))
                        self.console.print(f"[cyan]Extracted to {extracted.name}[/cyan]")
                        code_root, resolved_extra = self._setup_roots(
                            extracted, subproject_path, extra_docs_paths
                        )
                    else:
                        # Subpath filter saves bandwidth; download_zipball returns the subdir directly
                        extracted = client.download_zipball(repo, Path(tmp), subproject_path or None)
                        self.console.print(f"[cyan]Extracted to {extracted.name}[/cyan]")
                        code_root = extracted
                        resolved_extra = []
                    file_count, loc = self._ingest_from_root(
                        resource_slug, repo, collection_types, code_root, resolved_extra, active_collections
                    )

            self.registry.update_indexed_at(resource_slug, active_collections)
            self.registry.update_ingestion_stats(resource_slug, file_count, loc)
            self.registry.update_status(resource_slug, ProjectStatus.ACTIVE)
            self.console.print(
                f"[green]Ingestion complete. {len(active_collections)} collections populated.[/green]"
            )

        except Exception as e:
            self.registry.update_status(resource_slug, ProjectStatus.ERROR, str(e))
            raise

    def _setup_roots(
        self,
        full_root: Path,
        subproject_path: str | None,
        extra_docs_paths: list[str],
    ) -> tuple[Path, list[tuple[str, Path]]]:
        """Derive (code_root, resolved_extra) from a repo root (downloaded or local)."""
        if subproject_path:
            code_root = full_root / subproject_path
            resolved_extra = [(p, full_root / p) for p in extra_docs_paths]
        else:
            code_root = full_root
            resolved_extra = []
        return code_root, resolved_extra

    def _ingest_from_root(
        self,
        resource_slug: str,
        repo,
        collection_types: list[CollectionType],
        code_root: Path,
        resolved_extra: list[tuple[str, Path]],
        active_collections: list[str],
    ) -> tuple[int, int]:
        """Run the ingestion loop over collection_types; return (file_count, loc)."""
        with Progress(console=self.console) as progress:
            task = progress.add_task("Ingesting...", total=len(collection_types))
            for ctype in collection_types:
                collection_name = f"{resource_slug}_{ctype.name}"
                extra = resolved_extra if ctype.name in self._DOC_CTYPES else []
                count = self._ingest_collection(
                    repo, resource_slug, collection_name, ctype, code_root,
                    extra_paths=extra,
                )
                if count > 0:
                    active_collections.append(collection_name)
                progress.advance(task)

        file_count, loc = self._count_repo_stats(code_root)
        self._record_line_census(resource_slug, code_root)
        self._store_file_inventory(resource_slug, code_root, repo=repo)
        self._parse_dependencies(resource_slug, code_root)
        self._profile_data_files(resource_slug, code_root)
        self._read_dataset_descriptors(resource_slug, code_root)
        self._parse_ci_workflows(resource_slug, code_root)
        self._parse_supply_chain(resource_slug, code_root)
        self._parse_repo_conventions(resource_slug, code_root)
        return file_count, loc

    def _ingest_collection(
        self,
        repo,
        resource_slug: str,
        collection_name: str,
        ctype: CollectionType,
        local_root: Path | None = None,
        extra_paths: list[tuple[str, Path]] | None = None,
        changed_files: set[str] | None = None,
    ) -> int:
        """
        Ingest one collection type. local_root is the extracted repo directory;
        if None (incremental single-collection re-index), downloads on the spot.
        extra_paths is a list of (display_prefix, abs_path) for repo paths outside
        the code subproject; used by doc/example collections only.

        changed_files, when given, restricts insertion to chunks whose
        metadata["file_path"] is in the set — every parser below still walks
        the full local_root (cheap, disk-based; not worth threading a file
        filter through each one), but only the changed files' chunks actually
        get written. Callers pair this with a delete_by_metadata() targeting
        the same file set first, so the net effect is a real per-file
        replace instead of insert-only drift (migration plan decision D4).
        None (the default) ingests everything, unchanged from before.
        """
        from resource_explorer.ingestion.data_prep import DataPrep

        _code_dispatch = {
            "python_code": self._ingest_code,
            "javascript_code": self._ingest_code,
            "java_code": self._ingest_code,
            "go_code": self._ingest_code,
        }
        _doc_dispatch = {
            "markdown_docs": self._ingest_markdown,
            "web_docs": self._ingest_web_docs,
            "api_reference": self._ingest_api_specs,
            "examples": self._ingest_examples,
            "pdfs": self._ingest_pdfs,
        }
        _file_dispatch = {**_code_dispatch, **_doc_dispatch}
        extra_paths = extra_paths or []

        if ctype.name != "release_notes" and ctype.name not in _file_dispatch:
            return 0

        # release_notes use the GitHub releases API, not file content
        if ctype.name == "release_notes":
            chunks = self._ingest_releases(repo, resource_slug, ctype)
        else:
            if local_root is None:
                # Fallback for incremental single-collection calls
                from resource_explorer.github.client import GitHubClient
                client = GitHubClient()
                with tempfile.TemporaryDirectory() as tmp:
                    local_root = client.download_zipball(repo, Path(tmp))
                    chunks = _file_dispatch[ctype.name](local_root, resource_slug, ctype)
            elif ctype.name in self._DOC_CTYPES and extra_paths:
                chunks = _doc_dispatch[ctype.name](local_root, resource_slug, ctype, extra_paths)
            else:
                chunks = _file_dispatch[ctype.name](local_root, resource_slug, ctype)

        chunks = DataPrep().filter(chunks)
        if changed_files is not None:
            chunks = [c for c in chunks if c.metadata.get("file_path") in changed_files]
        if not chunks:
            return 0

        return self.store.insert(collection_name, [c.text for c in chunks],
                                 [c.metadata for c in chunks])

    def refresh_profile(
        self,
        resource_slug: str,
        github_url: str,
        collections: list[str],
        subproject_path: str | None = None,
        include_symbols: bool = False,
        client=None,
        repo=None,
    ) -> "ProfileResult":
        """
        Download the repo ONCE and refresh its file-inventory + data-profile
        tables, optionally also its code symbols — without touching pgvector.

        Combines what used to be two independently-downloading paths
        (IncrementalIndexer._run_profile_only + the old extract_symbols_only)
        into a single zipball download. include_symbols=False is the fast,
        common "what's actually in this repo" pass (the "Coarse Profile
        Survey" Survey Definition, and the scheduler's profile job);
        include_symbols=True additionally refreshes project_code_symbols,
        which is what Assessment's SecurityHygieneSurveyor/DocumentationSurveyor
        and Analysis's ApiStructureSurveyor depend on (previously only ever
        populated by the original full ingestion, never by any refresh path).

        client/repo are optional pass-throughs so a caller that already has
        both (IncrementalIndexer.refresh() already fetched `repo` to compare
        commit SHAs) doesn't pay for a second GitHubClient + get_repo() call.
        """
        from resource_explorer.github.client import GitHubClient
        from resource_explorer.configdata.collection_config import COLLECTION_TYPES

        client = client or GitHubClient()
        repo = repo if repo is not None else client.get_repo(github_url)
        symbol_count = 0
        file_count = 0

        self.console.print("[cyan]Downloading repository for profiling...[/cyan]")
        # D6.7 (docs/unified-survey-execution-model-plan.md) — shared with
        # repo_survey_definition_adapter.py's _acquire_zipball_root; one real
        # implementation of "download a zipball into a tempdir", not two.
        with client.zipball_root(repo, subproject_path) as local_root:
            file_count = self._store_file_inventory(resource_slug, local_root, repo=repo, client=client)
            self._profile_data_files(resource_slug, local_root)
            self._read_dataset_descriptors(resource_slug, local_root)
            # The census belongs on the CHEAP path too. It needs the files on
            # disk and nothing else, and refresh_profile already has them
            # extracted — requiring a full RAG re-ingest (with re-embedding)
            # to refresh a line count would price it out of ever being
            # refreshed, which is how ingestion_lines_of_code came to be NULL
            # for repos indexed any other way.
            self._record_line_census(resource_slug, local_root)
            self._parse_ci_workflows(resource_slug, local_root)
            self._parse_supply_chain(resource_slug, local_root)
            self._parse_repo_conventions(resource_slug, local_root)

            if include_symbols:
                from resource_explorer.ingestion.code_symbol_extractor import CodeSymbolExtractor

                code_ctypes = [
                    COLLECTION_TYPES[name]
                    for name in self._CTYPE_LANGUAGE
                    if name in COLLECTION_TYPES and f"{resource_slug}_{name}" in collections
                ]
                extractor = CodeSymbolExtractor()
                for ctype in code_ctypes:
                    language = self._CTYPE_LANGUAGE[ctype.name]
                    self.registry.clear_code_symbols(resource_slug, language)
                    symbols = []
                    for path, content in self._local_files(local_root, ctype.file_extensions):
                        symbols.extend(extractor.extract(path, content, resource_slug, language))
                    if symbols:
                        self.registry.upsert_code_symbols(resource_slug, symbols)
                        symbol_count += len(symbols)
                        self.console.print(f"  [dim]{ctype.name}: {len(symbols)} symbols[/dim]")

        return ProfileResult(file_count=file_count, symbol_count=symbol_count)

    def extract_symbols_only(self, resource_slug: str, github_url: str, collections: list[str]) -> int:
        """
        Download the repo and extract code symbols to SQLite without touching pgvector.

        Used to backfill symbols for projects that were indexed before code intelligence
        was added, and by 'refresh --symbols' when code hasn't changed.
        Returns the total number of symbols extracted.

        Thin wrapper over refresh_profile(include_symbols=True) — kept as its
        own method so the CLI's 'refresh --symbols' call site and its
        `-> int` contract are unchanged.
        """
        return self.refresh_profile(
            resource_slug, github_url, collections, include_symbols=True,
        ).symbol_count

    def _parse_dependencies(self, resource_slug: str, local_root: Path) -> None:
        try:
            from resource_explorer.ingestion.dependency_parser import DependencyParser
            deps = DependencyParser().parse(local_root, resource_slug)
            if deps:
                self.registry.upsert_dependencies(resource_slug, deps)
                self.console.print(f"[dim]Dependencies: {len(deps)} entries indexed.[/dim]")
        except Exception as exc:
            self.console.print(f"[dim]Dependency parsing skipped: {exc}[/dim]")

    def _parse_ci_workflows(self, resource_slug: str, local_root: Path) -> None:
        """Assessment expansion plan B4 — mirrors _parse_dependencies'
        pattern exactly, but writes straight into the generic
        project_analysis_findings table (kind="ci_quality") rather than a
        dedicated table, since CiQualitySurveyor only ever reads these
        findings back (same read-only-at-survey-time relationship
        DependencySurveyor has with project_dependencies) — there's no
        separate "latest state" shape to maintain beyond the findings
        themselves. Called from both full ingestion and refresh_profile()
        (unlike _parse_dependencies, which today only runs at full
        ingestion) so "Refresh & profile" keeps ci_quality current too."""
        try:
            from resource_explorer.ingestion.ci_workflow_parser import CiWorkflowParser
            findings = CiWorkflowParser().parse(local_root)
            if findings:
                self.registry.upsert_finding(
                    resource_slug, "ci_quality", findings,
                    surveyed_at=datetime.utcnow().isoformat(),
                )
                self.console.print(f"[dim]CI quality: {len(findings)} check(s) evaluated.[/dim]")
        except Exception as exc:
            self.console.print(f"[dim]CI workflow parsing skipped: {exc}[/dim]")

    def _parse_supply_chain(self, resource_slug: str, local_root: Path) -> None:
        """Supply-chain signals from the same workflow YAML, its own finding kind.

        Deliberately not folded into ci_quality. That analysis answers "does CI
        actually run anything"; these answer "can CI be trusted with a token" —
        different questions, different cards, and merging them would make one
        card's score move for reasons belonging to the other.
        """
        try:
            from resource_explorer.ingestion.supply_chain_parser import SupplyChainParser
            findings = SupplyChainParser().parse(local_root)
            if findings:
                self.registry.upsert_finding(
                    resource_slug, "supply_chain", findings,
                    surveyed_at=datetime.utcnow().isoformat(),
                )
                self.console.print(
                    f"[dim]Supply chain: {len(findings)} check(s) evaluated.[/dim]")
        except Exception as exc:
            self.console.print(f"[dim]Supply-chain parsing skipped: {exc}[/dim]")

    def _parse_repo_conventions(self, resource_slug: str, local_root: Path) -> None:
        """Discovery-tier convention signals (Part 2, security_policy_content/
        automated_build/deployment_docker/catalog_info/doc_breadth) — same
        pattern as _parse_ci_workflows: parsed once here (already-downloaded
        zipball), written to the generic findings table, read back read-only
        at survey time by RepoConventionsSurveyor. Called from both full
        ingestion and refresh_profile()."""
        try:
            from resource_explorer.ingestion.repo_conventions_parser import RepoConventionsParser
            findings = RepoConventionsParser().parse(local_root)
            if findings:
                self.registry.upsert_finding(
                    resource_slug, "repo_conventions", findings,
                    surveyed_at=datetime.utcnow().isoformat(),
                )
                self.console.print(f"[dim]Repo conventions: {len(findings)} check(s) evaluated.[/dim]")
        except Exception as exc:
            self.console.print(f"[dim]Repo conventions parsing skipped: {exc}[/dim]")

    # ── local file helpers ────────────────────────────────────────────────────

    _TEXT_SUFFIXES: frozenset[str] = frozenset({
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
        ".rb", ".cpp", ".c", ".h", ".cs", ".swift", ".kt", ".scala",
        ".r", ".md", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml",
        ".html", ".css", ".sh", ".bash", ".sql", ".xml",
    })

    def _record_line_census(self, resource_slug: str, code_root: Path) -> None:
        """Per-language code/comment/docstring/blank counts (design doc D1).

        This is the honest replacement for `ingestion_lines_of_code`, which
        counts every newline in every text-suffixed file and is named as
        though it counted code — 1,118,195 against 156,297 real Python code
        lines on egeria-python, 14%.

        Written here rather than in a surveyor because this is the only place
        with the files on disk. Persisted as a `code_volume` metric so the
        surveyor and the question layer can read it without a second
        download, the same arrangement `api_structure` already has with
        `project_code_symbols`.

        Best-effort: a census failure must not fail an ingestion that
        otherwise succeeded.
        """
        try:
            from resource_explorer.ingestion.line_census import census_tree, code_line_total

            census = census_tree(code_root)
            if not census:
                return
            by_language = {lang: c.as_dict() for lang, c in census.items()}
            code_total = code_line_total(census)
            self.registry.upsert_metric(
                resource_slug, "code_volume",
                {
                    "code_lines": code_total,
                    "comment_lines": sum(c.comment for c in census.values()),
                    "docstring_lines": sum(c.docstring for c in census.values()),
                    "blank_lines": sum(c.blank for c in census.values() if not c.text_only),
                },
                detail={
                    "by_language": by_language,
                    # Named explicitly: their lines are counted but never
                    # categorised, and their `code: 0` must not be read as
                    # "no code" -- nothing was categorised at all.
                    "text_only_languages": sorted(
                        lang for lang, c in census.items() if c.text_only),
                },
            )
        except Exception as exc:
            log.warning("Line census failed for %s: %s", resource_slug, exc)

    def _count_repo_stats(self, local_root: Path) -> tuple[int, int]:
        """Return (file_count, line_count) by walking the extracted repo directory."""
        file_count = 0
        line_count = 0
        for p in local_root.rglob("*"):
            if not p.is_file() or is_vendored_abs(p, local_root):
                continue
            file_count += 1
            if p.suffix.lower() in self._TEXT_SUFFIXES:
                try:
                    line_count += p.read_bytes().count(b"\n")
                except Exception:
                    pass
        return file_count, line_count

    def _store_file_inventory(
        self, resource_slug: str, local_root: Path, repo=None, client=None,
    ) -> int:
        """Persist every file path (relative to local_root) and its size to SQLite.

        repo is an optional PyGithub Repository — when given, this also
        fetches real git mode bits (100644/100755/120000) via
        GitHubClient.list_file_modes() and stores them alongside each path
        (Assessment sub-resource cataloging plan, D9 Tier 1). Best-effort:
        a failed/omitted mode fetch just leaves file_mode='' for every row,
        never fails the inventory write itself. `client` reuses an
        already-constructed GitHubClient when the caller has one (matching
        refresh_profile()'s existing "don't pay for a second client" contract
        — see test_reuses_passed_in_client_and_repo_no_new_githubclient);
        only constructed fresh here if no repo/client relationship already
        exists to reuse.

        Returns the number of paths stored (0 on skip/failure). The return
        value was unused by every existing caller prior to refresh_profile()
        — adding it is additive, not a behavior change for them.
        """
        paths_with_sizes: list[tuple[str, int]] = []
        for p in local_root.rglob("*"):
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except Exception:
                size = 0
            paths_with_sizes.append((str(p.relative_to(local_root)), size))

        modes_by_path: dict[str, str] = {}
        if repo is not None:
            try:
                if client is None:
                    from resource_explorer.github.client import GitHubClient
                    client = GitHubClient()
                result = client.list_file_modes(repo)
                if isinstance(result, dict):
                    modes_by_path = result
            except Exception:
                pass

        try:
            self.registry.upsert_file_inventory(resource_slug, paths_with_sizes, modes_by_path)
            self.console.print(
                f"[dim]File inventory: {len(paths_with_sizes)} paths stored.[/dim]"
            )
            return len(paths_with_sizes)
        except Exception as exc:
            self.console.print(f"[dim]File inventory skipped: {exc}[/dim]")
            return 0

    def _read_dataset_descriptors(self, resource_slug: str, local_root: Path) -> None:
        """Read the repository's dataset descriptors (datapackage.json, Croissant, CITATION.cff, a README DOI or a
        Hugging Face card) while the tree is on disk and store them as `dataset_descriptor` findings. No network call:
        the DOI is resolved by the import, not here. A tree with none is stored as that fact."""
        from resource_explorer.surveyors.sub_surveyors.dataset_descriptor import store_descriptors

        try:
            found = store_descriptors(self.registry, resource_slug, local_root)
            self.console.print(f"[dim]Dataset descriptors: {len(found)} found.[/dim]")
        except Exception as exc:
            self.console.print(f"[dim]Dataset descriptors skipped: {exc}[/dim]")

    def _profile_data_files(self, resource_slug: str, local_root: Path) -> None:
        """Profile CSV/XLSX/Parquet files while the repo is still on disk.

        Results are stored in project_data_profiles so DataProfilerSurveyor can
        emit column-level schema annotations without needing a local clone at
        survey time.  Skips files larger than 50 MB and silently degrades if
        pandas or optional readers are not installed.
        """
        from resource_explorer.surveyors.sub_surveyors.data_profiler import (
            _DATA_EXTENSIONS,
            _MAX_PROFILE_SIZE_MB,
            _PANDAS_READABLE,
            _SCHEMA_ONLY,
            DataProfilerSurveyor,
        )

        try:
            import pandas as pd
        except ImportError:
            self.console.print("[dim]Data profiling skipped (pandas not installed).[/dim]")
            return

        profiles: list[dict] = []
        limit_bytes = _MAX_PROFILE_SIZE_MB * 1_048_576

        for p in local_root.rglob("*"):
            if not p.is_file() or is_vendored_abs(p, local_root):
                continue
            ext = p.suffix.lstrip(".").lower()
            if ext not in _DATA_EXTENSIONS:
                continue
            try:
                size = p.stat().st_size
            except Exception:
                size = 0

            fmt = _DATA_EXTENSIONS[ext]
            profile: dict = {
                "file_path": str(p.relative_to(local_root)),
                "format": fmt,
                "file_size_bytes": size,
            }

            size_ok = size > 0 and (ext in _SCHEMA_ONLY or size <= limit_bytes)
            if ext in _PANDAS_READABLE and size_ok:
                try:
                    result = DataProfilerSurveyor._profile_file(p, ext, pd)
                    if result:
                        import json
                        profile.update({
                            "row_count": result["row_count"],
                            "col_count": result["col_count"],
                            "schema_json": json.dumps(result["columns"]),
                            "null_summary": result.get("null_summary", ""),
                        })
                except Exception as exc:
                    self.console.print(f"[dim]Profile skipped for {p.name}: {exc}[/dim]")

            profiles.append(profile)

        if profiles:
            try:
                self.registry.store_data_profiles(resource_slug, profiles)
                readable = sum(1 for p in profiles if p.get("row_count") is not None)
                self.console.print(
                    f"[dim]Data profiles: {len(profiles)} data file(s) found"
                    + (f", {readable} profiled (row/column schema)." if readable else ".")
                    + "[/dim]"
                )
            except Exception as exc:
                self.console.print(f"[dim]Data profiles skipped: {exc}[/dim]")

    def _local_files(self, local_root: Path, extensions: list[str]) -> list[tuple[str, str]]:
        """Walk the extracted repo and return (relative_path, content) for matching files."""
        results = []
        for p in local_root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in extensions:
                continue
            # Somebody else's library checked in is not this repository's
            # code: not its symbols, not its chunks, not a citation.
            if is_vendored_abs(p, local_root):
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                results.append((str(p.relative_to(local_root)), content))
            except Exception:
                pass
        return results

    def _local_files_for_paths(
        self,
        extra: list[tuple[str, Path]],
        extensions: list[str],
    ) -> list[tuple[str, str]]:
        """
        Return (display_path, content) for extra_docs_paths entries.
        Each entry is (display_prefix, abs_path) where abs_path may be a file or directory.
        """
        results = []
        for display_prefix, abs_path in extra:
            if abs_path.is_file():
                if abs_path.suffix.lower() in extensions:
                    try:
                        content = abs_path.read_text(encoding="utf-8", errors="ignore")
                        results.append((display_prefix, content))
                    except Exception:
                        pass
            elif abs_path.is_dir():
                for f in abs_path.rglob("*"):
                    # An extra path lives outside local_root; vendored-ness is
                    # relative to the extra directory itself. (Was `local_root`,
                    # unbound here -- a NameError on any extra docs directory.)
                    if not f.is_file() or f.suffix.lower() not in extensions or is_vendored_abs(f, abs_path):
                        continue
                    try:
                        content = f.read_text(encoding="utf-8", errors="ignore")
                        rel = display_prefix.rstrip("/") + "/" + str(f.relative_to(abs_path))
                        results.append((rel, content))
                    except Exception:
                        pass
        return results

    def _report_tree_result(self, result, resource_slug: str) -> None:
        """Surface anything other than a clean run or a disabled feature.

        console, not logging: this module reports through rich throughout, and
        a logger here would be the only one in the file.
        """
        if result.near_empty:
            # The only reliable way to learn OCR was needed: Docling reports no
            # error for a page it could not read.
            self.console.print(
                f"[yellow]{result.near_empty} artifact(s) in {resource_slug} "
                f"parsed to almost no text — likely rasterised or scanned. "
                f"Enable OCR (PDF_OCR_ENABLED=true) to read them.[/yellow]"
            )
        if result.status in ("disabled", "stored"):
            return
        self.console.print(
            f"[yellow]artifact trees for {resource_slug}: {result.status} "
            f"(stored={result.stored} skipped={result.skipped})"
            f"{' — ' + result.reason if result.reason else ''}[/yellow]"
        )

    # ── per-collection-type ingestors ─────────────────────────────────────────

    _CTYPE_LANGUAGE: dict[str, str] = {
        "python_code": "python",
        "javascript_code": "javascript",
        "java_code": "java",
        "go_code": "go",
    }

    def _ingest_code(self, local_root: Path, resource_slug: str, ctype: CollectionType) -> list:
        from resource_explorer.ingestion.code_parser import CodeParser
        from resource_explorer.ingestion.code_symbol_extractor import CodeSymbolExtractor

        language = self._CTYPE_LANGUAGE.get(ctype.name, "")
        parser = CodeParser(ctype.chunk_size, ctype.chunk_overlap)
        extractor = CodeSymbolExtractor() if language else None

        # Clear stale symbols for this language before re-ingesting
        if language:
            self.registry.clear_code_symbols(resource_slug, language)

        chunks = []
        all_symbols = []
        walked: list[tuple[str, str]] = []
        for path, content in self._local_files(local_root, ctype.file_extensions):
            walked.append((path, content))
            chunks.extend(parser.parse(path, content, resource_slug))
            if extractor:
                all_symbols.extend(extractor.extract(path, content, resource_slug, language))

        if all_symbols:
            self.registry.upsert_code_symbols(resource_slug, all_symbols)

        # Containment trees, off by default. Distinct from the symbols above:
        # a symbol table answers what is declared and where it is referenced,
        # a tree answers what contains what so a unit can be dropped or
        # summarised coherently. Same walk, no second read.
        if language:
            from resource_explorer.ingestion.artifact_tree_sink import build_code_trees
            self._report_tree_result(
                build_code_trees(walked, resource_slug, language), resource_slug,
            )

        return chunks

    def _ingest_markdown(
        self, local_root: Path, resource_slug: str, ctype: CollectionType,
        extra_paths: list[tuple[str, Path]] | None = None,
    ) -> list:
        from resource_explorer.ingestion.doc_parser import DocParser
        parser = DocParser(ctype.chunk_size, ctype.chunk_overlap)
        chunks = []
        walked: list[tuple[str, str]] = []
        for path, content in self._local_files(local_root, ctype.file_extensions):
            walked.append((path, content))
            chunks.extend(parser.parse_markdown(content, path, resource_slug))
        for path, content in self._local_files_for_paths(extra_paths or [], ctype.file_extensions):
            walked.append((path, content))
            chunks.extend(parser.parse_markdown(content, path, resource_slug))

        # Containment trees, off by default (ARTIFACT_TREE_ENABLED). Built from
        # the SAME walk rather than a second read, and in addition to the chunks
        # above, never instead of them -- see artifact_tree_sink's docstring.
        # Fail-soft by construction: this cannot reduce what gets ingested.
        from resource_explorer.ingestion.artifact_tree_sink import build_trees
        self._report_tree_result(
            build_trees(walked, resource_slug, kind="markdown"), resource_slug,
        )

        return chunks

    def _ingest_web_docs(
        self, local_root: Path, resource_slug: str, ctype: CollectionType,
        extra_paths: list[tuple[str, Path]] | None = None,
    ) -> list:
        import re
        from resource_explorer.ingestion.doc_parser import DocChunk, DocParser
        parser = DocParser(ctype.chunk_size, ctype.chunk_overlap)
        chunks = []
        all_files = (
            self._local_files(local_root, ctype.file_extensions)
            + self._local_files_for_paths(extra_paths or [], ctype.file_extensions)
        )
        for path, content in all_files:
            text = re.sub(r"<[^>]+>", " ", content)
            text = re.sub(r"\s+", " ", text).strip()
            for chunk_text in parser._fixed_window(text):
                chunks.append(DocChunk(
                    text=chunk_text,
                    metadata={"file_path": path, "project_slug": resource_slug, "type": "web"},
                ))

        # Trees from the ORIGINAL markup, not the tag-stripped text above.
        # Stripping is right for retrieval and lossy for structure: it throws
        # away the h1-h6 hierarchy the page actually declares, which is exactly
        # what a containment tree is for.
        from resource_explorer.ingestion.artifact_tree_sink import build_trees
        self._report_tree_result(
            build_trees(all_files, resource_slug, kind="html"), resource_slug,
        )

        return chunks

    def _ingest_api_specs(
        self, local_root: Path, resource_slug: str, ctype: CollectionType,
        extra_paths: list[tuple[str, Path]] | None = None,
    ) -> list:
        from resource_explorer.ingestion.api_parser import APIParser
        parser = APIParser()
        chunks = []
        all_files = (
            self._local_files(local_root, ctype.file_extensions)
            + self._local_files_for_paths(extra_paths or [], ctype.file_extensions)
        )
        for path, content in all_files:
            parsed = parser.parse(path, content, resource_slug)
            if parsed:
                chunks.extend(parsed)
        return chunks

    def _ingest_examples(
        self, local_root: Path, resource_slug: str, ctype: CollectionType,
        extra_paths: list[tuple[str, Path]] | None = None,
    ) -> list:
        from resource_explorer.ingestion.code_parser import CodeParser
        from resource_explorer.ingestion.notebook_parser import NotebookParser
        import os
        code_parser = CodeParser(ctype.chunk_size, ctype.chunk_overlap)
        nb_parser = NotebookParser()
        chunks = []
        all_files = (
            self._local_files(local_root, ctype.file_extensions)
            + self._local_files_for_paths(extra_paths or [], ctype.file_extensions)
        )
        for path, content in all_files:
            if path.endswith(".ipynb"):
                with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False) as f:
                    f.write(content)
                    tmp = f.name
                try:
                    chunks.extend(nb_parser.parse(tmp, resource_slug))
                finally:
                    os.unlink(tmp)
            else:
                chunks.extend(code_parser.parse(path, content, resource_slug))
        return chunks

    def _ingest_pdfs(
        self, local_root: Path, resource_slug: str, ctype: CollectionType,
        extra_paths: list[tuple[str, Path]] | None = None,
    ) -> list:
        from resource_explorer.ingestion.doc_parser import DocParser
        parser = DocParser(ctype.chunk_size, ctype.chunk_overlap)
        chunks = []
        # (display_path, converted DoclingDocument). The display path keys the
        # artifact: an absolute path is a property of this checkout, not of the
        # document, and would make the same PDF a different artifact per host.
        converted: list[tuple[str, object]] = []

        # One converter for the whole collection, and one conversion per file.
        # Both matter: DocumentConverter() was being constructed per file, and
        # the chunker and the tree builder each used to convert independently,
        # so a PDF-heavy repo paid for conversion twice over.
        _converter = None

        def _convert(path_str: str):
            nonlocal _converter
            if _converter is None:
                from resource_explorer.ingestion.doc_parser import build_pdf_converter
                _converter = build_pdf_converter()
            return _converter.convert(path_str).document

        def _handle(display: str, abs_str: str) -> None:
            try:
                document = _convert(abs_str)
            except Exception:
                # Unchanged from before: an unconvertible PDF is skipped, and
                # now it yields neither chunks nor a tree rather than half of
                # each.
                return
            converted.append((display, document))
            try:
                chunks.extend(parser.parse_pdf(abs_str, resource_slug, document=document))
            except Exception:
                pass

        for path in local_root.rglob("*.pdf"):
            if is_vendored_abs(path, local_root):
                continue
            _handle(str(path.relative_to(local_root)), str(path))
        for display, abs_path in (extra_paths or []):
            if abs_path.is_file() and abs_path.suffix.lower() == ".pdf":
                _handle(f"{display}/{abs_path.name}", str(abs_path))
            elif abs_path.is_dir():
                for pdf in abs_path.rglob("*.pdf"):
                    if is_vendored_abs(pdf, abs_path):   # relative to the extra dir, not local_root
                        continue
                    _handle(f"{display}/{pdf.relative_to(abs_path)}", str(pdf))

        from resource_explorer.ingestion.artifact_tree_sink import (
            build_pdf_trees_from_documents,
        )
        self._report_tree_result(
            build_pdf_trees_from_documents(converted, resource_slug), resource_slug,
        )

        return chunks

    def _ingest_releases(self, repo, resource_slug: str, ctype: CollectionType) -> list:
        from resource_explorer.ingestion.doc_parser import DocChunk, DocParser
        parser = DocParser(ctype.chunk_size, ctype.chunk_overlap)
        chunks = []
        try:
            for release in repo.get_releases():
                body = release.body or ""
                if not body.strip():
                    continue
                date_str = (
                    release.published_at.strftime("%Y-%m-%d")
                    if release.published_at else ""
                )
                header = f"## {release.tag_name}" + (f" ({date_str})" if date_str else "")
                text = f"{header}\n\n{body}"
                for chunk_text in parser._fixed_window(text):
                    chunks.append(DocChunk(
                        text=chunk_text,
                        metadata={
                            "project_slug": resource_slug,
                            "tag": release.tag_name,
                            "published_at": release.published_at.isoformat() if release.published_at else "",
                            "type": "release_notes",
                        },
                    ))
        except Exception:
            pass
        return chunks
