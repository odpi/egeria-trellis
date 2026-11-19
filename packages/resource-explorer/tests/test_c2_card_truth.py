"""Item C2 (2026-11-19): the file-system survey card tells the truth, and updates itself.

TR-70: a run in flight is in the view, and its settling moves the stage header; every press on the card leaves one Activity
row (what, by whom, outcome). D5/D7/D8/D10/D12/D13/D14: absences are not zeros, counts are of what was profiled, the missing
CSV is named, "Check again" says why nothing changed, one size format, a price for "Measure beside", a walk refreshes the
reach line. Fakes only: no Egeria, no shared database.
"""
from __future__ import annotations

import contextlib
import json

import pytest

from resource_explorer import file_reach
from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from tests.test_files2_reach_fork_folder_survey import (  # noqa: F401  (fixtures and the scripted Egeria are shared)
    ASSET, _Port, _ann, _fs, _row, api, registry)

DEPLOY = "/deployments/data-lake"


def _verified(registry, at="2026-10-20T10:00:00+00:00"):
    _row(registry, "egeria_verified", at=at, slug="lake", path=DEPLOY)


def _submit(registry, port):
    return nsr.submit_native_survey(registry, port, "filesystem", "lake", nsr.FOLDER_SURVEY_QN,
                                    technology_type="File System Directory", submitted_by="dan")


def _complete(registry, port):
    port.status = ("COMPLETED", "done")
    port.reports["ea-folder-9"] = "report-9"
    port.originators["report-9"] = "ea-folder-9"
    port.annotations["report-9"] = [_ann(1, "Capture File Counts", resource_properties={
        "Number of files": "120", "Total file size": "1.2345678E7", "Number of subdirectories (folders)": "9",
        "Number of inaccessible files": "0", "Number of unclassified files": "5"})]
    run = registry.list_native_survey_runs("filesystem", "lake", nsr.FOLDER_SURVEY_QN)[0]
    nsr.refresh_run(registry, port, run, entity_type="filesystem", slug="lake")


def _forget_the_asset(registry):
    with registry._conn() as conn:
        conn.execute("UPDATE file_systems SET egeria_asset_guid = '' WHERE slug = ?", ("lake",))


# ── TR-70: the card shows the run in flight; the header follows the read-back ─────────────────────────────────────────

def test_the_view_carries_the_run_in_flight_and_when_it_was_submitted(registry, tmp_path):
    from resource_explorer.folder_survey_view import folder_survey_view

    _fs(registry, tmp_path)
    _verified(registry)
    assert folder_survey_view(registry, "lake")["egeria_run_state"] == ""
    port = _Port()
    _submit(registry, port)
    v = folder_survey_view(registry, "lake")
    assert v["egeria_run_state"] in nsr.IN_FLIGHT_STATES and v["egeria_run_since"]
    _complete(registry, port)
    v = folder_survey_view(registry, "lake")
    assert v["egeria_run_state"] not in nsr.IN_FLIGHT_STATES and v["state"] == "measured"


def test_the_header_moves_to_the_egeria_survey_once_it_is_read_back(api, registry, tmp_path):
    client, headers = api
    _fs(registry, tmp_path)
    _verified(registry)

    def listed():
        return next(r for r in client.get("/api/filesystems/", headers=headers).json() if r["slug"] == "lake")

    port = _Port()
    _submit(registry, port)
    assert listed()["last_surveyed_at"] == ""                       # submitted is not surveyed
    _complete(registry, port)
    _forget_the_asset(registry)                                     # as after an Egeria reset: no cached asset guid
    assert registry.get_filesystem("lake").egeria_asset_guid == ""
    row = listed()
    assert row["last_surveyed_at"].startswith("2026-10-20")         # the report's own time
    assert row["is_published"] is True and row["egeria_publish_note"] == ""


