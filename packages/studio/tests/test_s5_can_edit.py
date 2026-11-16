"""S5 (Studio side): /api/can-edit answers yes / no / unknown as the signed-in user, from pyegeria.authoring's own
check; with no such check it is "unknown" with the reason (never "no", never a guess). Opening a held artifact
checks again and refuses on no and on unknown."""
from __future__ import annotations

import logging

import pytest

from trellis_studio import rights
from test_s1_drafts import clients  # noqa: F401  (fixture)


@pytest.fixture
def no_check(monkeypatch):
    import pyegeria.authoring as a
    monkeypatch.delattr(a, "async_can_edit", raising=False)


def _check(monkeypatch, answer=None, raises=None, seen=None):
    import pyegeria.authoring as a

    async def can_edit(kind, ref, client):
        if seen is not None:
            seen.append((kind, ref, client.user_id))
        if raises:
            raise raises
        return answer
    monkeypatch.setattr(a, "async_can_edit", can_edit, raising=False)


def test_without_pyegerias_check_it_is_unknown_with_the_reason(authed, no_check, caplog):
    with caplog.at_level(logging.INFO, logger="trellis_studio"):
        body = authed.get("/api/can-edit", params={"ref": "ReportSpec::Glossary Terms Lite"}).json()
    assert body["answer"] == "unknown" and "no edit-rights check" in body["why"] and body["kind"] == "spec"
    assert any("can-edit" in r.message and "unknown" in r.message for r in caplog.records)


def test_yes_and_no_come_from_pyegeria_as_the_user(authed, clients, monkeypatch):
    seen = []
    _check(monkeypatch, {"answer": "yes", "why": "you own it"}, seen=seen)
    assert authed.get("/api/can-edit", params={"ref": "Dashboard::catalog-health"}).json()["answer"] == "yes"
    _check(monkeypatch, {"answer": "no", "why": "zone Clinical is not yours"}, seen=seen)
    body = authed.get("/api/can-edit", params={"ref": "ReportSpec::X"}).json()
    assert body == {"answer": "no", "why": "zone Clinical is not yours", "kind": "spec", "ref": "X"}
    assert seen == [("sheet", "catalog-health", "peterprofile"), ("spec", "X", "peterprofile")]


def test_a_failed_or_odd_check_is_unknown_never_no(authed, clients, monkeypatch):
    _check(monkeypatch, raises=ConnectionError("down"))
    assert authed.get("/api/can-edit", params={"ref": "ReportSpec::X"}).json()["answer"] == "unknown"
    _check(monkeypatch, {"answer": "maybe"})
    body = authed.get("/api/can-edit", params={"ref": "ReportSpec::X"}).json()
    assert body["answer"] == "unknown" and "not yes/no/unknown" in body["why"]


def test_a_guid_needs_a_kind(authed, no_check):
    assert authed.get("/api/can-edit", params={"ref": "216cc161-fee1-4bab-b460-b2a2bd66ed82"}).status_code == 400
    body = authed.get("/api/can-edit", params={"ref": "216cc161-fee1-4bab-b460-b2a2bd66ed82", "kind": "report"}).json()
    assert body["answer"] == "unknown" and body["ref"] == "216cc161-fee1-4bab-b460-b2a2bd66ed82"


def test_can_edit_needs_a_session(client):
    assert client.get("/api/can-edit", params={"ref": "ReportSpec::X"}).status_code == 401


def test_open_held_refuses_unless_yes(authed, clients, monkeypatch, no_check):
    r = authed.get("/api/held/spec/X")
    assert r.status_code == 409 and "can't show that you may edit" in r.json()["detail"]
    _check(monkeypatch, {"answer": "no", "why": "not yours"})
    r = authed.get("/api/held/spec/X")
    assert r.status_code == 403 and "You can't edit spec 'X': not yours" == r.json()["detail"]


def test_open_held_on_yes_loads_it_as_the_user(authed, clients, monkeypatch):
    import pyegeria.authoring as a
    _check(monkeypatch, {"answer": "yes", "why": ""})
    loaded = []

    async def load_held(kind, label, client):
        loaded.append((kind, label, client.user_id))
        return a.new_from(template="find-spec", new_label=label)
    monkeypatch.setattr(a, "async_load_held", load_held)
    body = authed.get("/api/held/spec/Glossary Terms Lite").json()
    assert body["draft"]["label"] == "Glossary Terms Lite" and body["can_edit"]["answer"] == "yes"
    assert loaded == [("spec", "Glossary Terms Lite", "peterprofile")]


def test_page_opens_hand_offs_through_the_checked_route():
    from pathlib import Path
    js = (Path(__file__).parent.parent / "trellis_studio" / "static" / "studio.js").read_text()
    assert "api(`/api/held/${kind}/${encodeURIComponent(label)}`)" in js
    assert "history.replaceState(null, '', window.location.pathname);" in js
