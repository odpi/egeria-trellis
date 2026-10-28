"""Cancel a waiting or running Egeria survey action (an Egeria WRITE, as the signed-in person).

End to end through the real route, the real access decision, the real port and the real client factory. The
ONLY fake is the pyegeria client boundary: `pyegeria.AutomatedCuration` and `pyegeria.MetadataExpert` (and the
two access-check clients), each method bound against the INSTALLED pyegeria signature first, so a call the
real library would reject fails here too. Nothing here contacts Egeria.

What the real library says (pyegeria 6.2.1): `AutomatedCuration.cancel_engine_action(engine_action_guid: str)
-> None`.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

sys.path.insert(0, str(Path(__file__).parent))
pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

from tests.zone_fakes import FakeEgeria, install  # noqa: E402
from tests.zone_fakes import element as _element  # noqa: E402

USER = "dana"
SURVEY = "PostgreSQLSurvey::survey-postgres-database"
G_OURS = "11111111-1111-1111-1111-111111111111"
G_OTHER = "22222222-2222-2222-2222-222222222222"      # another resource's action
G_DONE = "33333333-3333-3333-3333-333333333333"
URL = "/api/native-surveys/database/d/egeria-actions/{}/cancel"


def _sig(cls_path: str, name: str) -> inspect.Signature:
    module, _, cls = cls_path.rpartition(".")
    return inspect.signature(getattr(getattr(__import__(module, fromlist=[cls]), cls), name))


REAL_CANCEL = _sig("pyegeria.omvs.automated_curation.AutomatedCuration", "cancel_engine_action")
REAL_READ = _sig("pyegeria.omvs.metadata_expert.MetadataExpert", "get_metadata_element_by_guid")


def test_the_real_signature_is_what_the_fake_is_bound_to():
    assert list(REAL_CANCEL.parameters) == ["self", "engine_action_guid"]
    assert REAL_CANCEL.return_annotation in (None, "None")


class Egeria:
    """Egeria's engine actions, behind the two pyegeria classes the cancel path uses."""

    def __init__(self):
        self.messages: dict[str, str] = {}
        self.status = {G_OURS: "IN_PROGRESS", G_OTHER: "IN_PROGRESS", G_DONE: "COMPLETED"}
        self.cancel_calls: list[tuple] = []         # (guid, user, token)
        self.reads: list[str] = []
        self.cancel_error: Exception | None = None
        self.read_error: Exception | None = None     # every status read fails
        self.reports: dict[str, str] = {}            # action guid -> report guid (ReportOriginator link)
        self.ignore_cancel = False                  # Egeria accepts the call and the action keeps going

    def classes(self):
        eg = self

        class _Base:
            def __init__(self, view_server, platform_url, user_id, user_pwd=None, *a, **k):
                self.user, self.token = user_id, None

            def set_bearer_token(self, token):
                self.token = token

        class AutomatedCuration(_Base):
            def cancel_engine_action(self, *a, **k):
                REAL_CANCEL.bind(self, *a, **k)
                guid = (a or [k.get("engine_action_guid")])[0]
                eg.cancel_calls.append((guid, self.user, self.token))
                if eg.cancel_error:
                    raise eg.cancel_error
                if not eg.ignore_cancel:
                    eg.status[guid] = "CANCELLED"

        class MetadataExpert(_Base):
            def get_metadata_element_by_guid(self, *a, **k):
                REAL_READ.bind(self, *a, **k)
                guid = (a or [k.get("guid")])[0]
                eg.reads.append(guid)
                if eg.read_error:
                    raise eg.read_error
                return {"elementProperties": {"propertyValueMap": {
                    "activityStatus": {"symbolicName": eg.status[guid]},
                    "completionMessage": {"primitiveValue": eg.messages.get(guid, "")}}}}

            def get_related_metadata_elements(self, *a, **k):
                guid = a[0]
                if guid in eg.reports:
                    return {"elementList": [{"element": {"elementGUID": eg.reports[guid]}}]}
                return "No elements found"

        return AutomatedCuration, MetadataExpert


@pytest.fixture
def egeria(monkeypatch):
    eg = Egeria()
    cur, exp = eg.classes()
    monkeypatch.setattr("pyegeria.AutomatedCuration", cur)
    monkeypatch.setattr("pyegeria.MetadataExpert", exp)
    return eg


