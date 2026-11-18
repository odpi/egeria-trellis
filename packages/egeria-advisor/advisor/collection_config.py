"""
Collection configuration and metadata for multi-collection RAG system.

This module defines the metadata for each vector store collection, including
source repositories, paths, domain terms, and relationships.

Domain terms can be loaded from config/routing.yaml or use defaults.
"""

from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import yaml
from loguru import logger


class ContentType(Enum):
    """Type of content in a collection."""
    CODE = "code"
    DOCUMENTATION = "documentation"
    EXAMPLES = "examples"
    MIXED = "mixed"


class Language(Enum):
    """Primary language of collection content."""
    PYTHON = "python"
    JAVA = "java"
    MARKDOWN = "markdown"
    MIXED = "mixed"


@dataclass
class CollectionMetadata:
    """Metadata for a vector store collection."""
    
    # Basic info
    name: str
    description: str
    
    # Source information
    source_repo: str
    source_paths: List[str]
    
    # Content classification
    content_type: ContentType
    language: Language
    
    # Routing information
    domain_terms: List[str]
    related_collections: List[str] = field(default_factory=list)
    
    # Indexing configuration
    include_patterns: List[str] = field(default_factory=lambda: ["*.py", "*.md"])
    exclude_patterns: List[str] = field(default_factory=lambda: ["**/__pycache__/**", "**/deprecated/**"])
    
    # Collection settings
    enabled: bool = True
    priority: int = 1  # Higher = search first
    
    # RAG-specific parameters (NEW - Phase 2)
    chunk_size: int = 512  # Tokens per chunk
    chunk_overlap: int = 100  # Token overlap between chunks
    min_score: float = 0.35  # Minimum similarity score threshold
    default_top_k: int = 8  # Default number of results to retrieve
    
    def matches_query(self, query_lower: str, query_terms: List[str]) -> bool:
        """
        Check if this collection is relevant for a query.
        
        Args:
            query_lower: Lowercase query string
            query_terms: List of extracted query terms
            
        Returns:
            True if collection matches query domain
        """
        # Check if any domain terms appear in query
        for term in self.domain_terms:
            if term.lower() in query_lower:
                return True
            if term.lower() in [t.lower() for t in query_terms]:
                return True
        return False


# Configuration loading for domain terms
_routing_config_cache: Optional[Dict[str, Any]] = None
_routing_config_path = Path(__file__).parent / "configdata" / "routing.yaml"


def _load_routing_config() -> Dict[str, Any]:
    """Load routing configuration from YAML file."""
    global _routing_config_cache
    
    if _routing_config_cache is not None:
        return _routing_config_cache
    
    if not _routing_config_path.exists():
        logger.warning(f"Routing config not found at {_routing_config_path}, using defaults")
        _routing_config_cache = {}
        return _routing_config_cache
    
    try:
        with open(_routing_config_path, 'r') as f:
            config = yaml.safe_load(f)
            _routing_config_cache = config or {}
            logger.info(f"Loaded routing configuration for collections from {_routing_config_path}")
            return _routing_config_cache
    except Exception as e:
        logger.error(f"Error loading routing config: {e}, using defaults")
        _routing_config_cache = {}
        return _routing_config_cache


def _get_collection_domain_terms(collection_name: str, default_terms: List[str]) -> List[str]:
    """
    Get domain terms for a collection from config or use defaults.
    
    Args:
        collection_name: Name of the collection
        default_terms: Default terms to use if not in config
        
    Returns:
        List of domain terms
    """
    config = _load_routing_config()
    if 'collection_domain_terms' in config and collection_name in config['collection_domain_terms']:
        return config['collection_domain_terms'][collection_name]
    return default_terms


# Phase 1: Python Collections
# Default domain terms (used if config file not available)
_DEFAULT_PYEGERIA_TERMS = [
    "pyegeria", "python-client", "rest-client", "async-client",
    "widget", "egeria-client", "python-api", "python-sdk",
    "py-egeria"
]