def test_an_rs_walk_that_is_newer_than_the_egeria_survey_keeps_its_own_date(api, registry, tmp_path):
    client, headers = api
    fs, _ = _fs(registry, tmp_path)
    _verified(registry)
    port = _Port()
    _submit(registry, port)
    _complete(registry, port)
    registry.add_filesystem_survey("lake", "2026-11-18T09:00:00", {"total_files": 3, "surveyed_at": "2026-11-18T09:00:00"})
    row = next(r for r in client.get("/api/filesystems/", headers=headers).json() if r["slug"] == "lake")
    assert row["last_surveyed_at"].startswith("2026-11-18")


def test_the_settled_run_writes_a_second_activity_row_once(registry, tmp_path):
    _fs(registry, tmp_path)
    _verified(registry)
    port = _Port()
    _submit(registry, port)
    _complete(registry, port)
    run = registry.list_native_survey_runs("filesystem", "lake", nsr.FOLDER_SURVEY_QN)[0]
    nsr.refresh_run(registry, port, run, entity_type="filesystem", slug="lake")      # a second poll
    rows = [r for r in registry.list_activity(entity_slug="lake") if "Folder Survey" in r["summary"]]
    assert len(rows) == 1 and "complete" in rows[0]["summary"] and rows[0]["status"] == "ok"


# ── TR-70 item 4: every press on the card writes one Activity row, with who pressed it ────────────────────────────────

def _rows(registry, slug="lake"):
    return registry.list_activity(entity_slug=slug, limit=50)


def _one_row(registry, what):
    rows = [r for r in _rows(registry) if json.loads(r["detail"] or "{}").get("press") == what]
    assert len(rows) == 1, (what, [r["summary"] for r in _rows(registry)])
    r = rows[0]
    assert f"{what} · by " in r["summary"] and json.loads(r["detail"])["by"], r
    return r