@pytest.fixture
def access(monkeypatch) -> FakeEgeria:
    return install(monkeypatch)                     # the access-check clients; no zone in use


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "c.db"))
    assert str(tmp_path) in r.database_url
    for slug in ("d", "other"):
        r.register_database(DatabaseEntity(slug=slug, display_name=slug, db_type="postgresql", host="h", port=5432,
                                           database_name=slug, db_user="u", db_password="s3cretpw",
                                           egeria_asset_guid=f"asset-{slug}"))
    for slug, guid, status in (("d", G_OURS, "IN_PROGRESS"), ("d", G_DONE, "COMPLETED"), ("other", G_OTHER, "IN_PROGRESS")):
        r.record_native_survey_submission("database", slug, SURVEY, ("2026-09-30T01:41:00" if guid == G_DONE else "2026-10-01T01:41:00"),
                                          engine_action_guid=guid, submitted_by=USER, started_as="gov_action_type")
        r.record_native_survey_readback(guid, read_at="2026-10-11T12:00:00", status=status)
    return r


@pytest.fixture
def web(registry, monkeypatch):
    who = {"user_id": USER, "role": "user", "identity": True}
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    claims = lambda request: {"user_id": who["user_id"], "egeria_token": f"tok-{who['user_id']}", "role": who["role"]}  # noqa: E731
    monkeypatch.setattr("resource_explorer.auth.get_current_user", claims)
    from resource_explorer import auth as _auth

    real_identity = _auth.identity_from_claims
    monkeypatch.setattr("resource_explorer.auth.identity_from_claims",
                        lambda c: real_identity(c) if who["identity"] else None)
    from resource_explorer.web.app import app

    return TestClient(app), who


def _activity(registry):
    import json

    with registry._conn() as conn:
        rows = [dict(x) for x in conn.execute("SELECT summary, status, detail FROM activity_log ORDER BY rowid").fetchall()]
    for r in rows:
        try:
            r["detail"] = json.loads(r["detail"] or "{}")
        except ValueError:
            r["detail"] = {}
    return rows


G_PROC = "44444444-4444-4444-4444-444444444444"       # a process INSTANCE under a name no one configured
G_REG = "55555555-5555-5555-5555-555555555555"        # the registration (Catalog and Survey) process
CATALOG = "PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess"


def _add_row(registry, guid, process_qn, started_as="", status="IN_PROGRESS"):
    registry.record_native_survey_submission("database", "d", process_qn, "2026-10-02T01:41:00",
                                             engine_action_guid=guid, submitted_by=USER, started_as=started_as)
    registry.record_native_survey_readback(guid, read_at="2026-10-11T12:00:00", status=status)


def _status(client, guid=G_OURS):
    rows = client.get("/api/native-surveys/database/d").json()["surveys"]
    return next(r for r in rows if r["qualified_name"] == SURVEY)["run"]


def test_success_cancels_as_the_caller_and_the_read_back_says_cancelled(web, egeria, access, registry):
    client, _ = web
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancelled"] is True and body["egeria_status"] == "CANCELLED"
    # the write reached Egeria once, for this GUID, as THE PERSON (their token), never the daemon
    assert egeria.cancel_calls == [(G_OURS, USER, f"tok-{USER}")]
    # the read-back is the status refresh: it read the action and RE's row now carries what Egeria said
    assert egeria.reads == [G_OURS] * 3              # the gate's fresh read, the read right before the write, the read-back
    row = next(x for x in registry.list_native_survey_runs("database", "d") if x["engine_action_guid"] == G_OURS)
    assert row["engine_action_status"] == "CANCELLED" and row["engine_action_read_at"] > "2026-10-11T12:00:01"
    run = next(s for s in body["surveys"] if s["qualified_name"] == SURVEY)["run"]
    assert run["state"] == "failed" and run["egeria_status"] == "CANCELLED" and run["message"] == "cancelled in Egeria"
    assert body["access_basis"] == "no_zones"
    # an activity entry was written (rule 16)
    with registry._conn() as conn:
        acts = [dict(x) for x in conn.execute("SELECT summary, status FROM activity_log").fetchall()]
    assert any(f"Cancelled Egeria action {G_OURS}" in a["summary"] and a["status"] == "ok" for a in acts), acts
    assert _activity(registry)[-1]["detail"]["cancelled_by"] == USER


