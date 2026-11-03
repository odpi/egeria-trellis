"""Files build step 6 in Resource Explorer: the walk keeps access times, file systems take Analytic Library steps, the
per-analysis run route, the card's derived results and the price line before the Survey press (F12).

The four steps themselves (fs_change_rates, fs_duplicates, fs_activity, fs_permissions) are tested in
trellis-analytics (tests/test_files_derived_steps.py). Here they are installed the way pip would and run through RE's
own host, executor, run queue and routes, on a temp SQLite registry. Nothing contacts Egeria; nothing is published.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from resource_explorer.registry import FileSystemEntity, ProjectRegistry

FS_STEPS = ("fs_activity", "fs_change_rates", "fs_duplicates", "fs_layout_signals", "fs_permissions")
PKG = Path(__file__).resolve().parents[1]


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "files6.db"))


@pytest.fixture
def fs_installed(tmp_path, monkeypatch):
    """The file system steps (step 6's four and step 7's fs_layout_signals) installed (dist-info + source on the path) and attached; put back afterwards."""
    from tests.library_step_install import _entries

    from resource_explorer.surveyors import survey_step_host

    for entry in reversed(_entries(tmp_path, FS_STEPS)):
        monkeypatch.syspath_prepend(entry)
    survey_step_host.refresh("filesystem")
    yield
    monkeypatch.undo()
    survey_step_host.refresh("filesystem")


def _tree(tmp_path) -> Path:
    root = tmp_path / "tree"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "x.txt").write_text("same")
    (root / "b" / "y.txt").write_text("same")
    (root / ".env").write_text("K=V")
    (root / ".env").chmod(0o644)
    return root


def _register(registry, root, slug="tree"):
    registry.register_filesystem(FileSystemEntity(slug=slug, display_name=slug, local_mount_point=str(root)))
    return registry.get_filesystem(slug)


def _walk(registry, fs):
    """RE's own walk with its extensions, stored the way the folder survey stores it."""
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    sd = LocalFileSystemSurveyor(fs, registry).run(profile_contents=False, walk_extensions=True)
    we.attach_extensions(fs, registry, sd, led_by="re")
    sd.pop("walk_files", None)
    registry.add_filesystem_survey(fs.slug, sd["surveyed_at"], sd)
    return sd


# ── DDL: filesystem_walk_files.accessed_at ──

def test_a_fresh_registry_has_accessed_at_and_a_second_init_is_a_no_op(tmp_path):
    db = tmp_path / "fresh.db"
    ProjectRegistry(db_path=str(db))
    ProjectRegistry(db_path=str(db))
    cols = {r[1]: r[2] for r in sqlite3.connect(db).execute("PRAGMA table_info(filesystem_walk_files)")}
    assert cols["accessed_at"] == "TEXT"


def test_an_existing_registry_gains_accessed_at_and_its_old_rows_read_not_recorded(tmp_path):
    db = tmp_path / "old.db"
    reg = ProjectRegistry(db_path=str(db))
    _register(reg, tmp_path)
    reg.add_filesystem_walk_files("tree", "2026-10-01T00:00:00", [{"file_path": "a", "folder_path": "", "folder_depth": 0, "size_bytes": 1, "hash_state": "x"}])
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE filesystem_walk_files DROP COLUMN accessed_at")     # a pre-step-6 shape
    con.commit()
    con.close()
    ProjectRegistry(db_path=str(db))
    con = sqlite3.connect(db)
    assert con.execute("SELECT accessed_at FROM filesystem_walk_files").fetchall() == [(None,)]


def test_the_walk_keeps_each_files_access_time_and_its_own_wall_time(registry, tmp_path):
    import socket

    fs = _register(registry, _tree(tmp_path))
    sd = _walk(registry, fs)
    run = registry.list_filesystem_walk_runs("tree")[0]["run_id"]
    rows = registry.list_filesystem_walk_files("tree", run)
    assert rows and all(r["accessed_at"] for r in rows)
    assert sd["walk_limits"]["walk_seconds"] >= 0 and sd["walk_limits"]["machine"] == socket.gethostname()


# ── the host: file systems take library steps ──

def test_the_four_steps_attach_to_file_systems_beside_filesystem_inventory(fs_installed):
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.filesystem import survey_definition_adapter as fsa

    assert set(FS_STEPS) <= set(fsa._ADAPTER.re_analysis_steps) and "filesystem_inventory" in fsa._ADAPTER.re_analysis_steps
    assert {k for k in fsa.FILESYSTEM_ANALYSIS_STEP_MAP if k.startswith("fs_")} == set(FS_STEPS)
    assert survey_step_host.attached_user_steps("filesystem") == frozenset(FS_STEPS)
    for read in ("filesystem_walk_runs", "filesystem_walk_files", "filesystem_hash_matches",
                 "filesystem_walk_not_walked"):
        assert read in survey_step_host.STEP_ODS_METHODS


