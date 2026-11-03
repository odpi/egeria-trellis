"""A fresh SQLite registry file starts as a copy of one built once per process.

Measured 2026-10-29 (re-test-speedup): `ProjectRegistry(db_path=<new file>)` costs
~170 ms, almost all of it `_init_schema` creating 102 tables and their indexes one
statement at a time. Opening the same file a second time costs ~6 ms, because every
statement is `IF NOT EXISTS` / guarded. Nearly every test builds at least one fresh
tmp_path registry, and fixture setup was ~90% of the suite's wall time.

So: at session start conftest builds ONE registry in a temp directory (`build()`),
and from then on, when `_run_schema_setup` is about to run against a SQLite file
that does not exist yet (or is empty), the file is first created as a byte copy of
that template. `_run_schema_setup` and `_init_schema` then run exactly as before,
on the copy, and find nothing to do. Nothing is skipped: the same code runs over
the same end state, it just has no DDL left to issue.

The end state is identical, not merely similar: a `sqlite3.iterdump()` of a fresh
file and of a copy-then-init file compared equal when this was written (the schema
plus the seeded `annotation_types` rows, no timestamps). Each test still gets its
own file, so isolation between tests is unchanged.

What is visibly different, and how a test that cares opts out:

* The start-up line says `registry: no schema change` instead of
  `registry: created 102 tables: ...`, because on the copy nothing was created.
* A test that asserts what `_init_schema` DOES to an empty file (spies on its
  statements, counts its writes) sees it do nothing.

Those tests carry `@pytest.mark.fresh_registry_schema`, and get the original
empty-file path. Also never copied, without a marker: a non-empty existing file (a
test building an old schema to migrate), `:memory:`, any Postgres URL, and any run
where a test has replaced `ProjectRegistry._init_schema`/`_table_names` or the
annotation-type seed list, since the template was built by the originals.

Off switch for comparing against the old behaviour: RE_TESTS_NO_REGISTRY_TEMPLATE=1.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

OFF_ENV = "RE_TESTS_NO_REGISTRY_TEMPLATE"

_state: dict[str, Any] = {
    "installed": False,
    "template": None,          # Path of the built template file, once build() ran
    "suspended": False,         # True for the duration of a fresh_registry_schema test
    "originals": None,          # (init_schema, table_names, seed list) the template was built by
}


def _sqlite_file(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    path = database_url[len("sqlite:///"):]
    if not path or path == ":memory:" or "mode=memory" in path:
        return None
    return Path(path.split("?", 1)[0])


def _originals(cls: type) -> tuple:
    """What the schema of a fresh file depends on, as seen from `cls` (a test may subclass)."""
    from resource_explorer.surveyors import survey_report

    return (getattr(cls, "_init_schema", None), getattr(cls, "_table_names", None),
            survey_report.ANNOTATION_TYPES_REGISTRY)


def _should_copy(target: Path | None, cls: type) -> bool:
    if target is None or _state["template"] is None or _state["suspended"]:
        return False
    if os.environ.get(OFF_ENV, "").strip().lower() not in ("", "0", "false", "no"):
        return False
    if target.exists() and target.stat().st_size > 0:
        return False
    current = _originals(cls)
    return all(a is b for a, b in zip(current, _state["originals"]))


def install() -> None:
    """Idempotent. Wraps ProjectRegistry._run_schema_setup for the life of the process."""
    if _state["installed"]:
        return
    from resource_explorer.registry import ProjectRegistry

    real = ProjectRegistry._run_schema_setup

    def _run_schema_setup(self) -> None:
        target = _sqlite_file(self.database_url)
        if _should_copy(target, type(self)):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_state["template"], target)
        return real(self)

    _run_schema_setup.__wrapped__ = real  # type: ignore[attr-defined]
    ProjectRegistry._run_schema_setup = _run_schema_setup  # type: ignore[method-assign]
    _state["installed"] = True


def build(directory: Path) -> Path:
    """Build the template by the ordinary path, in `directory`. Call before any test runs."""
    from resource_explorer.registry import ProjectRegistry

    _state["template"] = None
    path = Path(directory) / "registry-template.db"
    if path.exists():
        path.unlink()
    registry = ProjectRegistry(database_url=f"sqlite:///{path}")
    registry.engine.dispose()   # nothing pending or open when the bytes are copied
    _state["originals"] = _originals(ProjectRegistry)
    _state["template"] = path
    return path


def suspended(value: bool) -> None:
    _state["suspended"] = value
