"""Candidate resources to offer when a cross-resource question names too few.

Shared by the web chat (disambiguation chips) and the A2A compare/integration agents
(`input_required` choices), so both surfaces offer the same candidates in the same order.
"""
from __future__ import annotations

CHIP_LIMIT = 8


def rank_chip_candidates(
    query: str, candidates: list[dict], named: list[str], scope_slug: str | None
) -> list[dict]:
    """Order candidate chips for a cross-resource question and cap them.

    Resources the question already names are dropped (they are not a choice). The resource the user
    has open comes first; the rest rank by how many words of the question appear in the slug or
    display name, then alphabetically so the order is stable.
    """
    import re

    taken = set(named)
    words = {w for w in re.split(r"[^a-z0-9]+", query.lower()) if len(w) >= 3}
    # A named resource's own words ("repo" in repo_a) would otherwise boost every sibling that shares them.
    for slug in named:
        words -= set(re.split(r"[^a-z0-9]+", slug.lower()))

    def _score(chip: dict) -> tuple:
        tokens = {t for t in re.split(r"[^a-z0-9]+", f"{chip['slug']} {chip['name']}".lower()) if t}
        return (
            0 if chip["slug"] == scope_slug else 1,
            -len(words & tokens),
            chip["slug"],
        )

    return sorted((c for c in candidates if c["slug"] not in taken), key=_score)[:CHIP_LIMIT]


def candidate_chips(query: str, named: list[str], scope_slug: str | None = None) -> list[dict]:
    """Ranked repo and database candidates from the registry, minus what the question names."""
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry()
    candidates = [
        {"slug": p.slug, "name": p.display_name or p.slug, "type": "repo"} for p in reg.list_all()
    ] + [
        {"slug": d.slug, "name": d.display_name or d.slug, "type": "database"}
        for d in reg.list_databases()
    ]
    return rank_chip_candidates(query, candidates, named, scope_slug)