def test_without_the_packages_the_catalogued_steps_say_not_installed(registry, tmp_path):
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.filesystem.walk_derived import derived_view

    survey_step_host.refresh("filesystem")
    _register(registry, tmp_path)
    steps = {s["step"]: s for s in derived_view(registry, "tree")["steps"]}
    assert set(steps) == set(FS_STEPS)
    if not survey_step_host.attached_user_steps("filesystem") - set(survey_step_host.missing_library_steps("filesystem")):
        assert all(not s["installed"] and "step not installed here" in s["missing_reason"] for s in steps.values())


def test_a_run_stores_through_the_executor_and_the_view_reads_it_back(fs_installed, registry, tmp_path):
    from resource_explorer.surveyors.filesystem.walk_derived import derived_view
    from resource_explorer.workflows.analysis import run_filesystem_analysis

    fs = _register(registry, _tree(tmp_path))
    before = {s["step"]: s for s in derived_view(registry, "tree")["steps"]}
    assert all(s["installed"] and s["run"] == "" and s["results"] == [] for s in before.values())     # not run yet
    _walk(registry, fs)
    for a in FS_STEPS:
        assert run_filesystem_analysis("tree", a, registry=registry).status == "ok", a
    steps = {s["step"]: s for s in derived_view(registry, "tree")["steps"]}
    dup = {r["measure"]: r for r in steps["fs_duplicates"]["results"]}
    assert dup["fs_duplicates"]["state"] == "measured" and dup["fs_duplicates"]["values"]["Redundant Files"] == "1"
    assert dup["fs_duplicates_elsewhere"]["state"] == "not_established"
    change = steps["fs_change_rates"]["results"][0]
    assert change["state"] == "not_established" and "one walk only" in change["summary"]
    perm = {r["measure"]: r for r in steps["fs_permissions"]["results"]}
    assert perm["world_readable_sensitive"]["annotation_type"] == "RequestForActionAnnotation"
    assert perm["world_readable_sensitive"]["state"] == "measured"
    # the step_runs row is the executor's, under the file system
    assert registry.query_step_runs(slug="tree", step_key="fs_duplicates", entity_type="filesystem", limit=5)


def test_an_unknown_or_built_in_analysis_is_refused_by_the_runner(fs_installed, registry, tmp_path):
    from resource_explorer.workflows.analysis import run_filesystem_analysis

    _register(registry, tmp_path)
    for bad in ("filesystem_inventory", "nope"):
        r = run_filesystem_analysis("tree", bad, registry=registry)
        assert r.status == "error" and "not an Analytic Library step for file systems" in r.error
    assert run_filesystem_analysis("ghost", "fs_duplicates", registry=registry).error == "FileSystem 'ghost' not found"


# ── the route and the run queue ──

@pytest.fixture
def api(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    from tests.zone_fakes import open_access

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    open_access(monkeypatch, caller_if_none="files6-tester")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="files6-tester", egeria_token="tok")}
    return TestClient(app), headers


