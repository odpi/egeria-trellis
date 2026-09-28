"""Investigations — the framing step ahead of Scouting.

`docs/investigation-framing-design.md` §1. These pin the two decisions that make
promotion to Egeria a fill-in rather than a migration, and the one validation
that stops a purpose silently ranking nothing.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app

# Several tests here inject a stub project_manager/collection_manager but
# reach helper code (e.g. _apply_investigation_marker) that constructs its
# OWN pyegeria client directly — an unmocked construction that spends ~30s
# per call on a real check_connection() handshake when Egeria is unreachable.
# None of these tests assert on that marker step's own success; they assert
# on registry state and the stub's captured calls. See
# conftest.py::mock_egeria_client_connections's own docstring.
pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")


@pytest.fixture(autouse=True)
def _isolated_registry(pg_test_schema, monkeypatch):
    """Point the investigation routes at the throwaway schema, never the shared
    `resource_explorer` one.

    These tests drive the real FastAPI app, so they were CREATING REAL
    INVESTIGATIONS in the registry every other session reads — and relying on a
    teardown to remove them again. A teardown that does not run (an interrupt, a
    crash, a `-x` exit mid-module) leaks rows into a store five sessions share.
    Reading the shared registry can only give you a false red; writing it can
    give somebody else false data, which is the worse direction.

    `_registry()` is a single module-level factory behind all 18 routes, which
    is the whole reason this is a four-line fix rather than a refactor.

    Consequence worth stating: these now require a reachable Postgres and skip
    without one, the same trade the rest of the integration tier already makes.
    """
    from resource_explorer.config import get_config

    cfg = get_config()
    url = (f"postgresql://{cfg.pgvector.db_user}:{cfg.pgvector.password}"
           f"@{cfg.pgvector.host}:{cfg.pgvector.port}/{cfg.pgvector.dbname}"
           f"?options=-csearch_path%3D{pg_test_schema}")
    # Patched on the CONFIG, not on the routes' `_registry()` factory. The
    # factory covers the 18 routes; this file also builds `ProjectRegistry()`
    # directly 27 times inside test bodies to assert against what the routes
    # wrote. Patching only the factory sent writes and reads to different
    # schemas — 24 tests failed instantly, which is a better outcome than the
    # half-isolated version passing.
    #
    # Every `ProjectRegistry()` resolves its URL through this one attribute, so
    # one seam covers the app, the tests, and the teardown alike. monkeypatch
    # restores it.
    monkeypatch.setattr(cfg.registry, "database_url", url)
    return url


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture
def made(client, _isolated_registry):
    created = []

    def _make(**kw):
        body = {"display_name": "Test Investigation", **kw}
        r = client.post("/api/investigations/", json=body)
        assert r.status_code == 200, r.text
        created.append(r.json()["slug"])
        return r.json()

    yield _make
    from resource_explorer.registry import ProjectRegistry
    # The isolated schema, not the shared registry — otherwise this deletes
    # from the wrong place (harmlessly, since nothing matches, but the belt
    # would not be attached to the braces).
    reg = ProjectRegistry()          # resolves to the isolated schema
    with reg._conn() as conn:
        for slug in created:
            # EVERY collection this investigation owns, not just its folio. An
            # investigation now has a Folio plus one WorkingSet per disposition
            # in use, so a cleanup that hand-lists one leaks the rest into the
            # next test — which is how these passed alone and failed in suite.
            rows = conn.execute(
                "SELECT working_set_slug FROM investigation_resource_lists WHERE investigation_slug = ?",
                (slug,),
            ).fetchall()
            for r in rows:
                ws = r["working_set_slug"]
                conn.execute("DELETE FROM working_set_members WHERE working_set_slug = ?", (ws,))
                conn.execute("DELETE FROM working_sets WHERE slug = ?", (ws,))
            conn.execute("DELETE FROM investigation_resource_lists WHERE investigation_slug = ?", (slug,))
            conn.execute("DELETE FROM investigations WHERE slug = ?", (slug,))


def test_purposes_are_served_not_hardcoded_in_the_spa(client):
    """The UI must not carry its own copy of this vocabulary.

    Purpose is ProjectCharter.purposes (§2), and the same eight values all 41
    catalog questions are tagged with. A second copy in the SPA is exactly the
    frontend/backend mirror that has drifted twice in this codebase already.
    """
    r = client.get("/api/investigations/purposes")
    assert r.status_code == 200
    assert r.json()["purposes"] == [
        "Assess", "Certify", "Deploy", "Explore", "Learn", "Maintain", "Select", "Share",
    ]


from contextlib import contextmanager


@contextmanager
def _as(user_id: str):
    """Run a block as `user_id`. `''` is the shared/service identity."""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    reset = current_caller.set(
        CallerIdentity(user_id=user_id, egeria_token=None, auth_source="app-jwt"))
    try:
        yield
    finally:
        current_caller.reset(reset)


def test_a_personal_investigation_is_hidden_from_everyone_else(made):
    """The point of Phase 3, and the assertion that has to be seen to FAIL.

    "Alice can see her own" passes under every broken variant of this — a
    filter that does nothing at all passes it. The load-bearing assertion is
    that a DIFFERENT user cannot see it.
    """
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()

    with _as("alice"):
        inv = reg.create_investigation("Alice's Notes",
                                       project_classification="PersonalProject")
        mine = [i["slug"] for i in reg.list_investigations()]
    with _as("bob"):
        theirs = [i["slug"] for i in reg.list_investigations()]

    try:
        assert inv["slug"] in mine
        assert inv["slug"] not in theirs, "bob can see alice's personal investigation"
    finally:
        with reg._conn() as conn:
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))


def test_a_private_investigation_is_not_found_rather_than_forbidden(made):
    """404, not 403 — deliberately the OPPOSITE of this codebase's usual rule
    that absence must be distinguishable from emptiness.

    A 403 confirms the investigation exists and leaks its name to anyone who
    guesses a slug, and slugs are derived from display names. For a caller who
    may not see it, "does not exist" is the honest answer.
    """
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Alice's Experiment",
                                       project_classification="Experiment",
                                       hypothesis="this is mine alone")
    try:
        with _as("bob"):
            assert reg.get_investigation(inv["slug"]) is None
        with _as("alice"):
            assert reg.get_investigation(inv["slug"]) is not None
    finally:
        with reg._conn() as conn:
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))


def test_shared_classifications_stay_visible_to_everyone(made):
    """The other half. Task / Campaign / Study are not private, and a filter
    that hid them would be as wrong as one that hid nothing."""
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()
    made_slugs = []
    try:
        for cls in ("Task", "Campaign", "StudyProject"):
            with _as("alice"):
                inv = reg.create_investigation(f"Alice {cls}", project_classification=cls)
            made_slugs.append(inv["slug"])
        with _as("bob"):
            seen = {i["slug"] for i in reg.list_investigations()}
        assert set(made_slugs) <= seen
    finally:
        with reg._conn() as conn:
            for slug in made_slugs:
                conn.execute("DELETE FROM investigations WHERE slug = ?", (slug,))


def test_a_private_investigation_does_not_leak_through_a_resources_own_page(made):
    """The back-reference leak, which the object-level filter alone misses.

    `find_entity_investigations` is entity-centric — "which investigations is
    this repo in" — and runs on a page anyone can open. A shared repo inside
    alice's personal investigation would otherwise announce it by name.
    """
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Alice Private Sweep",
                                       project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "a-shared-repo")
    try:
        with _as("bob"):
            found = reg.find_entity_investigations("repo", "a-shared-repo")
            assert not any(f["investigation_slug"] == inv["slug"] for f in found), (
                "a private investigation leaked through the repo's own page")
        with _as("alice"):
            found = reg.find_entity_investigations("repo", "a-shared-repo")
            assert any(f["investigation_slug"] == inv["slug"] for f in found)
    finally:
        with reg._conn() as conn:
            rows = conn.execute(
                "SELECT working_set_slug FROM investigation_resource_lists "
                "WHERE investigation_slug = ?", (inv["slug"],)).fetchall()
            for r in rows:
                conn.execute("DELETE FROM working_set_members WHERE working_set_slug = ?",
                             (r["working_set_slug"],))
                conn.execute("DELETE FROM working_sets WHERE slug = ?", (r["working_set_slug"],))
            conn.execute("DELETE FROM investigation_resource_lists WHERE investigation_slug = ?",
                         (inv["slug"],))
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))


def test_a_private_investigations_egeria_binding_is_not_inherited_by_others(made):
    """The subtler back-reference. `inherited_egeria_project_context` returns
    `_inherited_from_name` — the investigation's display name — and the
    Project's qualifiedName, onto a resource anyone can look at. It would also
    let one user publish a shared repo into another user's private Project."""
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Alice Bound Private",
                                       project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "another-shared-repo")
        reg.set_investigation_egeria_project(inv["slug"], {
            "status": "linked", "egeria_project_guid": "guid-private",
            "egeria_project_qualified_name": "Project::Alice::Private"})
    try:
        with _as("bob"):
            assert reg.inherited_egeria_project_context("repo", "another-shared-repo") is None
        with _as("alice"):
            ctx = reg.inherited_egeria_project_context("repo", "another-shared-repo")
            assert ctx and ctx["egeria_project_guid"] == "guid-private"
    finally:
        with reg._conn() as conn:
            rows = conn.execute(
                "SELECT working_set_slug FROM investigation_resource_lists "
                "WHERE investigation_slug = ?", (inv["slug"],)).fetchall()
            for r in rows:
                conn.execute("DELETE FROM working_set_members WHERE working_set_slug = ?",
                             (r["working_set_slug"],))
                conn.execute("DELETE FROM working_sets WHERE slug = ?", (r["working_set_slug"],))
            conn.execute("DELETE FROM investigation_resource_lists WHERE investigation_slug = ?",
                         (inv["slug"],))
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))


