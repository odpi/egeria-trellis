"""The registry's start-up schema setup logs exactly one line saying what it changed."""
from __future__ import annotations

import logging

import pytest

from resource_explorer.registry import ProjectRegistry

LOGGER = "resource_explorer.registry"
# not the separate "registry: <label>" open line
_PREFIXES = ("registry: created", "registry: no schema", "registry: schema change")


def _lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.getMessage().startswith(_PREFIXES)]


@pytest.mark.fresh_registry_schema   # about what setup does to an EMPTY file
def test_fresh_database_reports_created_tables(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        ProjectRegistry(db_path=str(tmp_path / "a.db"))
    (line,) = _lines(caplog)
    assert line.startswith("registry: created ")
    assert "tables: " in line and "projects" in line


def test_unchanged_schema_reports_no_change(tmp_path, caplog):
    path = str(tmp_path / "b.db")
    ProjectRegistry(db_path=path)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        ProjectRegistry(db_path=path)
    assert _lines(caplog) == ["registry: no schema change"]


def test_extra_migration_adding_one_table_is_named(tmp_path, caplog, monkeypatch):
    path = str(tmp_path / "c.db")
    ProjectRegistry(db_path=path)
    original = ProjectRegistry._init_schema

    def with_extra_table(self):
        original(self)
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS zz_fake_migration (id INTEGER)")

    monkeypatch.setattr(ProjectRegistry, "_init_schema", with_extra_table)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        ProjectRegistry(db_path=path)
    assert _lines(caplog) == ["registry: created 1 tables: zz_fake_migration"]


def test_introspection_error_is_reported_and_startup_continues(tmp_path, caplog, monkeypatch):
    def boom(self):
        raise RuntimeError("catalog unreadable")

    monkeypatch.setattr(ProjectRegistry, "_table_names", boom)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        reg = ProjectRegistry(db_path=str(tmp_path / "d.db"))
    assert _lines(caplog) == ["registry: schema change not checked (RuntimeError)"]
    # start-up continued: the schema exists and the registry is usable
    with reg._conn() as conn:
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0


def test_after_introspection_error_is_reported(tmp_path, caplog, monkeypatch):
    calls = {"n": 0}
    real = ProjectRegistry._table_names

    def second_fails(self):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("gone")
        return real(self)

    monkeypatch.setattr(ProjectRegistry, "_table_names", second_fails)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        ProjectRegistry(db_path=str(tmp_path / "e.db"))
    assert _lines(caplog) == ["registry: schema change not checked (ValueError)"]
