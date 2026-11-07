"""F60: every route that calls Egeria as the signed-in PERSON answers a dead token the same way.

Before: only the file-profiles press (F58) raised `EgeriaTokenRefused`; the other person-call sites surfaced
pyegeria's raw AUTHORIZATION_ERROR_401 text, or a 500/502 wrapping it. Now the ONE seam every person-call passes
through — `EgeriaClients.of()` for a Caller hands out a `_PersonClient` wrapper — raises `EgeriaTokenRefused` at the
call, and the app answers the 401 `egeria_token_refused` marker even when a route's own helpers wrapped the refusal
(`_token_refusal_in` walks `__cause__` / `__context__`). The daemon's clients are never wrapped (F57: they re-mint).

Only the pyegeria boundary is faked (`egeria_clients._build`), as test_caller_identity_reads.py does."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer import egeria_clients as ec
from resource_explorer.registry import FileSystemEntity, ProjectRegistry

MARKER_BODY = {"error": "egeria_token_refused",
               "detail": "your Egeria sign-in is no longer valid (Egeria restarted since you signed in) · sign in again"}
GUID = "11111111-2222-3333-4444-555555555555"


class PyegeriaUnauthorizedException(Exception):
    """pyegeria's own type by NAME (what `classify_token_check_failure` reads), with the live 2026-11-05 text."""


def live_401():
    return PyegeriaUnauthorizedException("=> AUTHORIZATION_ERROR_401 => User not authorized received for user - "
                                         "`peterprofile` requesting on behalf of `peterprofile`")


def transport_failure():
    return ConnectionError("connection refused")


class SecurityRefusal(Exception):
    """Egeria's security connector refusing what this person may do: Brief I's `EgeriaRefused`, never a dead token."""

    related_http_code = 403

    def __init__(self):
        super().__init__("OPEN-METADATA-SECURITY-403-020 User peterprofile is not authorized to read element x")


class _DeadClient:
    """A pyegeria sub-client whose every call fails with `fail()`; the token plumbing itself works."""

    def __init__(self, cls, view_server, platform_url, user_id, password, fail):
        self.cls, self.user_id, self.password, self.fail = cls, user_id, password, fail
        self.calls: list[str] = []
        self.plain = "a plain attribute"

    def set_bearer_token(self, token):
        self.token = token

    def create_egeria_bearer_token(self):
        return "minted-daemon-token"

    def close_session(self):
        self.calls.append("close_session")

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.calls.append(name)
            raise self.fail()
        return call


def dead_boundary(monkeypatch, fail):
    """Every client the factory builds fails with `fail()`; returns the built clients for inspection."""
    built: list[_DeadClient] = []

    def build(cls, view_server, platform_url, user_id, password):
        c = _DeadClient(cls, view_server, platform_url, user_id, password, fail)
        built.append(c)
        return c
    monkeypatch.setattr(ec, "_build", build)
    return built


# ── the seam itself ───────────────────────────────────────────────────────────────────────────────────────────────

class FakeClient:
    pass


def _person(monkeypatch):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id="peterprofile", egeria_token="tok-dead", auth_source="app-jwt"))
    return lambda: current_caller.reset(reset)


def test_a_persons_call_egeria_refuses_as_a_dead_token_raises_egeria_token_refused_with_the_cause(monkeypatch):
    built = dead_boundary(monkeypatch, live_401)
    undo = _person(monkeypatch)
    try:
        client = ec.egeria_client(ec.Caller(), purpose="t").of(FakeClient)
        with pytest.raises(ec.EgeriaTokenRefused) as info:
            client.find_metadata_elements_with_string("x")
    finally:
        undo()
    assert str(info.value) == MARKER_BODY["detail"]
    assert isinstance(info.value.__cause__, PyegeriaUnauthorizedException), "the raw text is kept as the cause"
    assert built[0].calls == ["find_metadata_elements_with_string"]
    assert built[0].token == "tok-dead", "the person's token still went on the client"


@pytest.mark.parametrize("fail", [transport_failure, SecurityRefusal])
def test_every_other_failure_of_a_persons_call_passes_unchanged(monkeypatch, fail):
    """A refused connection is not a refusal; Egeria's security 403 is Brief I's `EgeriaRefused` (the route's)."""
    dead_boundary(monkeypatch, fail)
    undo = _person(monkeypatch)
    try:
        client = ec.egeria_client(ec.Caller(), purpose="t").of(FakeClient)
        with pytest.raises(Exception) as info:
            client.get_metadata_element_by_guid(GUID)
    finally:
        undo()
    assert type(info.value) is (ConnectionError if fail is transport_failure else SecurityRefusal)
    assert not isinstance(info.value, ec.EgeriaTokenRefused)


def test_a_daemons_401_is_untouched(monkeypatch):
    """F57 owns the daemon: its clients re-mint, and the sweep reads the raw refusal itself."""
    dead_boundary(monkeypatch, live_401)
    client = ec.egeria_client(ec.Daemon(ec.DaemonReason.SCHEDULER), purpose="t").of(FakeClient)
    assert not isinstance(client, ec._PersonClient)
    with pytest.raises(PyegeriaUnauthorizedException):
        client.get_metadata_element_by_guid(GUID)


def test_the_wrapper_is_one_per_sub_client_and_forwards_attributes(monkeypatch):
    built = dead_boundary(monkeypatch, live_401)
    undo = _person(monkeypatch)
    try:
        clients = ec.egeria_client(ec.Caller(), purpose="t")
        a = clients.of(FakeClient)
        assert clients.of(FakeClient) is a
        assert ec.unwrap_client(a) is built[0]
        assert a.plain == "a plain attribute"
        a.plain = "written through"
        assert built[0].plain == "written through"
        assert a.close_session.__name__ == "close_session", "a method's own attributes read through the guard"
    finally:
        undo()


