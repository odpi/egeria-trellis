"""Does a repository have its own chat index? One predicate, one source.

A project "has its own index" only if it lists at least one collection named
`f"{slug}_{type}"` for a type in `COLLECTION_TYPES`. A collection the project merely
lists, such as the shared `web_docs_*` site collections other projects list too, is
not the repository's own content and does not count.

The incremental indexer, the RAG-ingestion surveyor, the project API and the /next
UI all ask this module. Do not write a second copy of the rule: on 2026-10-19 the
indexer, which asked "does it have collections named slug_type" and the surveyor,
which asked "is the list non-empty", disagreed about the same project and one of
them reported a shared site's chunk count as the repository's own.
"""
from __future__ import annotations

from typing import Iterable

INDEXED = "indexed"
NOT_YET_INDEXED = "not_yet_indexed"


def own_collections(slug: str, collections: Iterable[str] | None) -> list[str]:
    """The listed collections that belong to `slug`'s own repository content."""
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES

    own = {f"{slug}_{name}" for name in COLLECTION_TYPES}
    return [c for c in (collections or []) if c in own]


def shared_collections(slug: str, collections: Iterable[str] | None) -> list[str]:
    """The listed collections that are NOT `slug`'s own (e.g. `web_docs_*`)."""
    mine = set(own_collections(slug, collections))
    return [c for c in (collections or []) if c not in mine]


def has_own_index(slug: str, collections: Iterable[str] | None) -> bool:
    return bool(own_collections(slug, collections))


def index_state(project) -> str:
    """`"indexed"` or `"not_yet_indexed"`, derived from the persisted collections list."""
    return INDEXED if has_own_index(project.slug, project.collections) else NOT_YET_INDEXED
