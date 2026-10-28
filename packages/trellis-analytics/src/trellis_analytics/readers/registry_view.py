"""A read-only stand-in for RE's `ProjectRegistry`, just wide enough for `catalogue_scope.resolve_node_set`.

Curate's scope tree gets its nodes from `resource_explorer.catalogue_scope.resolve_node_set(registry, slug)`.
A routine that counts schemas must count the same ones, so it calls that same function. That function only
needs the methods listed in `COVERED_METHODS`; this adapter answers exactly those, by running the registry's
OWN method bodies (`ProjectRegistry.<name>(adapter, ...)`) over a read-only connection. No SQL is mirrored,
so the two cannot drift; `tests/test_registry_view.py` pins that each returns what a real registry returns.

It never constructs `ProjectRegistry` (whose constructor runs migrations). Anything outside the list raises
AttributeError, so a new dependency in `resolve_node_set` fails loudly instead of reading something else.

`resource_explorer` is an optional dependency (extra `re`): importing this module without it raises
`ResourceExplorerMissing`, which the routine turns into `not_applicable`.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

COVERED_METHODS = (
    "_conn", "_normalize_slug", "query_detail_rows", "get_database_surveys",
    "list_native_survey_runs", "count_native_survey_annotations", "query_native_survey_annotations",
)


class ResourceExplorerMissing(ImportError):
    """resource_explorer is not installed (install trellis-analytics[re])."""


def _registry_module():
    try:
        import resource_explorer.registry as reg
    except ImportError as exc:
        raise ResourceExplorerMissing(f"resource_explorer is not installed: {exc}") from exc
    return reg


class RegistryReadView:
    def __init__(self, url: str):
        self.database_url = url
        self._reg = _registry_module()
        self._database_surveys_cache: dict = {}

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        is_pg = self.database_url.startswith("postgresql")
        if is_pg:
            import psycopg2
            raw = psycopg2.connect(self.database_url)
            raw.set_session(readonly=True, autocommit=True)
        else:
            raw = sqlite3.connect(f"file:{self.database_url.removeprefix('sqlite:///')}?mode=ro", uri=True)
            raw.row_factory = sqlite3.Row
        conn = self._reg.ConnectionWrapper(raw, is_pg)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _normalize_slug(slug: str) -> str:
        return slug.replace("-", "_").lower()

    def _database_surveys_freshness(self, slug: str):
        return self._reg.ProjectRegistry._database_surveys_freshness(self, slug)

    def query_detail_rows(self, *a, **kw):
        return self._reg.ProjectRegistry.query_detail_rows(self, *a, **kw)

    def get_database_surveys(self, *a, **kw):
        return self._reg.ProjectRegistry.get_database_surveys(self, *a, **kw)

    def list_native_survey_runs(self, *a, **kw):
        return self._reg.ProjectRegistry.list_native_survey_runs(self, *a, **kw)

    def count_native_survey_annotations(self, *a, **kw):
        return self._reg.ProjectRegistry.count_native_survey_annotations(self, *a, **kw)

    def query_native_survey_annotations(self, *a, **kw):
        return self._reg.ProjectRegistry.query_native_survey_annotations(self, *a, **kw)
