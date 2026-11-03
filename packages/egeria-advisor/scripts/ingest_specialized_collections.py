#!/usr/bin/env python3
"""
Ingest specialized egeria_docs collections with collection-specific parameters.

This script ingests the three new specialized collections:
- egeria_concepts: Core concepts and definitions
- egeria_types: Type system definitions
- egeria_general: Tutorials and guides

Usage:
    python scripts/ingest_specialized_collections.py --collection egeria_concepts
    python scripts/ingest_specialized_collections.py --collection egeria_types
    python scripts/ingest_specialized_collections.py --collection egeria_general
    python scripts/ingest_specialized_collections.py --all
"""

import sys
import argparse
from pathlib import Path
from loguru import logger

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from advisor.ingest import CodeIngester
from advisor.collection_config import (
    get_collection,
    EGERIA_CONCEPTS_COLLECTION,
    EGERIA_TYPES_COLLECTION,
    EGERIA_GENERAL_COLLECTION
)
from advisor.vector_store import get_vector_store


def ingest_collection(collection_name: str, repo_path: Path, drop_existing: bool = False):
    """
    Ingest a specialized collection.
    
    Args:
        collection_name: Name of collection to ingest
        repo_path: Path to egeria-docs repository
        drop_existing: Whether to drop existing collection
        
    Returns:
        Tuple of (files_processed, chunks_created)
    """
    collection_meta = get_collection(collection_name)
    if not collection_meta:
        logger.error(f"Collection {collection_name} not found in config")
        return 0, 0
    
    logger.info("=" * 80)
    logger.info(f"Ingesting Collection: {collection_name}")
    logger.info("=" * 80)
    logger.info(f"Description: {collection_meta.description}")
    logger.info(f"Source paths: {collection_meta.source_paths}")
    logger.info(f"Chunk size: {collection_meta.chunk_size}")
    logger.info(f"Chunk overlap: {collection_meta.chunk_overlap}")
    logger.info(f"Min score: {collection_meta.min_score}")
    logger.info(f"Default top_k: {collection_meta.default_top_k}")
    logger.info("=" * 80)
    
    # Initialize vector store and create collection
    vector_store = get_vector_store()
    
    if drop_existing:
        logger.warning(f"Dropping existing collection: {collection_name}")
    
    vector_store.create_collection(
        collection_name,
        description=collection_meta.description,
        drop_if_exists=drop_existing
    )
    
    # Initialize ingester with collection-specific parameters
    ingester = CodeIngester(
        collection_name=collection_name
        # chunk_size and chunk_overlap will be loaded from collection config
    )
    
    # Build full paths for ingestion
    total_files = 0
    total_chunks = 0
    
    for source_path in collection_meta.source_paths:
        full_path = repo_path / source_path
        
        if not full_path.exists():
            logger.warning(f"Path does not exist: {full_path}")
            continue
        
        logger.info(f"\nIngesting from: {full_path}")
        
        # Ingest markdown files
        files, chunks = ingester.ingest_directory(
            dir_path=full_path,
            file_pattern="*.md",
            recursive=True,
            batch_size=50,
            exclude_patterns=collection_meta.exclude_patterns
        )
        
        total_files += files
        total_chunks += chunks
        
        logger.info(f"✓ Processed {files} files, created {chunks} chunks")
    
    # Create index
    logger.info("\nCreating vector index...")
    vector_store.create_index(
        collection_name,
        index_type="IVF_FLAT",
        metric_type="L2"
    )
    
    logger.info("=" * 80)
    logger.info(f"Collection {collection_name} ingestion complete!")
    logger.info(f"Total files: {total_files}")
    logger.info(f"Total chunks: {total_chunks}")
    logger.info("=" * 80)
    
    return total_files, total_chunks


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Ingest specialized egeria_docs collections"
    )
    parser.add_argument(
        "--collection",
        choices=["egeria_concepts", "egeria_types", "egeria_general"],
        help="Collection to ingest"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Ingest all three specialized collections"
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path("data/repos/egeria-docs"),
        help="Path to egeria-docs repository"
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing collections before ingesting"
    )
    
    args = parser.parse_args()
    
    if not args.collection and not args.all:
        parser.error("Must specify either --collection or --all")
    
    # Verify repo path exists
    if not args.repo_path.exists():
        logger.error(f"Repository path does not exist: {args.repo_path}")
        logger.error("Please clone egeria-docs first:")
        logger.error("  cd ~/localGit/egeria-v6")
        logger.error("  git clone https://github.com/odpi/egeria-docs.git")
        return 1
    
    # Determine which collections to ingest
    collections = []
    if args.all:
        collections = ["egeria_concepts", "egeria_types", "egeria_general"]
    else:
        collections = [args.collection]
    
    # Ingest each collection
    results = {}
    for collection_name in collections:
        try:
            files, chunks = ingest_collection(
                collection_name,
                args.repo_path,
                drop_existing=args.drop_existing
            )
            results[collection_name] = {"files": files, "chunks": chunks}
        except Exception as e:
            logger.error(f"Failed to ingest {collection_name}: {e}")
            import traceback
            traceback.print_exc()
            results[collection_name] = {"error": str(e)}
    
    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("INGESTION SUMMARY")
    logger.info("=" * 80)
    for collection_name, result in results.items():
        if "error" in result:
            logger.error(f"{collection_name}: ERROR - {result['error']}")
        else:
            logger.info(f"{collection_name}: {result['files']} files, {result['chunks']} chunks")
    logger.info("=" * 80)
    
    # Check for errors
    if any("error" in r for r in results.values()):
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())