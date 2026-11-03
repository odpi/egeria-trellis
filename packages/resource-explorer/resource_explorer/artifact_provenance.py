"""How a reader says what a symbol or marker row knows about where it came from:
its file's role and the commit its extraction surveyed (artifacts slice 1,
DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md §3 and §6).

Both columns are NULL on rows written before they existed. NULL is shown as
"not recorded", never filled in with a guess: today's role of the same path, or
the project's current commit, would be a confident claim about a run that did
not record it.
"""
from __future__ import annotations

NOT_RECORDED = "not recorded"


def role_label(role: str | None) -> str:
    return role if role else f"role {NOT_RECORDED}"


def commit_label(sha: str | None) -> str:
    return f"commit {sha[:12]}" if sha else f"commit {NOT_RECORDED}"


#: StepOutcome labels that mean the extraction ran to the end (step_outcome.py). A failed extraction
#: reports none (it raised, or BaseSurveyor._warn'd a step_error), so its row is skipped.
_COMPLETED_OUTCOMES = frozenset({"recovered", "partial", "no_signal", "regression"})


def extraction_commit(registry, slug: str) -> str | None:
    """The commit the latest SUCCESSFUL symbol extraction of this repo read, from
    its own step_runs row (`survey_commit_sha`). A failed extraction after it does
    not hide it. None when no successful extraction is recorded, or it recorded no
    commit (read no zipball). Never the project's `last_commit_sha`."""
    try:
        rows = registry.query_step_runs(slug=slug, step_key="repo_symbol_extraction",
                                        entity_type="repo", limit=200)
    except Exception:
        return None
    for row in rows:  # newest first
        outcomes = set((row.get("metrics") or {}).get("outcomes") or ())
        if outcomes & _COMPLETED_OUTCOMES:
            return row.get("survey_commit_sha") or None
    return None
