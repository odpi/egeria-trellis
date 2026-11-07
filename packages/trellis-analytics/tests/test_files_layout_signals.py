"""Files build step 7: fs_layout_signals, the recognisable structures in a file system from its walk's names (F10).

Installed the way pip would (a dist-info carrying its own entry point) and run through the real `SurveyStep` wrapper
against a real ProjectRegistry on a scratch SQLite file, read through `OdsReader`.

Pinned: the guard (no walk, or a walk that kept no rows: "not measured"), a walk with no signal ("nothing_found", never a zero
that reads as measured), each structure's recognition, the capped and "not recorded" caveats, and that no file,
folder or noise-directory path reaches a result.
"""
import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "examples" / "steps" / "fs_layout_signals"


@pytest.fixture
def layout_installed(tmp_path, monkeypatch):
    project = tomllib.loads((PKG / "pyproject.toml").read_text())["project"]
    version = re.search(r'__version__ = "([^"]+)"', (PKG / "src" / "fs_layout_signals" / "__init__.py").read_text()).group(1)
    d = tmp_path / "site" / f"{project['name'].replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n")
    (d / "entry_points.txt").write_text("[pyegeria.analytics]\n" + "".join(
        f"{k} = {v}\n" for k, v in project["entry-points"]["pyegeria.analytics"].items()))
    monkeypatch.syspath_prepend(str(PKG / "src"))
    monkeypatch.syspath_prepend(str(tmp_path / "site"))


@pytest.fixture
def fs_registry(tmp_path):
    """(registry, url). Never the shared registry: a scratch SQLite file."""
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "ods.sqlite"))
    assert str(tmp_path) in reg.database_url
    reg.register_filesystem(FileSystemEntity(slug="tree", display_name="tree", local_mount_point=str(tmp_path)))
    return reg, reg.database_url


def row(path, size=10):
    return {"file_path": path, "folder_path": path.rpartition("/")[0], "folder_depth": path.count("/"),
            "size_bytes": size, "modified_at": "2026-10-01T00:00:00", "accessed_at": None, "owner_name": "alice",
            "group_name": "staff", "mode_octal": "0644", "world_readable": 1, "content_hash": "",
            "hash_state": "over_limit"}


def walk(reg, run_id, rows, *, capped=False, not_walked=None):
    """One stored walk: its per-file rows and its filesystem_surveys row, as RE writes both. `not_walked=None` stores
    a walk from before the noise directories were counted."""
    reg.add_filesystem_walk_files("tree", run_id, rows)
    limits = {"capped": capped, "max_entries": 100000, "capped_reasons": ["entry cap"] if capped else []}
    if not_walked is not None:
        limits["not_walked_by_name"] = not_walked
    reg.add_filesystem_survey("tree", run_id, {"surveyed_at": run_id, "total_files": len(rows), "walk_limits": limits})


def run(url):
    from trellis_analytics.discovery import discover
    from trellis_analytics_re.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    host = HostContext(resource_type="filesystem", slug="tree", producing_run="run-x", ods=OdsReader(url))
    out = SurveyStep(discover()["fs_layout_signals"]).run(host)
    assert len(out) == 1
    return out[0]


def state(a):
    return a.envelope.result_state.value


def props(a):
    """The result's values as published: every value a string."""
    return a.resource_properties


def test_the_manifest_declares_in_full(layout_installed):
    from trellis_analytics.discovery import discover

    m = discover()["fs_layout_signals"]
    assert m.resource_types == ["filesystem"] and m.inputs == ["ods"] and m.credential_tier == "none"
    assert m.declaration is not None and m.declaration.is_complete(), m.declaration.missing()
    assert "not measured" in m.description and m.nothing_to_derive_guard == "survey-completed"
    assert m.stage == "discovery" and m.questions == ["Q-0104"]
    assert m.annotation_types() == ["ResourceProfileAnnotation"]


def test_no_walk_is_not_measured(layout_installed, fs_registry):
    a = run(fs_registry[1])
    assert state(a) == "not_established" and "no walk" in a.summary


def test_a_walk_that_kept_no_rows_has_no_run_to_read_so_is_not_measured(layout_installed, fs_registry):
    # The walk runs are read from the kept rows, so a walk that kept none is not a run here: "no walk yet", never 0.
    reg, url = fs_registry
    walk(reg, "2026-10-28T09:00:00", [], not_walked={})
    a = run(url)
    assert state(a) == "not_established" and "no walk yet" in a.summary


def test_a_walk_with_no_signal_is_nothing_found_naming_what_was_looked_for(layout_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "2026-10-28T09:00:00", [row("notes/a.txt"), row("photos/b.jpg")], not_walked={})
    a = run(url)
    assert state(a) == "nothing_found"
    assert "no project manifest" in a.summary and "no archive or backup" in a.summary
    assert props(a)["Recognised Structures"] == "0"


