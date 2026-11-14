"""S3: history, diff, publish / promote / withdraw with the impact view, as the user.

The diff and the withdraw plan are pyegeria's real code (no Egeria needed). Egeria reads (load_held, the publish
plan's target resolution, apply) are faked at pyegeria.authoring's own functions, so Studio's rules are what's
under test: a plan is rebuilt on every step, process runs only on the previewed digest, breaking changes (or an
unreadable held version) need their own confirmation, and "Used by" is never "none" by default."""
from __future__ import annotations

import copy

import pytest

from trellis_studio import lifecycle
from test_s1_drafts import clients  # noqa: F401  (fixture)


def _spec(label="Glossary Terms Lite"):
    import pyegeria.authoring as a
    return a.new_from(template="find-spec", new_label=label)


def _without_last_column(d):
    d2 = copy.deepcopy(d.model_dump(mode="json"))
    d2["format_set"]["formats"][0]["attributes"].pop()
    return d2


@pytest.fixture
def fake_egeria(monkeypatch):
    """load_held returns `state["held"]` (or refuses with `state["refuse"]`); publish plans are built from the draft
    with a digest of its JSON; apply records its calls."""
    import pyegeria.authoring as a
    state = {"held": None, "refuse": None, "applied": [], "digest_salt": ""}

    async def load_held(kind, label, client):
        assert client.user_id == "peterprofile"
        if state["refuse"]:
            raise a.AuthoringRefused(state["refuse"])
        return state["held"]

    async def publish_plan(draft, client, *, folders=(), **kw):
        import hashlib, json as j
        body = j.dumps(draft.model_dump(mode="json"), sort_keys=True) + state["digest_salt"]
        return {"kind": draft.kind, "label": getattr(draft, "label", None), "qualified_name": "ReportSpec::x",
                "markdown": "# doc", "digest": hashlib.sha256(body.encode()).hexdigest(),
                "steps": [f"publish spec as version 1 into {', '.join(folders)}"], "folders": list(folders),
                "publication": {"resolved": True}}

    async def apply(plan, *, step, run=None, client=None, confirmed=False, digest=None):
        state["applied"].append({"step": step, "run": run is not None, "confirmed": confirmed, "digest": digest})
        return {**plan, "step": step, "result": {"success": True, "commands_total": 2, "commands_succeeded": 2}}

    monkeypatch.setattr(a, "async_load_held", load_held)
    monkeypatch.setattr(a, "async_publish_plan", publish_plan)
    monkeypatch.setattr(a, "async_apply", apply)
    monkeypatch.setattr(a, "inprocess_runner", lambda client: (lambda md, d: {"success": True}))
    return state


def _publish(authed, draft, **extra):
    body = {"op": "publish", "draft": draft, "folders": ["Portal"], **extra}
    return authed.post("/api/lifecycle/plan", json=body)


def test_plan_shows_breaking_changes_from_pyegerias_diff(authed, clients, fake_egeria):
    held = _spec()
    fake_egeria["held"] = held
    r = _publish(authed, _without_last_column(held))
    body = r.json()
    assert r.status_code == 200
    assert body["plan"]["steps"] and body["plan"]["digest"]
    assert body["needs_breaking_confirm"] is True
    assert any(c["breaking"] and c["change"] == "removed" for c in body["impact"]["breaking"])
    assert body["impact"]["used_by"]["available"] is False
    assert "not \"used by none\"" in body["impact"]["used_by"]["words"]


def test_unreadable_held_version_needs_confirmation_too(authed, clients, fake_egeria):
    fake_egeria["refuse"] = "Couldn't confirm whether Egeria holds a Report named 'x'."
    body = _publish(authed, _spec().model_dump(mode="json")).json()
    assert body["impact"]["breaking"] is None and body["needs_breaking_confirm"] is True
    assert body["impact"]["notes"] == ["Couldn't confirm whether Egeria holds a Report named 'x'."]


def test_not_held_creates_and_needs_no_breaking_confirm(authed, clients, fake_egeria):
    fake_egeria["refuse"] = "Egeria holds no report spec 'Glossary Terms Lite' that you can see."
    body = _publish(authed, _spec().model_dump(mode="json")).json()
    assert body["impact"]["breaking"] == [] and body["needs_breaking_confirm"] is False


def _apply(authed, draft, digest, **flags):
    body = {"op": "publish", "draft": draft, "folders": ["Portal"], "step": "process", "digest": digest, **flags}
    return authed.post("/api/lifecycle/apply", json=body)


def test_process_refuses_a_digest_other_than_the_previewed_one(authed, clients, fake_egeria):
    fake_egeria["refuse"] = "Egeria holds no report spec 'Glossary Terms Lite' that you can see."
    d = _spec().model_dump(mode="json")
    digest = _publish(authed, d).json()["plan"]["digest"]
    fake_egeria["digest_salt"] = "egeria changed in between"
    r = _apply(authed, d, digest, confirmed=True)
    assert r.status_code == 409 and "Preview it again" in r.json()["detail"]
    assert fake_egeria["applied"] == []


