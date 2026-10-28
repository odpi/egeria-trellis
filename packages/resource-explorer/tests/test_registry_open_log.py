"""Every registry open names the registry (host/database only) and never a credential."""
from __future__ import annotations

import logging

import pytest

from resource_explorer import registry_label as L
from resource_explorer.registry import ProjectRegistry

PW = "s3cretpw"
SHARED = f"postgresql://egeria_advisor:{PW}@localhost:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer"


@pytest.fixture(autouse=True)
def _fresh_log_state():
    ProjectRegistry._opened_logged.clear()
    yield
    ProjectRegistry._opened_logged.clear()


def test_label_shared_default_is_marked_and_has_no_credentials():
    line = L.describe_registry(SHARED)
    assert line == "registry: localhost:5442/egeria_advisor (shared) schema=resource_explorer"
    assert PW not in line and "egeria_advisor:" not in line and "@" not in line


def test_label_test_schema_is_not_shared():
    url = SHARED.replace("resource_explorer", "resource_explorer_test_9", 1).replace(
        "csearch_path%3Dresource_explorer", "csearch_path%3Dresource_explorer_test_9")
    assert "(shared)" not in L.describe_registry(url)


def test_label_sqlite_is_basename_only(tmp_path):
    assert L.describe_registry(f"sqlite:///{tmp_path}/reg.db") == "registry: sqlite:///reg.db"
    assert str(tmp_path) not in L.describe_registry(f"sqlite:///{tmp_path}/reg.db")


def test_open_logs_one_info_line(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    ProjectRegistry(database_url=f"sqlite:///{tmp_path}/a.db")
    lines = [r for r in caplog.records if r.getMessage().startswith("registry: ")]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert lines[0].getMessage() == "registry: sqlite:///a.db"


def test_open_logs_once_per_registry_not_per_construction_or_query(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    url = f"sqlite:///{tmp_path}/b.db"
    reg = ProjectRegistry(database_url=url)
    ProjectRegistry(database_url=url)
    reg.list_projects() if hasattr(reg, "list_projects") else None
    lines = [r for r in caplog.records if r.getMessage().startswith("registry: ")]
    assert len(lines) == 1


def test_no_log_record_ever_carries_the_password(tmp_path, caplog, monkeypatch):
    """Even the pre-existing registry_init timing line must not leak the URL."""
    caplog.set_level(logging.DEBUG)
    import resource_explorer.registry as R
    ticks = iter(range(0, 10_000, 1))
    monkeypatch.setattr("time.perf_counter", lambda: next(ticks))   # force >5ms
    ProjectRegistry(database_url=f"sqlite:///{tmp_path}/c.db?x={PW}")
    assert any(r.getMessage().startswith("registry_init") for r in caplog.records)
    assert PW not in caplog.text
    assert PW not in "".join(r.getMessage() for r in caplog.records)
    assert R  # silence linter


def test_describe_registry_prints_only_host_port_and_database_even_for_an_awkward_password():
    url = "postgresql://dwolfson:p%40ss%3Aw%2Frd@db.example:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer"
    line = L.describe_registry(url)
    assert line == "registry: db.example:5442/egeria_advisor schema=resource_explorer"
    for leak in ("dwolfson", "p%40ss", "p@ss", "w%2Frd", "w/rd"):
        assert leak not in line
    assert "<unparseable url>" == L.describe_registry("postgresql://u:pw@[bad/db").split("registry: ")[-1]
