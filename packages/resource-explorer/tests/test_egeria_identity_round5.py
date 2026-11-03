"""Brief I round 5: only elements a commit CREATED are stamped; an unrecognised element shape writes
nothing; the data-class rules read falls back without a sign-in and bounds the hang-prone lookup;
Survey Definition runs that target Egeria publish file systems as before, and a run that did not
publish says so in its own words; an Ownership client that cannot be built is a partial outcome,
never a skipped record. Fakes only; nothing reaches Egeria."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import choose, fake, registry, world  # noqa: E402,F401
from test_egeria_identity_round4 import _press_then_queue  # noqa: E402

from resource_explorer import egeria_identity as ident  # noqa: E402
from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)


@pytest.fixture(autouse=True)
def _daemon(monkeypatch):
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("re-daemon", "pw"))


# ── 1: a second commit never re-stamps the shared server or the existing database ─

def test_a_second_commit_by_another_person_leaves_the_server_and_database_untouched(world, fake):
    choose(world, "sales", "catalogue")
    _press_then_queue(world, fake, "dana")
    first = [c for c in fake.calls if c[0] == "mark_on_behalf" and c[1] in (fake.server_guid, fake.db_guid)]
    assert {c[2] for c in first} == {"dana"}
    fake.calls.clear()
    choose(world, "sales", "catalogue")
    _press_then_queue(world, fake, "erin")
    again = [c for c in fake.calls if c[0] == "mark_on_behalf" and c[1] in (fake.server_guid, fake.db_guid)]
    assert again == [], "an existing server or database is never re-stamped or re-owned"


def test_an_existing_server_from_outside_re_is_not_stamped_on_the_first_commit(world, fake):
    """A server shared by host:port that someone else created (the Egeria lead's) is adopted, not owned."""
    fake.elements["s-theirs"] = {"qn": "PostgreSQL Server::host.docker.internal:5442", "type": "SoftwareServer",
                                 "parent": "", "archived": False, "deleted": False}
    choose(world, "sales", "catalogue")
    _press_then_queue(world, fake, "dana")
    stamped = {c[1] for c in fake.calls if c[0] == "mark_on_behalf"}
    assert fake.server_guid not in stamped and fake.db_guid in stamped


# ── 2: an unrecognised shape is refused, nothing written ──────────────────────

def test_an_unrecognised_element_shape_writes_nothing():
    class Odd:
        updates = []

        def get_metadata_element_by_guid(self, guid):
            return {"elementHeader": {"guid": guid}, "somethingElse": {}}

        def update_metadata_element_properties(self, guid, body):
            self.updates.append(body)

    c = Odd()
    why = ident.record_requested_by("g", ident.OnBehalf("dana", "dana"), client=c)
    assert c.updates == [] and "unrecognised element shape" in why


# ── 3: the data-class rules read ──────────────────────────────────────────────

def test_the_rules_read_answers_the_local_rules_to_nobody_signed_in():
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app

    r = TestClient(app).get("/api/egeria/rules/dataclasses")
    assert r.status_code == 200
    assert {x["source"] for x in r.json()} == {"Local Fallback"}


def test_a_hanging_guid_lookup_is_bounded(monkeypatch):
    import time as _t
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web import app as web_app
    from resource_explorer.web.routes import egeria as routes

    monkeypatch.setattr(routes, "_DATACLASS_GUID_TIMEOUT_SECONDS", 0.2)

    class Designer:
        def __init__(self, *a):
            pass

        def set_bearer_token(self, t):
            pass

        def get_metadata_element_by_unique_name(self, qn, property_name="qualifiedName"):
            _t.sleep(2)                                   # the known hang (now guarded on the unique-name read)
            return {"elementGUID": "11111111-1111-1111-1111-111111111111", "type": {"typeName": "DataClass"}}

    class Ref(Designer):
        def find_valid_value_definitions(self, **k):
            return []
    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", Designer)
    monkeypatch.setattr("pyegeria.omvs.reference_data.ReferenceDataManager", Ref)
    hdr = {"Authorization": "Bearer " + create_access_token(user_id="dan", egeria_token="tok-dan")}
    started = _t.monotonic()
    r = TestClient(web_app.app).get("/api/egeria/rules/dataclasses", headers=hdr)
    assert r.status_code == 200 and len(r.json()) == 6
    assert _t.monotonic() - started < 6 * 2, "each lookup gave up at the bound instead of waiting it out"


# ── 4: file-system publishing on Survey Definition runs ───────────────────────

def _fs(tmp_path, monkeypatch, url="https://localhost:9443", server="v"):
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry
    from resource_explorer.surveyors.filesystem import hybrid_filesystem_surveyor as hfs

    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    fs = FileSystemEntity(slug="fs", display_name="FS", local_mount_point=str(tmp_path),
                          egeria_url=url, egeria_server=server)
    reg.register_filesystem(fs)
    published = []

    class Local:
        def __init__(self, *a):
            pass

        def run(self):
            return {"surveyed_at": "2026-10-09T00:00:00", "total_files": 0}
    monkeypatch.setattr(hfs, "LocalFileSystemSurveyor", Local)
    monkeypatch.setattr("resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor",
                        lambda **k: type("E", (), {"catalog_and_survey": lambda self, *a, **kw: published.append(1) or {"ok": 1}})())
    return reg, reg.get_filesystem("fs"), published


def test_a_definition_run_targeting_egeria_publishes_a_file_system_that_names_its_egeria(tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem.survey_definition_adapter import PUBLISHED, _run_egeria_adaptive

    reg, fs, published = _fs(tmp_path, monkeypatch)
    out = _run_egeria_adaptive(fs, reg, object())          # a Survey Definition run passes no choice
    assert published == [1] and out["publish_state"] == PUBLISHED


def test_a_person_who_did_not_choose_gets_local_only_in_its_own_words(tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
        PUBLISH_NOT_CHOSEN, _run_egeria_adaptive,
    )

    reg, fs, published = _fs(tmp_path, monkeypatch)
    out = _run_egeria_adaptive(fs, reg, object(), force_egeria_publish=False)
    assert published == [] and out["publish_state"] == PUBLISH_NOT_CHOSEN
    assert "fail" not in out["publish_state"]


def test_a_definition_run_without_a_named_egeria_stays_local_and_says_so(tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
        PUBLISH_NOT_CHOSEN, _run_egeria_adaptive,
    )

    reg, fs, published = _fs(tmp_path, monkeypatch, url="", server="")
    out = _run_egeria_adaptive(fs, reg, object())
    assert published == [] and out["publish_state"] == PUBLISH_NOT_CHOSEN


# ── 5: an Ownership client that cannot be built is partial, never a skipped record ─

def test_a_blueprint_whose_ownership_client_cannot_be_built_is_still_recorded_and_partial(monkeypatch):
    from resource_explorer.egeria_clients import PlatformNotAllowed
    from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintMaterializer

    recorded = []

    def refuse(*a, **k):
        raise PlatformNotAllowed("https://evil.invalid:443")
    monkeypatch.setattr(ident, "classification_client", refuse)

    class SA:
        def create_solution_blueprint(self, body):
            return "11111111-1111-1111-1111-111111111111"

    m = BlueprintMaterializer(platform_url="https://localhost:9443")
    m._solution_architect = SA()
    m._metadata_expert = type("MX", (), {
        "get_metadata_element_by_unique_name": lambda self, *a, **k: "No elements found"})()
    m._connect = lambda: None
    monkeypatch.setattr(m, "_record", lambda *a: recorded.append(a))
    with acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by="dana")):
        out = m.materialize_blueprint_element("repo", "p", "deployment", "root", display_name="P Blueprint")
    assert recorded, "the created blueprint is recorded"
    assert out["status"] == "materialized"
    assert out["requester_not_recorded"].startswith("requester not recorded (Ownership: the Ownership client could not be built")