def test_the_service_identity_still_sees_private_investigations(made):
    """The worker legitimately acts on a user's behalf without carrying their
    token (see `egeria_identity`'s module docstring on why a queued run
    publishes as the service account). If the shared identity were filtered,
    every queued promotion of a personal investigation would fail with the row
    apparently missing."""
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Alice Queued",
                                       project_classification="PersonalProject")
    try:
        with _as(""):
            assert reg.get_investigation(inv["slug"]) is not None
    finally:
        with reg._conn() as conn:
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))


def test_an_ownerless_private_row_stays_visible_and_says_so(made):
    """Rows written before `created_by` existed have no owner, so nothing can
    scope them to anyone. They stay visible — which is what they have always
    been — rather than vanishing from the person who made them. The point is
    that this is SAID, not silently tolerated: a privately-classified row that
    everyone can see is a contradiction somebody should be able to act on."""
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Legacy Personal",
                                       project_classification="PersonalProject")
    try:
        with reg._conn() as conn:
            conn.execute("UPDATE investigations SET created_by = '' WHERE slug = ?",
                         (inv["slug"],))
        with _as("bob"):
            seen = reg.get_investigation(inv["slug"])
        assert seen is not None, "an ownerless row must not vanish"
        assert seen["visibility"] == "shared"
        assert "visible to everyone" in seen.get("visibility_note", "")
    finally:
        with reg._conn() as conn:
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))


def test_visibility_is_reported_not_left_to_be_re_derived(made):
    """The UI must not recompute `classification in PRIVATE and created_by ==
    me` for itself — that is a second copy of the rule, in the place least able
    to be tested."""
    from resource_explorer.registry import ProjectRegistry
    reg = ProjectRegistry()
    with _as("alice"):
        priv = reg.create_investigation("Alice Priv2",
                                        project_classification="PersonalProject")
        shared = reg.create_investigation("Alice Shared2", project_classification="Task")
        got = {i["slug"]: i for i in reg.list_investigations()}
    try:
        assert got[priv["slug"]]["visibility"] == "private"
        assert got[priv["slug"]]["is_mine"] is True
        assert got[shared["slug"]]["visibility"] == "shared"
    finally:
        with reg._conn() as conn:
            for i in (priv, shared):
                conn.execute("DELETE FROM investigations WHERE slug = ?", (i["slug"],))


def test_classifications_are_served_not_hardcoded_in_the_spa(client):
    """Same rule as `/purposes`, for the same reason.

    These are Egeria's own `ProjectKind` subtypes and go to Egeria as type
    names. A copy in the SPA is the frontend/backend mirror that has drifted
    twice in this codebase — and here drift means offering a classification
    Egeria will reject, or hiding one it accepts.
    """
    from resource_explorer.registry import ProjectRegistry

    r = client.get("/api/investigations/classifications")
    assert r.status_code == 200
    body = r.json()
    assert [c["name"] for c in body["classifications"]] == list(
        ProjectRegistry.PROJECT_CLASSIFICATIONS)
    assert "Experiment" in [c["name"] for c in body["classifications"]]
    # Every entry carries the prose the dropdown renders, so the SPA never has
    # to invent a label for a value it does not recognise.
    assert all(c["label"] and c["description"] for c in body["classifications"])


def test_the_hypothesis_requirement_is_served_not_inferred(client):
    """The SPA shows the hypothesis field off `requires_hypothesis`, not off a
    second copy of the rule that says "Experiment". If a sixth classification
    ever needs one, the flag carries it and the frontend needs no change."""
    r = client.get("/api/investigations/classifications")
    needs = {c["name"]: c["requires_hypothesis"] for c in r.json()["classifications"]}
    assert needs["Experiment"] is True
    assert not any(v for k, v in needs.items() if k != "Experiment")


def test_ad_hoc_is_a_binding_not_a_sixth_classification(client):
    """The structural decision this phase turns on.

    "Ad-hoc" is the ABSENCE of an Egeria Project, not a kind of one. Egeria has
    no `adHoc` classification, so a value like that in the classification column
    would be sent as a type name and rejected — or worse, dropped. It belongs on
    its own axis, which is also where the nullable `egeria_project_guid` already
    lived.
    """
    from resource_explorer.registry import ProjectRegistry

    body = client.get("/api/investigations/classifications").json()
    names = [c["name"] for c in body["classifications"]]
    assert not any(n.lower().replace("-", "") == "adhoc" for n in names), (
        "ad-hoc must not appear as a classification — it is not an Egeria type")
    assert [b["name"] for b in body["bindings"]] == [
        ProjectRegistry.BINDING_EGERIA, ProjectRegistry.BINDING_LOCAL]


def test_an_experiment_without_a_hypothesis_is_refused(client):
    """Egeria's Experiment is "a project testing a hypothesis (documented in
    the hypothesis attribute)". Creating one with an empty hypothesis publishes
    a classification whose entire point is missing — the same absence-reads-as-
    presence failure, in a catalog description rather than a metric."""
    r = client.post("/api/investigations/", json={
        "display_name": "Hypothesis-free", "project_classification": "Experiment"})
    assert r.status_code == 400
    assert "hypothesis" in r.json()["detail"].lower()


def test_a_hypothesis_on_a_non_experiment_is_refused_rather_than_dropped(client):
    """The other direction, and the less obvious one.

    `_initial_classifications` only sends `hypothesis` for Experiment, so a
    hypothesis on a Task would be stored locally and silently vanish at publish
    time. Refusing is better than accepting a value we know we will discard.
    """
    r = client.post("/api/investigations/", json={
        "display_name": "Task with a theory", "project_classification": "Task",
        "hypothesis": "this will be dropped"})
    assert r.status_code == 400
    assert "only meaningful for" in r.json()["detail"]


def test_an_experiment_carries_its_hypothesis_all_the_way_into_egeria(made):
    """Collected, stored, and actually SENT — the Phase 1 defect in miniature.

    A required field that reaches the database and not the create body is the
    same bug the classification itself had.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Does caching help", project_classification="Experiment",
               hypothesis="Warm source cache cuts survey wall-clock by >50%")
    assert inv["hypothesis"] == "Warm source cache cuts survey wall-clock by >50%"

    pm = _StubPM()
    EgeriaInvestigationPublisher(
        ProjectRegistry(), project_manager=pm, collection_manager=_StubCM()
    ).promote(inv["slug"])
    sent = pm.calls[0][3]["initialClassifications"]["Experiment"]
    assert sent["class"] == "ExperimentProperties"
    assert sent["hypothesis"] == "Warm source cache cuts survey wall-clock by >50%"


def test_the_publishers_hypothesis_list_matches_the_registrys(made):
    """Two copies of one rule, in modules that cannot import each other's
    intent. Pinned rather than trusted: if the registry ever requires a
    hypothesis for a second classification and the publisher is not taught, the
    value would be collected, validated, stored — and dropped at the body."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import egeria_investigation_publisher as pub

    assert tuple(pub._HYPOTHESIS_CLASSIFICATIONS) == tuple(
        ProjectRegistry.HYPOTHESIS_REQUIRED_FOR)