PYEGERIA_COLLECTION = CollectionMetadata(
    name="pyegeria",
    description="Core PyEgeria Python library for Egeria REST API",
    source_repo="https://github.com/odpi/egeria-python.git",
    source_paths=["pyegeria", "tests"],
    content_type=ContentType.CODE,
    language=Language.PYTHON,
    domain_terms=_get_collection_domain_terms("pyegeria", _DEFAULT_PYEGERIA_TERMS),
    related_collections=["pyegeria_cli", "pyegeria_drE", "egeria_docs"],
    include_patterns=["*.py", "*.md"],
    exclude_patterns=["**/__pycache__/**", "**/deprecated/**", "**/.pytest_cache/**"],
    priority=10,  # High priority for Python queries
    # RAG parameters optimized for Python code
    chunk_size=512,  # Standard for code
    chunk_overlap=100,  # 20% overlap
    min_score=0.35,  # Moderate threshold
    default_top_k=10  # More results for code queries
)

_DEFAULT_PYEGERIA_CLI_TERMS = [
    "hey-egeria", "hey_egeria", "cli", "command", "commands",
    "command-line", "terminal"
]

PYEGERIA_CLI_COLLECTION = CollectionMetadata(
    name="pyegeria_cli",
    description="hey_egeria CLI commands and tools",
    source_repo="https://github.com/odpi/egeria-python.git",
    source_paths=["commands"],
    content_type=ContentType.CODE,
    language=Language.PYTHON,
    domain_terms=_get_collection_domain_terms("pyegeria_cli", _DEFAULT_PYEGERIA_CLI_TERMS),
    related_collections=["pyegeria", "egeria_docs"],
    include_patterns=["*.py", "*.md"],
    exclude_patterns=["**/__pycache__/**"],
    priority=9,
    # RAG parameters optimized for CLI code
    chunk_size=512,  # Standard for code
    chunk_overlap=100,  # 20% overlap
    min_score=0.35,  # Moderate threshold
    default_top_k=10  # More results for code queries
)

_DEFAULT_PYEGERIA_DRE_TERMS = [
    "dr-egeria", "dr_egeria", "dr egeria", "dr. egeria",
    "pyegeria dre", "pyegeria-dre", "pyegeria_dre",
    "markdown", "document-automation",
    "markdown-translator", "dre", "markdown-to-pyegeria"
]

PYEGERIA_DRE_COLLECTION = CollectionMetadata(
    name="pyegeria_drE",
    description="Dr. Egeria markdown-to-pyegeria translator",
    source_repo="https://github.com/odpi/egeria-python.git",
    source_paths=["md_processing", "docs"],
    content_type=ContentType.CODE,
    language=Language.PYTHON,
    domain_terms=_get_collection_domain_terms("pyegeria_drE", _DEFAULT_PYEGERIA_DRE_TERMS),
    related_collections=["pyegeria", "egeria_docs"],
    include_patterns=["*.py", "*.md"],
    exclude_patterns=["**/__pycache__/**"],
    priority=8,
    # RAG parameters optimized for markdown processing code
    chunk_size=512,  # Standard for code
    chunk_overlap=100,  # 20% overlap
    min_score=0.35,  # Moderate threshold
    default_top_k=8  # Standard results
)

# Phase 2: Java + Docs + Workspaces Collections
_DEFAULT_EGERIA_JAVA_TERMS = [
    "java", "java-code", "java-implementation",
    "access-service", "view-service", "integration-service",
    "governance-server", "metadata-server", "repository-proxy",
    "egeria-core", "egeria-server", "spring-boot"
]

