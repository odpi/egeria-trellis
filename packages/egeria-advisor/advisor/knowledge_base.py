"""Is EA's knowledge base built at all? (TR-49, newcomer install, Rocky 2026-11-18.)

A fresh install has every vector collection empty. EA then answered each question "I don't have enough
information …", which reads like a model failure. When retrieval finds nothing AND every enabled collection
holds zero rows, EA says plainly that its knowledge base hasn't been built, and names the commands that build it.

Only a confirmed count says "not built": if any collection can't be counted, nothing is claimed (the existing
retrieval diagnosis stands).
"""
from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

#: From the repository root, after `make sync`. `.venv/bin/python`, not `uv run` in a package directory, which
#: can re-sync the workspace.
BUILD_COMMANDS = (
    "`.venv/bin/python packages/egeria-advisor/scripts/clone_repos.py --phase all` (fetches the Egeria repositories)",
    "`.venv/bin/python packages/egeria-advisor/scripts/ingest_collections.py --phase all` (indexes them)",
)


def not_built_message(count: int) -> str:
    steps = "\n".join(f"{i}. {c}" for i, c in enumerate(BUILD_COMMANDS, 1))
    return (f"My knowledge base hasn't been built yet: all {count} of its collections are empty, so I have nothing "
            f"from the Egeria code and documentation to answer from. This isn't a problem with the model or your "
            f"question. To build it, from the repository root run:\n{steps}\nThen ask again.")


def enabled_collection_names() -> List[str]:
    from advisor.collection_config import get_enabled_collections
    return [c.name for c in get_enabled_collections()]


def row_counts(store: Any, names: List[str]) -> Optional[List[int]]:
    """Rows in each collection (0 for one that doesn't exist yet), or None when any can't be counted."""
    counts = []
    for name in names:
        try:
            stats = store.get_collection_stats(name)
            counts.append(int((stats or {}).get("num_entities") or 0))
        except Exception as exc:  # noqa: BLE001 -- can't count: claim nothing
            logger.info(f"knowledge base: could not count {name}: {type(exc).__name__}: {exc}")
            return None
    return counts


def not_built(store: Any, names: Optional[List[str]] = None) -> Optional[str]:
    """The words for an unbuilt knowledge base, or None (built, partly built, or couldn't tell)."""
    names = enabled_collection_names() if names is None else names
    if not names or store is None:
        return None
    counts = row_counts(store, names)
    if counts is None or any(counts):
        return None
    return not_built_message(len(names))
