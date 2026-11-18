#!/usr/bin/env python3
"""
Clone Egeria git repositories for multi-collection RAG system.

This script clones all required repositories into the data/repos directory.
"""

import sys
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from advisor.collection_config import (
    PYEGERIA_COLLECTION,
    PYEGERIA_CLI_COLLECTION,
    PYEGERIA_DRE_COLLECTION,
    EGERIA_JAVA_COLLECTION,
    EGERIA_DOCS_COLLECTION,
    EGERIA_WORKSPACES_COLLECTION,
    get_phase1_collections,
    get_phase2_collections
)
from advisor.config import resolve_advisor_data_root
from loguru import logger


# Repository configurations
REPOS = {
    "egeria-python": {
        "url": "https://github.com/odpi/egeria-python.git",
        "collections": ["pyegeria", "pyegeria_cli", "pyegeria_drE", "egeria_templates"],
        "phase": 1
    },
    "egeria": {
        "url": "https://github.com/odpi/egeria.git",
        "collections": ["egeria_java"],
        "phase": 2
    },
    "egeria-docs": {
        "url": "https://github.com/odpi/egeria-docs.git",
        # TR-51: egeria_docs is disabled; the docs are indexed as these three (collection_config.REPLACED_BY).
        "collections": ["egeria_concepts", "egeria_types", "egeria_general"],
        "phase": 2
    },
    "egeria-workspaces": {
        "url": "https://github.com/odpi/egeria-workspaces.git",
        "collections": ["egeria_workspaces"],
        "phase": 2
    },
    "egeria-trellis": {
        "url": "https://github.com/odpi/egeria-trellis.git",
        "collections": ["trellis"],
        "phase": 2
    }
}


def get_repos_dir() -> Path:
    """Get the data/repos directory path.

    Under resolve_advisor_data_root() (ADVISOR_DATA_PATH), same writable-state
    root advisor_cache_dir/feedback already use -- NOT a path relative to this
    script. The old package-relative path sat outside the container's only
    persistent volume (trellis-ea-data:/app/data), so every clone here was
    silently wiped on the next redeploy: admin.py's "pull" button would always
    find nothing and start from a fresh clone rather than an incremental
    update. See admin.py/ingest_collections.py for the matching fix on the
    read side.
    """
    return resolve_advisor_data_root() / "repos"


def ensure_repos_dir() -> Path:
    """Ensure data/repos directory exists."""
    repos_dir = get_repos_dir()
    repos_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Repos directory: {repos_dir}")
    return repos_dir


def is_repo_cloned(repo_path: Path) -> bool:
    """Check if a repository is already cloned."""
    return repo_path.exists() and (repo_path / ".git").exists()


def clone_repo(repo_name: str, repo_url: str, target_dir: Path) -> bool:
    """
    Clone a git repository.
    
    Args:
        repo_name: Name of the repository
        repo_url: Git URL
        target_dir: Target directory for clone
        
    Returns:
        True if successful, False otherwise
    """
    repo_path = target_dir / repo_name
    
    # Check if already cloned
    if is_repo_cloned(repo_path):
        logger.info(f"✓ Repository already cloned: {repo_name}")
        return True
    
    logger.info(f"Cloning {repo_name} from {repo_url}...")
    
    try:
        # Clone the repository
        result = subprocess.run(
            ["git", "clone", repo_url, str(repo_path)],
            capture_output=True,
            text=True,
            check=True
        )
        
        logger.info(f"✓ Successfully cloned {repo_name}")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"✗ Failed to clone {repo_name}: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"✗ Error cloning {repo_name}: {e}")
        return False


