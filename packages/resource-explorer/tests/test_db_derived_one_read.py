"""G10 (2026-10-29): one read computes `run_db_derived` once, and nothing outlives the read.

Measured on `laz_local_adventureworks` before this: the Discovery checklist (`/questions?phase=discovery`) took 23.9s
on the live server, re-running `run_db_derived` nine times, and single question answers up to 27s, each run loading
all 21 registered databases' stored rows twice. These pin the fix and its limits: a repeat inside one read is
shared; a new read always recomputes from what is stored then (stored rows can be rewritten in place); a caller's
mutation of its copy never reaches the next caller.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database import db_derived
from resource_explorer.surveyors.database.db_derived import one_read, run_db_derived
from tests.test_db_derived_step import NOW, _normalised_schema, _store


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    for slug in ("coco_ods", "coco_ods_restore"):
        r.register_database(DatabaseEntity(
            slug=slug, display_name=slug, db_type="postgresql",
            host="localhost", port=5442, database_name=slug,
        ))
    return r


@pytest.fixture
def runs(monkeypatch):
    """Count the real computations behind `run_db_derived`."""
    count = {"n": 0}
    real = db_derived._run_db_derived_once

    def counted(*a, **k):
        count["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(db_derived, "_run_db_derived_once", counted)
    return count


def test_inside_one_read_a_repeat_is_shared(registry, runs):
    _normalised_schema(registry)
    with one_read():
        first = run_db_derived(registry, "coco_ods")
        second = run_db_derived(registry, "coco_ods")
    assert runs["n"] == 1
    assert first["derived"] == second["derived"]


def test_outside_a_read_every_call_recomputes(registry, runs):
    _normalised_schema(registry)
    run_db_derived(registry, "coco_ods")
    run_db_derived(registry, "coco_ods")
    assert runs["n"] == 2


def test_a_new_read_sees_rows_stored_after_the_last_one(registry):
    """No cache outlives its read: a peer surveyed between two reads is compared in the second."""
    tables, columns, _ = _normalised_schema(registry)
    with one_read():
        before = run_db_derived(registry, "coco_ods")["derived"]["db_fingerprint"]
    _store(registry, "coco_ods_restore", NOW, tables=tables, columns=columns)
    with one_read():
        after = run_db_derived(registry, "coco_ods")["derived"]["db_fingerprint"]
    assert before["comparable_databases"] == 0
    assert after["comparable_databases"] == 1
    assert after["matches"][0]["slug"] == "coco_ods_restore"


def test_a_callers_mutation_does_not_reach_the_next_caller(registry):
    """The readers attach `_status`/`_run` to the dict they get back; the next reader must get the clean result."""
    _normalised_schema(registry)
    with one_read():
        first = run_db_derived(registry, "coco_ods")
        first["derived"]["db_classification"]["_run"] = {"read_snapshot": "mutated"}
        second = run_db_derived(registry, "coco_ods")
    assert "_run" not in second["derived"]["db_classification"]


def test_a_lens_is_never_shared(registry, runs):
    _normalised_schema(registry)
    with one_read():
        run_db_derived(registry, "coco_ods", lens={"subjects": ["x"]})
        run_db_derived(registry, "coco_ods", lens={"subjects": ["x"]})
    assert runs["n"] == 2


def test_one_run_loads_each_peers_rows_once(registry, monkeypatch):
    """`fingerprint_database` and `fingerprint_by_container` both walk the peers; the second walk reuses the first's."""
    tables, columns, _ = _normalised_schema(registry)
    _store(registry, "coco_ods_restore", NOW, tables=tables, columns=columns)
    loads: list[str] = []
    real = db_derived.load_inputs

    def counted(reg, slug, *a, **k):
        loads.append(slug)
        return real(reg, slug, *a, **k)

    monkeypatch.setattr(db_derived, "load_inputs", counted)
    result = run_db_derived(registry, "coco_ods")
    assert result["derived"]["db_fingerprint"]["comparable_databases"] == 1
    assert loads.count("coco_ods_restore") == 1
    assert loads.count("coco_ods") == 1


def test_a_database_answer_and_checklist_each_run_inside_one_read(monkeypatch):
    from resource_explorer.facts import FactLayer
    from resource_explorer.workflows import scouting

    seen = {}

    def answer_probe(self, slug, question):
        seen["answer"] = db_derived._READ_MEMO.get() is not None

    def checklist_probe(registry, entity_type, *a):
        seen[f"checklist:{entity_type}"] = db_derived._READ_MEMO.get() is not None
        return {}

    monkeypatch.setattr(FactLayer, "_answer", answer_probe)
    monkeypatch.setattr(scouting, "_build_question_checklist", checklist_probe)
    FactLayer(registry=object(), resource_type="database").answer("coco_ods", {})
    scouting.build_question_checklist(object(), "database", "coco_ods", "discovery")
    scouting.build_question_checklist(object(), "repo", "some_repo", "discovery")
    assert seen == {"answer": True, "checklist:database": True, "checklist:repo": False}
    assert db_derived._READ_MEMO.get() is None, "the read ends with its block"