def test_an_ad_hoc_investigation_is_not_promoted_by_accident(made):
    """`local` is a decision, not a not-yet. Promoting one anyway would
    overturn it silently and leave the row bound while still flagged local."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Just Looking", egeria_binding="local")
    assert inv["egeria_binding"] == "local"

    pm = _StubPM()
    res = EgeriaInvestigationPublisher(
        ProjectRegistry(), project_manager=pm, collection_manager=_StubCM()
    ).promote(inv["slug"])

    assert pm.calls == [], "nothing may be written to Egeria for an ad-hoc investigation"
    assert not res.ok
    assert any("ad-hoc" in e for e in res.errors)


def test_binding_an_egeria_project_moves_the_investigation_off_ad_hoc(made):
    """Asking for a Project IS the decision that this is no longer ad-hoc.

    Without this, a promoted investigation would hold a GUID and still claim to
    want none — a contradiction nothing downstream could resolve.
    """
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Changed My Mind", egeria_binding="local")
    reg = ProjectRegistry()
    after = reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "g-1",
        "egeria_project_qualified_name": "Project::X"})
    assert after["egeria_binding"] == "egeria"


def test_unbinding_does_not_silently_make_an_investigation_ad_hoc(made):
    """The reverse is NOT symmetric, on purpose. Losing or clearing a binding
    is not the same as deciding to stay local, and only the second is a choice
    somebody made — recording it as one would invent an intent."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Unbind Me")
    reg = ProjectRegistry()
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "g-2"})
    after = reg.set_investigation_egeria_project(inv["slug"], {
        "status": "unset", "egeria_project_guid": ""})
    assert after["egeria_binding"] == "egeria", (
        "unbinding must not be read as choosing ad-hoc")


def test_existing_investigations_backfill_to_egeria_not_ad_hoc(made):
    """A judgement, stated where it is made.

    Before this column existed, ad-hoc was not a choice anyone could make —
    every investigation was headed for Egeria whether or not it had arrived.
    Defaulting old rows to `local` would invent a deliberate decision nobody
    took and hide them from anything keying on the binding.
    """
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry()
    inv = made(display_name="Legacy Row")
    with reg._conn() as conn:
        # Simulate a row written before the column existed.
        conn.execute("UPDATE investigations SET egeria_binding = NULL WHERE slug = ?",
                     (inv["slug"],))
        row = conn.execute(
            "SELECT egeria_binding FROM investigations WHERE slug = ?",
            (inv["slug"],)).fetchone()
    assert row["egeria_binding"] is None
    # The publisher must read a NULL binding as 'egeria', not refuse it as local.
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )
    pm = _StubPM()
    res = EgeriaInvestigationPublisher(
        reg, project_manager=pm, collection_manager=_StubCM()
    ).promote(inv["slug"])
    assert res.project_guid, "a pre-existing row must still be promotable"


def test_an_investigation_starts_local_with_no_egeria_write(made):
    """§1's third starting mode, and the structural decision behind it.

    One local row in all three modes, with a nullable egeria_project_guid, so
    promotion later is a fill-in rather than a migration.
    """
    inv = made(display_name="Local Only", purposes=["Explore"])
    assert inv["egeria_project_guid"] == ""
    assert inv["status"] == "open"
    assert inv["purposes"] == ["Explore"]


def test_an_unknown_purpose_is_rejected_rather_than_stored(client):
    """A purpose outside the vocabulary would rank nothing and look like an
    empty result — indistinguishable from 'nothing matched'."""
    r = client.post("/api/investigations/",
                    json={"display_name": "Bad", "purposes": ["Nonsense"]})
    assert r.status_code == 400
    assert "Nonsense" in r.json()["detail"]


def test_membership_goes_through_a_working_set(client, made):
    """Storage mirrors Egeria's real two-hop shape:

        Project --ResourceList--> WorkingSet --CollectionMembership--> resource

    A flat Project->resource link (which an earlier pass here used, and which
    design §6 itself sketched) has nowhere to put a per-resource rationale and
    makes the working set unnameable. The API stays flat because every caller
    only wants "what is in scope"; the two hops are a storage-fidelity decision
    so promotion to Egeria is a replay rather than a migration.
    """
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Two Hop")
    reg = ProjectRegistry()

    # A brand-new investigation has NO working set — that is what makes the
    # empty sidebar an honest prompt rather than an empty shell.
    assert reg.investigation_working_set_slug(inv["slug"]) == ""

    r = client.post(f"/api/investigations/{inv['slug']}/members", json={
        "entity_type": "repo", "entity_slug": "milvus",
        "membership_rationale": "already adopted; tracking health",
    })
    assert r.status_code == 200
    assert r.json()[0]["membership_rationale"] == "already adopted; tracking health"

    # ...and the working set was created lazily, on first use.
    ws_slug = reg.investigation_working_set_slug(inv["slug"])
    assert ws_slug
    assert reg.get_working_set(ws_slug)["members"][0]["entity_slug"] == "milvus"

    assert client.get(f"/api/investigations/{inv['slug']}").json()["member_count"] == 1

    d = client.delete(f"/api/investigations/{inv['slug']}/members/repo/milvus")
    assert d.status_code == 200 and d.json() == []


def test_members_on_a_missing_investigation_404s(client):
    assert client.get("/api/investigations/nope/members").status_code == 404


class _StubPM:
    """Egeria's Project side.

    `get_project_by_guid` echoes back whatever `create_project` was asked for,
    so the default stub models an Egeria that HONOURS the request. The
    interesting cases are the stubs below that model one that doesn't (
    `_DroppingPM`) and one that cannot be asked (`_UnreadablePM`) — the
    publisher has to tell those two apart, because "we didn't check" and "we
    checked and it's missing" are different facts.
    """

    def __init__(self, guid="proj-1", fail=False):
        self.guid, self.fail, self.calls = guid, fail, []
        self._applied: list[str] = []

    def create_egeria_bearer_token(self):
        pass

    def create_project(self, display_name=None, description=None, body=None, **kw):
        self.calls.append(("create_project", display_name, description, body))
        if self.fail:
            raise RuntimeError("Egeria unreachable")
        self._applied = list((body or {}).get("initialClassifications") or {})
        return self.guid

    def get_project_by_guid(self, guid, **kw):
        # The REAL payload shape, measured live 2026-09-07 (see
        # `_confirm_classification`): an OpenMetadataRootElement whose project
        # classifications sit under elementHeader.projectKinds, separate from
        # elementHeader.anchor, and whose `projectKinds` key is OMITTED rather
        # than null when there is no kind.
        #
        # The first version of this stub invented a top-level `classifications`
        # list. Both it and the code agreed with each other and neither agreed
        # with Egeria, so all five tests passed against a function that returned
        # "could not tell" on every real call. A stub is a claim about the other
        # system; this one is now a measured claim.
        header = {"guid": guid, "type": {"typeName": "Project"},
                  "anchor": {"classificationName": "Anchors"}}
        if self._applied:
            header["projectKinds"] = [
                {"class": "ElementClassification", "classificationName": c,
                 "type": {"typeName": c, "superTypeNames": ["ProjectKind"]}}
                for c in self._applied
            ]
        return {"class": "OpenMetadataRootElement", "elementHeader": header,
                "properties": {"displayName": "stub"}}


