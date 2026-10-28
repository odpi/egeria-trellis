"""Brief I: the factory's own rules — named identities, a closed reason list, one token per scope,
no silent fallback, and the recorded value the connection popover shows. Fake clients only."""
from __future__ import annotations

import time

import pytest

from resource_explorer import egeria_clients as ec
from resource_explorer.a2a_auth import CallerIdentity, current_caller


@pytest.fixture(autouse=True)
def _daemon(monkeypatch):
    monkeypatch.setattr(ec, "_daemon_credential", lambda: ("re-daemon", "pw-daemon"))


class FakeClient:
    built: list = []

    def __init__(self, *a):
        self.user, self.token, self.mints = a[2], None, 0
        FakeClient.built.append(self)

    def set_bearer_token(self, t):
        self.token = t

    def create_egeria_bearer_token(self, *a):
        self.mints += 1
        self.token = f"minted-{self.mints}"
        return self.token


class OtherClient(FakeClient):
    pass


@pytest.fixture(autouse=True)
def _reset():
    FakeClient.built = []


def _signed_in(token="tok-p"):
    return current_caller.set(CallerIdentity(user_id="p", egeria_token=token, auth_source="app-jwt"))


def test_a_daemon_reason_outside_the_closed_list_is_refused():
    with pytest.raises(ValueError, match="not a DaemonReason"):
        ec.Daemon("outbox_drain")                      # the right words, but not the enum member
    forged = ec.Daemon(ec.DaemonReason.SCHEDULER).__class__(
        user_id="x", password="y", is_service_account=True, kind="daemon", reason="made-up")
    with pytest.raises(ValueError, match="unknown reason"):
        ec.egeria_client(forged, purpose="t")


def test_an_unnamed_identity_is_refused_by_the_factory():
    from resource_explorer.egeria_identity import EgeriaIdentity

    with pytest.raises(ValueError):
        ec.egeria_client(EgeriaIdentity(user_id="svc", password="pw", is_service_account=True), purpose="t")


def test_no_caller_and_no_declared_job_raises_never_the_daemon():
    with pytest.raises(ec.NoCallerIdentity):
        ec.current_principal()
    reset = current_caller.set(CallerIdentity(user_id="queued", egeria_token=None, auth_source="queued-run"))
    try:
        with pytest.raises(ec.NoCallerIdentity):     # a caller with no token is not a caller
            ec.Caller()
    finally:
        current_caller.reset(reset)


def test_an_expired_caller_token_raises_the_sentence_and_cannot_be_refreshed(monkeypatch):
    reset = _signed_in()
    try:
        clients = ec.egeria_client(ec.Caller(), purpose="t")
        monkeypatch.setattr(ec, "_token_expiry", lambda tok: int(time.time()) - 1)
        with pytest.raises(ec.CallerTokenExpired, match="your Egeria sign-in expired; sign in again"):
            ec.Caller()
        with pytest.raises(ec.CallerTokenExpired):
            clients.of(FakeClient)
        with pytest.raises(ec.CallerTokenExpired):
            clients.refresh()                            # RE holds no caller password: no refresh
    finally:
        current_caller.reset(reset)


def test_a_daemon_mints_once_and_its_sub_clients_share_the_token():
    clients = ec.egeria_client(ec.Daemon(ec.DaemonReason.BOOTSTRAP), purpose="t")
    a, b = clients.of(FakeClient), clients.of(OtherClient)
    assert (a.user, b.user) == ("re-daemon", "re-daemon")
    assert a.mints + b.mints == 1 and a.token == b.token == "minted-1"
    assert clients.of(FakeClient) is a


def test_a_daemon_re_mints_when_its_token_nears_expiry(monkeypatch):
    clients = ec.egeria_client(ec.Daemon(ec.DaemonReason.SCHEDULER), purpose="t")
    a = clients.of(FakeClient)
    b = clients.of(OtherClient)
    monkeypatch.setattr(ec, "_token_expiry", lambda tok: int(time.time()) + 30)   # inside the margin
    clients.of(FakeClient)
    assert a.mints == 2 and b.token == a.token == "minted-2"


