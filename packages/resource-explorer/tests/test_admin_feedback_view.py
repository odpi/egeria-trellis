"""Feedback readable across resources, not only where it was written — AND
from both of the two independent stores that back the Admin pane.

`resource_feedback` has been collected per resource since Curate shipped, and
until 2026-08-31 the only reader was `list_resource_feedback(entity_type, slug)`.
That answers "what did people say about this one", which is right from a
resource page and useless from Admin: finding out whether anyone had said
anything meant visiting every resource in turn.

Separately, until 2026-09-01, `GET /api/curate/feedback` (this pane's only
data source) read `resource_feedback` alone. Page-level feedback — the
`feedback` table written by the page-widget in `feedback_store.py` — has its
own dedicated reader (`GET /api/feedback`, admin-feedback.html) but was never
folded into THIS pane, so a page-level submission looked, from here, exactly
like it had been dropped. It hadn't: the pane just never read the store it
landed in. See resource_explorer/web/routes/curate.py::list_all_feedback.

These tests pin what makes the combined Admin view honest rather than merely
present: both sources show up, each row is badged with which one it came
from, and an empty pane says which of "nobody wrote anything" or "your filter
excluded everything" is true.
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app


@pytest.fixture
def feedback_store(tmp_path):
    from resource_explorer.feedback_store import FeedbackStore
    return FeedbackStore(db_path=str(tmp_path / "test_feedback.db"))


#: A configured admin token, plus the header that presents it.
#:
#: `admin_auth.is_admin_request` is FAIL-CLOSED: with neither admin_token nor
#: admin_users configured it denies everything, so a test that did not set one
#: would see 403 for the right value and the wrong reason, and the
#: known-negative below (a valid credential is ADMITTED) could never pass.
@pytest.fixture
def admin_headers(monkeypatch):
    from resource_explorer import config as cfg_mod

    monkeypatch.setattr(cfg_mod.get_config().feedback, "admin_token", "test-token",
                        raising=False)
    return {"X-Admin-Token": "test-token"}


@pytest.fixture
def client(monkeypatch, tmp_path, feedback_store):
    """An isolated SQLite registry AND an isolated page-feedback store, so this
    never reads or writes the shared Postgres instance either table normally
    lives in."""
    from resource_explorer import config as cfg_mod
    cfg = cfg_mod.get_config()
    monkeypatch.setattr(cfg.registry, "database_url", f"sqlite:///{tmp_path}/t.db", raising=False)
    # Route handlers instantiate FeedbackStore() with no args (curate.py's
    # list_all_feedback does exactly this) — redirect every instance to the
    # fixture's already-initialized tmp_path-backed store, same trick
    # test_feedback_routes.py uses for the dedicated /api/feedback routes.
    monkeypatch.setattr(
        "resource_explorer.feedback_store.FeedbackStore.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", feedback_store.__dict__) or None,
    )
    return TestClient(app)


def _registry(tmp_path):
    from resource_explorer.registry import ProjectRegistry
    return ProjectRegistry(database_url=f"sqlite:///{tmp_path}/t.db")


def test_feedback_spans_entity_types_not_just_repos(tmp_path):
    """The cross-resource reader must not quietly default to repos.

    Feedback is recorded against databases and filesystems too. A default of
    entity_type="repo" would make the other two look like nobody had ever
    commented on them.
    """
    reg = _registry(tmp_path)
    reg.add_resource_feedback("repo", "a", 5, "quality", "solid")
    reg.add_resource_feedback("database", "b", None, "", "no rating given")
    reg.add_resource_feedback("filesystem", "c", 2, "docs", "hard to follow")

    rows = reg.list_all_resource_feedback()
    assert len(rows) == 3, f"expected all three entity types, got {[r['entity_type'] for r in rows]}"
    assert {r["entity_type"] for r in rows} == {"repo", "database", "filesystem"}

    only_db = reg.list_all_resource_feedback(entity_type="database")
    assert len(only_db) == 1 and only_db[0]["entity_slug"] == "b"


def test_counts_distinguish_volume_from_reach(tmp_path):
    """"40 items" and "40 items across 3 resources" say different things about
    whether the feature is being used."""
    reg = _registry(tmp_path)
    for i in range(4):
        reg.add_resource_feedback("repo", "same-repo", 3, "", f"note {i}")
    reg.add_resource_feedback("repo", "other-repo", 4, "", "elsewhere")

    counts = reg.count_all_resource_feedback()
    assert counts == {"total": 5, "resources": 2}, counts


def test_the_route_says_whether_an_empty_list_was_filtered(client, admin_headers):
    """An empty list is ambiguous on its own: "nobody has left feedback" and
    "your filter excluded everything" look identical, and only the first is a
    fact about the world."""
    unfiltered = client.get("/api/curate/feedback", headers=admin_headers)
    assert unfiltered.status_code == 200, unfiltered.text
    assert unfiltered.json()["filtered"] is False

    filtered = client.get("/api/curate/feedback?category=nonexistent-category", headers=admin_headers)
    assert filtered.status_code == 200
    assert filtered.json()["filtered"] is True
    assert filtered.json()["feedback"] == []


def test_both_stores_are_genuinely_empty_and_both_counts_say_so(client, admin_headers):
    """Neither store has a row. The response must make that a stated fact for
    EACH source, not one combined total that hides which store is empty."""
    resp = client.get("/api/curate/feedback", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["feedback"] == []
    assert data["filtered"] is False
    assert data["counts"] == {
        "resource": {"total": 0, "resources": 0},
        "page": {"total": 0},
    }


def test_the_route_combines_both_sources_badged_by_origin(client, tmp_path, feedback_store, admin_headers):
    """The bug, verbatim: a page-level submission (`feedback` table) must show
    up here at all, alongside resource_feedback — and each row must say which
    store it came from, since the two are deliberately not merged."""
    from resource_explorer.feedback_store import FeedbackEntry

    reg = _registry(tmp_path)
    reg.add_resource_feedback("repo", "some-repo", 4, "quality", "from Curate")
    feedback_store.add(FeedbackEntry(page="/scouting", rating=5, message="from the page widget"))

    resp = client.get("/api/curate/feedback", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["feedback"]) == 2, data["feedback"]

    sources = {r["source"] for r in data["feedback"]}
    assert sources == {"resource", "page"}, (
        "a page-level row must appear here — it was the entire bug report"
    )
    assert data["counts"]["resource"] == {"total": 1, "resources": 1}
    assert data["counts"]["page"] == {"total": 1}

    page_row = next(r for r in data["feedback"] if r["source"] == "page")
    resource_row = next(r for r in data["feedback"] if r["source"] == "resource")
    assert page_row["message"] == "from the page widget"
    assert page_row["page"] == "/scouting"
    # A page row has no entity_type/entity_slug at all — it was never
    # recorded against a resource.
    assert "entity_type" not in page_row or page_row.get("entity_type") in (None, "")
    assert resource_row["message"] == "from Curate"
    assert resource_row["entity_slug"] == "some-repo"
    # A resource_feedback row has no triage_status — that column belongs
    # only to the page-level `feedback` table's workflow.
    assert "triage_status" not in resource_row


def test_source_is_itself_a_filter(client, tmp_path, feedback_store, admin_headers):
    """Badging exists so the two stores can be told apart later; filtering by
    source is the same capability exercised from the query string."""
    from resource_explorer.feedback_store import FeedbackEntry

    reg = _registry(tmp_path)
    reg.add_resource_feedback("repo", "some-repo", 4, "quality", "from Curate")
    feedback_store.add(FeedbackEntry(page="/scouting", rating=5, message="from the page widget"))

    only_page = client.get("/api/curate/feedback?source=page", headers=admin_headers).json()
    assert len(only_page["feedback"]) == 1
    assert only_page["feedback"][0]["source"] == "page"
    assert only_page["filtered"] is True

    only_resource = client.get("/api/curate/feedback?source=resource", headers=admin_headers).json()
    assert len(only_resource["feedback"]) == 1
    assert only_resource["feedback"][0]["source"] == "resource"


def test_an_entity_type_filter_excludes_page_rows_rather_than_ignoring_them(
    client, tmp_path, feedback_store
, admin_headers):
    """entity_type has no meaning for page-level feedback — it was never
    recorded against a resource. Filtering by it must exclude every page row,
    not silently show them anyway because the filter doesn't apply to them."""
    from resource_explorer.feedback_store import FeedbackEntry

    reg = _registry(tmp_path)
    reg.add_resource_feedback("repo", "some-repo", 4, "quality", "from Curate")
    feedback_store.add(FeedbackEntry(page="/scouting", rating=5, message="from the page widget"))

    filtered = client.get("/api/curate/feedback?entity_type=repo", headers=admin_headers).json()
    assert {r["source"] for r in filtered["feedback"]} == {"resource"}

    filtered_other = client.get("/api/curate/feedback?entity_type=database", headers=admin_headers).json()
    assert filtered_other["feedback"] == []


