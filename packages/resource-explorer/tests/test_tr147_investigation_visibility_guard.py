"""TR-147 (security, medium): an investigation's data request and a work list linked to an investigation are visible only to the people
who can see that investigation.

Before: PUT /api/open-data/investigations/{slug}/data-request checked only that someone was signed in, then replaced the row keyed on the slug,
so any signed-in user could REPLACE another person's request; GET checked nothing. `lens-terms` and `fit` read the stored request of any
slug, and the work-list routes never asked whether the caller could see the list's investigation.

One guard: sign-in FIRST (never the shared identity "", which sees every investigation by design), then `registry.get_investigation(slug)` as
that caller; None is a 404 with the SAME words as a slug that does not exist, so it never confirms that a private investigation is there.
Everything here goes through the web layer with real tokens: A's creator-only investigation, B who cannot see it, an anonymous caller.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, NO_EGERIA, _chosen, _register_demo, reg, store  # noqa: E402,F401

from resource_explorer.open_data import service  # noqa: E402

A, B = "tr147-user-a", "tr147-user-b"
NEED = {"subjects": ["water"], "licence": {"commercial": True}}


def _as(user_id: str):
    from contextlib import contextmanager

    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    @contextmanager
    def cm():
        reset = current_caller.set(CallerIdentity(user_id=user_id, egeria_token=None, auth_source="app-jwt"))
        try:
            yield
        finally:
            current_caller.reset(reset)
    return cm()


@pytest.fixture
def world(reg, store):
    """A's creator-only investigation (+ a work list linked to it), a shared one, an ownerless one, and one dataset on a plain list."""
    from resource_explorer.work_lists import WorkLists

    with _as(A):
        private = reg.create_investigation("A private", project_classification="PersonalProject")["slug"]
        shared = reg.create_investigation("A shared", project_classification="StudyProject")["slug"]
    with _as(A):                      # the legacy shape: creator-only, created before RE recorded an owner (created_by empty)
        ownerless = reg.create_investigation("ownerless", project_classification="PersonalProject")["slug"]
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET created_by = '' WHERE slug = ?", (ownerless,))
    for slug in (private, shared, ownerless):
        store.save_data_request(slug, NEED, A)
    portal = _register_demo(store)
    plain = service.import_datasets(store, _chosen(portal["slug"], "annakarenina"), work_list_name="plain", transport=FakeTransport(),
                                    egeria_lookup=NO_EGERIA)["work_list"]
    wls = WorkLists(reg)
    lists = {"private": wls.create("A private list", [], investigation=private)["slug"],
             "shared": wls.create("A shared list", [], investigation=shared)["slug"],
             "ownerless": wls.create("ownerless list", [], investigation=ownerless)["slug"]}
    # a list WITH a dataset, linked to the private investigation: the open-data routes act on its datasets, so an empty list proves nothing
    private_data = service.import_datasets(store, _chosen(portal["slug"], "warandpeace"), work_list_name="A private data list",
                                           transport=FakeTransport(), egeria_lookup=NO_EGERIA)["work_list"]
    wls.set_investigation(private_data, private)
    return {"private": private, "shared": shared, "ownerless": ownerless, "plain": plain, "private_data": private_data,
            "lists": lists, "wls": wls, "portal": portal["slug"]}


@pytest.fixture
def api(reg, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", reg.__dict__) or None)
    client = TestClient(app)
    tokens = {u: {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="tok")} for u in (A, B)}
    return client, tokens


# ── the data request ─────────────────────────────────────────────────────────

@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_cannot_read_a_creator_only_investigations_data_request_and_the_words_match_a_missing_slug(api, world):
    client, tokens = api
    url = lambda s: f"/api/open-data/investigations/{s}/data-request"             # noqa: E731
    hidden = client.get(url(world["private"]), headers=tokens[B])
    missing = client.get(url("no-such-investigation"), headers=tokens[B])
    assert hidden.status_code == 404 and missing.status_code == 404
    assert hidden.json()["detail"].replace(world["private"], "X") == missing.json()["detail"].replace("no-such-investigation", "X")
    assert "subjects" not in hidden.text and "water" not in hidden.text                     # nothing of the request leaks


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_cannot_replace_it_and_the_row_is_unchanged(api, store, world):
    client, tokens = api
    before = store.get_data_request(world["private"])
    r = client.put(f"/api/open-data/investigations/{world['private']}/data-request", headers=tokens[B],
                   json={"subjects": ["overwritten by B"], "licence": {}})
    assert r.status_code == 404
    assert store.get_data_request(world["private"]) == before                               # B's PUT WROTE NOTHING
    assert "overwritten by B" not in str(store.get_data_request(world["private"]))


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_can_read_and_replace_their_own(api, store, world):
    client, tokens = api
    url = f"/api/open-data/investigations/{world['private']}/data-request"
    got = client.get(url, headers=tokens[A])
    assert got.status_code == 200 and got.json()["stated"] is True and got.json()["request"]["subjects"] == ["water"]
    put = client.put(url, headers=tokens[A], json={"subjects": ["rivers"], "licence": {}})
    assert put.status_code == 200 and store.get_data_request(world["private"])["subjects"] == ["rivers"]


