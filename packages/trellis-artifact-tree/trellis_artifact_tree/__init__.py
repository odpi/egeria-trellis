"""Shared containment tree over ingested artifacts.

See README.md for why this is a package rather than one app's table.

CodeAdapter and PdfAdapter are importable without their extras installed -- they
import tree_sitter and docling inside parse(), so a missing extra surfaces at
the point of use with an install hint rather than as an import-time stack trace.
Neither is in AdapterRegistry's defaults: the default registry stays stdlib-only
(markdown plus the generic fallback), and an app registers what it installed.
"""
from trellis_artifact_tree.adapters import (
    Adapter,
    AdapterRegistry,
    GenericTextAdapter,
    HtmlAdapter,
    MarkdownAdapter,
)
from trellis_artifact_tree.adapters_code import CodeAdapter
from trellis_artifact_tree.adapters_pdf import (
    DoclingDocumentAdapter,
    DocItem,
    items_from_docling,
    PdfAdapter,
    tree_from_items,
)
from trellis_artifact_tree.config import ArtifactTreeConfig
from trellis_artifact_tree.diagnostics import TreeDiagnosis, diagnose
from trellis_artifact_tree.profile import SliceProfile, compare_to, profile_trees
from trellis_artifact_tree.model import (
    ArtifactTree,
    Node,
    Provenance,
    Rung,
    TreeError,
    validate,
)
from trellis_artifact_tree.schema import create_schema_sql
from trellis_artifact_tree.store import ArtifactTreeStore, SchemaNotReady

__all__ = [
    "Adapter",
    "AdapterRegistry",
    "ArtifactTree",
    "ArtifactTreeConfig",
    "ArtifactTreeStore",
    "SchemaNotReady",
    "CodeAdapter",
    "DocItem",
    "TreeDiagnosis",
    "DoclingDocumentAdapter",
    "GenericTextAdapter",
    "HtmlAdapter",
    "MarkdownAdapter",
    "PdfAdapter",
    "Node",
    "Provenance",
    "SliceProfile",
    "Rung",
    "TreeError",
    "create_schema_sql",
    "compare_to",
    "diagnose",
    "profile_trees",
    "items_from_docling",
    "tree_from_items",
    "validate",
]