def test_a_cancel_Egeria_accepts_but_does_not_report_is_not_called_cancelled(web, egeria, access):
    client, _ = web
    egeria.ignore_cancel = True
    body = client.post(URL.format(G_OURS)).json()
    assert body["cancelled"] is False and body["egeria_status"] == "IN_PROGRESS"


def test_anonymous_gets_401_and_nothing_is_sent(web, egeria, access):
    client, who = web
    who["identity"] = False
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 401
    assert egeria.cancel_calls == [] and egeria.reads == []


def test_a_guid_that_is_not_this_resources_is_refused_before_egeria(web, egeria, access):
    client, _ = web
    for guid in (G_OTHER, "99999999-9999-9999-9999-999999999999"):
        r = client.post(URL.format(guid))
        assert r.status_code == 404, r.text
        assert "not one of the Egeria actions RE started" in r.json()["detail"]
    assert egeria.cancel_calls == [] and egeria.reads == []


def test_an_access_refusal_gives_the_word_and_sends_nothing(web, egeria, access, registry):
    client, _ = web
    access.elements[G_OURS] = _element(G_OURS, zones=["sales-zone"])
    access.controls["sales-zone"] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 403
    assert r.json()["detail"].startswith(f"zone sales-zone does not grant {USER} ")
    assert egeria.cancel_calls == []
    acts = _activity(registry)
    assert acts[-1]["status"] == "error" and "refused" in acts[-1]["summary"] and acts[-1]["detail"]["cancelled_by"] == USER
    # the operations Egeria checks for a cancel: read the element, then update its properties
    assert ("element", G_OURS) in access.calls


def test_an_egeria_error_is_a_502_with_the_reason_and_the_row_still_says_running(web, egeria, access, registry):
    client, _ = web
    egeria.cancel_error = RuntimeError("engine host unreachable")
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 502 and "engine host unreachable" in r.json()["detail"]
    assert _status(client)["egeria_status"] == "IN_PROGRESS"          # nothing was guessed onto the row
    last = _activity(registry)[-1]
    assert last["status"] == "error" and "engine host unreachable" in last["summary"] and last["detail"]["cancelled_by"] == USER


def test_egeria_security_refusal_is_a_403_in_egerias_words(web, egeria, access):
    client, _ = web
    egeria.cancel_error = RuntimeError("OPEN-METADATA-SECURITY-403-002 user dana is not authorized to update")
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 403 and "OPEN-METADATA-SECURITY-403" in r.json()["detail"]


def test_a_finished_action_cannot_be_cancelled(web, egeria, access, registry):
    client, _ = web
    r = client.post(URL.format(G_DONE))
    assert r.status_code == 409 and "Egeria now says COMPLETED" in r.json()["detail"]
    assert egeria.cancel_calls == []
    last = _activity(registry)[-1]
    assert last["status"] == "error" and "refused" in last["summary"] and last["detail"]["cancelled_by"] == USER
    # and a second press after a success is refused the same way
    assert client.post(URL.format(G_OURS)).status_code == 200
    assert client.post(URL.format(G_OURS)).status_code == 409
    assert len(egeria.cancel_calls) == 1


def test_an_action_that_finished_since_the_last_poll_is_never_overwritten(web, egeria, access, registry):
    """RE's stored row still says IN_PROGRESS; Egeria already says COMPLETED. Egeria's cancel has no status
    guard, so sending it would turn COMPLETED into CANCELLED. The gate reads first and refuses (409)."""
    client, _ = web
    egeria.status[G_OURS] = "COMPLETED"
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 409 and "Egeria now says COMPLETED" in r.json()["detail"]
    assert egeria.cancel_calls == []
    assert egeria.status[G_OURS] == "COMPLETED"
    row = next(x for x in registry.list_native_survey_runs("database", "d") if x["engine_action_guid"] == G_OURS)
    assert row["engine_action_status"] == "COMPLETED"              # the fresh read was recorded