EGERIA_JAVA_COLLECTION = CollectionMetadata(
    name="egeria_java",
    description="Egeria Java codebase - OMAS, OMAG, OMRS services",
    source_repo="https://github.com/odpi/egeria.git",
    source_paths=["."],
    content_type=ContentType.CODE,
    language=Language.JAVA,
    domain_terms=_get_collection_domain_terms("egeria_java", _DEFAULT_EGERIA_JAVA_TERMS),
    related_collections=["egeria_docs", "egeria_workspaces"],
    include_patterns=["*.java", "*.md"],
    exclude_patterns=["**/target/**", "**/build/**", "**/.gradle/**"],
    priority=7,
    enabled=True,  # Phase 2 - ENABLED
    # RAG parameters optimized for Java code (larger chunks)
    chunk_size=768,  # Larger for Java methods
    chunk_overlap=150,  # ~20% overlap
    min_score=0.35,  # Moderate threshold
    default_top_k=8  # Standard results
)

_DEFAULT_EGERIA_DOCS_TERMS = [
    "documentation", "guide", "tutorial", "concept",
    "reference", "docs", "manual", "walkthrough",
    "egeria-docs", "egeria-documentation",
    "omas", "omag", "omrs", "ocf", "oif",
    "architecture", "design", "overview",
    "myprofile", "my-profile", "my profile"
]

EGERIA_DOCS_COLLECTION = CollectionMetadata(
    name="egeria_docs",
    description="Egeria documentation - guides, tutorials, concepts (TO BE SPLIT)",
    source_repo="https://github.com/odpi/egeria-docs.git",
    source_paths=["."],
    content_type=ContentType.DOCUMENTATION,
    language=Language.MARKDOWN,
    domain_terms=_get_collection_domain_terms("egeria_docs", _DEFAULT_EGERIA_DOCS_TERMS),
    related_collections=["pyegeria", "egeria_java", "egeria_workspaces"],
    include_patterns=["*.md", "*.rst"],
    exclude_patterns=["**/node_modules/**", "**/.git/**"],
    priority=11,  # Higher than pyegeria (10) to prioritize docs when mentioned
    enabled=False,  # DISABLED - Replaced by specialized collections
    # RAG parameters - TEMPORARY (will be replaced by split collections)
    chunk_size=1024,  # Mixed content
    chunk_overlap=200,  # ~20% overlap
    min_score=0.38,  # Lower threshold for docs
    default_top_k=8  # Standard results
)

_DEFAULT_EGERIA_WORKSPACES_TERMS = [
    "workspace", "notebook", "jupyter", "example", "deployment",
    "docker", "kubernetes", "helm", "sample", "demo",
    "mcp", "model context protocol", "dr egeria", "web handler",
    "markdown command", "dr_egeria_run_block", "egeria_execute_command",
    "pyegeria web handler", "fastmcp"
]

EGERIA_WORKSPACES_COLLECTION = CollectionMetadata(
    name="egeria_workspaces",
    description="Egeria workspaces - Jupyter notebooks, deployment configs, examples, Dr. Egeria MCP server and markdown commands",
    source_repo="https://github.com/odpi/egeria-workspaces.git",
    source_paths=["."],
    content_type=ContentType.EXAMPLES,
    language=Language.MIXED,
    domain_terms=_get_collection_domain_terms("egeria_workspaces", _DEFAULT_EGERIA_WORKSPACES_TERMS),
    related_collections=["pyegeria", "egeria_java", "egeria_docs"],
    include_patterns=["*.ipynb", "*.py", "*.md", "*.yaml", "*.yml"],
    exclude_patterns=["**/node_modules/**", "**/.git/**", "**/venv/**"],
    priority=5,
    enabled=True,  # Phase 2 - ENABLED
    # RAG parameters optimized for examples and tutorials
    chunk_size=1536,  # Large for complete examples
    chunk_overlap=300,  # ~20% overlap
    min_score=0.38,  # Lower threshold for examples
    default_top_k=6  # Fewer results for examples
)


# Phase 2b: Split egeria_docs into specialized collections
_DEFAULT_EGERIA_CONCEPTS_TERMS = [
    "concept", "definition", "what is", "explain",
    "metadata", "governance", "lineage", "catalog",
    "asset", "glossary", "classification", "relationship",
    "reference data", "reference data management",
    "valid values", "valid value", "reference data set",
    "governance zone", "zone", "project", "collection",
    "agreement", "digital product", "data class",
]