class _DroppingPM(_StubPM):
    """Creates the Project and silently discards the classification.

    This is the failure the whole change exists to catch, and it is exactly
    what the code did before: `create_project` returns a GUID, everything looks
    fine, and the classification is gone. Modelled the way Egeria really
    expresses it — the `projectKinds` key simply is not there.
    """

    def get_project_by_guid(self, guid, **kw):
        return {"class": "OpenMetadataRootElement",
                "elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                  "anchor": {"classificationName": "Anchors"}}}


class _UnreadablePM(_StubPM):
    """The read-back itself fails. Not evidence about the classification."""

    def get_project_by_guid(self, guid, **kw):
        raise RuntimeError("view server unreachable")


class _StrangePayloadPM(_StubPM):
    """Returns something that is not the shape we know how to read.

    Distinct from `_DroppingPM` on purpose. An absent `projectKinds` key means
    "no classification" ONLY when the rest of the header is recognisable —
    measured, because Egeria omits that key rather than nulling it. If the
    payload shape ever changes, the honest answer is "could not tell", not an
    accusation that Egeria dropped the classification. Without this test the
    two are indistinguishable, which is how the first version of the read-back
    silently reported nothing at all.
    """

    def get_project_by_guid(self, guid, **kw):
        return {"someNewEnvelope": {"project": {"guid": guid}}}


class _StubCM:
    def __init__(self):
        self.attached, self.members = [], []

    def create_egeria_bearer_token(self):
        pass

    def create_collection(self, display_name=None, description=None, **kw):
        return "coll-1"

    def attach_collection(self, parent_guid, collection_guid, body=None):
        self.attached.append((parent_guid, collection_guid))

    def add_to_collection(self, collection_guid, element_guid, body=None):
        self.members.append((collection_guid, element_guid))


def test_promotion_replays_the_local_shape_into_egeria(made):
    """Project -> Collection -> ResourceList -> CollectionMembership, in that
    order, from rows that already had that shape."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Promote Me", purposes=["Certify"])
    reg = ProjectRegistry()
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "never-published-repo")

    pm, cm = _StubPM(), _StubCM()
    res = EgeriaInvestigationPublisher(reg, project_manager=pm, collection_manager=cm).promote(inv["slug"])

    assert res.project_guid == "proj-1"
    assert res.collection_guid == "coll-1"
    assert res.resource_list_linked
    # Purposes must survive, and must land in additionalProperties rather than
    # being appended to free text: mission/purposes are ProjectCharter (0442)
    # properties that create_project cannot reach, and Project is a
    # Referenceable, so additionalProperties is the sanctioned carrier.
    props = pm.calls[0][3]["properties"]
    assert "Certify" in props["additionalProperties"]["purposes"]
    assert "Certify" not in (props.get("description") or "")


def test_the_chosen_classification_actually_reaches_egeria(made):
    """The bug this phase fixes.

    `project_classification` has been stored (`registry.py`), validated on
    create and shown in the UI since the feature shipped — and the promote body
    carried `properties` and nothing else, so every investigation RE ever
    promoted arrived in Egeria as an unclassified `Project`.

    Asserting on the body is the point: a test that only checked
    `create_project` was called would have passed for the whole period this was
    broken.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Classify Me", project_classification="Campaign")
    pm, cm = _StubPM(), _StubCM()
    res = EgeriaInvestigationPublisher(
        ProjectRegistry(), project_manager=pm, collection_manager=cm
    ).promote(inv["slug"])

    body = pm.calls[0][3]
    assert body["initialClassifications"] == {"Campaign": {"class": "CampaignProperties"}}
    assert res.classification_requested == "Campaign"
    assert res.classification_confirmed == "Campaign"
    assert res.ok


def test_every_classification_in_the_vocabulary_maps_to_a_properties_class(made):
    """The `<Name>Properties` convention holds for all of them, so the mapping
    is derived rather than table-driven. If Egeria ever breaks the convention
    for a new classification, this is where it shows up — and RE's vocabulary
    is checked against the publisher's, so adding one to the registry without
    teaching the publisher cannot pass silently."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher, _initial_classifications,
    )

    reg = ProjectRegistry()
    for name in reg.PROJECT_CLASSIFICATIONS:
        assert _initial_classifications(name) == {name: {"class": f"{name}Properties"}}

    inv = made(display_name="Personal One", project_classification="PersonalProject")
    pm = _StubPM()
    EgeriaInvestigationPublisher(
        reg, project_manager=pm, collection_manager=_StubCM()
    ).promote(inv["slug"])
    assert pm.calls[0][3]["initialClassifications"] == {
        "PersonalProject": {"class": "PersonalProjectProperties"}
    }


def test_a_classification_egeria_drops_is_reported_not_assumed(made):
    """A returned GUID proves the call worked, not that the body was honoured.

    This is the failure mode that hid the original bug: everything succeeds and
    the classification is simply not there. The Project is real and stays
    bound — the route binds on `project_guid`, not on `ok` — but the promotion
    is not clean and must not say it is.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Dropped", project_classification="Task")
    res = EgeriaInvestigationPublisher(
        ProjectRegistry(), project_manager=_DroppingPM(), collection_manager=_StubCM()
    ).promote(inv["slug"])

    assert res.project_guid == "proj-1", "the Project exists and must stay bindable"
    assert res.classification_confirmed == "", "checked, and genuinely absent"
    assert not res.ok
    assert any("does not carry" in e for e in res.errors)


def test_an_unverifiable_classification_is_not_reported_as_missing(made):
    """"We could not check" is a fact about us, not about Egeria.

    Treating a failed read-back as absence would raise a false alarm on every
    promotion whenever the view server is briefly unreachable; treating it as
    confirmation would be the original bug wearing a badge. It is neither, and
    `classification_confirmed is None` is how that is said.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Unverifiable", project_classification="StudyProject")
    res = EgeriaInvestigationPublisher(
        ProjectRegistry(), project_manager=_UnreadablePM(), collection_manager=_StubCM()
    ).promote(inv["slug"])

    assert res.classification_requested == "StudyProject"
    assert res.classification_confirmed is None
    assert res.ok, "an unverifiable read-back is not a failed promotion"
    assert not any("does not carry" in e for e in res.errors)


def test_an_unrecognised_payload_is_could_not_tell_not_a_dropped_classification(made):
    """The distinction that the first read-back could not make.

    Egeria OMITS `projectKinds` when a Project has no kind, so "the key is not
    there" is a real answer — but only when the rest of the header is the shape
    we measured. If the payload changes underneath us, reporting absence would
    accuse Egeria of a bug that is ours.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Strange Payload", project_classification="Campaign")
    res = EgeriaInvestigationPublisher(
        ProjectRegistry(), project_manager=_StrangePayloadPM(),
        collection_manager=_StubCM(),
    ).promote(inv["slug"])

    assert res.classification_confirmed is None, "unknown shape is not evidence"
    assert res.ok
    assert not any("does not carry" in e for e in res.errors)


def test_an_unknown_classification_refuses_before_writing_anything(made):
    """Promoting anyway would recreate the exact defect being fixed — a Project
    silently without its classification — and leave a real element in Egeria to
    clean up. `create_project` must not be reached at all.

    Written through the registry rather than the API because `create_investigation`
    rejects an unknown value; the row can only get into this state by predating
    the validation or being edited underneath it.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Bad Classification")
    reg = ProjectRegistry()
    with reg._conn() as conn:
        conn.execute(
            "UPDATE investigations SET project_classification = ? WHERE slug = ?",
            ("Sprint", inv["slug"]),
        )

    pm = _StubPM()
    res = EgeriaInvestigationPublisher(
        reg, project_manager=pm, collection_manager=_StubCM()
    ).promote(inv["slug"])

    assert pm.calls == [], "nothing may be written to Egeria on a bad classification"
    assert not res.project_guid
    assert not res.ok
    assert any("Sprint" in e and "not one of" in e for e in res.errors)


def test_a_member_with_no_egeria_asset_is_reported_not_invented(made):
    """The honest half. A repo never published to Egeria has no asset GUID; the
    promotion says so per member rather than silently dropping it or fabricating
    a link."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Partial Promote")
    reg = ProjectRegistry()
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "definitely-not-published-xyz")

    cm = _StubCM()
    res = EgeriaInvestigationPublisher(reg, project_manager=_StubPM(), collection_manager=cm).promote(inv["slug"])

    assert res.members_linked == []
    assert len(res.members_unlinkable) == 1
    assert "not published to Egeria yet" in res.members_unlinkable[0]["reason"]
    assert cm.members == []


def test_an_unreachable_egeria_fails_loudly_and_binds_nothing(made):
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="No Egeria")
    reg = ProjectRegistry()
    res = EgeriaInvestigationPublisher(
        reg, project_manager=_StubPM(fail=True), collection_manager=_StubCM()
    ).promote(inv["slug"])

    assert not res.ok
    assert res.project_guid == ""
    assert any("create_project failed" in e for e in res.errors)
    assert reg.get_investigation(inv["slug"])["egeria_project_guid"] == ""