@pytest.mark.usefixtures("mock_egeria_client_connections")
@pytest.mark.parametrize("which", ["shared", "ownerless"])
def test_a_shared_or_ownerless_investigation_is_visible_to_both(api, world, which):
    client, tokens = api
    for user in (A, B):
        r = client.get(f"/api/open-data/investigations/{world[which]}/data-request", headers=tokens[user])
        assert r.status_code == 200 and r.json()["stated"] is True, (user, r.text)


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_an_anonymous_caller_gets_401_never_the_shared_view(api, world):
    client, _ = api
    for method, kw in (("get", {}), ("put", {"json": {"subjects": ["x"]}})):
        r = getattr(client, method)(f"/api/open-data/investigations/{world['private']}/data-request", **kw)
        assert r.status_code == 401, (method, r.status_code)


def test_the_guard_itself_refuses_a_request_with_no_signed_in_caller_before_looking_anything_up(reg):
    from fastapi import HTTPException

    from resource_explorer.web.visibility import visible_investigation_or_404

    looked = []

    class Spy:
        def get_investigation(self, *a, **k):
            looked.append((a, k))
            return {"slug": "x"}

    with pytest.raises(HTTPException) as err:
        visible_investigation_or_404(object(), "anything", registry=Spy())
    assert err.value.status_code == 401 and looked == []          # never asked the registry, never fell back to ""


# ── lens-terms and fit ───────────────────────────────────────────────────────

@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_lens_terms_and_fit_refuse_b_for_a_private_investigation_and_serve_a(api, world):
    client, tokens = api
    for user, want in ((B, 404), (A, 200)):
        lens = client.post(f"/api/open-data/lens-terms?investigation={world['private']}", headers=tokens[user], json={})
        fit = client.post(f"/api/open-data/work-lists/{world['plain']}/fit", headers=tokens[user],
                          json={"request": {}, "investigation": world["private"]})
        assert (lens.status_code, fit.status_code) == (want, want), (user, lens.text[:200], fit.text[:200])
        if want == 404:
            assert "water" not in lens.text + fit.text


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_lens_terms_without_an_investigation_is_unchanged(api, world):
    client, tokens = api
    r = client.post("/api/open-data/lens-terms", headers=tokens[B], json={"subjects": ["rivers"]})
    assert r.status_code == 200 and r.json()["source"] == "lens"


# ── work lists ───────────────────────────────────────────────────────────────

