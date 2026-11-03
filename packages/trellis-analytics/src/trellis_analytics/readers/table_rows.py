"""The table-rows seam: how a reader hands a table result's stored rows over, through the declaration (B1,
DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02 §4; Architect ruling 2026-11-02: the PROTOCOL is the library's, the
implementation is the host's).

A host that stores table results (Resource Explorer: `analysis_table_rows`) installs one reader as the
`table_rows` entry point of the `trellis_analytics.readers` group; a step, a renderer or a compile opens it by name
and never knows where the rows are kept. An entry point, not a runtime registration: it is found wherever the host is
installed, including a process (the Portal, pyegeria's resolver) that never imported the host. The factory takes what
it needs (a registry, a URL) as constructor arguments and resolves nothing at import. The library stores nothing and
opens nothing, and this module imports no host.

What `table_rows` returns, for the declaration's table:
    {"stored": bool, "run_id", "total", "shown", "truncation", "fields", "key_fields", "order", "columns", "rows"}
`rows` are the first `limit` stored rows (the declaration's `max_rows` by default; None for every row), in the declared
order, each typed by its column and with an empty cell worded by its column's `absence`; `total` counts every stored
row; `stored` False (total 0) when no run stored rows, which is never "measured, no rows".
"""
from __future__ import annotations

from typing import Any, Protocol, TypedDict


class TableRun(TypedDict, total=False):
    """Which stored table: the resource, the analysis, and the run (absent or None: the latest)."""
    resource_kind: str
    slug: str
    analysis_id: str
    run_id: str | None


class TableRowsReader(Protocol):
    def table_rows(self, run: TableRun, declaration: Any, limit: int | None = -1) -> dict:
        """The typed read of one table result; `limit` -1 means the declaration's `max_rows`."""
        ...


#: The reader's entry-point name in the `trellis_analytics.readers` group.
TABLE_ROWS = "table_rows"
