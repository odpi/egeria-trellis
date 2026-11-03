"""B1: the table-rows seam is the library's protocol; a host installs its reader as an entry point (Architect and the
LS2 seam, 2026-11-02). The library module imports no host."""
import ast
from pathlib import Path

from trellis_analytics.readers import table_rows as seam


class _Reader:
    def table_rows(self, run, declaration, limit=-1):
        return {"stored": True, "run_id": run.get("run_id"), "total": 1, "shown": 1, "rows": [{"k": 1}]}


def test_a_reader_satisfies_the_protocol_by_shape():
    r: seam.TableRowsReader = _Reader()
    run: seam.TableRun = {"resource_kind": "database", "slug": "s", "analysis_id": "a", "run_id": "r1"}
    assert r.table_rows(run, None)["run_id"] == "r1"
    assert seam.TABLE_ROWS == "table_rows"


def test_the_seam_module_imports_no_host():
    tree = ast.parse(Path(seam.__file__).read_text(encoding="utf-8"))
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not [n for n in names if n.startswith("resource_explorer")]
    assert not hasattr(seam, "register_table_rows_reader")