def test_the_per_resource_route_still_works(client):
    """The new collection route must not shadow the two-segment one."""
    res = client.get("/api/curate/feedback/repo/anything")
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


class TestContactFieldStrippingHelperIsKept:
    """`_without_contact_fields` is no longer applied by the listing route.

    It was the INTERIM fix (cb99d72) while `/api/curate/feedback` was ungated.
    The route is gated now and serves the full row, matching the equally-gated
    `/api/feedback` — two gated views of one store disagreeing about its
    contents would be a new drift replacing the one just closed.

    The helper is kept and tested rather than deleted: the shape it encodes —
    REMOVE a key rather than blank it, so absence cannot be mistaken for a
    measured empty — is worth keeping available if an ungated caller ever
    exists again.
    """

    def test_keys_are_removed_not_blanked(self):
        from resource_explorer.web.routes.curate import _without_contact_fields

        out = _without_contact_fields(
            {"email": "a@b.c", "message": "hi", "session_id": "s1"})
        assert "email" not in out and "session_id" not in out
        assert out["message"] == "hi"

    def test_it_keeps_the_feedback_itself(self):
        """Known-negative: a helper that removed everything would satisfy the
        assertion above."""
        from resource_explorer.web.routes.curate import _without_contact_fields

        out = _without_contact_fields(
            {"email": "a@b.c", "message": "hi", "category": "bug", "rating": 5})
        assert out == {"message": "hi", "category": "bug", "rating": 5}


