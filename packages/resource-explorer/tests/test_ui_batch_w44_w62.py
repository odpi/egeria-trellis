"""UI batch W44–W62 / F49–F50 (2026-11-03 read-backs): one test or more per grade, words as served."""
from __future__ import annotations

import pytest

from resource_explorer.facts import Fact, FactLayer
from resource_explorer.surveyors.database import db_derived
from resource_explorer.surveyors.result_status import NEVER_RUN, NO_READER


# --- W54: an unrun row's answer line is one sentence, whatever its facts' states -----------------------------------

@pytest.mark.parametrize("states", [(NEVER_RUN, NEVER_RUN), (NEVER_RUN, NO_READER)])
def test_w54_unrun_answer_line_is_one_sentence(monkeypatch, states):
    layer = FactLayer()
    monkeypatch.setattr(layer, "facts", lambda slug, ids, level=None: [
        Fact(aid, st, can_run=[aid]) for aid, st in zip(["postgres_column_profile", "postgres_nested_columns"], states)])
    q = {"id": "Q-1", "question": "q", "answering": {"kind": "analysis",
         "analysis_ids": ["postgres_column_profile", "postgres_nested_columns"]}}
    env = layer.answer("slug", q)
    assert env.answerable is False
    assert env.blocked_reason.startswith("Nothing has been measured for this yet.")
    assert "Not yet run" not in env.blocked_reason   # the row's source line says WHEN, once (F23)


def test_w54_a_known_count_of_databases_is_not_database_s():
    assert db_derived._databases(1) == "database"
    assert db_derived._databases(11) == "databases"
    src = open(db_derived.__file__, encoding="utf-8").read()
    assert "database(s)" not in src.replace('"database(s)" beside', "")


# --- W55: a per-card Run → says the stage it was pressed on beside the intent it declares ---------------------------

