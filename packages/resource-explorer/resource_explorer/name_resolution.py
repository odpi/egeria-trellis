"""Turn the name a person used for a database into the registered slug, or say it is ambiguous.

A database is registered as `laz_local_adventureworks` but people (and the model) say
"adventureworks". Matching only the slug sent such a question to "not found", which the model then
phrased as missing data. Order of attempts, first unique hit wins: the slug, the display name, the
database's own name, a slug that ends in `_<name>`. More than one hit at the same step is
ambiguous and is returned as such: guessing which of several same-named databases was meant would
answer about the wrong one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry


def _fold(text: str) -> str:
    return (text or "").strip().lower().replace("-", "_").replace(" ", "_")


def resolve_database(registry: "ProjectRegistry", name: str) -> tuple[str | None, list[str]]:
    """`(slug, [])` for one match, `(None, [slugs])` for several, `(None, [])` for none."""
    if not name or not name.strip():
        return None, []
    exact = registry._normalize_slug(name)
    if registry.get_database(exact, allow_unreadable=True):
        return exact, []

    wanted = _fold(name)
    databases = registry.list_databases()
    steps = (
        lambda d: _fold(d.display_name) == wanted,
        lambda d: _fold(d.database_name or "") == wanted,
        lambda d: _fold(d.slug).endswith("_" + wanted),
    )
    for matches_step in steps:
        hits = [d.slug for d in databases if matches_step(d)]
        if len(hits) == 1:
            return hits[0], []
        if len(hits) > 1:
            return None, sorted(hits)
    return None, []


def ambiguity_message(name: str, candidates: list[str]) -> str:
    return (
        f"'{name}' matches more than one registered database: {', '.join(candidates)}. "
        "Ask again with the full name of the one you mean."
    )