def test_the_liveness_gate_comes_before_the_access_check(web, egeria, access):
    """A finished action in a zone the person cannot update is a 409 (it is finished), not a 403."""
    client, _ = web
    egeria.status[G_OURS] = "COMPLETED"
    access.elements[G_OURS] = _element(G_OURS, zones=["sales-zone"])
    access.controls["sales-zone"] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
    assert client.post(URL.format(G_OURS)).status_code == 409


def test_read_granted_but_update_not_granted_is_refused_naming_update(web, egeria, access, registry):
    client, _ = web
    access.elements[G_OURS] = _element(G_OURS, zones=["sales-zone"])
    access.controls["sales-zone"] = {"associatedSecurityList": {"READ": [USER], "UPDATE_PROPERTIES": ["salesTeam"]}}
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 403 and "update" in r.json()["detail"], r.text
    assert egeria.cancel_calls == []


def test_a_guid_that_is_not_ours_is_logged_as_refused_with_the_person(web, egeria, access, registry):
    client, _ = web
    assert client.post(URL.format(G_OTHER)).status_code == 404
    last = _activity(registry)[-1]
    assert last["status"] == "error" and "refused" in last["summary"] and last["detail"]["cancelled_by"] == USER


# ── round 3: fail closed everywhere (Egeria's cancel has no status guard) ──────────────────────────────

def test_a_failed_fresh_read_refuses_even_though_egeria_says_completed(web, egeria, access, registry):
    client, _ = web
    egeria.status[G_OURS] = "COMPLETED"
    egeria.read_error = RuntimeError("platform unreachable")
    r = client.post(URL.format(G_OURS))
    assert r.status_code == 502 and "could not read Egeria's current status · not cancelled" in r.json()["detail"]
    assert egeria.cancel_calls == []
    last = _activity(registry)[-1]
    assert last["status"] == "error" and last["detail"]["cancelled_by"] == USER


def test_the_gate_itself_refuses_on_a_failed_read(registry):
    """Unit: remove the gate's read-error check and this fails (the route test above is also covered by the
    second read, so it cannot pin the gate alone)."""
    from tests.test_native_survey_run import FakePort

    port = FakePort()
    port.actions[G_OURS] = ("IN_PROGRESS", "")
    port.read_action_error = RuntimeError("down")
    with pytest.raises(nsr.NativeStatusUnreadable):
        nsr.cancel_gate(registry, port, "database", "d", G_OURS)


def test_the_second_read_right_before_the_write_refuses_an_action_that_just_finished(registry):
    from tests.test_native_survey_run import FakePort

    class Racy(FakePort):
        def __init__(self):
            super().__init__()
            self.cancelled = []

        def read_action(self, guid):
            r = super().read_action(guid)
            if len([c for c in self.calls if c[0] == "read_action"]) == 1:
                self.actions[guid] = ("COMPLETED", "done")     # finishes after the gate's read
            return r

        def cancel(self, guid):
            self.cancelled.append(guid)

    port = Racy()
    port.actions[G_OURS] = ("IN_PROGRESS", "")
    with pytest.raises(nsr.NativeSurveyCannotRun):
        nsr.cancel_native_action(registry, port, "database", "d", G_OURS)
    assert port.cancelled == []
    port2 = Racy(); port2.actions[G_OURS] = ("IN_PROGRESS", "")
    port2.read_action_error = None
    # and a failed second read refuses too
    calls = {"n": 0}
    real = port2.read_action

    def flaky(guid):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("down")
        return real(guid)
    port2.read_action = flaky
    with pytest.raises(nsr.NativeStatusUnreadable):
        nsr.cancel_native_action(registry, port2, "database", "d", G_OURS)
    assert port2.cancelled == []


@pytest.mark.parametrize("guid,name,stamp", [
    (G_PROC, "SomeDiscoveredProcess::unlisted", "process"),      # stamped as a process instance
    (G_PROC, "SomeDiscoveredProcess::unlisted", ""),             # an old row: the allowlist, not the denylist
    (G_PROC, SURVEY, "process"),                                 # even under a listed name, the stamp wins
    (G_REG, CATALOG, "process"),                                 # the registration
    (G_REG, CATALOG, ""),
    (G_PROC, SURVEY, ""),                                        # unstamped under a LISTED name: refused outright
])
def test_only_a_single_action_survey_is_ever_sent_to_egeria(web, egeria, access, registry, guid, name, stamp):
    client, _ = web
    egeria.status[guid] = "IN_PROGRESS"
    _add_row(registry, guid, name, stamp)
    r = client.post(URL.format(guid))
    assert r.status_code == 409, r.text
    assert egeria.cancel_calls == [] and egeria.reads == []
    assert _activity(registry)[-1]["detail"]["cancelled_by"] == USER


