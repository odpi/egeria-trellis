"""TR-87: a person presses "Request action" on a file-system finding; it becomes ONE RequestForActionAnnotation on that
step run's SurveyReport, once. Every Egeria call is a fake (`tests/fs_egeria_fake.py`); a temp sqlite registry throughout.
Nothing here files a request by itself, and the press never touches the publish table's Publish / Keep in RE choice."""
from __future__ import annotations

import dataclasses
import json

import pytest

from resource_explorer import fs_findings
from resource_explorer import publish_table as pt
from resource_explorer.surveyors import survey_step_host
from tests.fs_egeria_fake import FakeEgeria, install
from tests.library_step_install import install_steps  # noqa: F401
from tests.test_files_walk_extensions import _register, _tree, api, registry  # noqa: F401 - fixtures reused

RUN = "2026-11-19T03:00:00"
URL = "/api/filesystems/walkme/findings/request-action"
BODY = {"step": "fs_permissions", "finding": "world_readable_sensitive", "run_id": RUN}
SENSITIVE = {"measure": "world_readable_sensitive", "field": "World Readable Secret Named Files"}


def _store(registry, step="fs_permissions", measure="world_readable_sensitive", field="World Readable Secret Named Files",
           count=3, state="measured", run=RUN, extra=None):
    key = survey_step_host.measure_step_key(step, measure)
    registry.record_step_annotations("filesystem", "walkme", key, run=run, measured_at=run, annotations=[{
        "annotation_type": "RequestForActionAnnotation", "scope": "walkme", "summary": f"{count} found",
        "resourceProperties": {field: count, "asOf": run, **(extra or {})}, "additionalProperties": {"resultState": state}}])


def _setup(registry, tmp_path, **kw):
    _register(registry, _tree(tmp_path))
    _store(registry, **kw)


def _rfa_rows(registry):
    return [e for e in registry.list_activity(entity_slug="walkme", operation="rfa", limit=50)
            if any(a.get("finding_key") for a in e.get("annotations") or [])]


# ── the route ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_signed_out_press_is_refused_and_creates_nothing(registry, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    _setup(registry, tmp_path)
    egeria = install(monkeypatch, FakeEgeria())
    r = TestClient(app).post(URL, json=BODY)             # no token, access not opened
    assert r.status_code in (401, 403), r.text
    assert egeria.annotation_creates == [] and egeria.report_creates == [] and _rfa_rows(registry) == []


@pytest.mark.usefixtures("signed_in_caller")
def test_a_caller_without_publish_is_403(api, registry, tmp_path, monkeypatch):
    from resource_explorer.workflows.curate import AccessDecision

    client, headers = api
    _setup(registry, tmp_path)
    monkeypatch.setattr("resource_explorer.workflows.curate.resource_access",
                        lambda *a, **k: AccessDecision(False, "not authorized to publish this filesystem", "anonymous"))
    egeria = install(monkeypatch, FakeEgeria())
    assert client.post(URL, json=BODY, headers=headers).status_code == 403
    assert egeria.annotation_creates == [] and _rfa_rows(registry) == []


@pytest.mark.usefixtures("signed_in_caller")
@pytest.mark.parametrize("step,finding", [("fs_permissions", "nope"), ("fs_duplicates_elsewhere", "fs_duplicates_elsewhere"),
                                          ("fs_access_activity", "fs_access_activity"), ("fs_change_rates", "fs_change_rates"),
                                          ("fs_layout_signals", "fs_layout_signals"), ("", "")])
def test_an_unknown_finding_is_400_and_names_the_allowed_ones(api, registry, tmp_path, monkeypatch, step, finding):
    client, headers = api
    _setup(registry, tmp_path)
    install(monkeypatch, FakeEgeria())
    r = client.post(URL, json={"step": step, "finding": finding, "run_id": RUN}, headers=headers)
    assert r.status_code == 400
    assert "fs_permissions::world_readable_sensitive" in r.json()["detail"] and "fs_activity::fs_activity" in r.json()["detail"]