def test_is_person_token_refused_reads_the_login_checks_verdict_minus_the_security_refusal():
    assert ec.is_person_token_refused(live_401())
    assert not ec.is_person_token_refused(SecurityRefusal())
    assert not ec.is_person_token_refused(transport_failure())
    assert not ec.is_person_token_refused(TimeoutError("no answer"))


# ── the routes ────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "f60.db"))


@pytest.fixture
def api(registry, monkeypatch):
    """The app over this registry, a signed-in person (`peterprofile`), every zone read permitted."""
    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    from tests.zone_fakes import open_access

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    open_access(monkeypatch, caller_if_none="peterprofile")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="peterprofile", egeria_token="tok-dead")}
    return TestClient(app), headers


def _lake(registry, tmp_path, *, guid: str = ""):
    root = tmp_path / "lake"
    root.mkdir(exist_ok=True)
    registry.register_filesystem(FileSystemEntity(slug="lake", display_name="Lake", local_mount_point=str(root),
                                                  canonical_mount_point="/deployments/lake", egeria_asset_guid=guid))
    return registry.get_filesystem("lake")


def test_attach_root_answers_the_marker_and_stores_nothing(api, registry, tmp_path, monkeypatch):
    """A write route whose FIRST person-call is a read (the root folder's qualifiedName lookup): refused before any
    create, no GUID cached, no proof row. The surveyor wraps the refusal in its own error and the route in a 500 —
    the app still answers the marker."""
    client, headers = api
    _lake(registry, tmp_path)
    built = dead_boundary(monkeypatch, live_401)
    r = client.post("/api/filesystems/lake/attach-root", headers=headers)
    assert r.status_code == 401, r.text
    assert r.json() == MARKER_BODY
    assert "AUTHORIZATION_ERROR_401" not in r.text and "Publish failed" not in r.text
    assert registry.get_filesystem("lake").egeria_asset_guid == ""
    assert registry.latest_filesystem_root_proof("lake") is None
    assert all(c.password == "" for c in built), "only the person's clients were built"
    assert "create_folder_element_from_template" not in [n for c in built for n in c.calls]


def test_attach_root_with_a_transport_failure_keeps_its_own_words(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _lake(registry, tmp_path)
    dead_boundary(monkeypatch, transport_failure)
    r = client.post("/api/filesystems/lake/attach-root", headers=headers)
    assert r.status_code == 500, r.text
    assert "Publish failed" in r.json()["detail"] and "could not be made" in r.json()["detail"]
    assert "egeria_token_refused" not in r.text


def test_native_surveys_check_answers_the_marker_not_an_unreadable_row(api, registry, tmp_path, monkeypatch):
    """A read route (the stored pointers read back from Egeria as the person)."""
    client, headers = api
    _lake(registry, tmp_path, guid=GUID)
    built = dead_boundary(monkeypatch, live_401)
    r = client.post("/api/native-surveys/filesystem/lake/check", headers=headers)
    assert r.status_code == 401, r.text
    assert r.json() == MARKER_BODY
    assert "AUTHORIZATION_ERROR_401" not in r.text
    assert [n for c in built for n in c.calls if n != "close_session"] == ["get_metadata_element_by_guid"]


def test_native_surveys_check_with_a_transport_failure_keeps_the_unreadable_row(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _lake(registry, tmp_path, guid=GUID)
    dead_boundary(monkeypatch, transport_failure)
    r = client.post("/api/native-surveys/filesystem/lake/check", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["pointers"]["database"] == "unreadable"
    assert "connection refused" in r.json()["pointers"]["error"]
    assert "egeria_token_refused" not in r.text


def test_data_class_rules_answer_the_marker_not_the_local_fallback(api, monkeypatch):
    """A read with a documented fallback (the built-in rules) still asks the person to sign in again: everything
    else they press would fail the same way."""
    client, headers = api
    dead_boundary(monkeypatch, live_401)
    r = client.get("/api/egeria/rules/dataclasses", headers=headers)
    assert r.status_code == 401, r.text
    assert r.json() == MARKER_BODY


def test_data_class_rules_with_a_transport_failure_keep_the_local_fallback(api, monkeypatch):
    client, headers = api
    dead_boundary(monkeypatch, transport_failure)
    r = client.get("/api/egeria/rules/dataclasses", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() and {x["source"] for x in r.json()} == {"Local Fallback"}


def test_the_file_profiles_press_still_says_nothing_was_profiled(api, registry, tmp_path, monkeypatch):
    """F58's own tail is kept: the run route adds "· nothing was profiled" to the shared sentence."""
    client, headers = api
    _lake(registry, tmp_path, guid=GUID)
    dead_boundary(monkeypatch, live_401)
    r = client.post("/api/filesystems/lake/file-profiles", headers=headers)
    # no walk yet is a 409 before any Egeria call; the shared sentence is F58's business (test_file_profiles.py)
    assert r.status_code in (401, 409), r.text
    if r.status_code == 401:
        assert r.json()["detail"].endswith("· nothing was profiled")


def test_token_refusal_in_walks_cause_and_context_and_stops_on_a_cycle():
    from fastapi import HTTPException

    from resource_explorer.web.app import _token_refusal_in

    refused = ec.EgeriaTokenRefused()
    try:
        try:
            raise RuntimeError("wrapped") from refused
        except RuntimeError:
            raise HTTPException(status_code=500, detail="Publish failed")
    except HTTPException as http:
        assert _token_refusal_in(http) is refused
    plain = HTTPException(status_code=404, detail="not here")
    assert _token_refusal_in(plain) is None
    loop = RuntimeError("a")
    loop.__cause__ = loop
    assert _token_refusal_in(loop) is None
