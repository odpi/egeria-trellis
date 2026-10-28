#!/usr/bin/env python3
"""
Ingest code from git repositories into pgvector collections.

This script processes cloned repositories and ingests code into
appropriate pgvector collections based on collection configuration.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Sequence, Tuple
import fnmatch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from advisor.collection_config import (
    CollectionMetadata,
    get_collection,
    get_enabled_collections,
    get_phase1_collections,
    get_phase2_collections
)
from advisor.vector_store import get_vector_store
from advisor.embeddings import get_embedding_generator
from advisor.collection_sources import (
    get_collection_source_paths,
    get_file_patterns,
    get_repos_dir,
)
from advisor.ingest import CodeIngester, existing_chunk_count, path_is_excluded
from loguru import logger


@dataclass
class IngestResult:
    """Outcome of ingesting one collection.

    `files`/`chunks` are what actually landed. `candidates` is how many files
    matched `patterns` under the resolved source paths *before* ingestion, so
    a zero can be told apart from "nothing to ingest". `reason` is set whenever
    files == 0 and says why — the summary prints it instead of a bare
    "Files: 0" (trevor, 2026-09-04: pyegeria/pyegeria_cli reported 0 of
    280/100 estimated files with no explanation).
    """
    collection: str
    files: int = 0
    chunks: int = 0
    candidates: int = 0
    patterns: List[str] = field(default_factory=list)
    reason: Optional[str] = None
    skipped: bool = False  # True when nothing was attempted (exists / dry-run / no paths)

    def __iter__(self):
        # Backwards compatible with the old `files, chunks = ingest_collection(...)`
        yield self.files
        yield self.chunks


# NOTE: get_repos_dir / get_collection_source_paths / get_file_patterns moved to
# advisor/collection_sources.py (2026-09-10) so incremental_indexer's CLI resolves
# sources identically instead of carrying a second copy of these rules. They are
# imported above, so `from ingest_collections import get_repos_dir` still works.


def count_files(
    paths: List[Path],
    patterns: List[str],
    exclude_patterns: Optional[Sequence[str]] = None,
) -> int:
    """
    Count files matching patterns in paths.

    Applies `exclude_patterns` the same way ingest_directory() does, so the
    "Estimated files" figure and the zero-candidate skip below reflect what
    will actually be ingested rather than the pre-exclusion total.

    Args:
        paths: List of directory paths
        patterns: List of file patterns
        exclude_patterns: Glob patterns to skip (collection.exclude_patterns)
        
    Returns:
        Total file count
    """
    count = 0
    for path in paths:
        if path.is_file():
            if path_is_excluded(path, exclude_patterns):
                continue
            for pattern in patterns:
                if fnmatch.fnmatch(path.name, pattern):
                    count += 1
                    break
        elif path.is_dir():
            for pattern in patterns:
                try:
                    count += sum(
                        1 for f in path.rglob(pattern)
                        if not path_is_excluded(f, exclude_patterns, root=path)
                    )
                except (PermissionError, OSError) as e:
                    logger.warning(f"Skipping inaccessible path {path}: {e}")
                    continue
    return count


def _record_ingest_time(collection_name: str, files: int, chunks: int) -> None:
    """Record completion so the admin page can show "last indexed"; best effort."""
    try:
        from advisor.web.admin import record_ingest_time
        record_ingest_time(collection_name, files=files, chunks=chunks, source="ingest_script")
    except Exception:
        pass


def ingest_collection(
    collection: CollectionMetadata,
    force: bool = False,
    dry_run: bool = False
) -> IngestResult:
    """
    Ingest a single collection.
    
    Args:
        collection: Collection metadata
        force: Force re-ingestion even if collection exists
        dry_run: Don't actually ingest, just show what would be done
        
    Returns:
        IngestResult (unpacks as (files_processed, chunks_created) for old callers)
    """
    logger.info("=" * 60)
    logger.info(f"Ingesting Collection: {collection.name}")
    logger.info("=" * 60)

    result = IngestResult(collection=collection.name)
    
    # Get source paths
    source_paths = get_collection_source_paths(collection)
    if not source_paths:
        result.reason = (
            f"no source paths found under {get_repos_dir()} for "
            f"{collection.source_repo} {collection.source_paths} — run scripts/clone_repos.py"
        )
        result.skipped = True
        logger.error(f"No valid source paths found for {collection.name}: {result.reason}")
        return result
    
    # Get file patterns
    patterns = get_file_patterns(collection)
    result.patterns = patterns
    
    # Count files
    file_count = count_files(source_paths, patterns, collection.exclude_patterns)
    result.candidates = file_count
    logger.info(f"Source paths: {len(source_paths)}")
    logger.info(f"File patterns: {patterns}")
    logger.info(f"Estimated files: {file_count}")

    if file_count == 0:
        result.reason = (
            f"0 candidate files matched patterns {patterns} under "
            f"{[str(p) for p in source_paths]}"
        )
        result.skipped = True
        logger.error(f"{collection.name}: {result.reason}")
        return result
    
    if dry_run:
        result.reason = "dry run — no ingestion performed"
        result.skipped = True
        logger.info("DRY RUN - No ingestion performed")
        return result
    
    # Check whether the collection already holds data
    vector_store = get_vector_store()
    vector_store.connect()  # Ensure connected

    existing = existing_chunk_count(vector_store, collection.name)
    if existing is not None and existing > 0:
        if not force:
            result.reason = (
                f"collection already holds {existing} chunks; skipped "
                f"(use --force to drop and re-ingest)"
            )
            result.skipped = True
            logger.warning(f"Collection {collection.name} {result.reason}")
            return result
        logger.info(f"Dropping existing collection: {collection.name} ({existing} chunks)")
        vector_store.delete_collection(collection.name)
    elif existing == 0:
        logger.info(
            f"Collection {collection.name} exists but is empty (auto-provisioned at "
            f"app startup) — ingesting into it"
        )
    
    # Create collection
    logger.info(f"Creating collection: {collection.name}")
    vector_store.create_collection(
        collection_name=collection.name,
        description=collection.description,
        drop_if_exists=False
    )

    # Clear the code-symbol table once per collection, before the source-path loop
    # below. ingest_directory() no longer clears it per-call — collections like
    # 'pyegeria' have multiple source directories, and clearing per-call wiped out
    # each earlier directory's symbols as soon as the next one was ingested.
    try:
        from advisor.code_symbol_store import get_symbol_store
        get_symbol_store().clear_collection(collection.name)
    except Exception as exc:
        logger.warning(f"CodeSymbolStore clear failed: {exc}")

    # Create ingester
    ingester = CodeIngester(
        collection_name=collection.name,
        chunk_size=1000,
        chunk_overlap=200
    )
    
    # Ingest each source path
    total_files = 0
    total_chunks = 0
    
    for source_path in source_paths:
        logger.info(f"Processing: {source_path}")
        
        if source_path.is_file():
            # Single file
            files, chunks, _ = ingester.ingest_file(source_path)
            total_files += files
            total_chunks += chunks
        else:
            # Directory
            for pattern in patterns:
                files, chunks = ingester.ingest_directory(
                    source_path,
                    file_pattern=pattern,
                    recursive=True,
                    exclude_patterns=collection.exclude_patterns
                )
                total_files += files
                total_chunks += chunks

    result.files = total_files
    result.chunks = total_chunks

    if total_files == 0:
        failures = getattr(ingester, "failure_summary", lambda: "")()
        result.reason = (
            f"0 of {file_count} candidate files matching {patterns} were ingested — "
            f"{failures or 'no per-file errors were recorded (check the log above)'}"
        )
        logger.error(f"{collection.name}: {result.reason}")
    elif getattr(ingester, "failed_files", None):
        logger.warning(f"{collection.name}: {ingester.failure_summary()}")
    
    # Create index after ingestion
    if total_chunks > 0:
        logger.info(f"Creating index for {collection.name}...")
        vector_store.create_index(
            collection_name=collection.name,
            index_type="IVF_FLAT",
            metric_type="L2"
        )
    
    logger.info(f"✓ Ingested {total_files} files, {total_chunks} chunks into {collection.name}")

    if total_chunks > 0:
        _record_ingest_time(collection.name, total_files, total_chunks)

    return result


def ingest_phase1(force: bool = False, dry_run: bool = False) -> Dict[str, IngestResult]:
    """
    Ingest Phase 1 (Python) collections.
    
    Args:
        force: Force re-ingestion
        dry_run: Don't actually ingest
        
    Returns:
        Dict mapping collection name to IngestResult
    """
    logger.info("=" * 60)
    logger.info("Ingesting Phase 1 Collections (Python)")
    logger.info("=" * 60)
    
    results = {}
    collections = get_phase1_collections()
    
    for collection in collections:
        results[collection.name] = ingest_collection(collection, force=force, dry_run=dry_run)
    
    return results


def ingest_phase2(force: bool = False, dry_run: bool = False) -> Dict[str, IngestResult]:
    """
    Ingest Phase 2 (Java/Docs/Workspaces) collections.
    
    Args:
        force: Force re-ingestion
        dry_run: Don't actually ingest
        
    Returns:
        Dict mapping collection name to IngestResult
    """
    logger.info("=" * 60)
    logger.info("Ingesting Phase 2 Collections (Java/Docs/Workspaces)")
    logger.info("=" * 60)
    
    results = {}
    collections = get_phase2_collections()
    
    for collection in collections:
        results[collection.name] = ingest_collection(collection, force=force, dry_run=dry_run)
    
    return results


def ingest_all(force: bool = False, dry_run: bool = False) -> Dict[str, IngestResult]:
    """
    Ingest all enabled collections.
    
    Args:
        force: Force re-ingestion
        dry_run: Don't actually ingest
        
    Returns:
        Dict mapping collection name to IngestResult
    """
    logger.info("=" * 60)
    logger.info("Ingesting All Enabled Collections")
    logger.info("=" * 60)
    
    results = {}
    collections = get_enabled_collections()
    
    for collection in collections:
        results[collection.name] = ingest_collection(collection, force=force, dry_run=dry_run)
    
    return results


def show_ingestion_summary(results: Dict[str, IngestResult]):
    """
    Show ingestion summary.

    A collection that ingested zero files is never printed as a bare
    "Files: 0" — its `reason` (skipped because it already holds data, no
    source paths, no candidate files, or every candidate file failed) is
    printed alongside, so the operator can tell a legitimate skip from a bug.
    """
    logger.info("\n" + "=" * 60)
    logger.info("Ingestion Summary")
    logger.info("=" * 60)
    
    total_files = 0
    total_chunks = 0
    
    for collection_name, result in results.items():
        logger.info(f"{collection_name}:")
        logger.info(f"  Files: {result.files}")
        logger.info(f"  Chunks: {result.chunks}")
        if result.files == 0:
            logger.info(f"  Candidate files: {result.candidates}")
            logger.warning(f"  Why zero: {result.reason or 'unknown — no reason recorded'}")
        total_files += result.files
        total_chunks += result.chunks
    
    logger.info("-" * 60)
    logger.info(f"Total Files: {total_files}")
    logger.info(f"Total Chunks: {total_chunks}")


def exit_code_for(results: Dict[str, IngestResult]) -> int:
    """Non-zero only when nothing at all was ingested (and it wasn't a dry run).

    Individual zero-file collections are reported with their reason but do
    not fail the run — a collection legitimately skipped because it already
    holds data must not turn a successful phase into exit 1.
    """
    if not results:
        return 1
    if all(r.skipped and r.reason and r.reason.startswith("dry run") for r in results.values()):
        return 0
    if sum(r.files for r in results.values()) == 0:
        return 1
    return 0


def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Ingest code into pgvector collections")
    parser.add_argument(
        "--phase",
        choices=["1", "2", "all"],
        default="1",
        help="Which phase to ingest (default: 1)"
    )
    parser.add_argument(
        "--collection",
        help="Ingest specific collection only"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-ingestion (drop existing collection)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ingested without actually doing it"
    )
    
    args = parser.parse_args()
    
    # Ingest collections
    if args.collection:
        # Single collection
        collection = get_collection(args.collection)
        if not collection:
            logger.error(f"Collection not found: {args.collection}")
            sys.exit(1)
        
        results = {collection.name: ingest_collection(
            collection,
            force=args.force,
            dry_run=args.dry_run
        )}
    elif args.phase == "1":
        results = ingest_phase1(force=args.force, dry_run=args.dry_run)
    elif args.phase == "2":
        results = ingest_phase2(force=args.force, dry_run=args.dry_run)
    else:
        results = ingest_all(force=args.force, dry_run=args.dry_run)
    
    # Show summary
    show_ingestion_summary(results)
    
    # Zero-file collections have already been explained in the summary; only
    # a run that ingested nothing at all is a failure.
    zero = [name for name, r in results.items() if r.files == 0]
    if zero:
        logger.warning(f"Collections with no files ingested: {', '.join(zero)} (see 'Why zero' above)")
    code = exit_code_for(results)
    if code != 0:
        logger.error("Nothing was ingested by this run")
    sys.exit(code)


if __name__ == "__main__":
    main()