def test_list_all_as_b_omits_a_private_investigations_list_but_keeps_the_rest(reg, world):
    wls = world["wls"]
    with _as(B):
        slugs = {w["slug"] for w in wls.list_all()}
        only_private = wls.list_all(investigation=world["private"])
    assert world["lists"]["private"] not in slugs
    assert {world["lists"]["shared"], world["lists"]["ownerless"], world["plain"]} <= slugs         # shared, ownerless, no investigation
    assert only_private == []
    with _as(A):
        assert world["lists"]["private"] in {w["slug"] for w in wls.list_all()}


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_work_list_api_hides_a_private_list_from_b_with_the_words_of_a_missing_one(api, world):
    client, tokens = api
    private = world["lists"]["private"]
    listed = client.get("/api/work-lists/", headers=tokens[B]).json()
    assert private not in {w["slug"] for w in listed}
    assert private in {w["slug"] for w in client.get("/api/work-lists/", headers=tokens[A]).json()}
    for method, path, kw in (
        ("get", f"/api/work-lists/{private}", {}),
        ("get", f"/api/work-lists/{private}/export.csv", {}),
        ("put", f"/api/work-lists/{private}/hidden", {"json": {"hidden": True}}),
        ("delete", f"/api/work-lists/{private}", {}),
        ("post", f"/api/work-lists/{private}/members", {"json": {"entity_type": "repo", "entity_slug": "x", "member": True}}),
        ("post", f"/api/work-lists/{private}/runs/per-kind", {"json": {"analyses": {}}}),
        ("get", f"/api/work-lists/{private}/set-steps", {}),
    ):
        hidden = getattr(client, method)(path, headers=tokens[B], **kw)
        assert hidden.status_code == 404, (method, path, hidden.status_code, hidden.text[:160])
        missing = getattr(client, method)(path.replace(private, "no-such-list"), headers=tokens[B], **kw)
        assert hidden.json()["detail"].replace(private, "X") == missing.json()["detail"].replace("no-such-list", "X"), (method, path)
    ok = client.get(f"/api/work-lists/{private}", headers=tokens[A])
    assert ok.status_code == 200 and ok.json()["slug"] == private


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_cannot_batch_run_over_a_private_list_or_make_a_list_linked_to_a_private_investigation(api, world):
    client, tokens = api
    batch = client.post("/api/work-lists/runs/batch", headers=tokens[B],
                        json={"analysis_id": "repository_health", "work_list_slug": world["lists"]["private"]})
    assert batch.status_code == 404
    made = client.post("/api/work-lists/", headers=tokens[B], json={"display_name": "sneaky", "entity_slugs": [],
                                                                   "investigation": world["private"]})
    assert made.status_code == 404


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_list_with_no_investigation_stays_visible_to_everyone(api, world):
    client, tokens = api
    for user in (A, B):
        assert client.get(f"/api/work-lists/{world['plain']}", headers=tokens[user]).status_code == 200
        assert client.get(f"/api/work-lists/{world['lists']['shared']}", headers=tokens[user]).status_code == 200


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_open_data_work_list_routes_refuse_b_for_a_list_with_datasets_linked_to_a_private_investigation(api, world):
    client, tokens = api
    private = world["private_data"]
    for suffix, method, kw in (("scout-price", "get", {}), ("scout", "post", {}), ("profile", "get", {}), ("profile", "post", {}),
                               ("refresh-price", "get", {}), ("refresh", "post", {}), ("fit", "post", {"json": {"request": {}}})):
        path = f"/api/open-data/work-lists/{private}/{suffix}"
        hidden = getattr(client, method)(path, headers=tokens[B], **kw)
        missing = getattr(client, method)(path.replace(private, "no-such-list"), headers=tokens[B], **kw)
        assert hidden.status_code == missing.status_code == 404, (suffix, method, hidden.status_code, missing.status_code)
        assert hidden.json()["detail"].replace(private, "X") == missing.json()["detail"].replace("no-such-list", "X")   # the same words
    for suffix, method, kw in (("scout-price", "get", {}), ("fit", "post", {"json": {"request": {}}})):
        assert getattr(client, method)(f"/api/open-data/work-lists/{private}/{suffix}", headers=tokens[A], **kw).status_code == 200


# ── (1) imports and list creation write only into what the caller can see ────

def _writes(reg, store, investigation, *lists):
    """Everything an import or a list creation could touch: dataset rows, the investigation's members, the lists' members, the lists."""
    with reg._conn() as conn:
        lists_n = conn.execute("SELECT COUNT(*) FROM work_lists").fetchone()[0]
    return {"datasets": sorted(d["slug"] for d in store.list_datasets()),
            "inv_members": sorted((m["entity_type"], m["entity_slug"]) for m in reg.list_investigation_members(investigation)),
            "list_members": {l: sorted(m["entity_slug"] for m in (world_wls(reg).get(l) or {}).get("members", [])) for l in lists},
            "lists": lists_n}


def world_wls(reg):
    from resource_explorer.work_lists import WorkLists

    return WorkLists(reg)