EGERIA_CONCEPTS_COLLECTION = CollectionMetadata(
    name="egeria_concepts",
    description="Egeria core concepts - short definitions and explanations",
    source_repo="https://github.com/odpi/egeria-docs.git",
    source_paths=["site/docs/concepts"],
    content_type=ContentType.DOCUMENTATION,
    language=Language.MARKDOWN,
    domain_terms=_get_collection_domain_terms("egeria_concepts", _DEFAULT_EGERIA_CONCEPTS_TERMS),
    related_collections=["egeria_types", "egeria_general", "pyegeria"],
    include_patterns=["*.md"],
    exclude_patterns=["**/node_modules/**", "**/.git/**"],
    priority=12,  # Highest priority for concept queries
    enabled=True,  # ENABLED - Phase 2b
    # RAG parameters optimized for short concept definitions
    chunk_size=768,  # Medium chunks for concepts
    chunk_overlap=150,  # ~20% overlap
    min_score=0.45,  # High threshold for precise concepts
    default_top_k=5  # Fewer, more precise results
)

_DEFAULT_EGERIA_TYPES_TERMS = [
    "type", "schema", "attribute", "property",
    "entity", "relationship-type", "classification-type",
    "typedef", "type-definition", "type-system"
]

EGERIA_TYPES_COLLECTION = CollectionMetadata(
    name="egeria_types",
    description="Egeria type system - detailed type definitions and schemas",
    source_repo="https://github.com/odpi/egeria-docs.git",
    source_paths=["site/docs/types"],
    content_type=ContentType.DOCUMENTATION,
    language=Language.MARKDOWN,
    domain_terms=_get_collection_domain_terms("egeria_types", _DEFAULT_EGERIA_TYPES_TERMS),
    related_collections=["egeria_concepts", "egeria_general", "egeria_java"],
    include_patterns=["*.md"],
    exclude_patterns=["**/node_modules/**", "**/.git/**"],
    priority=11,  # High priority for type queries
    enabled=True,  # ENABLED - Phase 2b
    # RAG parameters optimized for detailed type definitions
    chunk_size=1024,  # Larger chunks for complete type definitions
    chunk_overlap=200,  # ~20% overlap
    min_score=0.42,  # High threshold for type precision
    default_top_k=6  # Moderate results for types
)

_DEFAULT_EGERIA_GENERAL_TERMS = [
    "tutorial", "guide", "how-to", "walkthrough",
    "getting-started", "setup", "configuration",
    "deployment", "installation", "usage"
]

EGERIA_GENERAL_COLLECTION = CollectionMetadata(
    name="egeria_general",
    description="Egeria general docs - tutorials, guides, and how-tos",
    source_repo="https://github.com/odpi/egeria-docs.git",
    source_paths=["site/docs"],  # All docs except concepts/ and types/
    content_type=ContentType.DOCUMENTATION,
    language=Language.MARKDOWN,
    domain_terms=_get_collection_domain_terms("egeria_general", _DEFAULT_EGERIA_GENERAL_TERMS),
    related_collections=["egeria_concepts", "egeria_types", "egeria_workspaces"],
    include_patterns=["*.md"],
    exclude_patterns=[
        "**/node_modules/**", "**/.git/**",
        "**/concepts/**",  # Exclude concepts (separate collection)
        "**/types/**"      # Exclude types (separate collection)
    ],
    priority=9,  # Lower priority than concepts/types
    enabled=True,  # ENABLED - Phase 2b
    # RAG parameters optimized for tutorials and guides
    chunk_size=1536,  # Large chunks for complete tutorials
    chunk_overlap=300,  # ~20% overlap
    min_score=0.38,  # Lower threshold for broader content
    default_top_k=8  # More results for general queries
)