def test_w55_the_pressed_on_stage_reaches_the_one_analysis_surveys_activity_row(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    from resource_explorer.run_queue import _handle_database_analysis_run
    from resource_explorer.surveyors.survey_definition_executor import get_adapter
    from tests.test_database_library_step_run import _fake_library_step

    registry = ProjectRegistry(db_path=str(tmp_path / "w55.db"))
    registry.register_database(DatabaseEntity(slug="nocreds", display_name="No Creds", db_type="postgresql",
                                              host="localhost", port=5432, database_name="nocreds"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setitem(get_adapter("database").re_analysis_steps, "database_staleness", _fake_library_step([]))
    from resource_explorer.web.app import app

    resp = TestClient(app).post("/api/databases/nocreds/analyses/database_staleness/run?pressed_on=4%20Analysis")
    assert resp.status_code == 200, resp.text
    [queued] = registry.list_runs(state="queued")
    target = json.loads(queued["target"]) if isinstance(queued["target"], str) else queued["target"]
    assert target["pressed_on"] == "4 Analysis"

    assert _handle_database_analysis_run(target, resp.json()["activity_id"]).state == "succeeded"
    wrappers = [a for a in registry.list_activity(limit=50)
                if str(json.loads(a.get("detail") or "{}").get("survey_definition_ref", "")).startswith("synthetic::")]
    assert [json.loads(w["detail"])["pressed_on"] for w in wrappers] == ["4 Analysis"]
    assert wrappers[0]["intent"] == "discovery"     # the declared intent stays; the stage is said beside it


def test_w55_no_pressed_on_outside_a_per_card_handler():
    from resource_explorer.activity_logger import PRESSED_ON, pressed_on

    assert PRESSED_ON.get() == ""
    with pressed_on("4 Analysis"):
        assert PRESSED_ON.get() == "4 Analysis"
    assert PRESSED_ON.get() == ""


# --- W56: a card whose last run was its own Run → says so, not "via survey" ---------------------------------------

def test_w56_a_per_card_run_is_named_on_the_card_and_a_real_survey_keeps_its_word(tmp_path):
    import json

    from resource_explorer.activity_logger import log_survey
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    from resource_explorer.workflows.stage_page import _last_run_info

    registry = ProjectRegistry(db_path=str(tmp_path / "w56.db"))
    registry.register_database(DatabaseEntity(slug="d", display_name="D", db_type="postgresql",
                                              host="localhost", port=5432, database_name="d"))
    step = lambda key: {"step": f"x::{key}", "re_analysis_step": key, "status": "ok"}  # noqa: E731
    wrapper_id = log_survey(registry, "database", "d", "D", "", intent="discovery", status="ok", summary="s",
                            detail=json.dumps({"survey_definition_ref": "synthetic::database::d::statistics_freshness",
                                               "steps": [step("statistics_freshness")]}))
    log_survey(registry, "database", "d", "D", "", intent="discovery", status="ok", summary="s",
               detail=json.dumps({"survey_definition_ref": "RE::db-survey", "steps": [step("change_rates")]}))

    card = _last_run_info(registry, "d", "statistics_freshness", entity_type="database")
    assert card["last_run_via"] == "per_card" and card["last_run_survey_id"] == wrapper_id
    other = _last_run_info(registry, "d", "change_rates", entity_type="database")
    assert other["last_run_via"] == "survey" and other["last_run_survey_id"] == ""


# --- W57: a basis whose numerator counts a part of the whole names what it counts ----------------------------------

def test_w57_a_state_count_is_never_read_as_n_measured():
    from trellis_analytics.declaration import BasisDeclaration, MeasureDeclaration

    from resource_explorer import declared_words

    basis = BasisDeclaration(denominator_field="Tables", denominator_label="tables")
    subset = MeasureDeclaration(name="statistics_freshness_summary", unit="tables never analyzed or stale", as_of="asOf",
                                value_field="Needs Attention", basis=basis)
    said, _ = declared_words.measure_sentence(subset, {"Needs Attention": 215, "Tables": 266})
    assert said == "215 tables never analyzed or stale of 266 tables (81% of the tables)"
    # A numerator in the whole's own words keeps the short form.
    whole = MeasureDeclaration(name="profiled_columns", unit="columns", as_of="asOf", value_field="n",
                               basis=BasisDeclaration(denominator_field="t", denominator_label="columns"))
    assert declared_words.measure_sentence(whole, {"n": 456, "t": 1236})[0] == "456 of 1,236 columns (37%)"


# --- W52: the value-profile header says the class of a reason, never the database's raw error text ---------------

def test_w52_header_reason_classes():
    from resource_explorer.value_profile_screen import _reason_phrase, reason_class

    raw = 'permission denied for schema demo\nLINE 429: FROM "demo"."feedback"\n          ^'
    assert _reason_phrase(raw, 1) == "1 table: permission denied (schema demo)"
    assert reason_class('relation "public.gone" does not exist\nLINE 1: SELECT') == "relation not found"
    assert reason_class('syntax error at or near "("\nLINE 3: ...') == "query error"
    assert reason_class("no rows to profile") == "no rows to profile"     # RE's own words are already a class
    assert _reason_phrase("a view, not profiled", 87) == "87 views"


def test_w52_the_raw_error_stays_on_the_tables_own_line():
    from resource_explorer.value_profile_screen import header_line, table_line

    raw = 'permission denied for schema demo\nLINE 429: FROM "demo"."feedback"\n          ^'
    t = {"schema_name": "demo", "table_name": "feedback", "state_counts": {"not_permitted": 3}, "column_count": 3,
         "record_ct": None, "reasons": {raw: 3}}
    head = header_line({"column_count": 3, "measured_count": 0, "surveyed_at": "2026-11-03T14:53:00"}, [t])
    assert "LINE 429" not in head and "^" not in head and "permission denied (schema demo)" in head
    assert "LINE 429" in table_line(t, "2026-11-03T14:53:00")


# --- W51: after an Egeria-led Folder Survey the data-file profiles say the real prerequisite ----------------------

def test_w51_egeria_counts_only_points_at_measure_beside():
    from resource_explorer.folder_survey_view import _egeria_counts_only
    from resource_explorer.surveyors.filesystem.file_profiles import EGERIA_COUNTS_ONLY_WORDS, NO_WALK_WORDS

    fp = {"price": {"walk": False, "words": NO_WALK_WORDS}, "run": None}
    _egeria_counts_only(fp, {"surveyor": "Egeria's Folder Survey", "at": "2026-11-03T14:47:49Z"})
    assert fp["price"] == {"walk": False, "words": EGERIA_COUNTS_ONLY_WORDS, "needs": "measure_beside"}
    assert EGERIA_COUNTS_ONLY_WORDS.startswith("Egeria's survey reports counts, not files")
    # No Egeria survey at all (or only a run that left no report): the words stay "no walk yet".
    for egeria in (None, {"none": True, "run_state": "failed"}):
        fp = {"price": {"walk": False, "words": NO_WALK_WORDS}}
        _egeria_counts_only(fp, egeria)
        assert fp["price"] == {"walk": False, "words": NO_WALK_WORDS}
    # A walk exists: the price is the walk's.
    fp = {"price": {"walk": True, "words": "16 data files, ..."}}
    _egeria_counts_only(fp, {"surveyor": "x"})
    assert fp["price"]["words"] == "16 data files, ..."


# --- W44: Egeria's Folder Survey row checks the lead before the asset --------------------------------------------

def _folder_survey_reason(monkeypatch, fork, asset_guid=""):
    from types import SimpleNamespace

    from resource_explorer import file_reach
    from resource_explorer import native_survey_run as nsr
    from resource_explorer.surveyors.technology_type_processes import NativeProcess

    monkeypatch.setattr(file_reach, "fork_for", lambda registry, entity: fork)
    process = NativeProcess(qualified_name=nsr.FOLDER_SURVEY_QN, display_name="Survey Folder",
                            kind=nsr.KIND_SURVEY_EXISTING, action_target_name="fileToSurvey")
    fs = SimpleNamespace(slug="egeria_v6", local_mount_point="/x", egeria_asset_guid=asset_guid)
    return nsr.cannot_run_reason(process, fs, registry=object())


def test_w44_an_re_led_file_system_is_not_told_to_attach_a_root_folder(monkeypatch):
    from resource_explorer import file_reach

    fork = file_reach.ForkState(state=file_reach.RE_ONLY, sentence="only Resource Explorer can read this path · checked 2026-10-21",
                                surveyor=file_reach.SURVEYOR_RE)
    words = _folder_survey_reason(monkeypatch, fork)
    assert words.startswith("RE-led walk: no Egeria asset needed")
    assert "only Resource Explorer can read this path · checked 2026-10-21" in words
    assert "Attach the root folder" not in words


def test_w44_an_s3_bucket_says_egeria_cannot_reach_it(monkeypatch):
    from resource_explorer import file_reach

    fork = file_reach.ForkState(state=file_reach.REMOTE, sentence="read through S3", surveyor=file_reach.SURVEYOR_RE)
    assert _folder_survey_reason(monkeypatch, fork).startswith("Egeria cannot reach this bucket; the walk is RE-led")


def test_w44_an_egeria_led_unattached_file_system_keeps_the_w39_words(monkeypatch):
    from resource_explorer import file_reach
    from resource_explorer import native_survey_run as nsr

    fork = file_reach.ForkState(state=file_reach.EXPECTED, sentence="expected reachable", surveyor=file_reach.SURVEYOR_EGERIA)
    assert _folder_survey_reason(monkeypatch, fork) == nsr.FS_NEEDS_ROOT_ASSET
    assert _folder_survey_reason(monkeypatch, fork, asset_guid="de51f58d") == ""


# --- W45: a walk run on this host is priced for it, whether its machine was recorded short or in full ------------

def test_w45_short_and_full_host_names_are_one_machine(monkeypatch):
    import socket

    from resource_explorer.surveyors.filesystem import walk_derived

    monkeypatch.setattr(socket, "gethostname", lambda: "laz.local")
    assert walk_derived.same_machine("laz") and walk_derived.same_machine("laz.local") and walk_derived.same_machine("LAZ")
    assert not walk_derived.same_machine("hedwig") and not walk_derived.same_machine("") and not walk_derived.same_machine(None)

    sd = {"surveyed_at": "2026-11-03T10:37:00", "total_files": 12, "total_size_bytes": 19600,
          "walk_limits": {"walk_seconds": 0.2, "machine": "laz"}}
    monkeypatch.setattr(walk_derived, "_newest_walk", lambda registry, slug: sd)
    words = walk_derived.price(object(), "dl_minio_datalake")["words"]
    assert "another machine" not in words and "under a second to walk again on this machine" in words


# --- W46: an S3 walk's access line says object storage keeps no access time, not that the walk predates them -----

def test_w46_fs_activity_on_an_object_store():
    from fs_activity import step

    from resource_explorer import reach_mechanisms as rm

    assert step.NOT_REPORTED == rm.NOT_REPORTED

    class _Ods:
        def filesystem_walk_runs(self, slug, limit=2):
            return [{"run_id": "2026-11-03T10:37:00"}]

        def filesystem_walk_files(self, slug, run_id):
            return [{"file_path": f"t/{i}.parquet", "size_bytes": 10, "modified_at": "2026-11-01T00:00:00",
                     "accessed_at": None, "owner_name": rm.NOT_REPORTED, "group_name": rm.NOT_REPORTED,
                     "mode_octal": rm.NOT_REPORTED, "hash_state": "hashed"} for i in range(3)]

    class _In:
        slug = "dl_minio_datalake"
        ods = _Ods()
        parameters = {}

    access = next(r for r in step.run(_In()) if r.get("measure") == step.ACCESS)
    assert access["state"] == "not_established"
    assert access["summary"] == "not measured · S3 objects carry no access time; reads cannot be told"
    assert "walked before" not in access["reason"]