def test_promoting_an_already_bound_investigation_is_refused(made):
    """Two Projects for one body of work is worse than none."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Already Bound")
    reg = ProjectRegistry()
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "existing-guid",
    })
    res = EgeriaInvestigationPublisher(
        reg, project_manager=_StubPM(), collection_manager=_StubCM()
    ).promote(inv["slug"])
    assert not res.ok
    assert any("already bound" in e for e in res.errors)


def test_promote_route_does_not_call_pyegeria_on_the_event_loop(client, made, monkeypatch):
    """Regression for a bug a CLI test structurally cannot catch.

    pyegeria's synchronous methods drive their own event loop internally. Called
    inline from an async FastAPI route they raise "this event loop is already
    running" — but exercised from a script, where no loop is running, they work
    perfectly. So the promoter passed every test and failed the moment a human
    clicked the button.

    This asserts the work happens off the loop: the stub raises if it finds a
    running loop, exactly as pyegeria effectively does.
    """
    import asyncio

    from resource_explorer.surveyors import egeria_investigation_publisher as mod

    inv = made(display_name="Loop Check")

    class _LoopSensitivePublisher:
        def __init__(self, registry, **kw):
            pass

        def promote(self, slug):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                from resource_explorer.surveyors.egeria_investigation_publisher import (
                    PromotionResult,
                )
                return PromotionResult(project_guid="off-loop-ok")
            raise RuntimeError("this event loop is already running")

    monkeypatch.setattr(mod, "EgeriaInvestigationPublisher", _LoopSensitivePublisher)
    r = client.post(f"/api/investigations/{inv['slug']}/promote")
    assert r.status_code == 200, r.text
    assert r.json()["project_guid"] == "off-loop-ok", (
        "promote ran on the event loop — pyegeria's sync wrappers cannot be "
        "called inline from an async route"
    )


def test_a_qualified_name_collision_is_explained_not_dumped(made):
    """Egeria signals "this qualifiedName is taken" as a 409 inside a large Java
    error payload. Raw, it tells a user nothing and fills the toast with a stack
    trace. It is also the single most likely real failure here — it happens
    whenever a promotion succeeded and the local binding was later cleared,
    which orphans the Project.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Collision")

    class _CollidingPM(_StubPM):
        def create_project(self, display_name=None, description=None, body=None, **kw):
            raise RuntimeError(
                "PyegeriaAPIException: OMAG-COMMON-409-001 ... qualifiedName is defined "
                "as a unique property and value Project::Investigation::collision is not "
                "available for use"
            )

    res = EgeriaInvestigationPublisher(
        ProjectRegistry(), project_manager=_CollidingPM(), collection_manager=_StubCM()
    ).promote(inv["slug"])

    assert not res.ok
    joined = " ".join(res.errors)
    assert "already" in joined and "Bind to the existing" in joined
    assert "OMAG-COMMON" not in joined, "raw Egeria payload leaked to the user"


def test_binding_an_existing_project_also_gets_a_working_set(made, monkeypatch):
    """Both routes to a Project need the same next thing.

    Promotion created a Project AND a Collection, so it worked by accident.
    Binding to an existing Project recorded the GUID and stopped, leaving the
    investigation with nowhere in Egeria for its membership to live — the gap
    only existed because one path happened to do both.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import egeria_investigation_publisher as mod

    inv = made(display_name="Bind And Set")
    reg = ProjectRegistry()
    cm = _StubCM()

    real_managers = mod.EgeriaInvestigationPublisher._managers
    monkeypatch.setattr(mod.EgeriaInvestigationPublisher, "_managers",
                        lambda self: (_StubPM(), cm))

    res = mod.EgeriaInvestigationPublisher(reg).ensure_working_set(inv["slug"])
    assert not res.ok  # unbound: refuses rather than inventing a Project
    assert any("not bound" in e for e in res.errors)

    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "existing-proj",
    })
    res = mod.EgeriaInvestigationPublisher(reg).ensure_working_set(inv["slug"])
    assert res.collection_guid == "coll-1"
    assert res.resource_list_linked
    assert cm.attached == [("existing-proj", "coll-1")]

    # Idempotent: a second call must not create a second Collection.
    res2 = mod.EgeriaInvestigationPublisher(reg).ensure_working_set(inv["slug"])
    assert res2.collection_guid == "coll-1"
    assert cm.attached == [("existing-proj", "coll-1")], "created a duplicate Collection"
    monkeypatch.setattr(mod.EgeriaInvestigationPublisher, "_managers", real_managers)


def test_a_failed_adoption_check_is_reported_because_that_is_when_duplicates_happen(made, monkeypatch):
    """Caught by the no-silent-success ratchet, on my own code.

    ensure_working_set asks Egeria what is already attached before creating
    anything. If that lookup fails it still proceeds — a missing working set
    should not be blocked by an unreadable list — but failing to look is
    precisely the moment a duplicate Collection gets made, so it cannot be
    swallowed into a debug log.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import egeria_investigation_publisher as mod

    inv = made(display_name="Blind Adoption")
    reg = ProjectRegistry()
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-x",
    })

    class _BlindCM(_StubCM):
        def get_attached_collections(self, parent_guid, **kw):
            raise RuntimeError("listing unavailable")

    monkeypatch.setattr(mod.EgeriaInvestigationPublisher, "_managers",
                        lambda self: (_StubPM(), _BlindCM()))
    res = mod.EgeriaInvestigationPublisher(reg).ensure_working_set(inv["slug"])

    assert res.collection_guid == "coll-1", "should still create the missing working set"
    assert any("may have created a second" in e for e in res.errors), (
        "a failed adoption check was not surfaced — that is when duplicates happen"
    )


def test_a_repo_inherits_its_project_binding_from_the_investigation(made):
    """Membership answers the publish gate's question.

    An investigation IS the context everything else runs inside, so when a repo
    sits in the working set of one bound to an Egeria Project, that binding
    already says which Project it belongs to. Asking again per resource asks a
    question that has been answered — which is what left 17 repos unpublishable
    after an Egeria reseed.
    """
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Inheriting")
    reg = ProjectRegistry()
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-inherit",
        "egeria_project_qualified_name": "Project::Inherit",
    })
    ws = reg.get_or_create_working_set(inv["slug"])

    # Not a member yet: nothing to inherit, so the gate must still prompt.
    assert reg.inherited_egeria_project_context("repo", "some-unrelated-repo") is None

    reg.add_working_set_member(ws["slug"], "repo", "some-unrelated-repo")
    got = reg.inherited_egeria_project_context("repo", "some-unrelated-repo")
    assert got and got["egeria_project_guid"] == "proj-inherit"
    assert got["_inherited_from"] == inv["slug"]


def test_only_a_linked_investigation_supplies_a_binding(made):
    """personal / declined / deferred are deliberate answers about the
    INVESTIGATION; none of them names a Project a member could inherit. Treating
    them as inheritable would publish a repo into a Project nobody chose."""
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry()
    for status in ("personal", "declined", "deferred", "unset"):
        inv = made(display_name=f"Not Linked {status}")
        reg.set_investigation_egeria_project(inv["slug"], {
            "status": status, "egeria_project_guid": "",
        })
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", f"probe-{status}")
        assert reg.inherited_egeria_project_context("repo", f"probe-{status}") is None, (
            f"'{status}' must not supply a Project binding"
        )


def test_an_excluded_member_does_not_inherit(made):
    """§7's excluded state means "considered and ruled out" — it must not carry
    the investigation's Project binding with it."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Excluding")
    reg = ProjectRegistry()
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-x",
    })
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "ruled-out-repo", state="excluded")
    assert reg.inherited_egeria_project_context("repo", "ruled-out-repo") is None


def test_a_folio_holds_scope_and_working_sets_hold_dispositions(made):
    """The correction that prompted this: a WorkingSet carries a SINGLE
    Disposition, so one per investigation cannot be the membership list.

    Folio = everything in scope. WorkingSet = the resources carrying one
    disposition. A resource's disposition within an investigation IS which
    WorkingSet it sits in, so nothing stores it twice.
    """
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Folio Model")
    reg = ProjectRegistry()

    folio = reg.get_or_create_folio(inv["slug"])
    assert folio["collection_kind"] == "folio"
    assert folio["disposition"] == ""

    reg.set_investigation_disposition(inv["slug"], "repo", "alpha", "tracking")
    reg.set_investigation_disposition(inv["slug"], "repo", "beta", "using")

    by_disp = reg.investigation_dispositions(inv["slug"])
    assert sorted(by_disp) == ["tracking", "using"]
    # in scope regardless of judgement
    assert {m["entity_slug"] for m in reg.list_investigation_members(inv["slug"])} == {"alpha", "beta"}


def test_changing_a_disposition_moves_rather_than_adds(made):
    """A WorkingSet carries one disposition, so membership of two would assert
    two contradictory judgements about the same resource at once."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Moving")
    reg = ProjectRegistry()
    reg.set_investigation_disposition(inv["slug"], "repo", "gamma", "investigating")
    reg.set_investigation_disposition(inv["slug"], "repo", "gamma", "abandoned")

    by_disp = reg.investigation_dispositions(inv["slug"])
    holding = [d for d, ms in by_disp.items() if any(m["entity_slug"] == "gamma" for m in ms)]
    assert holding == ["abandoned"], f"in {len(holding)} sets at once: {holding}"
    assert reg.disposition_of(inv["slug"], "repo", "gamma") == "abandoned"