_DEFAULT_EGERIA_TEMPLATES_TERMS = [
    "create", "link", "attach", "detach", "remove", "add", "set",
    "template", "command", "dr egeria", "dr_egeria",
    "glossary", "glossary term", "collection", "project", "governance",
    "data class", "data field", "data structure", "data spec",
    "digital product", "agreement", "regulation", "certification",
    "solution blueprint", "information supply chain",
    "governance zone", "governance policy", "governance definition",
    "external reference", "feedback", "rating", "comment",
]

EGERIA_TEMPLATES_COLLECTION = CollectionMetadata(
    name="egeria_templates",
    description="Dr. Egeria markdown command templates — basic and advanced, organized by family",
    source_repo="https://github.com/odpi/egeria-python.git",
    source_paths=["sample-data/templates/basic", "sample-data/templates/advanced"],
    content_type=ContentType.DOCUMENTATION,
    language=Language.MARKDOWN,
    domain_terms=_get_collection_domain_terms("egeria_templates", _DEFAULT_EGERIA_TEMPLATES_TERMS),
    related_collections=["pyegeria_drE", "pyegeria_cli", "egeria_concepts"],
    include_patterns=["*.md"],
    exclude_patterns=[],
    priority=12,  # Highest priority — primary surface for action routing
    enabled=True,
    # Each template file is a single command — keep chunks large to preserve structure
    chunk_size=2048,
    chunk_overlap=0,  # No overlap — each file is self-contained
    min_score=0.30,   # Lower threshold — intent matching is fuzzy
    default_top_k=5   # Fewer results — we want the best matching command(s)
)


# Phase 2c: Trellis itself — Resource Explorer, Egeria Advisor, and the shared
# packages both apps sit on, so EA can answer questions about its own and RE's
# codebase the same way it already answers questions about pyegeria/egeria.
_DEFAULT_TRELLIS_TERMS = [
    "trellis", "resource explorer", "resource-explorer", "egeria advisor",
    "egeria-advisor", "survey definition", "survey", "canary", "bootstrap",
    "leader election", "advisory lock", "worker role", "run queue",
    "investigation", "governance zone", "draft zone", "reconciler",
    "trellis-auth", "trellis-context", "trellis-querycache",
    "trellis-artifact-tree", "portal sso", "login required", "admin panel",
    "vector collection", "pgvector", "ingest", "clone_repos",
]

TRELLIS_COLLECTION = CollectionMetadata(
    name="trellis",
    description=(
        "Trellis monorepo — Resource Explorer and Egeria Advisor apps, "
        "their shared packages (trellis-auth, trellis-context, "
        "trellis-querycache, trellis-artifact-tree), Dr.Egeria template "
        "config, and design/backlog docs"
    ),
    source_repo="https://github.com/odpi/egeria-trellis.git",
    source_paths=["."],
    content_type=ContentType.MIXED,
    language=Language.MIXED,
    domain_terms=_get_collection_domain_terms("trellis", _DEFAULT_TRELLIS_TERMS),
    related_collections=["pyegeria", "egeria_workspaces"],
    include_patterns=["*.py", "*.md", "*.html", "*.js", "*.yaml", "*.yml"],
    # Mirrors egeria_workspaces' exclusions plus this repo's own generated/
    # vendored/state directories: .venv (uv-managed per-package envs),
    # data/ (each app's own runtime cache/feedback/repos — see admin.py's
    # _DATA_DIR fix above for why this must never recurse into itself),
    # uv.lock (huge, not prose), coverage/build artifacts.
    exclude_patterns=[
        "**/node_modules/**", "**/.git/**", "**/venv/**", "**/.venv/**",
        "**/__pycache__/**", "**/.pytest_cache/**", "**/data/**",
        "**/dist/**", "**/build/**", "**/*.egg-info/**", "**/htmlcov/**",
    ],
    priority=8,
    enabled=True,
    # Large mixed monorepo, so same chunk sizing as egeria_workspaces (its
    # closest structural analogue: also source_paths=["."] over a whole repo)
    # -- content_type differs (MIXED here, not EXAMPLES: trellis is mostly
    # real application source, not deployment examples).
    chunk_size=1536,
    chunk_overlap=300,
    min_score=0.38,
    default_top_k=6,
)


