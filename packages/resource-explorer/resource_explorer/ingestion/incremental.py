"""Incremental indexer — re-indexes only collection types affected by changed files."""
from __future__ import annotations

from enum import Enum
from pathlib import PurePath

from resource_explorer.github.client import GitHubClient
from resource_explorer.ingestion.pipeline import IngestionPipeline
from resource_explorer.vector_store_pg import MultiCollectionStore
from resource_explorer.index_state import has_own_index
from resource_explorer.registry import Project, ProjectRegistry, ProjectStatus


class RefreshResult(str, Enum):
    """What `IncrementalIndexer.refresh` did, so callers stop inferring it from side effects."""

    INDEXED = "indexed"                      # re-embedded one or more of the project's own collections
    NO_CHANGES = "no_changes"                # latest commit equals the stored one
    NOT_YET_INDEXED = "not_yet_indexed"      # no own collection: nothing to refresh, SHA left alone
    NOTHING_AFFECTED = "nothing_affected"    # changed files touch none of the project's own collection types


class IncrementalIndexer:
    """
    Commit-diff based incremental update strategy:
      1. Fetch latest commit SHA from GitHub
      2. Compare against stored last_commit_sha in registry
      3. git diff --name-only last_sha..HEAD → list of changed files
      4. Determine which collection types are affected by the changed files
      5. Delete-then-reinsert just the changed files' chunks per affected
         collection (real row-level delete-by-filter — migration plan
         decision D4), falling back to collection-level drop+full-rebuild
         only where per-file precision doesn't apply: release_notes (chunks
         have no file_path — they're per-GitHub-release, not per-file) and
         doc collections drawing from extra_docs_paths (those chunks'
         file_path uses a different display-prefix scheme than the
         subproject-relative paths GitHub's compare API returns, so a
         precise match can't be guaranteed without risking a silent
         under-delete).

    Previously always re-indexed at collection granularity (drop + full
    rebuild) specifically because Milvus made delete-by-metadata-filter
    awkward on arbitrary schemas — pgvector makes it a plain SQL DELETE, so
    that workaround is gone for the common case.
    """

    def __init__(self) -> None:
        self.registry = ProjectRegistry()
        self.client = GitHubClient()

    def refresh(self, project: Project) -> RefreshResult:
        import tempfile
        from pathlib import Path

        repo = self.client.get_repo(project.github_url)
        latest_sha = self.client.get_latest_commit_sha(repo)

        if not has_own_index(project.slug, project.collections):
            # This step refreshes collections a project already has; it cannot make the first
            # index (that is a consent-and-cost decision, not a side effect of a refresh). Moving
            # last_commit_sha here would make every later run say "No changes" about content that
            # was never indexed, so leave it where it is.
            print(f"Not yet indexed: {project.slug} has no collection of its own to refresh")
            return RefreshResult.NOT_YET_INDEXED

        last_sha = self._get_last_sha(project.slug)
        if last_sha == latest_sha:
            print(f"No changes since last index ({latest_sha[:8]})")
            # Still profile data files if that step was never run
            if not self.registry.get_data_profiles(project.slug):
                self._run_profile_only(repo, project)
            return RefreshResult.NO_CHANGES

        changed_files = self._get_changed_files(repo, last_sha, latest_sha)
        n = len(changed_files)
        print(f"{n} file{'s' if n != 1 else ''} changed since {last_sha[:8] if last_sha else 'initial index'}")

        if not changed_files:
            self._store_last_sha(project.slug, latest_sha)
            return RefreshResult.NOTHING_AFFECTED

        # Find which collection types are touched by the changed files
        from resource_explorer.configdata.collection_config import COLLECTION_TYPES
        affected_names = set()
        for path in changed_files:
            ext = PurePath(path).suffix.lower()
            for ctype in COLLECTION_TYPES.values():
                if ext in ctype.file_extensions:
                    affected_names.add(ctype.name)

        # release_notes are always worth refreshing when anything changed
        affected_names.add("release_notes")

        # Filter to collections the project actually has
        project_ctypes = {
            name: COLLECTION_TYPES[name]
            for name in affected_names
            if name in COLLECTION_TYPES
            and f"{project.slug}_{name}" in (project.collections or [])
        }

        if not project_ctypes:
            # The project has an own index, and this commit touches none of its collection types:
            # a correct no-op, so the SHA advances.
            self._store_last_sha(project.slug, latest_sha)
            return RefreshResult.NOTHING_AFFECTED

        print(f"Re-indexing {len(project_ctypes)} collection(s): {', '.join(project_ctypes)}")

        store = MultiCollectionStore()
        pipeline = IngestionPipeline()

        # Download repo once for all affected file-based collections
        file_ctypes = {k: v for k, v in project_ctypes.items() if k != "release_notes"}
        surviving = [
            c for c in (project.collections or [])
            if not any(c == f"{project.slug}_{name}" for name in project_ctypes)
        ]

        subproject_path = project.subproject_path or None
        extra_docs_paths = project.extra_docs_paths or []

        _doc_ctypes = frozenset({"markdown_docs", "web_docs", "api_reference", "examples", "pdfs"})

        # Subproject-relative view of changed_files, for matching against
        # chunk metadata["file_path"] (which is relative to local_root, i.e.
        # to the subproject subdir when one is set) — GitHub's compare API
        # returns full-repo-relative paths.
        if subproject_path:
            prefix = subproject_path.rstrip("/") + "/"
            relative_changed = {f[len(prefix):] for f in changed_files if f.startswith(prefix)}
        else:
            relative_changed = set(changed_files)

        with tempfile.TemporaryDirectory() as tmp:
            local_root = None
            resolved_extra: list[tuple[str, Path]] = []
            if file_ctypes:
                print("Downloading repository...")
                if subproject_path and extra_docs_paths:
                    # Need full repo to access extra_docs_paths outside subproject_path
                    full_root = self.client.download_zipball(repo, Path(tmp))
                    local_root = full_root / subproject_path
                    resolved_extra = [(p, full_root / p) for p in extra_docs_paths]
                else:
                    local_root = self.client.download_zipball(repo, Path(tmp), subproject_path)

            for name, ctype in project_ctypes.items():
                collection_name = f"{project.slug}_{name}"
                extra = resolved_extra if (name in _doc_ctypes and resolved_extra) else []

                # release_notes chunks have no file_path (they're per-GitHub-
                # release, not per-file) and doc collections drawing from
                # extra_docs_paths use a different path scheme than
                # relative_changed can match — both fall back to the
                # original collection-level drop+full-rebuild.
                use_full_rebuild = name == "release_notes" or bool(extra)

                if use_full_rebuild:
                    store.drop_collection(collection_name)
                    count = pipeline._ingest_collection(
                        repo, project.slug, collection_name, ctype,
                        local_root if name != "release_notes" else None,
                        extra_paths=extra,
                    )
                    if count > 0:
                        surviving.append(collection_name)
                        print(f"  {collection_name}: {count} chunks (full rebuild)")
                else:
                    # Real row-level delete-then-reinsert: only the changed
                    # files' chunks are touched, everything else in the
                    # collection is left alone (migration plan decision D4).
                    deleted = store.delete_by_metadata(collection_name, "file_path", list(relative_changed))
                    count = pipeline._ingest_collection(
                        repo, project.slug, collection_name, ctype, local_root,
                        changed_files=relative_changed,
                    )
                    surviving.append(collection_name)
                    print(f"  {collection_name}: -{deleted} +{count} chunks (partial update)")

            if local_root is not None:
                file_count, loc = pipeline._count_repo_stats(local_root)
                self.registry.update_ingestion_stats(project.slug, file_count, loc)
                pipeline._store_file_inventory(project.slug, local_root, repo=repo)
                pipeline._parse_dependencies(project.slug, local_root)
                pipeline._profile_data_files(project.slug, local_root)

        self.registry.update_indexed_at(project.slug, surviving)
        self.registry.update_status(project.slug, ProjectStatus.ACTIVE)
        self._store_last_sha(project.slug, latest_sha)
        return RefreshResult.INDEXED

    def _run_profile_only(self, repo, project: Project) -> None:
        """Download the repo just to populate file inventory and data profiles.

        Thin wrapper over IngestionPipeline.refresh_profile() (single zipball
        download, shared with the Scouting 'Profile' tab's on-demand refresh).
        """
        print("  Downloading repository for data profiling…")
        IngestionPipeline().refresh_profile(
            project.slug,
            project.github_url,
            project.collections or [],
            subproject_path=project.subproject_path or None,
            include_symbols=False,
            client=self.client,
            repo=repo,
        )
        print("  Data profiles updated.")

    def _get_changed_files(self, repo, old_sha: str, new_sha: str) -> list[str]:
        if not old_sha:
            return []  # first run — full ingestion handled by IngestionPipeline
        try:
            comparison = repo.compare(old_sha, new_sha)
            return [f.filename for f in comparison.files]
        except Exception:
            return []

    def _get_last_sha(self, slug: str) -> str:
        project = self.registry.get(slug)
        return project.last_commit_sha if project else ""

    def _store_last_sha(self, slug: str, sha: str) -> None:
        self.registry.update_commit_sha(slug, sha)