def test_clearing_a_disposition_leaves_it_in_scope(made):
    """In scope but unjudged is the normal state straight after scouting, and
    must be expressible — otherwise clearing a decision would silently remove
    the resource from the investigation."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Unjudged")
    reg = ProjectRegistry()
    reg.set_investigation_disposition(inv["slug"], "repo", "delta", "tracking")
    reg.set_investigation_disposition(inv["slug"], "repo", "delta", "")

    assert reg.disposition_of(inv["slug"], "repo", "delta") == ""
    assert any(m["entity_slug"] == "delta" for m in reg.list_investigation_members(inv["slug"]))


def test_undecided_is_not_a_working_set(made):
    """"Nobody has judged this" is the ABSENCE of a WorkingSet. Creating one for
    it would make an unjudged resource indistinguishable from one deliberately
    marked undecided."""
    from resource_explorer.registry import ProjectRegistry
    import pytest as _pytest

    reg = ProjectRegistry()
    assert "undecided" not in reg.WORKING_SET_DISPOSITIONS
    inv = made(display_name="No Undecided Set")
    with _pytest.raises(ValueError):
        reg.get_or_create_disposition_set(inv["slug"], "undecided")


def test_dispositions_route_returns_what_the_filter_needs(client, made):
    """The left-column chips read this directly, so its shape is a contract."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Chips")
    reg = ProjectRegistry()
    reg.set_investigation_disposition(inv["slug"], "repo", "one", "tracking")
    reg.set_investigation_disposition(inv["slug"], "repo", "two", "using")

    r = client.get(f"/api/investigations/{inv['slug']}/dispositions")
    assert r.status_code == 200
    body = r.json()
    assert sorted(body) == ["tracking", "using"]
    assert body["tracking"][0]["entity_slug"] == "one"


def test_only_dispositions_in_use_are_returned(client, made):
    """An investigation where nothing is abandoned must not offer an 'abandoned'
    chip that always matches nothing — an always-empty filter reads as broken
    rather than as an empty category."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Sparse")
    reg = ProjectRegistry()
    reg.set_investigation_disposition(inv["slug"], "repo", "solo", "tracking")

    body = client.get(f"/api/investigations/{inv['slug']}/dispositions").json()
    assert list(body) == ["tracking"]
    assert len(body) < len(reg.WORKING_SET_DISPOSITIONS)


def test_an_unknown_disposition_is_rejected(client, made):
    inv = made(display_name="Bad Disposition")
    r = client.post(f"/api/investigations/{inv['slug']}/dispositions/repo/x?disposition=nonsense")
    assert r.status_code == 400
    assert "nonsense" in r.json()["detail"]


def test_a_disposition_member_is_always_in_the_folio(made):
    """Why the disposition filter can compose with the working-set filter.

    set_investigation_disposition adds to the Folio BEFORE assigning, so a
    disposition member is in scope by construction. That is what makes
    "working set AND disposition" safe to combine — the intersection can never
    be spuriously empty, so the UI must not widen the base filter to compensate
    for a problem that cannot occur.

    It also matters for intent: disposition exists so an investigation can focus
    on what matters within it. Widening to every registered repo would discard
    that focus at the moment the user asked for it.
    """
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Composes")
    reg = ProjectRegistry()
    reg.set_investigation_disposition(inv["slug"], "repo", "never-added-first", "tracking")

    in_scope = {(m["entity_type"], m["entity_slug"])
                for m in reg.list_investigation_members(inv["slug"])}
    for disposition, members in reg.investigation_dispositions(inv["slug"]).items():
        for m in members:
            assert (m["entity_type"], m["entity_slug"]) in in_scope, (
                f"{m['entity_slug']} is '{disposition}' but not in the Folio — "
                "the two filters would then disagree"
            )


def test_next_steps_retire_as_the_gaps_close(client, made):
    """Offers, not a checklist to fill in.

    The creation form asks only what §1 says an investigation IS — a name, why
    it exists, and what kind of work it is. Everything else surfaces here, where
    the absence already is, and disappears when it stops being true. A step that
    lingered after being satisfied would train people to ignore the list.
    """
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Retiring", purposes=["Select"])
    reg = ProjectRegistry()
    slug = inv["slug"]

    def ids():
        return [s["id"] for s in client.get(f"/api/investigations/{slug}/next-steps").json()["steps"]]

    assert ids() == ["add_resources", "bind_egeria"]

    ws = reg.get_or_create_folio(slug)
    reg.add_working_set_member(ws["slug"], "repo", "some-repo")
    assert ids() == ["set_dispositions", "bind_egeria"], "scope offer should retire once scoped"

    reg.set_investigation_disposition(slug, "repo", "some-repo", "investigating")
    assert ids() == ["bind_egeria"], "judgement offer should retire once judged"

    reg.set_investigation_egeria_project(slug, {
        "status": "linked", "egeria_project_guid": "g",
    })
    body = client.get(f"/api/investigations/{slug}/next-steps").json()
    assert body["steps"] == [] and body["complete"] is True


def test_a_purposeless_investigation_is_told_so(client, made):
    """Purpose is the one field that does real work — it ranks what gets
    proposed — so its absence is worth surfacing rather than defaulting."""
    inv = made(display_name="No Purpose", purposes=[])
    ids = [s["id"] for s in client.get(f"/api/investigations/{inv['slug']}/next-steps").json()["steps"]]
    assert "declare_purpose" in ids


def test_classification_is_recorded_and_validated(client, made):
    """§1 mode 2 carries a classification. Kept even for a local-only
    investigation, so promoting later does not re-ask something already decided."""
    inv = made(display_name="Kinded", project_classification="Campaign")
    assert inv["project_classification"] == "Campaign"
    bad = client.post("/api/investigations/", json={"display_name": "Bad Kind",
                                                    "project_classification": "Nonsense"})
    assert bad.status_code == 400


def test_partly_judged_scope_is_not_reported_as_judged(client, made):
    """One judged resource must not retire the offer for the other eighteen.

    Measured against real data: q3-health-review had 5 of 19 judged and reported
    `complete: true`, which the UI renders as "Nothing outstanding -- scoped,
    judged and catalogued." That is the failure mode this whole vocabulary
    exists to prevent: a status that reads as an answer while the work it claims
    is done is mostly not.
    """
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Partly judged", purposes=["Assess"])
    reg = ProjectRegistry()
    slug = inv["slug"]
    ws = reg.get_or_create_folio(slug)
    for n in range(4):
        reg.add_working_set_member(ws["slug"], "repo", f"repo-{n}")

    def step():
        steps = client.get(f"/api/investigations/{slug}/next-steps").json()["steps"]
        return next((s for s in steps if s["id"] == "set_dispositions"), None)

    assert step()["title"] == "Nothing judged yet"

    reg.set_investigation_disposition(slug, "repo", "repo-0", "using")
    s = step()
    assert s is not None, "one judged resource retired the offer for the other three"
    assert s["title"] == "3 resource(s) not judged yet"
    assert "1 of 4" in s["detail"]

    # Only judging ALL of them retires it.
    for n in (1, 2, 3):
        reg.set_investigation_disposition(slug, "repo", f"repo-{n}", "tracking")
    assert step() is None


def test_complete_means_every_resource_judged(client, made):
    """`complete` drives a summary line that claims the work is finished, so it
    has to be true of the whole scope rather than of one member of it."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Completeness", purposes=["Assess"])
    reg = ProjectRegistry()
    slug = inv["slug"]
    ws = reg.get_or_create_folio(slug)
    reg.add_working_set_member(ws["slug"], "repo", "a")
    reg.add_working_set_member(ws["slug"], "repo", "b")
    reg.set_investigation_disposition(slug, "repo", "a", "using")
    reg.set_investigation_egeria_project(slug, {"status": "linked", "egeria_project_guid": "g"})

    assert client.get(f"/api/investigations/{slug}/next-steps").json()["complete"] is False
    reg.set_investigation_disposition(slug, "repo", "b", "ignored")
    assert client.get(f"/api/investigations/{slug}/next-steps").json()["complete"] is True