def test_process_needs_breaking_confirmation_then_runs_as_the_user(authed, clients, fake_egeria):
    held = _spec()
    fake_egeria["held"] = held
    d = _without_last_column(held)
    digest = _publish(authed, d).json()["plan"]["digest"]
    r1 = _apply(authed, d, digest, confirmed=True)
    assert r1.status_code == 409 and "Confirm that first" in r1.json()["detail"]
    assert fake_egeria["applied"] == []
    r2 = _apply(authed, d, digest, confirmed=True, confirm_breaking=True)
    assert r2.status_code == 200 and r2.json()["result"]["success"] is True
    assert fake_egeria["applied"] == [{"step": "process", "run": True, "confirmed": True, "digest": digest}]


def test_process_needs_confirmed(authed, clients, fake_egeria):
    fake_egeria["refuse"] = "Egeria holds no report spec 'x' that you can see."
    d = _spec().model_dump(mode="json")
    digest = _publish(authed, d).json()["plan"]["digest"]
    r = _apply(authed, d, digest, confirm_breaking=True)
    assert r.status_code == 409 and "confirm" in r.json()["detail"] and fake_egeria["applied"] == []


def test_the_browser_never_supplies_the_plan(authed, clients, fake_egeria):
    fake_egeria["refuse"] = "Egeria holds no report spec 'x' that you can see."
    d = _spec().model_dump(mode="json")
    digest = _publish(authed, d).json()["plan"]["digest"]
    r = authed.post("/api/lifecycle/apply", json={"op": "publish", "draft": d, "folders": ["Portal"], "step": "process",
                                                   "digest": digest, "confirmed": True,
                                                   "plan": {"markdown": "# Delete everything"}})
    assert r.status_code == 200
    assert fake_egeria["applied"][0]["digest"] == digest   # ran the server's rebuilt plan; "plan" was ignored


def test_used_by_is_used_when_pyegeria_has_it(authed, clients, fake_egeria, monkeypatch):
    import pyegeria.authoring as a
    fake_egeria["held"] = _spec()

    async def used_by(kind, label, client):
        return {"items": [{"kind": "report", "name": "Glossary report", "via": "runs it"}], "complete": False,
                "notes": ["Showing at least 1 Reports."]}
    monkeypatch.setattr(a, "async_used_by", used_by, raising=False)
    body = _publish(authed, _spec().model_dump(mode="json")).json()
    used = body["impact"]["used_by"]
    assert used["available"] and used["complete"] is False and used["items"][0]["name"] == "Glossary report"


def test_withdraw_is_pyegerias_plan_and_always_confirmed(authed, clients):
    body = authed.post("/api/lifecycle/plan", json={"op": "withdraw", "kind": "spec", "label": "Glossary Terms Lite",
                                                     "folders": ["Portal"]}).json()
    assert body["needs_breaking_confirm"] is True
    assert body["plan"]["folders"] == ["ReportSpecFolder::Consumer::Portal"] or \
        "ReportSpecFolder::Consumer::Portal" in body["plan"]["markdown"]


def test_withdraw_a_report_is_refused_in_pyegerias_words(authed, clients):
    r = authed.post("/api/lifecycle/plan", json={"op": "withdraw", "kind": "report", "label": "x", "folders": []})
    assert r.status_code == 409 and "sheet that places it" in r.json()["detail"]


def test_unknown_folder_is_refused(authed, clients):
    r = authed.post("/api/lifecycle/plan", json={"op": "withdraw", "kind": "spec", "label": "x", "folders": ["Nowhere"]})
    assert r.status_code == 409 and "Unknown consumer folder" in r.json()["detail"]


def test_promote_a_draft_that_isnt_there(authed, clients, monkeypatch):
    class Store:
        def __init__(self, *a, **k):
            pass

        async def async_load(self, uid, kind, name):
            return None
    import pyegeria.authoring as a
    monkeypatch.setattr(a, "EgeriaDraftStore", Store)
    r = authed.post("/api/lifecycle/plan", json={"op": "promote", "ref": {"kind": "spec", "name": "Gone"}, "folders": ["Portal"]})
    assert r.status_code == 409 and "no spec draft named 'Gone'" in r.json()["detail"]


def test_history_unread_is_said(authed, clients, monkeypatch):
    import pyegeria.authoring as a

    async def hist(kind, label, client):
        return {"kind": kind, "label": label, "versions": [], "read": False,
                "why": "Egeria's history could not be read: ConnectionError"}
    monkeypatch.setattr(a, "async_history", hist)
    body = authed.get("/api/history", params={"kind": "spec", "label": "x"}).json()
    assert body["read"] is False and "could not be read" in body["why"]


def test_diff_needs_inputs(authed):
    assert authed.post("/api/diff", json={}).status_code == 400


def test_diff_against_held(authed, clients, fake_egeria):
    held = _spec()
    fake_egeria["held"] = held
    body = authed.post("/api/diff", json={"draft": _without_last_column(held)}).json()
    assert body["against"] == "held" and any(c["breaking"] for c in body["changes"])


def test_consumers_are_pyegerias(authed):
    body = authed.get("/api/consumers").json()
    assert "Portal" in body["consumers"] and body["spec_prefix"] == "ReportSpecFolder::Consumer::"