@pytest.fixture
def importing(api, world, monkeypatch):
    """The demo portal's slug, with the routes' portal transport and Egeria lookup replaced by the recorded fixtures."""
    from resource_explorer.web.routes import open_data as routes

    monkeypatch.setattr(routes, "_transport", lambda: FakeTransport())
    monkeypatch.setattr(routes, "_egeria_lookup", lambda: NO_EGERIA)
    return world["portal"]


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_importing_into_a_private_investigation_gets_the_missing_target_404_and_writes_nothing(api, reg, store, world, importing):
    client, tokens = api
    url = f"/api/open-data/portals/{importing}/import"
    body = lambda inv: {"rows": [{"portal_slug": importing, "dataset_id": "child-gg"}], "work_list_name": "b-import", "investigation": inv}   # noqa: E731
    before = _writes(reg, store, world["private"])
    hidden = client.post(url, headers=tokens[B], json=body(world["private"]))
    missing = client.post(url, headers=tokens[B], json=body("no-such-investigation"))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"].replace(world["private"], "X") == missing.json()["detail"].replace("no-such-investigation", "X")
    assert _writes(reg, store, world["private"]) == before                       # no dataset row, no member, no list
    ok = client.post(url, headers=tokens[A], json=body(world["private"]))
    assert ok.status_code == 200, ok.text
    after = _writes(reg, store, world["private"])
    assert "ckan_demo_child-gg" in after["datasets"] or len(after["datasets"]) > len(before["datasets"])
    assert len(after["inv_members"]) > len(before["inv_members"])               # A's import landed in A's investigation


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_importing_into_a_private_work_list_gets_the_missing_target_404_and_writes_nothing(api, reg, store, world, importing):
    client, tokens = api
    url = f"/api/open-data/portals/{importing}/import"
    private_list = world["private_data"]
    body = {"rows": [{"portal_slug": importing, "dataset_id": "child-gg"}], "work_list_slug": private_list}
    before = _writes(reg, store, world["private"], private_list)
    assert client.post(url, headers=tokens[B], json=body).status_code == 404
    assert _writes(reg, store, world["private"], private_list) == before
    ok = client.post(url, headers=tokens[A], json=body)
    assert ok.status_code == 200, ok.text
    assert len(_writes(reg, store, world["private"], private_list)["list_members"][private_list]) > len(before["list_members"][private_list])


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_importing_from_a_repository_into_a_private_investigation_is_refused_before_anything_is_read(api, reg, store, world):
    client, tokens = api
    before = _writes(reg, store, world["private"])
    r = client.post("/api/open-data/import-from-repository", headers=tokens[B],
                    json={"repo_slug": "any", "investigation": world["private"]})
    assert r.status_code == 404 and _writes(reg, store, world["private"]) == before


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_cannot_create_a_list_linked_to_a_private_investigation_and_a_can(api, reg, store, world):
    client, tokens = api
    before = _writes(reg, store, world["private"])
    refused = client.post("/api/work-lists/", headers=tokens[B], json={"display_name": "sneaky", "entity_slugs": [],
                                                                      "investigation": world["private"]})
    assert refused.status_code == 404 and _writes(reg, store, world["private"]) == before        # nothing was created
    made = client.post("/api/work-lists/", headers=tokens[A], json={"display_name": "mine", "entity_slugs": [],
                                                                   "investigation": world["private"]})
    assert made.status_code == 200 and made.json()["investigation"] == world["private"]


# ── (2) a batch set is a capability, not a permission ────────────────────────

def _a_set(reg, *, requested_by, work_list_slug, set_id):
    """One batch set as `enqueue_batch` records it: a runs row (with its requester) and a work_list_runs row naming the list."""
    run_id = reg.enqueue_run("analysis_run", {"slug": "x", "analysis_id": "repository_health"}, requested_by=requested_by)
    with reg._conn() as conn:
        conn.execute("INSERT INTO work_list_runs (set_id, run_id, work_list_slug, entity_slug, analysis_id, enqueued_at)"
                     " VALUES (?, ?, ?, ?, ?, ?)", (set_id, run_id, work_list_slug, "x", "repository_health", "2026-11-23T00:00:00+00:00"))
    return set_id


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_b_holding_a_set_id_for_a_private_list_gets_404_in_the_words_of_an_unknown_set_and_a_gets_200(api, reg, world):
    client, tokens = api
    set_id = _a_set(reg, requested_by=A, work_list_slug=world["lists"]["private"], set_id="set-privatelist01")
    hidden = client.get(f"/api/work-lists/runs/sets/{set_id}", headers=tokens[B])
    unknown = client.get("/api/work-lists/runs/sets/set-doesnotexist", headers=tokens[B])
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json()["detail"].replace(set_id, "X") == unknown.json()["detail"].replace("set-doesnotexist", "X")
    assert "repository_health" not in hidden.text                                       # nothing of the set's runs leaks
    assert client.get(f"/api/work-lists/runs/sets/{set_id}", headers=tokens[A]).status_code == 200
    assert client.get(f"/api/work-lists/runs/sets/{set_id}").status_code == 401           # nobody signed in


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_set_on_a_list_with_no_investigation_stays_readable_by_everyone(api, reg, world):
    client, tokens = api
    set_id = _a_set(reg, requested_by=A, work_list_slug=world["plain"], set_id="set-plainlist0001")
    assert [client.get(f"/api/work-lists/runs/sets/{set_id}", headers=tokens[u]).status_code for u in (A, B)] == [200, 200]


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_set_with_no_work_list_is_readable_only_by_whoever_requested_it(api, reg, world):
    client, tokens = api
    mine = _a_set(reg, requested_by=A, work_list_slug="", set_id="set-nolist-mine01")
    assert [client.get(f"/api/work-lists/runs/sets/{mine}", headers=tokens[u]).status_code for u in (A, B)] == [200, 404]
    script = _a_set(reg, requested_by="", work_list_slug="", set_id="set-nolist-script")     # queued by a daemon or script
    assert [client.get(f"/api/work-lists/runs/sets/{script}", headers=tokens[u]).status_code for u in (A, B)] == [404, 404]
