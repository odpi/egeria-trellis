"""
Resolve a collection's on-disk source paths and file patterns.

Moved out of scripts/ingest_collections.py (2026-09-10) so the full-ingest
script and advisor/incremental_indexer.py's CLI resolve sources the same way
instead of each carrying its own copy of the repos-dir/source-path rules.
scripts/ingest_collections.py re-exports these names, so anything importing
them from there keeps working.
"""
from pathlib import Path
from typing import List

from loguru import logger

from advisor.collection_config import CollectionMetadata, Language
from advisor.config import resolve_advisor_data_root


def get_repos_dir() -> Path:
    """Get the data/repos directory path.

    Under resolve_advisor_data_root() (ADVISOR_DATA_PATH) -- see
    clone_repos.py's get_repos_dir() for why this must match the clone
    target, and admin.py for the read side.
    """
    return resolve_advisor_data_root() / "repos"


def get_collection_source_paths(collection: CollectionMetadata) -> List[Path]:
    """
    Get source paths for a collection.

    Args:
        collection: Collection metadata

    Returns:
        List of absolute paths to source directories
    """
    repos_dir = get_repos_dir()

    # Extract repo name from source_repo URL
    # e.g., "https://github.com/odpi/egeria-python.git" -> "egeria-python"
    repo_name = collection.source_repo.split("/")[-1].replace(".git", "")
    repo_path = repos_dir / repo_name

    if not repo_path.exists():
        logger.warning(f"Repository not found: {repo_path}")
        return []

    # Build full paths
    source_paths = []
    for rel_path in collection.source_paths:
        full_path = repo_path / rel_path
        if full_path.exists():
            source_paths.append(full_path)
        else:
            logger.warning(f"Source path not found: {full_path}")

    return source_paths


def get_file_patterns(collection: CollectionMetadata) -> List[str]:
    """
    Get file patterns for a collection based on language.

    Args:
        collection: Collection metadata

    Returns:
        List of file patterns (e.g., ["*.py", "*.md"])
    """
    patterns = {
        Language.PYTHON: ["*.py"],
        Language.JAVA: ["*.java"],
        Language.MARKDOWN: ["*.md"],
        Language.MIXED: ["*.py", "*.java", "*.md", "*.yaml", "*.yml", "*.json"]
    }

    return patterns.get(collection.language, ["*.py", "*.md"])