def test_members_can_be_relinked_after_their_resources_are_published(client, made):
    """promote() links members once and then refuses to run again.

    So a member with no asset GUID at promotion time had no way to be linked
    later, and the Egeria Collection stayed permanently empty while RE showed
    the resources in scope. Hit for real after the 2026-08-26 redeploy: both
    investigations promoted with members_linked: [] because nothing had been
    republished yet, and no operation existed to finish the job.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Relink", purposes=["Assess"])
    reg = ProjectRegistry()
    slug = inv["slug"]

    pub = EgeriaInvestigationPublisher(reg)
    # Not bound yet: says so rather than silently doing nothing.
    res = pub.relink_members(slug)
    assert not res.ok and "promote or bind it first" in " ".join(res.errors)


def test_relink_reports_a_missing_working_set_rather_than_creating_one(client, made):
    """Creating a second Collection here would orphan the first and split the
    membership across both -- ensure_working_set owns that, deliberately."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Relink no ws", purposes=["Assess"])
    reg = ProjectRegistry()
    slug = inv["slug"]
    reg.set_investigation_egeria_project(slug, {"status": "linked", "egeria_project_guid": "g"})

    res = EgeriaInvestigationPublisher(reg).relink_members(slug)
    assert not res.ok
    assert "ensure_working_set" in " ".join(res.errors)


def test_relink_route_404s_on_an_unknown_investigation(client):
    assert client.post("/api/investigations/nope-nope/relink-members").status_code == 404


# --- Lifecycle: suspend/reopen (docs/investigation-framing-design.md follow-up) ---
#
# The bug this whole block exists to prevent: a user hit "Close" thinking it
# minimised a panel. It set status='closed', which hid the investigation from
# every list with no reopen route and no confirmation -- so it read as a
# delete even though the data survived. Recovering it needed a direct SQL
# update. These tests pin the two fixes: a reopen route, and a status
# ('suspended') that pauses without unbinding.

def test_suspend_then_reopen_round_trips_with_everything_intact(client, made):
    """Suspend and reopen must be a no-op on everything except `status` --
    members, purposes and the Egeria binding all have to survive the round
    trip, or "pause" would quietly be "lose some state"."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Round Trip", purposes=["Explore"])
    slug = inv["slug"]
    reg = ProjectRegistry()
    reg.set_investigation_egeria_project(slug, {
        "status": "linked", "egeria_project_guid": "proj-roundtrip",
        "egeria_project_qualified_name": "Project::RoundTrip",
    })
    ws = reg.get_or_create_working_set(slug)
    reg.add_working_set_member(ws["slug"], "repo", "roundtrip-repo")

    r = client.post(f"/api/investigations/{slug}/suspend")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "suspended"

    r = client.post(f"/api/investigations/{slug}/reopen")
    assert r.status_code == 200, r.text
    reopened = r.json()
    assert reopened["status"] == "open"
    assert reopened["purposes"] == ["Explore"]
    assert reopened["egeria_context"]["egeria_project_guid"] == "proj-roundtrip"
    members = client.get(f"/api/investigations/{slug}/members").json()
    assert [m["entity_slug"] for m in members] == ["roundtrip-repo"]


def test_suspended_still_inherits_egeria_project_but_closed_does_not(made):
    """The one behavioural difference that justifies two words instead of one.

    Both pause the investigation; only `closed` says the work is over, so only
    `closed` should stop a member from inheriting the Project binding. Tested
    against the real inherited_egeria_project_context query (registry.py
    ~line 4069), not a mock of it -- that query is the thing that would
    silently regress if 'suspended' were ever folded back into 'closed'.
    """
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry()

    suspended_inv = made(display_name="Suspended Inherits")
    reg.set_investigation_egeria_project(suspended_inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-suspended",
    })
    ws = reg.get_or_create_working_set(suspended_inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "suspended-member")
    reg.set_investigation_status(suspended_inv["slug"], "suspended")

    got = reg.inherited_egeria_project_context("repo", "suspended-member")
    assert got and got["egeria_project_guid"] == "proj-suspended", (
        "suspended must keep supplying its Project binding -- pausing work "
        "does not unbind the resources already in it"
    )

    closed_inv = made(display_name="Closed Stops Inheriting")
    reg.set_investigation_egeria_project(closed_inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-closed",
    })
    ws2 = reg.get_or_create_working_set(closed_inv["slug"])
    reg.add_working_set_member(ws2["slug"], "repo", "closed-member")
    reg.set_investigation_status(closed_inv["slug"], "closed")

    assert reg.inherited_egeria_project_context("repo", "closed-member") is None, (
        "closed must stop supplying a Project binding -- the work is over"
    )


def test_set_investigation_status_rejects_an_unknown_status(made):
    """The same guard create_investigation's purpose check has, for the same
    reason: a status outside the vocabulary would silently do nothing rather
    than fail loudly, and 'nothing happened' looks identical to success."""
    from resource_explorer.registry import ProjectRegistry

    inv = made(display_name="Bad Status")
    reg = ProjectRegistry()
    with pytest.raises(ValueError, match="unknown status"):
        reg.set_investigation_status(inv["slug"], "abandoned")


def test_suspend_route_404s_on_an_unknown_slug(client):
    assert client.post("/api/investigations/nope-nope/suspend").status_code == 404


def test_reopen_route_404s_on_an_unknown_slug(client):
    """The route that didn't exist before this fix -- without it, a closed
    investigation had no way back except a direct SQL UPDATE."""
    assert client.post("/api/investigations/nope-nope/reopen").status_code == 404


def test_list_investigations_default_view_includes_suspended_but_not_closed(client, made):
    """Pins the deliberate include_closed decision made in list_investigations:
    'suspended' is paused-but-will-resume, so it stays reachable in the default
    (unfiltered) list. Only 'closed' needs include_closed=True to see -- and
    that split is exactly why the bug report calls Close "a delete", not
    Suspend: only Close made the row disappear here.
    """
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry()
    suspended_inv = made(display_name="Listed Suspended")
    closed_inv = made(display_name="Listed Closed")
    reg.set_investigation_status(suspended_inv["slug"], "suspended")
    reg.set_investigation_status(closed_inv["slug"], "closed")

    default_slugs = {i["slug"] for i in client.get("/api/investigations/").json()}
    assert suspended_inv["slug"] in default_slugs
    assert closed_inv["slug"] not in default_slugs

    all_slugs = {i["slug"] for i in client.get("/api/investigations/?include_closed=true").json()}
    assert suspended_inv["slug"] in all_slugs
    assert closed_inv["slug"] in all_slugs


class _FailingCM(_StubCM):
    """Attaches the Collection fine, but every membership write fails —
    the shape of a real permissions or transient-server problem."""

    def add_to_collection(self, collection_guid, element_guid, body=None):
        raise RuntimeError("Egeria said no")


def test_a_failed_membership_becomes_a_retryable_row_not_a_forgotten_note(made, monkeypatch):
    """The gap the outbox closes for investigations.

    Before this, a member whose add_to_collection failed was appended to
    members_unlinkable and forgotten — the promotion reported partial success
    and nothing ever tried again. Now the failure is a durable row the
    scheduler retries.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Failing Members")
    reg = ProjectRegistry()
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "published-repo")
    monkeypatch.setattr(EgeriaInvestigationPublisher, "_asset_guid",
                        lambda self, et, es: "asset-guid-1")

    res = EgeriaInvestigationPublisher(
        reg, project_manager=_StubPM(), collection_manager=_FailingCM(),
    ).promote(inv["slug"])

    # Reported honestly to the caller...
    assert res.members_linked == []
    assert len(res.members_unlinkable) == 1
    assert "queued for retry" in res.members_unlinkable[0]["reason"]

    # ...AND still queued, which is the actual change.
    queued = [r for r in reg.list_outbox_elements(entity_slug=inv["slug"], limit=50)
              if r["element_kind"] == "collection_membership" and r["status"] != "done"]
    assert len(queued) == 1
    assert queued[0]["entity_type"] == "investigation"
    assert "Egeria said no" in queued[0]["last_error"]


