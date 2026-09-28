"""SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §2 — the sibling reader that
exposes _candidate_blueprints_results (already built, already tested in
test_candidate_blueprints_reader.py) as a route the /next Curate pane can
call, and the accept/reject round trip that already existed in
web/routes/curate.py's blueprint-verdicts endpoints.

This is a THIN test on top of two already-well-tested layers
(_candidate_blueprints_results, add_blueprint_verdict): it only proves the
new /components/blueprints route returns that same data shape, grouped by
reading, and that the existing accept endpoint's result round-trips back
through this new reader — the exact "sibling reader, not a schema change"
claim in the spec.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry

# See test_investigation_routes.py's identical pytestmark / conftest.py's
# mock_egeria_client_connections docstring — the accept round trip here
# reaches an unmocked pyegeria client construction that spends ~60s on two
# real check_connection() handshakes when Egeria is unreachable.
pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "peterprofile"})
    monkeypatch.setattr("resource_explorer.web.routes.curate._authorize_curation", lambda *a, **k: None)
    from resource_explorer.web.app import app
    return TestClient(app)


def _seed_cluster(registry, slug, *, name="core-services", perspective="physical",
                   members=None, children=None, surveyed_at="2026-09-03T00:00:00"):
    registry.upsert_finding(slug, "architecture_blueprints", [{
        "check_name": "candidate_blueprint", "label": name,
        "detail": {"name": name, "perspective": perspective, "signal": "compose",
                   "carrier": "compose.yaml", "composed_into": "",
                   "size": len(members or []), "members": members or [],
                   "children": children or [], "parent": "", "oversized": False,
                   "target_size": 8, "run_scope": "", "not_a_claim": True},
    }], surveyed_at=surveyed_at)


class TestTheRoute:
    def test_404_for_an_unknown_project(self, client):
        assert client.get("/api/projects/nope/components/blueprints").status_code == 404

    def test_empty_when_nothing_proposed(self, client, registry):
        out = client.get("/api/projects/p/components/blueprints").json()
        assert out == {"blueprints": [], "perspectives": []}

    def test_shape_matches_the_existing_reader_and_lists_perspectives(self, client, registry):
        _seed_cluster(registry, "p", perspective="physical", name="core")
        _seed_cluster(registry, "p", perspective="logical", name="metadata access services")
        out = client.get("/api/projects/p/components/blueprints").json()
        assert out["perspectives"] == ["logical", "physical"]
        names = {(bp["perspective"], bp["cluster_name"]) for bp in out["blueprints"]}
        assert names == {("physical", "core"), ("logical", "metadata access services")}

    def test_a_cluster_only_appears_in_its_own_reading(self, client, registry):
        _seed_cluster(registry, "p", perspective="physical", name="core")
        out = client.get("/api/projects/p/components/blueprints").json()
        assert all(bp["perspective"] == "physical" for bp in out["blueprints"])


class TestAcceptRoundTripsThroughTheNewReader:
    """The accept endpoint (web/routes/curate.py, already shipped) writes a
    verdict_target='blueprint' row and materialises a SolutionBlueprint;
    this proves the NEW route reads that write back, satisfying §2's "3 of
    its 9 members are accepted components" and §4's membership-honesty
    numbers without any second write path."""

    def test_accepting_materialises_and_the_new_route_sees_it(self, client, registry, monkeypatch):
        _seed_cluster(registry, "p", perspective="physical", name="core", members=["a", "b"])

        class FakeMaterializer:
            def __init__(self, registry=None):
                self.registry = registry

            def materialize_blueprint_element(self, entity_type, slug, perspective, cluster_name, *, display_name, oversized=False):
                guid = "bp-guid-1"
                qn = f"SolutionBlueprint::{entity_type}::{slug}::{perspective}::{cluster_name}"
                self.registry.record_materialized_blueprint(entity_type, slug, perspective, cluster_name, qn, guid)
                return {"status": "materialized", "guid": guid, "qualified_name": qn}

            def resolve_member_guids(self, registry, entity_type, slug, member_slugs, slug_to_scope):
                return {}, list(member_slugs)   # neither member has its own materialized component

            def resolve_child_blueprint_guids(self, registry, entity_type, slug, perspective, child_names):
                return {}, list(child_names)

        monkeypatch.setattr(
            "resource_explorer.surveyors.arch_recovery.blueprint_materializer.BlueprintMaterializer",
            FakeMaterializer,
        )
        r = client.post("/api/curate/blueprint-verdicts/repo/p",
                         json={"perspective": "physical", "cluster_name": "core", "verdict": "accepted"})
        assert r.status_code == 200, r.text
        assert r.json()["materialization"]["status"] in ("materialized", "partial")

        out = client.get("/api/projects/p/components/blueprints").json()
        bp = out["blueprints"][0]
        assert bp["verdict"]["verdict"] == "accepted"
        assert bp["materialized"]["guid"] == "bp-guid-1"
        # Neither member has its own materialized SolutionComponent (Decision
        # 2: accepting a blueprint does not implicitly materialize members),
        # so the honesty count the pane renders — materialized member/child
        # count — is genuinely zero here, not merely unread.
        assert all(not m["materialized"] for m in bp["member_status"])

    def test_reject_creates_nothing(self, client, registry):
        _seed_cluster(registry, "p", perspective="physical", name="core")
        r = client.post("/api/curate/blueprint-verdicts/repo/p",
                         json={"perspective": "physical", "cluster_name": "core", "verdict": "rejected"})
        assert r.status_code == 200, r.text
        out = client.get("/api/projects/p/components/blueprints").json()
        assert out["blueprints"][0]["verdict"]["verdict"] == "rejected"
        assert out["blueprints"][0]["materialized"] is None