class TestTheListingIsGated:
    """`/api/curate/feedback` and `/api/feedback` are two views of one store.

    Until 2026-09-01 one was gated and the other was not, and the ungated one
    had just been widened to serve the page-level feedback store. That is the
    drift `docs/admin-surface-options.md` names as the prerequisite before any
    further admin surface is extracted — the one place RE already split a
    surface produced duplicated logic and divergent auth.

    `admin_auth.is_admin_request` is fail-closed AND an admin token is
    configured, so the gate was real and being bypassed, not aspirational.
    """

    def test_without_a_credential_the_listing_is_refused(self, client, feedback_store):
        from resource_explorer.feedback_store import FeedbackEntry

        feedback_store.add(FeedbackEntry(page="/scouting", rating=5, message="hi"))
        resp = client.get("/api/curate/feedback")
        assert resp.status_code == 403, (
            "an ungated listing of submitted feedback, which can carry contact "
            "details, is the drift this closes")

    def test_a_refusal_is_not_an_empty_list(self, client):
        """403, never 200-with-nothing.

        A 200 carrying an empty list would be read as 'there is no feedback' —
        which is how this whole thread started: Dan's feedback looked dropped
        when the pane was simply reading the wrong store. Unauthenticated and
        empty must not look alike.
        """
        # NO credential on purpose — this test is about the refusal. A bulk
        # edit added `headers=admin_headers` here and it silently became a test
        # of the authenticated path instead, passing for the wrong reason.
        resp = client.get("/api/curate/feedback")
        assert resp.status_code != 200, "a refusal must not masquerade as an empty result"
        assert "feedback" not in resp.json(), "a refusal must not carry a rows key at all"

    def test_with_a_valid_credential_the_full_row_is_served(self, client, feedback_store, admin_headers):
        """The known-negative for the gate: it must ADMIT a valid credential.

        Without this, a route that refused everyone unconditionally would pass
        both tests above. Also pins that a gated caller gets the FULL row —
        stripping was the interim while the route was ungated, and keeping it
        would leave two equally-gated views of one store disagreeing.
        """
        from resource_explorer.feedback_store import FeedbackEntry

        feedback_store.add(FeedbackEntry(
            page="/scouting", rating=5, message="hi", email="a@b.c"))
        resp = client.get("/api/curate/feedback", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        page = [r for r in resp.json()["feedback"] if r["source"] == "page"]
        assert page, "no page rows — this test would pass vacuously"
        assert any("email" in r for r in page), (
            "a gated caller should see the same row the equally-gated "
            "/api/feedback serves")