@pytest.mark.usefixtures("signed_in_caller")
@pytest.mark.parametrize("state", ["not_established", "not_measured", "nothing_found", "unreadable", "measured_zero"])
def test_a_finding_that_is_not_measured_and_above_zero_is_409_and_no_button(api, registry, tmp_path, monkeypatch, state):
    client, headers = api
    _setup(registry, tmp_path, state="measured" if state == "measured_zero" else state, count=0 if state == "measured_zero" else 3)
    egeria = install(monkeypatch, FakeEgeria())
    r = client.post(URL, json=BODY, headers=headers)
    assert r.status_code == 409 and r.json()["detail"] == "this finding is not in that run's result"
    assert egeria.annotation_creates == [] and egeria.report_creates == [] and _rfa_rows(registry) == []
    assert fs_findings.offer_for(registry, "walkme", "fs_permissions", "world_readable_sensitive") is None, "no button either"


@pytest.mark.usefixtures("signed_in_caller")
def test_a_stale_run_is_409_a_rerun_is_a_new_finding(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _setup(registry, tmp_path)
    install(monkeypatch, FakeEgeria())
    r = client.post(URL, json={**BODY, "run_id": "2026-11-18T00:00:00"}, headers=headers)
    assert r.status_code == 409 and r.json()["detail"] == "this finding is not in that run's result"
    assert fs_findings.finding_key("fs_permissions", "world_readable_sensitive", RUN) != \
        fs_findings.finding_key("fs_permissions", "world_readable_sensitive", "2026-11-20T00:00:00")


@pytest.mark.usefixtures("signed_in_caller")
def test_a_file_system_not_in_egeria_is_409(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _setup(registry, tmp_path)
    egeria = install(monkeypatch, FakeEgeria())
    real = type(registry).get_filesystem
    monkeypatch.setattr(type(registry), "get_filesystem",
                        lambda self, slug: (lambda f: dataclasses.replace(f, egeria_asset_guid="") if f else f)(real(self, slug)))
    r = client.post(URL, json=BODY, headers=headers)
    assert r.status_code == 409 and "publish the file system to Egeria first" in r.json()["detail"]
    assert egeria.annotation_creates == [] and _rfa_rows(registry) == []


@pytest.mark.usefixtures("signed_in_caller")
def test_a_press_files_one_annotation_and_one_local_row_a_second_press_says_nothing_new(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _setup(registry, tmp_path)
    egeria = install(monkeypatch, FakeEgeria())
    first = client.post(URL, json=BODY, headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["nothing_new"] is False and body["annotation_guid"] and body["words"].startswith("requested · RFA ")
    assert len(egeria.annotation_creates) == 1 and len(egeria.report_creates) == 1
    rows = _rfa_rows(registry)
    assert len(rows) == 1
    ann = rows[0]["annotations"][0]
    assert ann["annotation_type"] == "RequestForActionAnnotation" and ann["status"] == "local"
    assert ann["finding_key"] == f"fs_permissions::world_readable_sensitive::{RUN}" and ann["egeria_annotation_guid"] == body["annotation_guid"]
    assert ann["analysis_name"] == "File Permissions"

    second = client.post(URL, json=BODY, headers=headers)
    assert second.status_code == 200 and second.json()["nothing_new"] is True
    assert second.json()["annotation_guid"] == body["annotation_guid"] and second.json()["words"] == "already requested · nothing new"
    assert len(egeria.annotation_creates) == 1 and len(egeria.report_creates) == 1 and len(_rfa_rows(registry)) == 1


@pytest.mark.usefixtures("signed_in_caller")
def test_an_egeria_failure_is_502_not_sent_and_no_local_row(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _setup(registry, tmp_path)
    install(monkeypatch, FakeEgeria(fail_after=0))
    r = client.post(URL, json=BODY, headers=headers)
    assert r.status_code == 502 and r.json()["detail"].startswith("not sent · ")
    assert _rfa_rows(registry) == []


@pytest.mark.usefixtures("signed_in_caller")
def test_the_press_is_made_as_the_caller(api, registry, tmp_path, monkeypatch):
    from resource_explorer.egeria_clients import EgeriaIdentity
    from resource_explorer.surveyors.filesystem import egeria_filesystem_surveyor as efs

    client, headers = api
    _setup(registry, tmp_path)
    install(monkeypatch, FakeEgeria())
    seen = []
    real_init = efs.EgeriaFileSystemSurveyor.__init__

    def spy(self, *a, **k):
        seen.append(k.get("identity"))
        return real_init(self, *a, **k)

    monkeypatch.setattr(efs.EgeriaFileSystemSurveyor, "__init__", spy)
    assert client.post(URL, json=BODY, headers=headers).status_code == 200
    assert seen and all(isinstance(i, EgeriaIdentity) for i in seen)


@pytest.mark.usefixtures("signed_in_caller")
def test_the_press_never_reads_or_changes_the_publish_choice(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _setup(registry, tmp_path)
    install(monkeypatch, FakeEgeria())
    before = pt.current_choices(registry, "filesystem", "walkme")
    assert before == {}                                   # "no choice" stays "no choice"
    assert client.post(URL, json=BODY, headers=headers).status_code == 200
    assert pt.current_choices(registry, "filesystem", "walkme") == before
    saved = pt.save_choice(registry, "filesystem", "walkme", analysis_id="fs_permissions", measure="fs_permissions",
                           choice="keep", author="dan")
    chosen = pt.current_choices(registry, "filesystem", "walkme")
    client.post(URL, json=BODY, headers=headers)
    assert pt.current_choices(registry, "filesystem", "walkme") == chosen and saved is not None


# ── the surveyor ────────────────────────────────────────────────────────────────────────────────────────────────

def _surveyor_press(registry, step, finding, field, measure, count=4, extra=None):
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    _store(registry, step=step, measure=measure, field=field, count=count, extra=extra)
    found = fs_findings.reread(registry, "walkme", step, finding, RUN)
    return EgeriaFileSystemSurveyor().request_action_for_finding(registry.get_filesystem("walkme"), found, "dan", registry)


def test_the_report_is_one_per_step_run_found_by_name_and_reused(registry, tmp_path, monkeypatch):
    _register(registry, _tree(tmp_path))
    egeria = install(monkeypatch, FakeEgeria())
    proof = _surveyor_press(registry, "fs_permissions", "world_readable_sensitive", "World Readable Secret Named Files", "world_readable_sensitive")
    assert egeria.report_creates == [f"SurveyReport::FileSystem::walkme::fs_permissions::{RUN}"]
    assert proof["report_reused"] is False and proof["nothing_new"] is False
    # a second finding of the same step run reuses the report and adds its own annotation
    _store(registry, step="fs_permissions", measure="fs_permissions", field="World Writable Files", count=2)
    found = fs_findings.reread(registry, "walkme", "fs_permissions", "world_writable", RUN)
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    again = EgeriaFileSystemSurveyor().request_action_for_finding(registry.get_filesystem("walkme"), found, "dan", registry)
    assert again["report_reused"] is True and len(egeria.report_creates) == 1 and len(egeria.annotation_creates) == 2


def test_the_annotation_says_what_to_review_and_names_the_file_system(registry, tmp_path, monkeypatch):
    _register(registry, _tree(tmp_path))
    egeria = install(monkeypatch, FakeEgeria())
    _surveyor_press(registry, "fs_permissions", "world_readable_sensitive", "World Readable Secret Named Files", "world_readable_sensitive")
    props = next(iter(egeria.annotations.values()))["body"]["properties"]
    assert props["actionRequested"] == "Review file permissions: secret-looking files are world-readable"
    assert props["actionProperties"]["actionTargetName"].startswith("FileFolder::walkme:")
    assert "4 secret-looking files readable by others on" in props["summary"] and f"run {RUN}" in props["summary"]
    assert "Requested by dan on " in props["explanation"] and "fs_permissions run" in props["explanation"]
    jp = json.loads(props["jsonProperties"])
    assert jp["step"] == "fs_permissions" and jp["finding"] == "world_readable_sensitive" and jp["run"] == RUN
    assert jp["count"] == 4 and jp["requestedBy"] == "dan" and jp["requestedAt"] and "files" not in jp


def test_world_readable_sensitive_never_sends_file_names(registry, tmp_path, monkeypatch):
    """TR-109 W1: fs_permissions promises "the names are in Resource Explorer, not published". Even if a stored result
    carried a file list, the finding sends none, and says where the names are."""
    _register(registry, _tree(tmp_path))
    egeria = install(monkeypatch, FakeEgeria())
    _surveyor_press(registry, "fs_permissions", "world_readable_sensitive", "World Readable Secret Named Files",
                    "world_readable_sensitive", count=25, extra={"files": [f"f{i}.pem" for i in range(25)]})
    props = next(iter(egeria.annotations.values()))["body"]["properties"]
    jp = json.loads(props["jsonProperties"])
    assert "files" not in jp
    assert "f0.pem" not in json.dumps(props)
    assert "names are in Resource Explorer, not published" in props["explanation"]


@pytest.mark.usefixtures("signed_in_caller")
def test_the_guard_is_the_senders_not_only_the_readers(registry, tmp_path, monkeypatch):
    """A `found` handed to the surveyor with names on it (any caller) still sends none for a finding that does not publish them."""
    _register(registry, _tree(tmp_path))
    egeria = install(monkeypatch, FakeEgeria())
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    _store(registry)
    found = {**fs_findings.reread(registry, "walkme", "fs_permissions", "world_readable_sensitive", RUN),
             "files": ["id_rsa", "prod.env"], "more": 3}
    EgeriaFileSystemSurveyor().request_action_for_finding(registry.get_filesystem("walkme"), found, "dan", registry)
    assert "id_rsa" not in json.dumps(next(iter(egeria.annotations.values()))["body"])


def test_the_reread_returns_no_names_for_a_finding_that_does_not_publish_them(registry, tmp_path, monkeypatch):
    _register(registry, _tree(tmp_path))
    _store(registry, extra={"files": ["id_rsa"]})
    found = fs_findings.reread(registry, "walkme", "fs_permissions", "world_readable_sensitive", RUN)
    assert found["files"] == [] and found["more"] == 0


def test_the_request_time_is_timezone_aware_utc(registry, tmp_path, monkeypatch):
    _register(registry, _tree(tmp_path))
    egeria = install(monkeypatch, FakeEgeria())
    _surveyor_press(registry, "fs_permissions", "world_readable_sensitive", "World Readable Secret Named Files", "world_readable_sensitive")
    at = json.loads(next(iter(egeria.annotations.values()))["body"]["properties"]["jsonProperties"])["requestedAt"]
    assert at.endswith("+00:00"), at


def test_an_existing_rfa_row_is_found_past_the_500_newest_entries(registry, tmp_path):
    """TR-109 W3: the dedupe looked at the 500 newest RFA entries only, so an older row was missed and a second one written."""
    from resource_explorer.activity_logger import log_rfa
    from resource_explorer.web.routes.filesystems import _local_rfa_row_exists

    _register(registry, _tree(tmp_path))
    key = f"fs_permissions::world_readable_sensitive::{RUN}"
    log_rfa(registry, "filesystem", "walkme", "walkme", "local", "old", extra={"finding_key": key})
    for i in range(520):
        log_rfa(registry, "filesystem", "walkme", "walkme", "local", f"newer {i}", extra={"finding_key": f"other::{i}"})
    assert _local_rfa_row_exists(registry, "walkme", key) is True
    assert _local_rfa_row_exists(registry, "walkme", key + "x") is False
    assert _local_rfa_row_exists(registry, "walkme", "fs_permissions::world_readable_sensitive::%") is False


def test_fs_activity_carries_no_file_list_and_says_cold_data(registry, tmp_path, monkeypatch):
    _register(registry, _tree(tmp_path))
    egeria = install(monkeypatch, FakeEgeria())
    _surveyor_press(registry, "fs_activity", "fs_activity", "Cold Files", "fs_activity", count=9)
    props = next(iter(egeria.annotations.values()))["body"]["properties"]
    assert "files" not in json.loads(props["jsonProperties"]) and props["actionRequested"] == "Review cold data: keep, archive or delete"
    assert "no file list" in props["explanation"]
    assert egeria.report_creates == [f"SurveyReport::FileSystem::walkme::fs_activity::{RUN}"]


@pytest.mark.parametrize("step,finding,measure,field,action", [
    ("fs_permissions", "world_writable", "fs_permissions", "World Writable Files", "Review file permissions: files are world-writable"),
    ("fs_duplicates", "fs_duplicates", "fs_duplicates", "Duplicate Sets", "Review duplicate content")])
def test_the_other_allowlisted_findings(registry, tmp_path, monkeypatch, step, finding, measure, field, action):
    _register(registry, _tree(tmp_path))
    egeria = install(monkeypatch, FakeEgeria())
    _surveyor_press(registry, step, finding, field, measure, count=2)
    assert next(iter(egeria.annotations.values()))["body"]["properties"]["actionRequested"] == action


# ── the table row, and "running a step publishes nothing" ───────────────────────────────────────────────────────

def test_the_row_offers_the_request_even_where_it_reads_keep_in_re_only(registry, tmp_path, monkeypatch, install_steps):  # noqa: F811
    install_steps("fs_permissions")
    _setup(registry, tmp_path)
    table = pt.build_rows(registry, "filesystem", "walkme")
    row = next(r for r in table["rows"] if r["measure"] == "world_readable_sensitive")
    assert row["publishable"] is False and row["request_action"] == {
        "step": "fs_permissions", "finding": "world_readable_sensitive", "run_id": RUN, "count": 3,
        "unit": "secret-looking files readable by others", "key": f"fs_permissions::world_readable_sensitive::{RUN}"}
    assert row["choice"] is None
    other = next(r for r in table["rows"] if r["measure"] == "fs_permissions")
    assert other["request_action"] is None                    # a measure with no stored result offers nothing


def test_storing_and_reading_a_step_result_publishes_nothing(registry, tmp_path, monkeypatch, install_steps):  # noqa: F811
    from resource_explorer.surveyors.filesystem import walk_derived

    install_steps("fs_permissions")
    egeria = install(monkeypatch, FakeEgeria())
    _setup(registry, tmp_path)
    walk_derived.derived_view(registry, "walkme")
    pt.build_rows(registry, "filesystem", "walkme")
    assert egeria.report_creates == [] and egeria.annotation_creates == [] and _rfa_rows(registry) == []


def test_the_report_carries_no_annotation_count_that_a_second_finding_would_make_stale(registry, tmp_path, monkeypatch):
    """Found in the live proof: the report was created with annotation_count "1" and, after a second finding, held two."""
    _register(registry, _tree(tmp_path))
    bodies = []
    real = FakeEgeria.create_asset
    monkeypatch.setattr(FakeEgeria, "create_asset", lambda self, body=None, **kw: bodies.append(body) or real(self, body=body, **kw))
    install(monkeypatch, FakeEgeria())
    _surveyor_press(registry, "fs_permissions", "world_readable_sensitive", "World Readable Secret Named Files", "world_readable_sensitive")
    assert len(bodies) == 1
    extra = bodies[0]["properties"]["additionalProperties"]
    assert "annotation_count" not in extra and extra["step"] == "fs_permissions" and extra["surveyor"] == "Resource Explorer"