def test_a_member_with_no_asset_is_never_queued(made):
    """The two outcomes stay distinct. No asset GUID is a fact about the
    catalog, not a failed write — queueing it would retry a fact forever."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Unpublished Member")
    reg = ProjectRegistry()
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "definitely-not-published-abc")

    res = EgeriaInvestigationPublisher(
        reg, project_manager=_StubPM(), collection_manager=_StubCM(),
    ).promote(inv["slug"])

    assert "not published to Egeria yet" in res.members_unlinkable[0]["reason"]
    # Scoped to THIS investigation: the registry is shared across tests in this
    # module, so an unscoped assertion would pass or fail on what ran before it.
    assert [r for r in reg.list_outbox_elements(entity_slug=inv["slug"], limit=50)
            if r["element_kind"] == "collection_membership"] == []


def test_a_successful_membership_still_links_before_promote_returns(made, monkeypatch):
    """The happy path is unchanged: enqueue and drain happen inline, so a
    caller sees the same result it saw before the outbox existed."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Happy Members")
    reg = ProjectRegistry()
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "published-repo")
    monkeypatch.setattr(EgeriaInvestigationPublisher, "_asset_guid",
                        lambda self, et, es: "asset-guid-1")

    cm = _StubCM()
    res = EgeriaInvestigationPublisher(
        reg, project_manager=_StubPM(), collection_manager=cm,
    ).promote(inv["slug"])

    assert res.members_linked == ["published-repo"]
    assert res.members_unlinkable == []
    assert cm.members == [("coll-1", "asset-guid-1")]


# ── Phase 4/5: the investigation's OWN Egeria elements ─────────────────────

def test_a_private_investigations_project_is_zoned(made):
    """Phase 5 zoned the artifacts a private investigation produces and left the
    Project itself public — so its name, description, purposes and membership
    were readable by everyone while its surveys were not.

    The owner's point 5 is "the project AND all related artifacts", and the
    Project is the half that names the work.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )
    from resource_explorer import egeria_identity as ident

    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Alice Zoned", project_classification="PersonalProject")
    zoned = {}
    before_state, before_set = ident._private_zone_state, ident.set_zone_membership
    ident._private_zone_state = {"status": "exists", "enforced": True,
                                 "zone": ident.private_zone(), "control_present": True}
    ident.set_zone_membership = lambda guid, zones, **k: zoned.setdefault(guid, list(zones)) or True
    try:
        res = EgeriaInvestigationPublisher(
            reg, project_manager=_StubPM(), collection_manager=_StubCM()
        ).promote(inv["slug"])
    finally:
        ident._private_zone_state, ident.set_zone_membership = before_state, before_set
        with reg._conn() as conn:
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))

    assert res.private_zoned is True, res.errors
    assert zoned.get("proj-1") == [ident.private_zone(), "alice"]


def test_a_private_project_that_cannot_be_zoned_is_reported_not_hidden(made):
    """An unenforced zone means the Project is public. Saying nothing would let
    someone believe their personal investigation is private when its name and
    membership are readable by everyone."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )
    from resource_explorer import egeria_identity as ident

    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Alice Unzonable", project_classification="Experiment",
                                       hypothesis="h")
    before = ident._private_zone_state
    ident._private_zone_state = {"status": "not_authorized", "enforced": False}
    try:
        res = EgeriaInvestigationPublisher(
            reg, project_manager=_StubPM(), collection_manager=_StubCM()
        ).promote(inv["slug"])
    finally:
        ident._private_zone_state = before
        with reg._conn() as conn:
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))

    assert res.private_zoned is False
    assert not res.ok
    assert any("visible to everyone" in e for e in res.errors), res.errors


def test_a_shared_investigations_project_is_not_zoned_private(made):
    """The guard must be narrow: Task/Campaign/Study Projects follow the normal
    rules and must not be swept into the private zone."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )
    from resource_explorer import egeria_identity as ident

    reg = ProjectRegistry()
    with _as("alice"):
        inv = reg.create_investigation("Alice Shared Proj", project_classification="Task")
    zoned = {}
    before_set = ident.set_zone_membership
    before_state = ident._private_zone_state
    # The zone MUST be enforced for this test to mean anything. Without it the
    # code never reaches the zoning branch at all, so the assertion below holds
    # even with the classification check deleted — which is exactly how the
    # first version of this test passed a sabotage run that zoned everything.
    ident._private_zone_state = {"status": "exists", "enforced": True,
                                 "zone": ident.private_zone(), "control_present": True}
    ident.set_zone_membership = lambda guid, zones, **k: zoned.setdefault(guid, list(zones)) or True
    try:
        res = EgeriaInvestigationPublisher(
            reg, project_manager=_StubPM(), collection_manager=_StubCM()
        ).promote(inv["slug"])
    finally:
        ident.set_zone_membership = before_set
        ident._private_zone_state = before_state
        with reg._conn() as conn:
            conn.execute("DELETE FROM investigations WHERE slug = ?", (inv["slug"],))

    assert res.private_zoned is False
    assert zoned == {}, f"a shared investigation's Project was zoned {zoned}"


def test_the_folio_is_anchored_to_the_project(made):
    """Anchored, not separately stamped.

    Measured 2026-09-08: existing Folios were their own anchor with no
    ZoneMembership, so a private investigation's collection was world-readable.
    Anchoring is one property set at creation that stays correct when the
    Project is re-zoned later — enforcement reads the LIVE anchor, not the copy
    cached on the child.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    class _RecordingCM(_StubCM):
        def __init__(self):
            super().__init__()
            self.bodies = []

        def create_collection(self, display_name=None, description=None, body=None, **kw):
            self.bodies.append(body)
            return "coll-1"

    reg = ProjectRegistry()
    inv = made(display_name="Folio Anchor")
    cm = _RecordingCM()
    EgeriaInvestigationPublisher(
        reg, project_manager=_StubPM(), collection_manager=cm
    ).promote(inv["slug"])

    assert cm.bodies, "no collection was created"
    body = cm.bodies[0]
    assert body.get("isOwnAnchor") is False
    assert body.get("anchorGUID") == "proj-1", (
        "the Folio is its own anchor, so it inherits no zones and is public")


# ── the Investigation marker (owner's decision, 2026-09-08) ────────────────

def test_the_investigation_marker_is_applied_after_the_create_not_in_it(made):
    """A SEPARATE classify call, deliberately not `initialClassifications`.

    In the create body, a classification the platform does not have fails the
    WHOLE create — and this type did not exist until the 2026-09-08 redeploy, and
    will not exist on any deployment running an older Egeria. As a follow-on
    step, a missing type costs the marker and not the investigation.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import egeria_investigation_publisher as pub
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Marked")
    marked = []
    real = pub._apply_investigation_marker
    pub._apply_investigation_marker = lambda pm, guid: (marked.append(guid) or (True, ""))
    try:
        pm = _StubPM()
        res = EgeriaInvestigationPublisher(
            ProjectRegistry(), project_manager=pm, collection_manager=_StubCM()
        ).promote(inv["slug"])
    finally:
        pub._apply_investigation_marker = real

    assert marked == ["proj-1"], "the marker was not applied on promote"
    assert res.investigation_marked is True
    body = pm.calls[0][3]
    assert pub.INVESTIGATION_MARKER not in (body.get("initialClassifications") or {}), (
        "the marker is in the create body — a platform without the type would "
        "fail the whole create")


def test_a_platform_without_the_marker_type_still_promotes(made):
    """The marker is a catalogue convenience, not a correctness property.
    Nothing in RE reads it — the KIND drives behaviour — so an investigation
    without it is fully functional, just less findable by someone browsing
    Egeria. It must never fail a promotion."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors import egeria_investigation_publisher as pub
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    inv = made(display_name="Old Platform")
    real = pub._apply_investigation_marker
    pub._apply_investigation_marker = lambda pm, guid: (
        False, "this Egeria does not have the 'Investigation' classification")
    try:
        res = EgeriaInvestigationPublisher(
            ProjectRegistry(), project_manager=_StubPM(), collection_manager=_StubCM()
        ).promote(inv["slug"])
    finally:
        pub._apply_investigation_marker = real

    assert res.project_guid == "proj-1", "a missing marker type lost the investigation"
    assert res.investigation_marked is False
    assert res.ok, res.errors


def test_the_marker_is_not_a_kind_and_so_survives_reclassification(made):
    """The owner's model: `Investigation` coexists with the kind and does not
    replace it. Verified live 2026-09-08 — promote gave
    `['Task', 'Investigation']` and a Task -> PersonalProject reclassification
    gave `['Investigation', 'PersonalProject']`.

    Structurally this holds because the reclassifier removes the old kind BY
    NAME, gated on PROJECT_CLASSIFICATIONS, and the marker is not in that set.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        INVESTIGATION_MARKER,
    )

    assert INVESTIGATION_MARKER not in ProjectRegistry.PROJECT_CLASSIFICATIONS, (
        "the marker is in the kind vocabulary — a reclassification would strip it")
    assert INVESTIGATION_MARKER not in ProjectRegistry.PRIVATE_CLASSIFICATIONS, (
        "the marker would drive zoning, which the owner said it must not")