def test_a_daemon_for_a_requester_owns_as_them_and_authenticates_as_the_daemon():
    identity = ec.Daemon(ec.DaemonReason.RUN_QUEUE, requested_by="erin")
    assert identity.user_id == "erin" and identity.requested_by == "erin"
    client = ec.egeria_client(identity, purpose="t").of(FakeClient)
    assert client.user == "re-daemon" and client.mints == 1


def test_one_client_per_identity_per_scope_and_none_shared_across_people():
    with ec.client_scope():
        r1 = _signed_in("tok-1")
        try:
            first = ec.egeria_client(ec.Caller(), purpose="a")
            again = ec.egeria_client(ec.Caller(), purpose="b")
        finally:
            current_caller.reset(r1)
        r2 = current_caller.set(CallerIdentity(user_id="q", egeria_token="tok-2", auth_source="app-jwt"))
        try:
            other = ec.egeria_client(ec.Caller(), purpose="a")
        finally:
            current_caller.reset(r2)
    assert first is again and other is not first
    assert other.of(FakeClient).token == "tok-2"


def test_acting_as_takes_only_a_daemon_and_wins_inside_its_block():
    reset = _signed_in()
    try:
        with pytest.raises(ValueError):
            with ec.acting_as(ec.Caller()):
                pass
        with ec.acting_as(ec.Daemon(ec.DaemonReason.RESYNC)):
            assert ec.current_principal().reason == "egeria_resync"
        assert ec.current_principal().kind == "caller"
    finally:
        current_caller.reset(reset)


def test_daemon_entry_keeps_a_carried_person_even_in_a_worker_marked_process(monkeypatch):
    @ec.daemon_entry(ec.DaemonReason.PREFECT_FLOW)
    def flow():
        return ec.current_principal()

    monkeypatch.setenv(ec.PREFECT_WORKER_MARKER, "flow-run-1")
    reset = _signed_in()
    try:
        assert flow().kind == "caller"               # never replaces a person who is there
    finally:
        current_caller.reset(reset)


def test_the_popover_value_is_recorded_per_person_not_inferred(monkeypatch):
    monkeypatch.setattr(ec, "_last_by_user", {})
    assert ec.last_egeria_identity("p") is None      # nothing recorded yet: not "you"
    reset = _signed_in()
    try:
        ec.egeria_client(ec.Caller(), purpose="publish").of(FakeClient)
    finally:
        current_caller.reset(reset)
    assert ec.last_egeria_identity("p")["as"] == "you"
    ec.egeria_client(ec.Daemon(ec.DaemonReason.RUN_QUEUE, requested_by="p"), purpose="survey").of(FakeClient)
    rec = ec.last_egeria_identity("p")
    assert rec["as"] == "service account (Resource Explorer) on your behalf" and rec["purpose"] == "survey"


def test_whoami_returns_the_recorded_value(monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app

    monkeypatch.setattr(ec, "_last_by_user", {})
    hdr = {"Authorization": "Bearer " + create_access_token(user_id="p", egeria_token="tok-p")}
    c = TestClient(app)
    assert c.get("/api/egeria/whoami", headers=hdr).json()["last_egeria_call"] is None
    ec.egeria_client(ec.Daemon(ec.DaemonReason.RUN_QUEUE, requested_by="p"), purpose="survey").of(FakeClient)
    got = c.get("/api/egeria/whoami", headers=hdr).json()["last_egeria_call"]
    assert got["as"] == "service account (Resource Explorer) on your behalf"


@pytest.mark.parametrize("url", ["https://egeria.invalid:99999", "https://egeria.invalid:notaport"])
def test_an_unreadable_port_is_a_platform_refusal_not_a_crash(url):
    reset = _signed_in()
    try:
        with pytest.raises(ec.PlatformNotAllowed, match="unreadable port"):
            ec.egeria_client(ec.Caller(), purpose="t", platform_url=url)
    finally:
        current_caller.reset(reset)