# Collection registry
ALL_COLLECTIONS: Dict[str, CollectionMetadata] = {
    "pyegeria": PYEGERIA_COLLECTION,
    "pyegeria_cli": PYEGERIA_CLI_COLLECTION,
    "pyegeria_drE": PYEGERIA_DRE_COLLECTION,
    "egeria_java": EGERIA_JAVA_COLLECTION,
    "egeria_docs": EGERIA_DOCS_COLLECTION,  # Will be disabled after split
    "egeria_concepts": EGERIA_CONCEPTS_COLLECTION,
    "egeria_types": EGERIA_TYPES_COLLECTION,
    "egeria_general": EGERIA_GENERAL_COLLECTION,
    "egeria_workspaces": EGERIA_WORKSPACES_COLLECTION,
    "egeria_templates": EGERIA_TEMPLATES_COLLECTION,
    "trellis": TRELLIS_COLLECTION,
}


def get_collection(name: str) -> Optional[CollectionMetadata]:
    """Get collection metadata by name."""
    return ALL_COLLECTIONS.get(name)


def get_enabled_collections() -> List[CollectionMetadata]:
    """Get all enabled collections."""
    return [c for c in ALL_COLLECTIONS.values() if c.enabled]


def get_collections_by_priority() -> List[CollectionMetadata]:
    """Get enabled collections sorted by priority (highest first)."""
    return sorted(get_enabled_collections(), key=lambda c: c.priority, reverse=True)


def get_phase1_collections() -> List[CollectionMetadata]:
    """Get Phase 1 (Python) collections: everything from egeria-python, the Dr.Egeria templates included (TR-50)."""
    return [
        PYEGERIA_COLLECTION,
        PYEGERIA_CLI_COLLECTION,
        PYEGERIA_DRE_COLLECTION,
        EGERIA_TEMPLATES_COLLECTION,
    ]


def get_phase2_collections() -> List[CollectionMetadata]:
    """Get Phase 2 (Java + Docs + Workspaces + Trellis) collections.

    TR-50: the docs are the three split collections; egeria_docs is disabled (REPLACED_BY) and is not listed."""
    return [
        EGERIA_JAVA_COLLECTION,
        EGERIA_CONCEPTS_COLLECTION,
        EGERIA_TYPES_COLLECTION,
        EGERIA_GENERAL_COLLECTION,
        EGERIA_WORKSPACES_COLLECTION,
        TRELLIS_COLLECTION,
    ]


#: A disabled collection and the enabled ones that replaced it (TR-51): a request for the old name is refused
#: with these words, and the clone script lists these for the repository.
REPLACED_BY: Dict[str, Tuple[str, ...]] = {
    "egeria_docs": ("egeria_concepts", "egeria_types", "egeria_general"),
}


def enable_collection(name: str) -> bool:
    """
    Enable a collection.
    
    Args:
        name: Collection name
        
    Returns:
        True if collection was enabled, False if not found
    """
    collection = get_collection(name)
    if collection:
        collection.enabled = True
        return True
    return False


def disable_collection(name: str) -> bool:
    """
    Disable a collection.
    
    Args:
        name: Collection name
        
    Returns:
        True if collection was disabled, False if not found
    """
    collection = get_collection(name)
    if collection:
        collection.enabled = False
        return True
    return False


def get_collection_summary() -> Dict[str, Any]:
    """Get summary of all collections."""
    return {
        "total": len(ALL_COLLECTIONS),
        "enabled": len(get_enabled_collections()),
        "phase1": len([c for c in get_phase1_collections() if c.enabled]),
        "phase2": len([c for c in get_phase2_collections() if c.enabled]),
        "collections": {
            name: {
                "enabled": meta.enabled,
                "priority": meta.priority,
                "content_type": meta.content_type.value,
                "language": meta.language.value,
            }
            for name, meta in ALL_COLLECTIONS.items()
        }
    }