@pytest.mark.usefixtures("signed_in_caller")
def test_folder_survey_led_by_egeria_writes_one_row_at_submit(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _fs(registry, tmp_path)
    _verified(registry)
    port = _Port()
    monkeypatch.setattr("resource_explorer.web.routes.native_surveys._port", lambda: port)
    r = client.post("/api/filesystems/lake/folder-survey", json={}, headers=headers)
    assert r.status_code == 200, r.text
    row = _one_row(registry, "Survey")
    assert row["status"] == "triggered" and "submitted to Egeria" in row["summary"]


@pytest.mark.usefixtures("signed_in_caller")
def test_a_refused_press_is_an_error_row_and_an_unauthorised_one_is_none(api, registry, tmp_path):
    client, headers = api
    _fs(registry, tmp_path)
    registry.record_reachability_check("lake", probed_at="2026-10-20T10:00:00+00:00", outcome="unknown", error_detail="timed out")
    assert client.post("/api/filesystems/lake/folder-survey", json={}, headers=headers).status_code == 409
    row = _one_row(registry, "Survey")
    assert row["status"] == "error" and "refused" in row["summary"]
    assert client.post("/api/filesystems/nope/folder-survey", json={}, headers=headers).status_code == 404
    assert _rows(registry, "nope") == []


@pytest.mark.usefixtures("signed_in_caller")
def test_reach_press_writes_a_row_and_says_when_it_kept_a_verified_row(api, registry, tmp_path):
    client, headers = api
    _fs(registry, tmp_path)
    _verified(registry)
    out = client.post("/api/filesystems/lake/reach", headers=headers).json()
    assert out["kept"] is True and out["state"] == "egeria"          # D10
    row = _one_row(registry, "Check again")
    assert "kept" in row["summary"] and "2026-10-20" in row["summary"]


@pytest.mark.usefixtures("signed_in_caller")
def test_reach_press_without_a_verified_row_is_not_kept(api, registry, tmp_path):
    client, headers = api
    _fs(registry, tmp_path)
    out = client.post("/api/filesystems/lake/reach", headers=headers).json()
    assert out["kept"] is False
    _one_row(registry, "Check again")


class _FakeSurveyor:
    def __init__(self, *a, **k):
        pass

    def attach_root_folder(self, fs, registry=None):
        return {"matched": True, "guid": ASSET, "created": True, "qualified_name": "q", "type_name": "FileFolder"}

    def publish_step_annotations(self, fs, sd, registry, declared_measures=None):
        return {"report_guid": "r" * 36, "annotation_count": 3, "annotations_in_egeria": 3, "derived": None,
                "report_read_back": True}


@pytest.mark.usefixtures("signed_in_caller")
def test_attach_root_and_the_publish_presses_each_write_one_row(api, registry, tmp_path, monkeypatch):
    from resource_explorer.web.routes import filesystems as routes

    client, headers = api
    _fs(registry, tmp_path)
    monkeypatch.setattr("resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor",
                        _FakeSurveyor)
    assert client.post("/api/filesystems/lake/attach-root", headers=headers).status_code == 200
    row = _one_row(registry, "Attach root folder")
    assert row["operation"] == "publish" and "created the root folder" in row["summary"]

    registry.add_filesystem_survey("lake", "2026-11-19T01:00:00", {"surveyed_at": "2026-11-19T01:00:00", "total_files": 2,
                                                                  "surveyed_by": {"reach": {}}, "walk_extensions": {}},
                                   source="walk-extensions")
    monkeypatch.setattr(routes, "_publish_walk", lambda *a, **k: {"published": True, "landed_words": "3 of 3 annotations in Egeria",
                                                                    "report_guid": "r" * 36})
    _verified(registry, at="2099-01-01T00:00:00+00:00")           # newer than the row the attach's own probe recorded
    r = client.post("/api/filesystems/lake/walk-extensions/publish", headers=headers)
    assert r.status_code == 200, r.text
    assert "published · 3 of 3" in _one_row(registry, "Publish measures")["summary"]
    assert client.post("/api/filesystems/lake/folder-survey/publish", headers=headers).status_code == 200
    _one_row(registry, "Publish walk")


@pytest.mark.usefixtures("signed_in_caller")
def test_the_profile_family_each_writes_one_row(api, registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem import file_class_match as cm
    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.surveyors.filesystem import narrative_docs as nd
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.web.routes import filesystems as routes

    client, headers = api
    _fs(registry, tmp_path)
    _verified(registry)
    monkeypatch.setattr(fp, "run_file_profiles", lambda *a, **k: {"run_id": "r1"})
    monkeypatch.setattr(fp, "profiles_view", lambda reg, slug: {"run": {"words": "50 files profiled (35 by Egeria, 15 by RE)"}})
    monkeypatch.setattr(fp, "read_back_egeria", lambda *a, **k: {"read": 35, "waiting": 1, "failed": 0, "no_columns": 0})
    monkeypatch.setattr(fp, "make_port", lambda identity=None: object())
    monkeypatch.setattr(nsr, "port_session", lambda port: contextlib.nullcontext(port))
    monkeypatch.setattr(cm, "run_file_class_match", lambda fs, reg: {"columns": 10, "established": 2})
    monkeypatch.setattr(cm, "view", lambda reg, slug: {})
    monkeypatch.setattr(nd, "run_narrative_docs", lambda fs, reg: {"files": 7})
    monkeypatch.setattr(nd, "view", lambda reg, slug: {"run": {"words": "reads 7 narrative files"}})
    monkeypatch.setattr(we, "run_alongside_egeria", lambda fs, reg, egeria_report_guid="": {
        "walk_extensions": {"run_id": "2026-11-19T01:32:00", "files": 151, "capped_words": "", "rows_kept": {}},
        "surveyed_at": "2026-11-19T01:32:00", "walk_limits": {}})
    for url, what in (("/file-profiles", "Profile data files"), ("/file-profiles/read-egeria", "Read Egeria's reports"),
                      ("/class-match", "Match data classes"), ("/narrative-docs", "Read narrative files"),
                      ("/walk-extensions", "Measure beside Egeria's survey")):
        r = client.post(f"/api/filesystems/lake{url}", json={}, headers=headers)
        assert r.status_code == 200, (url, r.text)
        _one_row(registry, what)
    assert "waiting 1" in _one_row(registry, "Read Egeria's reports")["summary"]
    # file-profiles/publish: claimed, then the publish itself (faked)
    monkeypatch.setattr(routes, "_publish_file_profiles_claimed", lambda reg, fs, slug: {"columns": 4})
    assert client.post("/api/filesystems/lake/file-profiles/publish", headers=headers).status_code == 200
    assert _one_row(registry, "Publish profiles")["operation"] == "publish"


# ── D5: a count a report did not state is not 0 ────────────────────────────────────────────────────────────────────────

def _reports(monkeypatch, *reports):
    monkeypatch.setattr(
        "resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor.get_survey_reports_by_guid",
        lambda self, guid: [dict(r) for r in reports])


def _report(guid, **kw):
    return {"guid": guid, "qualified_name": "q", "display_name": "d", "surveyed_at": "2026-11-19T01:20:55", "annotation_count": 0,
            "annotation_count_stated": False, "schema_count": 0, "table_count": 0, "column_count": 0, "description": "", **kw}


def test_a_native_report_shows_res_stored_count_and_a_report_with_none_shows_not_counted(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _fs(registry, tmp_path)
    _verified(registry)
    port = _Port()
    _submit(registry, port)
    _complete(registry, port)                                         # one annotation stored from report-9
    _reports(monkeypatch, _report("report-9"), _report("never-read-by-re"),
             _report("stated", annotation_count=4, annotation_count_stated=True))
    got = {r["guid"]: r["annotation_count"] for r in client.get("/api/filesystems/lake/egeria-surveys", headers=headers).json()}
    assert got == {"report-9": 1, "never-read-by-re": None, "stated": 4}      # None is "not counted", never 0


def test_the_reader_says_whether_the_report_stated_a_count():
    from resource_explorer.surveyors.egeria_survey_reader import get_survey_reports_by_guid

    def asset(additional):
        return {"reports": [{"relatedElement": {"elementHeader": {"guid": "g", "versions": {}},
                                                "properties": {"additionalProperties": additional}}}]}

    class Maker:
        def __init__(self, result):
            self.result = result

        def get_asset_by_guid(self, *a, **k):
            return self.result

    (stated,) = get_survey_reports_by_guid(Maker(asset({"annotation_count": "7"})), "x")
    (silent,) = get_survey_reports_by_guid(Maker(asset({})), "x")
    assert stated["annotation_count_stated"] is True and stated["annotation_count"] == 7
    assert silent["annotation_count_stated"] is False


# ── D7: the class-match header counts profiled files, not every file of the run ──────────────────────────────────────────

def test_class_match_files_counts_only_files_with_a_profiled_column(registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem import file_class_match as cm
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    fs, root = _fs(registry, tmp_path)
    (root / "a.csv").write_text("x,y\n1,2\n")
    rows = [
        {"file_path": "a.csv", "column_name": "x", "ordinal": 1, "state": fp.MEASURED, "declared_type": "", "inferred_general_type": "int",
         "stats_source": fp.STATS_SOURCE_RE},
        {"file_path": "big.xls", "column_name": "", "ordinal": 0, "state": fp.NOT_APPLICABLE, "stats_source": ""},
        {"file_path": "other.json", "column_name": "", "ordinal": 0, "state": fp.NOT_APPLICABLE, "stats_source": ""},
    ]
    monkeypatch.setattr(registry, "list_filesystem_file_column_runs", lambda slug: ["2026-11-19T01:37:15"])
    monkeypatch.setattr(registry, "list_filesystem_file_columns", lambda slug, run: rows)
    monkeypatch.setattr(cm, "sample_values", lambda *a, **k: ({"x": ["1"]}, 1, ""))
    out = cm.run_file_class_match(fs, registry, catalog_loader=lambda: _NoCatalog())
    assert out["files"] == 1 and "of 1 profiled file examined" in cm.run_words(out)


class _NoCatalog:
    available = False
    data_classes: list = []
    unavailable_reason = "not read in this test"

    def as_dict(self):
        return {"available": False, "unavailable_reason": self.unavailable_reason, "data_class_count": 0}


# ── D8: after a read-back that leaves files submitted, the line names them (three at most) ─────────────────────────────

def test_the_waiting_files_are_named_once_some_reports_have_been_read(registry, tmp_path):
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    _fs(registry, tmp_path)
    run = "2026-11-19T01:37:15+00:00"
    for rel, state, src in (("a.csv", fp.MEASURED, "egeria:rep1"), ("b.csv", fp.SUBMITTED, ""), ("c.csv", fp.SUBMITTED, ""),
                            ("d.csv", fp.SUBMITTED, ""), ("e.csv", fp.SUBMITTED, "")):
        registry.replace_filesystem_file_profile("lake", run, rel, [fp.marker_row(state, stats_source=src)] if state != fp.MEASURED else
                                                 [{**fp.marker_row(state, stats_source=src), "column_name": "x", "ordinal": 1}])
    counts = fp.profiles_view(registry, "lake")["run"]["counts"]
    assert counts["submitted"] == 4 and counts["submitted_names"] == ["b.csv", "c.csv", "d.csv"] and counts["submitted_more"] == 1


def test_nothing_is_named_before_any_report_was_read(registry, tmp_path):
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    _fs(registry, tmp_path)
    run = "2026-11-19T01:37:15+00:00"
    for rel in ("a.csv", "b.csv"):
        registry.replace_filesystem_file_profile("lake", run, rel, [fp.marker_row(fp.SUBMITTED)])
    counts = fp.profiles_view(registry, "lake")["run"]["counts"]
    assert counts["submitted"] == 2 and counts["submitted_names"] == []


# ── D12: one size format ──────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,words", [(0, "0 B"), (999, "999 B"), (1000, "1.0 KB"), (1_500, "1.5 KB"), (999_949, "999.9 KB"),
                                     (999_950, "1.0 MB"), (8_400_000_000, "8.4 GB"), (7_800_000_000, "7.8 GB"),
                                     (57_400_000, "57.4 MB"), (2_500_000_000_000, "2,500.0 GB")])
def test_the_walk_formatters_are_decimal_and_labelled_in_one_voice(n, words):
    from resource_explorer.surveyors.filesystem import walk_derived, walk_extensions

    assert walk_derived._fmt_bytes(n) == words
    assert walk_extensions._fmt_bytes(n) == words


# ── D13: "Measure beside" has a price line ────────────────────────────────────────────────────────────────────────────

def test_measure_beside_is_priced_from_the_last_walk_or_says_it_is_not(registry, tmp_path):
    from resource_explorer.folder_survey_view import folder_survey_view

    _fs(registry, tmp_path)
    _verified(registry)
    assert folder_survey_view(registry, "lake")["measure_price"]["words"].startswith("not priced")
    registry.add_filesystem_survey("lake", "2026-11-18T09:00:00", {
        "total_files": 151, "total_size_bytes": 57_400_000, "surveyed_at": "2026-11-18T09:00:00",
        "walk_limits": {"capped": False, "walk_seconds": 0.4, "machine": __import__("socket").gethostname()}})
    words = folder_survey_view(registry, "lake")["measure_price"]["words"]
    assert "151 files, 57.4 MB" in words and "to walk again on this machine" in words


# ── D14: a completed RE-led walk refreshes the reach line, once per walk ──────────────────────────────────────────────

def test_a_walk_records_a_dated_reach_row_once_and_never_over_a_verified_row(registry, tmp_path):
    fs, _ = _fs(registry, tmp_path)
    _row(registry, "re_only", at="2026-10-21T10:00:00+00:00", slug="lake", path=DEPLOY)
    assert file_reach.fork_for(registry, fs).sentence.endswith("checked 2026-10-21")
    assert file_reach.record_walk_read(registry, "lake", "2026-11-19T01:00:00") is True
    assert file_reach.fork_for(registry, fs).sentence.endswith(f"checked {file_reach.date_of(file_reach._now())}")
    assert file_reach.record_walk_read(registry, "lake", "2026-11-19T01:00:00") is False      # once per walk run
    n = len(registry.list_reachability_history("lake"))
    # a verified row is never overwritten by a walk beside Egeria's survey
    _verified(registry, at="2099-01-01T00:00:00+00:00")
    assert file_reach.record_walk_read(registry, "lake", "2026-11-19T02:00:00") is False
    assert len(registry.list_reachability_history("lake")) == n + 1 and file_reach.fork_for(registry, fs).state == file_reach.EGERIA
