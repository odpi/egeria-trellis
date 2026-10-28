"""Brief I round 2: daemon_entry's explicit marker, the popover record of a daemon call inside a
person's request, and the publisher's Ownership stamp no longer
swallowing a missing sign-in. (The queued-run token handoff was removed by the owner's
Egeria-consistent ruling; queued runs are covered in test_egeria_identity_e2e.) Fake clients
only; nothing reaches Egeria."""
from __future__ import annotations

import pytest

from resource_explorer import egeria_clients as ec


# ── item 5: daemon_entry only in a Prefect worker process ───────────────────

def test_daemon_entry_needs_the_prefect_worker_marker(monkeypatch):
    @ec.daemon_entry(ec.DaemonReason.PREFECT_FLOW)
    def flow():
        return ec.current_principal()

    monkeypatch.delenv(ec.PREFECT_WORKER_MARKER, raising=False)
    with pytest.raises(ec.NoCallerIdentity):               # a missing caller elsewhere raises
        flow()
    monkeypatch.setenv(ec.PREFECT_WORKER_MARKER, "flow-run-1")
    assert flow().reason == "prefect_flow"


# ── item 7: popover, publisher ──────────────────────────────────

class _Fake:
    def __init__(self, *a):
        self.token = None

    def set_bearer_token(self, t):
        self.token = t

    def create_egeria_bearer_token(self, *a):
        self.token = "minted"
        return "minted"


def test_a_daemon_call_inside_a_persons_request_is_recorded_under_that_person(monkeypatch, signed_in_caller):
    monkeypatch.setattr(ec, "_last_by_user", {})
    ec.egeria_client(ec.Daemon(ec.DaemonReason.REACHABILITY), purpose="reachability").of(_Fake)
    rec = ec.last_egeria_identity("test-caller")
    assert rec["as"] == "service account (background)" and rec["purpose"] == "reachability"


def test_the_publishers_ownership_stamp_lets_a_missing_sign_in_through(monkeypatch):
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    def no_one(*a, **k):
        raise ec.NoCallerIdentity()
    monkeypatch.setattr("resource_explorer.egeria_identity.classification_client", no_one)
    pub = EgeriaPublisher(platform_url="https://x")
    pub._identity = ec.Daemon(ec.DaemonReason.SCHEDULER)
    with pytest.raises(ec.NoCallerIdentity):
        pub._stamp_governance("g1")