def update_repo(repo_path: Path) -> bool:
    """
    Update an existing repository.
    
    Args:
        repo_path: Path to repository
        
    Returns:
        True if successful, False otherwise
    """
    if not is_repo_cloned(repo_path):
        logger.warning(f"Repository not found: {repo_path}")
        return False
    
    logger.info(f"Updating {repo_path.name}...")
    
    try:
        # Pull latest changes
        result = subprocess.run(
            ["git", "pull"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        
        logger.info(f"✓ Updated {repo_path.name}")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"✗ Failed to update {repo_path.name}: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"✗ Error updating {repo_path.name}: {e}")
        return False


def get_repo_info(repo_path: Path) -> Dict[str, str]:
    """
    Get information about a repository.
    
    Args:
        repo_path: Path to repository
        
    Returns:
        Dict with repo info
    """
    if not is_repo_cloned(repo_path):
        return {"status": "not_cloned"}
    
    try:
        # Get current branch
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        branch = branch_result.stdout.strip()
        
        # Get latest commit
        commit_result = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        commit = commit_result.stdout.strip()
        
        # Get remote URL
        remote_result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        remote = remote_result.stdout.strip()
        
        return {
            "status": "cloned",
            "branch": branch,
            "commit": commit,
            "remote": remote
        }
        
    except Exception as e:
        logger.error(f"Error getting repo info for {repo_path.name}: {e}")
        return {"status": "error", "error": str(e)}


def clone_phase1_repos(update: bool = False) -> Tuple[int, int]:
    """
    Clone Phase 1 (Python) repositories.
    
    Args:
        update: If True, update existing repos
        
    Returns:
        Tuple of (successful, failed) counts
    """
    logger.info("=" * 60)
    logger.info("Cloning Phase 1 Repositories (Python)")
    logger.info("=" * 60)
    
    repos_dir = ensure_repos_dir()
    successful = 0
    failed = 0
    
    for repo_name, config in REPOS.items():
        if config["phase"] == 1:
            repo_path = repos_dir / repo_name
            
            if update and is_repo_cloned(repo_path):
                if update_repo(repo_path):
                    successful += 1
                else:
                    failed += 1
            else:
                if clone_repo(repo_name, config["url"], repos_dir):
                    successful += 1
                else:
                    failed += 1
    
    return successful, failed


def clone_phase2_repos(update: bool = False) -> Tuple[int, int]:
    """
    Clone Phase 2 (Java/Docs/Workspaces) repositories.
    
    Args:
        update: If True, update existing repos
        
    Returns:
        Tuple of (successful, failed) counts
    """
    logger.info("=" * 60)
    logger.info("Cloning Phase 2 Repositories (Java/Docs/Workspaces)")
    logger.info("=" * 60)
    
    repos_dir = ensure_repos_dir()
    successful = 0
    failed = 0
    
    for repo_name, config in REPOS.items():
        if config["phase"] == 2:
            repo_path = repos_dir / repo_name
            
            if update and is_repo_cloned(repo_path):
                if update_repo(repo_path):
                    successful += 1
                else:
                    failed += 1
            else:
                if clone_repo(repo_name, config["url"], repos_dir):
                    successful += 1
                else:
                    failed += 1
    
    return successful, failed


def clone_all_repos(update: bool = False) -> Tuple[int, int]:
    """
    Clone all repositories.
    
    Args:
        update: If True, update existing repos
        
    Returns:
        Tuple of (successful, failed) counts
    """
    logger.info("=" * 60)
    logger.info("Cloning All Repositories")
    logger.info("=" * 60)
    
    repos_dir = ensure_repos_dir()
    successful = 0
    failed = 0
    
    for repo_name, config in REPOS.items():
        repo_path = repos_dir / repo_name
        
        if update and is_repo_cloned(repo_path):
            if update_repo(repo_path):
                successful += 1
            else:
                failed += 1
        else:
            if clone_repo(repo_name, config["url"], repos_dir):
                successful += 1
            else:
                failed += 1
    
    return successful, failed


def show_repo_status():
    """Show status of all repositories."""
    logger.info("=" * 60)
    logger.info("Repository Status")
    logger.info("=" * 60)
    
    repos_dir = get_repos_dir()
    
    for repo_name, config in REPOS.items():
        repo_path = repos_dir / repo_name
        info = get_repo_info(repo_path)
        
        logger.info(f"\n{repo_name}:")
        logger.info(f"  URL: {config['url']}")
        logger.info(f"  Phase: {config['phase']}")
        logger.info(f"  Collections: {', '.join(config['collections'])}")
        logger.info(f"  Status: {info['status']}")
        
        if info['status'] == 'cloned':
            logger.info(f"  Branch: {info['branch']}")
            logger.info(f"  Latest: {info['commit'][:60]}...")
        elif info['status'] == 'error':
            logger.error(f"  Error: {info['error']}")


def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Clone Egeria repositories")
    parser.add_argument(
        "--phase",
        choices=["1", "2", "all"],
        default="1",
        help="Which phase to clone (default: 1)"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update existing repositories"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show repository status"
    )
    
    args = parser.parse_args()
    
    if args.status:
        show_repo_status()
        return
    
    # Clone repositories
    if args.phase == "1":
        successful, failed = clone_phase1_repos(update=args.update)
    elif args.phase == "2":
        successful, failed = clone_phase2_repos(update=args.update)
    else:
        successful, failed = clone_all_repos(update=args.update)
    
    # Show summary
    logger.info("\n" + "=" * 60)
    logger.info("Summary")
    logger.info("=" * 60)
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")
    
    # Show status
    show_repo_status()
    
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()