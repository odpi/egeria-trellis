"""
Incremental indexing system for efficient vector store updates.

This module provides functionality to detect file changes and update
only modified content in the vector store, avoiding full re-indexing.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import List, Dict, Sequence, Set, Tuple, Optional, NamedTuple
from dataclasses import dataclass
from loguru import logger

from advisor.config import get_full_config
from advisor.vector_store import get_vector_store
from advisor.embeddings import get_embedding_generator
from advisor.ingest import CodeIngester, existing_chunk_count, path_is_excluded
from advisor.mlflow_tracking import track_operation
from advisor.db_consolidated import get_db_manager


class FileChange(NamedTuple):
    """Represents a detected file change."""
    path: Path
    change_type: str  # 'new', 'modified', 'deleted'
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None


@dataclass
class ChangeSet:
    """Collection of detected changes."""
    new_files: List[Path]
    modified_files: List[Path]
    deleted_files: List[Path]
    unchanged_files: List[Path]
    
    @property
    def total_changes(self) -> int:
        """Total number of changes."""
        return len(self.new_files) + len(self.modified_files) + len(self.deleted_files)
    
    @property
    def has_changes(self) -> bool:
        """Whether any changes were detected."""
        return self.total_changes > 0


@dataclass
class UpdateResult:
    """Result of an incremental update operation."""
    files_added: int
    files_modified: int
    files_deleted: int
    chunks_added: int
    chunks_removed: int
    duration: float
    success: bool
    error: Optional[str] = None


class FileTracker:
    """
    Track file states and detect changes using consolidated PostgreSQL.
    
    Maintains a database of indexed files with their modification times,
    content hashes, and associated vector store entity IDs.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize file tracker.
        
        Args:
            db_path: Ignored, retained for signature compatibility.
        """
        self.db_manager = get_db_manager()
        self.db_manager.connect()
    
    def compute_file_hash(self, file_path: Path) -> str:
        """
        Compute SHA256 hash of file content.
        
        Args:
            file_path: Path to file
            
        Returns:
            Hex digest of file hash
        """
        hasher = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                # Read in chunks for large files
                for chunk in iter(lambda: f.read(8192), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            logger.warning(f"Failed to hash {file_path}: {e}")
            return ""
    
    def get_tracked_file(self, file_path: str, collection_name: str) -> Optional[Dict]:
        """
        Get tracked file information.
        
        Args:
            file_path: File path (relative or absolute)
            collection_name: Collection name
            
        Returns:
            Dict with file info or None if not tracked
        """
        sql = "SELECT * FROM indexed_files WHERE file_path = %s AND collection_name = %s"
        rows = self.db_manager.execute_query(sql, (str(file_path), collection_name))
        return rows[0] if rows else None
    
    def track_file(
        self,
        file_path: Path,
        collection_name: str,
        content_hash: str,
        chunk_count: int,
        entity_ids: List[str]
    ):
        """
        Track a file in the database.
        
        Args:
            file_path: Path to file
            collection_name: Collection name
            content_hash: Content hash
            chunk_count: Number of chunks created
            entity_ids: List of vector store entity IDs (strings)
        """
        now = time.time()
        mtime = file_path.stat().st_mtime
        
        sql = """
            INSERT INTO indexed_files
            (file_path, collection_name, last_modified, content_hash, 
             last_indexed, chunk_count, entity_ids)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (file_path, collection_name) DO UPDATE SET
                last_modified = EXCLUDED.last_modified,
                content_hash = EXCLUDED.content_hash,
                last_indexed = EXCLUDED.last_indexed,
                chunk_count = EXCLUDED.chunk_count,
                entity_ids = EXCLUDED.entity_ids
        """
        self.db_manager.execute_update(sql, (
            str(file_path),
            collection_name,
            mtime,
            content_hash,
            now,
            chunk_count,
            json.dumps(entity_ids)
        ))
    
    def untrack_file(self, file_path: str, collection_name: str) -> Optional[List[str]]:
        """
        Remove file from tracking and return its entity IDs.
        
        Args:
            file_path: File path
            collection_name: Collection name
            
        Returns:
            List of entity IDs or None if not tracked
        """
        tracked = self.get_tracked_file(file_path, collection_name)
        if not tracked:
            return None
        
        entity_ids = json.loads(tracked['entity_ids'])
        
        sql = "DELETE FROM indexed_files WHERE file_path = %s AND collection_name = %s"
        self.db_manager.execute_update(sql, (file_path, collection_name))
        
        return entity_ids
    
    def get_tracked_files(self, collection_name: str) -> Dict[str, Dict]:
        """
        Get all tracked files for a collection.
        
        Args:
            collection_name: Collection name
            
        Returns:
            Dict mapping file paths to file info
        """
        sql = "SELECT * FROM indexed_files WHERE collection_name = %s"
        rows = self.db_manager.execute_query(sql, (collection_name,))
        return {row['file_path']: row for row in rows}
    
    def update_metadata(self, collection_name: str, total_files: int, total_chunks: int):
        """
        Update collection metadata.
        
        Args:
            collection_name: Collection name
            total_files: Total number of files
            total_chunks: Total number of chunks
        """
        now = time.time()
        sql = """
            INSERT INTO index_metadata
            (collection_name, last_full_index, total_files, total_chunks)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (collection_name) DO UPDATE SET
                last_full_index = EXCLUDED.last_full_index,
                total_files = EXCLUDED.total_files,
                total_chunks = EXCLUDED.total_chunks
        """
        self.db_manager.execute_update(sql, (collection_name, now, total_files, total_chunks))
    
    def get_metadata(self, collection_name: str) -> Optional[Dict]:
        """
        Get collection metadata.
        
        Args:
            collection_name: Collection name
            
        Returns:
            Dict with metadata or None
        """
        sql = "SELECT * FROM index_metadata WHERE collection_name = %s"
        rows = self.db_manager.execute_query(sql, (collection_name,))
        return rows[0] if rows else None


class IncrementalIndexer:
    """
    Manage incremental indexing for a collection.
    
    Detects file changes and updates only modified content in the vector store.
    """
    
    def __init__(
        self,
        collection_name: str,
        source_paths: List[Path],
        file_patterns: List[str],
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        exclude_patterns: Optional[Sequence[str]] = None
    ):
        """
        Initialize incremental indexer.
        
        Args:
            collection_name: Name of vector store collection
            source_paths: List of source directories/files
            file_patterns: List of file patterns to match (e.g., ["*.py"])
            chunk_size: Size of text chunks
            chunk_overlap: Overlap between chunks
            exclude_patterns: Glob patterns to skip, i.e. a collection's
                CollectionMetadata.exclude_patterns. Without these an
                incremental run re-adds exactly the files a full ingest
                excludes -- see path_is_excluded() in advisor/ingest.py.
        """
        self.collection_name = collection_name
        self.source_paths = source_paths
        self.file_patterns = file_patterns
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.exclude_patterns = list(exclude_patterns or [])
        
        # Initialize components. db_path is unused by FileTracker (it tracks
        # state in Postgres now, not sqlite — see FileTracker.__init__'s
        # docstring), but resolve it through the same writable-data-root
        # helper as everything else rather than a bare relative "data" default.
        from advisor.config import resolve_advisor_data_root
        config = get_full_config()
        db_path = Path(config.get("data_dir") or resolve_advisor_data_root()) / "index_state.db"
        self.tracker = FileTracker(db_path)
        self.vector_store = get_vector_store()
        self.embedding_generator = get_embedding_generator()
        self.ingester = CodeIngester(
            collection_name=collection_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        
        logger.info(f"Initialized IncrementalIndexer for {collection_name}")
    
    def _find_files(self) -> Set[Path]:
        """
        Find all files matching patterns in source paths.
        
        Returns:
            Set of file paths
        """
        files = set()
        for source_path in self.source_paths:
            if source_path.is_file():
                if not path_is_excluded(source_path, self.exclude_patterns):
                    files.add(source_path)
            elif source_path.is_dir():
                for pattern in self.file_patterns:
                    try:
                        files.update(
                            f for f in source_path.rglob(pattern)
                            if not path_is_excluded(
                                f, self.exclude_patterns, root=source_path
                            )
                        )
                    except (PermissionError, OSError) as e:
                        logger.warning(f"Skipping inaccessible path {source_path}: {e}")
        
        return files
    
    def detect_changes(self) -> ChangeSet:
        """
        Detect file changes since last index.
        
        Returns:
            ChangeSet with categorized changes
        """
        logger.info(f"Detecting changes for {self.collection_name}...")
        
        # Get current files
        current_files = self._find_files()
        logger.info(f"Found {len(current_files)} current files")
        
        # Get tracked files
        tracked = self.tracker.get_tracked_files(self.collection_name)
        tracked_paths = set(Path(p) for p in tracked.keys())
        logger.info(f"Tracking {len(tracked_paths)} files")
        
        # Categorize changes
        new_files = []
        modified_files = []
        deleted_files = []
        unchanged_files = []
        
        # Check current files
        for file_path in current_files:
            if file_path not in tracked_paths:
                # New file
                new_files.append(file_path)
            else:
                # Check if modified
                tracked_info = tracked[str(file_path)]
                current_mtime = file_path.stat().st_mtime
                tracked_mtime = tracked_info['last_modified']
                
                if current_mtime > tracked_mtime:
                    # File modified - verify with hash
                    current_hash = self.tracker.compute_file_hash(file_path)
                    if current_hash != tracked_info['content_hash']:
                        modified_files.append(file_path)
                    else:
                        unchanged_files.append(file_path)
                else:
                    unchanged_files.append(file_path)
        
        # Check for deleted files
        for tracked_path in tracked_paths:
            if tracked_path not in current_files:
                deleted_files.append(tracked_path)
        
        changeset = ChangeSet(
            new_files=new_files,
            modified_files=modified_files,
            deleted_files=deleted_files,
            unchanged_files=unchanged_files
        )
        
        logger.info(f"Changes detected: {len(new_files)} new, "
                   f"{len(modified_files)} modified, {len(deleted_files)} deleted, "
                   f"{len(unchanged_files)} unchanged")
        
        return changeset
    
    def apply_updates(self, changeset: ChangeSet, dry_run: bool = False) -> UpdateResult:
        """
        Apply incremental updates to the vector store.
        
        Args:
            changeset: Detected changes
            dry_run: If True, don't actually update
            
        Returns:
            UpdateResult with operation statistics
        """
        start_time = time.time()
        
        if dry_run:
            logger.info("DRY RUN - No actual updates will be performed")
            return UpdateResult(
                files_added=len(changeset.new_files),
                files_modified=len(changeset.modified_files),
                files_deleted=len(changeset.deleted_files),
                chunks_added=0,
                chunks_removed=0,
                duration=0,
                success=True
            )
        
        params = {
            "collection": self.collection_name,
            "new_files": len(changeset.new_files),
            "modified_files": len(changeset.modified_files),
            "deleted_files": len(changeset.deleted_files),
            "total_changes": changeset.total_changes
        }
        
        try:
            with track_operation(f"incremental_update_{self.collection_name}", params=params) as tracker:
                chunks_added = 0
                chunks_removed = 0
                
                # Process deleted files
                for file_path in changeset.deleted_files:
                    entity_ids = self.tracker.untrack_file(str(file_path), self.collection_name)
                    if entity_ids:
                        # Delete entities from the vector store
                        self.vector_store.delete_entities(self.collection_name, entity_ids)
                        chunks_removed += len(entity_ids)
                        logger.info(f"Deleted {len(entity_ids)} chunks for {file_path}")
                
                # Process modified files (delete old, add new)
                for file_path in changeset.modified_files:
                    # Delete old entities
                    entity_ids = self.tracker.untrack_file(str(file_path), self.collection_name)
                    if entity_ids:
                        self.vector_store.delete_entities(self.collection_name, entity_ids)
                        chunks_removed += len(entity_ids)
                    
                    # Ingest new version
                    files, chunks, entity_ids = self.ingester.ingest_file(file_path)
                    if files > 0:
                        # Track the file with entity IDs from ingester
                        content_hash = self.tracker.compute_file_hash(file_path)
                        self.tracker.track_file(
                            file_path,
                            self.collection_name,
                            content_hash,
                            chunks,
                            entity_ids
                        )
                        chunks_added += chunks
                        logger.info(f"Updated {chunks} chunks for {file_path} (entity IDs: {len(entity_ids)})")
                
                # Process new files
                for file_path in changeset.new_files:
                    files, chunks, entity_ids = self.ingester.ingest_file(file_path)
                    if files > 0:
                        content_hash = self.tracker.compute_file_hash(file_path)
                        self.tracker.track_file(
                            file_path,
                            self.collection_name,
                            content_hash,
                            chunks,
                            entity_ids
                        )
                        chunks_added += chunks
                        logger.info(f"Added {chunks} chunks for {file_path} (entity IDs: {len(entity_ids)})")
                
                duration = time.time() - start_time
                
                # Update metadata
                total_files = len(changeset.new_files) + len(changeset.modified_files) + len(changeset.unchanged_files)
                self.tracker.update_metadata(self.collection_name, total_files, chunks_added - chunks_removed)
                
                # Track metrics
                tracker.log_metrics({
                    "files_added": len(changeset.new_files),
                    "files_modified": len(changeset.modified_files),
                    "files_deleted": len(changeset.deleted_files),
                    "chunks_added": chunks_added,
                    "chunks_removed": chunks_removed,
                    "duration_seconds": duration
                })
                
                logger.info(f"Incremental update complete: +{chunks_added} -{chunks_removed} chunks in {duration:.2f}s")
                
                return UpdateResult(
                    files_added=len(changeset.new_files),
                    files_modified=len(changeset.modified_files),
                    files_deleted=len(changeset.deleted_files),
                    chunks_added=chunks_added,
                    chunks_removed=chunks_removed,
                    duration=duration,
                    success=True
                )
        
        except Exception as e:
            logger.error(f"Incremental update failed: {e}")
            return UpdateResult(
                files_added=0,
                files_modified=0,
                files_deleted=0,
                chunks_added=0,
                chunks_removed=0,
                duration=time.time() - start_time,
                success=False,
                error=str(e)
            )

def build_indexer(collection_name: str) -> "IncrementalIndexer":
    """Construct an IncrementalIndexer for a configured collection.

    Resolves source paths and file patterns through
    advisor/collection_sources.py -- the same resolution
    scripts/ingest_collections.py uses -- and carries the collection's
    chunking and exclude_patterns across.

    Raises:
        KeyError: unknown collection name
        FileNotFoundError: the repo is not cloned, or no source path exists
    """
    from advisor.collection_config import ALL_COLLECTIONS
    from advisor.collection_sources import (
        get_collection_source_paths,
        get_file_patterns,
        get_repos_dir,
    )

    collection = ALL_COLLECTIONS.get(collection_name)
    if collection is None:
        raise KeyError(
            f"Unknown collection '{collection_name}'. "
            f"Known: {', '.join(sorted(ALL_COLLECTIONS))}"
        )

    source_paths = get_collection_source_paths(collection)
    if not source_paths:
        raise FileNotFoundError(
            f"No source paths for '{collection_name}' under {get_repos_dir()} "
            f"({collection.source_repo} {collection.source_paths}) — "
            f"run scripts/clone_repos.py"
        )

    return IncrementalIndexer(
        collection_name=collection.name,
        source_paths=source_paths,
        file_patterns=get_file_patterns(collection),
        chunk_size=collection.chunk_size,
        chunk_overlap=collection.chunk_overlap,
        exclude_patterns=collection.exclude_patterns,
    )


def would_duplicate_untracked(indexer, collection_name: str) -> bool:
    """True if an incremental run would re-add a collection that is already
    populated but has no FileTracker state.

    The full-ingest path (scripts/ingest_collections.py) writes chunks to the
    vector store but never writes FileTracker rows -- `indexed_files` is only
    populated by this module. So immediately after a full ingest, every file
    looks new here and applying updates duplicates the whole collection
    rather than doing nothing. Observed 2026-09-10: egeria_concepts, freshly
    ingested at 720 chunks, reported "179 new, 0 unchanged".

    Returns False when the indexer exposes no tracker/store (a stub in
    tests), so the guard never invents a reason to fail.
    """
    tracker = getattr(indexer, "tracker", None)
    store = getattr(indexer, "vector_store", None)
    if tracker is None or store is None:
        return False

    try:
        if tracker.get_tracked_files(collection_name):
            return False  # tracked: a normal incremental run
        return bool(existing_chunk_count(store, collection_name))
    except Exception as exc:  # nothing here is worth failing the run over
        logger.debug(f"untracked-duplication check skipped: {exc}")
        return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point: `python -m advisor.incremental_indexer --collection X`.

    This module had no __main__ block until 2026-09-10, so that command --
    documented in CLAUDE.md and issued by the Admin panel's "Incremental
    re-index" button (advisor/web/admin.py) -- imported the module, ran
    nothing and exited 0. The caller reads exit 0 as success and stamps
    ingest_log, so egeria_concepts/egeria_types/egeria_general sat empty
    while the UI reported them freshly indexed.

    Exit codes: 0 on success (including "no changes"), 1 on failure.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m advisor.incremental_indexer",
        description="Incrementally re-index a collection (changed files only)",
    )
    parser.add_argument("--collection", required=True, help="Collection to index")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect and report changes without writing to the vector store",
    )
    parser.add_argument(
        "--allow-untracked",
        action="store_true",
        help=(
            "Index even when the collection is already populated but has no "
            "file-tracking state. Duplicates existing chunks — see "
            "would_duplicate_untracked()"
        ),
    )
    args = parser.parse_args(argv)

    try:
        indexer = build_indexer(args.collection)
    except (KeyError, FileNotFoundError) as exc:
        logger.error(str(exc))
        return 1

    changeset = indexer.detect_changes()
    logger.info(
        f"{args.collection}: {len(changeset.new_files)} new, "
        f"{len(changeset.modified_files)} modified, "
        f"{len(changeset.deleted_files)} deleted, "
        f"{len(changeset.unchanged_files)} unchanged"
    )

    if not changeset.has_changes:
        logger.info(f"{args.collection}: nothing to do")
        return 0

    if (
        not args.dry_run
        and not args.allow_untracked
        and would_duplicate_untracked(indexer, args.collection)
    ):
        logger.error(
            f"{args.collection} already holds chunks but has no file-tracking "
            f"state, so every file reads as new and indexing would duplicate "
            f"the collection. Re-run the full ingest instead "
            f"(scripts/ingest_collections.py --collection {args.collection} "
            f"--force), or pass --allow-untracked if duplication is intended."
        )
        return 1

    result = indexer.apply_updates(changeset, dry_run=args.dry_run)
    if not result.success:
        logger.error(f"{args.collection}: incremental update failed — {result.error}")
        return 1

    logger.info(
        f"{args.collection}: +{result.chunks_added} / -{result.chunks_removed} "
        f"chunks in {result.duration:.2f}s"
        + (" (dry run — nothing written)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