def test_a_row_stamped_as_an_action_type_is_allowed_even_under_an_unlisted_name(web, egeria, access, registry):
    client, _ = web
    egeria.status[G_PROC] = "IN_PROGRESS"
    _add_row(registry, G_PROC, "SomeDiscoveredSurvey::unlisted", "gov_action_type")
    assert client.post(URL.format(G_PROC)).status_code == 200
    assert egeria.cancel_calls[0][0] == G_PROC


def test_each_row_says_whether_it_is_cancellable(web, egeria, access, registry):
    client, _ = web
    _add_row(registry, G_REG, CATALOG, "process")
    rows = {r["qualified_name"]: r for r in client.get("/api/native-surveys/database/d").json()["surveys"]}
    assert rows[SURVEY]["cancellable"] is True
    assert rows[CATALOG]["cancellable"] is False
    registry.record_native_survey_readback(G_OURS, read_at="2026-10-11T13:00:00", error="connection refused")
    rows = {r["qualified_name"]: r for r in client.get("/api/native-surveys/database/d").json()["surveys"]}
    assert rows[SURVEY]["cancellable"] is False              # the last read failed: not offered


def test_a_cancel_that_lands_on_an_action_with_a_report_says_egeria_may_have_finished(web, egeria, access):
    client, _ = web
    egeria.reports[G_OURS] = "rep-9"
    body = client.post(URL.format(G_OURS)).json()
    assert body["cancelled"] is True and body["note"] == "Egeria may already have finished · the report may exist"


def test_a_submitted_survey_is_stamped_as_started_by_an_action_type(registry):
    import json

    registry.record_native_survey_submission("database", "d", SURVEY, "2026-10-03T00:00:00", engine_action_guid="g9",
                                             started_as=nsr.STARTED_AS_ACTION_TYPE)
    row = next(r for r in registry.list_native_survey_runs("database", "d") if r["engine_action_guid"] == "g9")
    assert json.loads(row["declared"]) == {"started_as": "gov_action_type"}


def test_the_second_reads_message_is_scrubbed_of_the_resources_password(web, egeria, access, registry):
    client, _ = web
    egeria.status[G_OURS] = "COMPLETED"
    egeria.messages[G_OURS] = "login failed for password s3cretpw"
    assert client.post(URL.format(G_OURS)).status_code == 409
    row = next(x for x in registry.list_native_survey_runs("database", "d") if x["engine_action_guid"] == G_OURS)
    assert "s3cretpw" not in (row["engine_action_message"] or "")
    assert "s3cretpw" not in str(client.get("/api/native-surveys/database/d").json())


def test_the_pre_write_read_scrubs_when_the_action_looked_live_at_the_gate(registry):
    """Unit on the SECOND read alone: the gate (gated=True) is skipped, so only that read's message is stored."""
    from tests.test_native_survey_run import FakePort

    port = FakePort()
    port.actions[G_OURS] = ("COMPLETED", "password s3cretpw rejected")
    with pytest.raises(nsr.NativeSurveyCannotRun):
        nsr.cancel_native_action(registry, port, "database", "d", G_OURS, gated=True)
    row = next(x for x in registry.list_native_survey_runs("database", "d") if x["engine_action_guid"] == G_OURS)
    assert "s3cretpw" not in row["engine_action_message"] and row["engine_action_message"]


def test_declared_that_is_already_a_dict_is_read_as_stamped():
    ok = {"declared": {"started_as": "gov_action_type"}, "executor_ref": "X", "step_key": "egeria-native:X"}
    assert nsr.cancellable_kind(ok) is True
    assert nsr.cancellable_kind({**ok, "declared": '{"started_as": "gov_action_type"}'}) is True
    assert nsr.cancellable_kind({**ok, "declared": "{}"}) is False
    assert nsr.cancellable_kind({**ok, "declared": {"started_as": "process"}}) is False