@pytest.mark.usefixtures("signed_in_caller")
def test_the_route_validates_then_queues_and_the_handler_records_the_run(fs_installed, api, registry, tmp_path):
    from resource_explorer.run_queue import HANDLERS

    client, headers = api
    _walk(registry, _register(registry, _tree(tmp_path)))
    assert client.post("/api/filesystems/ghost/analyses/fs_duplicates/run", headers=headers).status_code == 404
    bad = client.post("/api/filesystems/tree/analyses/filesystem_inventory/run", headers=headers)
    assert bad.status_code == 400 and "Analytic Library steps" in bad.json()["detail"]
    r = client.post("/api/filesystems/tree/analyses/fs_duplicates/run", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "started" and body["activity_id"] and body["run_id"]
    queued = [q for q in registry.list_runs(limit=10) if q["id"] == body["run_id"]][0]
    assert queued["kind"] == "filesystem_analysis_run"
    assert json.loads(queued["target"]) == {"slug": "tree", "analysis_id": "fs_duplicates"}
    outcome = HANDLERS["filesystem_analysis_run"](json.loads(queued["target"]), body["activity_id"])
    assert outcome.state == "succeeded", outcome.error
    entry = registry.get_activity(body["activity_id"])
    assert entry["status"] == "ok"
    view = client.get("/api/filesystems/tree/folder-survey", headers=headers).json()
    dup = next(s for s in view["derived"]["steps"] if s["step"] == "fs_duplicates")
    assert dup["run"] and dup["results"][0]["state"] == "measured"
    assert "words" in view["price"]


@pytest.mark.usefixtures("signed_in_caller")
def test_a_step_whose_package_is_missing_is_a_400_naming_it(api, registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors import survey_step_host

    client, headers = api
    _register(registry, tmp_path)
    survey_step_host.refresh("filesystem")
    if "fs_duplicates" not in survey_step_host.missing_library_steps("filesystem"):
        pytest.skip("fs_duplicates is installed in this environment")
    r = client.post("/api/filesystems/tree/analyses/fs_duplicates/run", headers=headers)
    assert r.status_code == 400 and "step not installed here" in r.json()["detail"]


# ── F12: the price line ──

def test_price_before_any_walk_is_not_priced_never_zero(registry, tmp_path):
    from resource_explorer.surveyors.filesystem.walk_derived import price

    _register(registry, tmp_path)
    p = price(registry, "tree")
    assert p == {"priced": False, "words": "not priced · no walk has measured this file system yet"}


def test_price_from_the_last_walk_on_this_machine_with_the_bulk(registry, tmp_path):
    from resource_explorer.surveyors.filesystem.walk_derived import price

    root = _tree(tmp_path)
    (root / "a" / "big.bin").write_bytes(b"x" * 5000)
    _walk(registry, _register(registry, root))
    p = price(registry, "tree")
    assert p["priced"] and p["machine_matches"] and not p["capped"] and p["files"] == 4
    assert "to walk again on this machine" in p["words"]
    assert p["heaviest_folders"][0]["path"] == "a" and "the bulk sits in a 99%" in p["words"]


def test_price_says_capped_other_machine_and_untimed_walks_and_egeria_lead(registry, tmp_path):
    from resource_explorer.surveyors.filesystem.walk_derived import price

    _register(registry, tmp_path)
    base = {"surveyed_at": "2026-10-01T00:00:00", "total_files": 100000, "total_size_bytes": 10**9}
    registry.add_filesystem_survey("tree", "2026-10-01T00:00:00", {**base, "walk_limits": {
        "capped": True, "capped_words": "capped at 100,000 entries", "walk_seconds": 42.0, "machine": "elsewhere",
        "not_walked_directories": 12, "not_walked_names_found": [".git", "node_modules"]}})
    p = price(registry, "tree")
    assert not p["priced"] and p["capped"]
    for words in ("capped at 100,000 entries: the full tree is larger and its size is not known",
                  "took 42 s on another machine (elsewhere); not priced for this one",
                  "12 directories not walked (.git, node_modules)"):
        assert words in p["words"]
    registry.add_filesystem_survey("tree", "2026-10-02T00:00:00", {**base, "walk_limits": {"capped": False}})
    assert "time not measured" in price(registry, "tree")["words"]
    lead = price(registry, "tree", egeria_leads=True)
    assert not lead["priced"] and "its run time is Egeria's" in lead["words"]


# ── the catalogs ──

def test_the_filesystem_catalog_block_is_the_generated_one(tmp_path):
    """The generator's --check, as a subprocess with every example step installed: the database AND filesystem
    blocks of analysis_catalog.yaml are what the manifests generate."""
    import os
    import subprocess
    import sys

    from tests.library_step_install import pythonpath

    env = {**os.environ, "PYTHONPATH": pythonpath(tmp_path)}
    out = subprocess.run([sys.executable, str(PKG / "scripts" / "generate_external_step_entries.py"), "--check"],
                         capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    cards = {e["id"]: e for e in yaml.safe_load((PKG / "resource_explorer" / "configdata" / "analysis_catalog.yaml")
                                                .read_text())["filesystem_analyses"] if "provided_by" in e}
    assert set(cards) == set(FS_STEPS)
    for name, e in cards.items():
        assert e["resource_types"] == ["filesystem"] and e["availability"] == "queued" and e["declaration"]
        assert "not measured" in e["description"], name
    assert cards["fs_permissions"]["annotation_types"] == ["ResourceProfileAnnotation", "RequestForActionAnnotation"]


def test_the_questions_name_the_steps_that_answer_them():
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    by_id = {q["id"]: q for q in get_questions("filesystem")}
    assert by_id["Q-0102"]["answering"]["analysis_ids"] == ["fs_change_rates"]
    assert by_id["Q-0102"]["answering"]["kind"] == "analysis"
    for qid, step in (("Q-0106", "fs_duplicates"), ("Q-0107", "fs_activity"), ("Q-0108", "fs_permissions")):
        assert by_id[qid]["answering"]["analysis_ids"] == [step], qid
    assert by_id["Q-0109"]["answering"]["kind"] == "direct"