def test_each_structure_is_recognised_with_its_counts(layout_installed, fs_registry):
    reg, url = fs_registry
    rows = [row("app/pyproject.toml"), row("app/README.md"), row("app/src/schema.py"),
            row("lake/2024-10/a.parquet"), row("lake/2024-11/b.parquet"), row("lake/region=eu/c.parquet"),
            row("lake/codebook.txt"),
            row("old/site.zip", 2048), row("db/nightly.sql.gz", 4096), row("backups/2024/x.csv", 100),
            row("cfg/app.conf.bak", 5)]
    walk(reg, "2026-10-28T09:00:00", rows, not_walked={".git": 2, "node_modules": 5})
    a = run(url)
    v = a.resource_properties
    assert state(a) == "measured"
    assert v["Project Manifests"] == "1" and v["Git Directories Not Walked"] == "2"
    # dated folders: lake/2024-10, lake/2024-11, backups/2024; one partition folder
    assert v["Dated Folders"] == "3" and v["Partition Folders"] == "1"
    # codebook.txt counts; schema.py is code, not a dictionary
    assert v["Data Dictionary Or Schema Files"] == "1"
    # a .sql.gz dump is a backup, not an archive
    assert v["Archive Files"] == "1" and v["Archive Bytes"] == "2048"
    assert v["Backup Files"] == "3" and v["Backup Folders"] == "1"
    assert v["Folders With A README"] == "1"
    assert v["Recognised Structures"] == "4"
    assert v["Recognised As"] == "a project; a dataset layout; an archive; backups"
    assert "a project (1 project manifest, 2 .git directories not walked)" in a.summary


def test_a_git_directory_alone_recognises_a_project(layout_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "2026-10-28T09:00:00", [row("src/main.c")], not_walked={".git": 1})
    a = run(url)
    assert state(a) == "measured" and "a project (1 .git directory not walked)" in a.summary


def test_one_dated_folder_is_not_yet_a_dataset_layout(layout_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "2026-10-28T09:00:00", [row("2024/a.csv")], not_walked={})
    a = run(url)
    assert state(a) == "nothing_found" and props(a)["Dated Folders"] == "1"


def test_a_walk_that_did_not_record_its_skips_says_not_recorded_never_zero(layout_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "2026-10-28T09:00:00", [row("a/package.json")], not_walked=None)
    a = run(url)
    assert a.resource_properties["Git Directories Not Walked"] == "not recorded on this walk"
    assert ".git directories not recorded on this walk" in a.summary


def test_a_capped_walk_says_the_counts_are_of_the_walked_part(layout_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "2026-10-28T09:00:00", [row("a/x.bak")], capped=True, not_walked={})
    a = run(url)
    assert a.resource_properties["Walk Capped"] == "yes"
    assert "the walk was capped, so this is the walked part only" in a.summary


def test_the_newest_walk_is_the_one_read(layout_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "2026-10-27T09:00:00", [row("a/x.bak")], not_walked={})
    walk(reg, "2026-10-28T09:00:00", [row("a/x.txt")], not_walked={})
    a = run(url)
    assert state(a) == "nothing_found" and a.resource_properties["asOf"] == "2026-10-28T09:00:00"


def test_no_path_reaches_a_result(layout_installed, fs_registry):
    reg, url = fs_registry
    rows = [row("secret-project/pyproject.toml"), row("customer-archive/q3.zip"), row("hr-backups/p.bak")]
    walk(reg, "2026-10-28T09:00:00", rows, not_walked={".git": 1})
    a = run(url)
    text = json.dumps({"s": a.summary, "e": a.explanation, "v": a.resource_properties})
    for name in ("secret-project", "customer-archive", "hr-backups", "q3.zip", "p.bak", "pyproject.toml"):
        assert name not in text, name


# ── a Delta Lake table (S3 slice Part B, 2026-11-02) ─────────────────────────────────────────────────────────────────

def test_a_delta_log_folder_recognises_a_delta_lake_table_and_says_the_log_was_not_read(layout_installed, fs_registry):
    reg, url = fs_registry
    rows = [row("demo/sales/_delta_log/00000000000000000000.json"), row("demo/sales/_delta_log/00000000000000000001.json"),
            row("demo/sales/part-0.parquet"), row("demo/sales/part-1.parquet"),
            row("demo/loose/part-9.parquet")]                 # Parquet outside any table is not a part of one
    walk(reg, "2026-11-02T09:00:00", rows, not_walked={})
    a = run(url)
    v = a.resource_properties
    assert state(a) == "measured"
    assert v["Delta Lake Tables"] == "1" and v["Delta Lake Parquet Parts"] == "2"
    assert v["Recognised As"] == "a Delta Lake table"
    assert ("a Delta Lake table (1 table, 2 Parquet parts as found · the Delta log was not read, so retired parts may "
            "be included)") in a.summary
    assert "demo/sales" not in json.dumps({"s": a.summary, "v": a.resource_properties})   # no path is published


def test_parquet_files_alone_are_not_a_delta_table(layout_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "2026-11-02T09:00:00", [row("x/part-0.parquet"), row("x/_delta_log/README.md")], not_walked={})
    a = run(url)
    assert state(a) == "nothing_found" and props(a)["Delta Lake Tables"] == "0"
    assert "no Delta Lake _delta_log folder" in a.summary
