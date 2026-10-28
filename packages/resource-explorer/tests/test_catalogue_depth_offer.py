"""The layer-2 catalogue-depth offer (owner's ruling, 2026-09-15, on the
designer's REPLY-CATALOGUE-IN-LAYERS.md §3) — DepthOffer's three rules
(not a nag, not a gate, not a scold) applied to component materialization
instead of never-run analyses, priced from real per-write Egeria timing
where it exists and an honest "not yet measured" fallback where it doesn't.
"""
from __future__ import annotations

import pytest

from resource_explorer.curate_plan import Curations
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.workflows.catalogue_depth_offer import (
    LAYER2_OFFER_OUTCOMES, build_catalogue_depth_offer,
)


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


def _fake_tree(total, accepted):
    return {"total_components": total, "accepted": accepted}


class TestNotAGateNorANag:
    def test_no_catalogue_record_means_layer_one_is_not_done(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(10, 0))
        offer = build_catalogue_depth_offer(registry, "p")
        assert offer["layer1_done"] is False and offer["curation_id"] is None
        # Not a gate: the offer is still computed and returned, never refused.
        assert offer["remaining_components"] == 10

    def test_a_finished_catalogue_commit_is_layer_one_done(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(10, 2))
        rec = Curations(registry).create("repo", "p", author="peterprofile", selection={},
                                         manifest={}, steps=["publish_asset"])
        Curations(registry).set_step(rec["id"], "publish_asset", "done")
        Curations(registry).finish(rec["id"])
        offer = build_catalogue_depth_offer(registry, "p")
        assert offer["layer1_done"] is True
        assert offer["curation_id"] == rec["id"]
        assert offer["total_components"] == 10 and offer["accepted"] == 2
        assert offer["remaining_components"] == 8

    def test_offered_once_per_catalogue_record_not_per_verdict(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(10, 2))
        rec = Curations(registry).create("repo", "p", author="peterprofile", selection={},
                                         manifest={}, steps=["publish_asset"])
        Curations(registry).finish(rec["id"])
        assert build_catalogue_depth_offer(registry, "p")["already_decided"] is False
        Curations(registry).record_layer2_offer(rec["id"], "declined", "peterprofile")
        assert build_catalogue_depth_offer(registry, "p")["already_decided"] is True


class TestNotAScold:
    def test_the_counts_are_facts_not_an_instruction(self, registry, monkeypatch):
        """The offer states total/accepted/remaining; composing "N recovered,
        M not catalogued" into an imperative sentence is the caller's job,
        not this module's -- so there is no field here that reads as one."""
        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(64, 0))
        offer = build_catalogue_depth_offer(registry, "p")
        assert offer["total_components"] == 64 and offer["remaining_components"] == 64
        assert not any("should" in str(v).lower() or "must" in str(v).lower()
                      for v in offer["cost"].values() if isinstance(v, str))


class TestThePriceHasABasis:
    def test_no_timing_recorded_is_the_honest_fallback(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(5, 0))
        cost = build_catalogue_depth_offer(registry, "p")["cost"]
        assert cost["basis"] == "unknown" and cost["seconds"] is None
        assert "not yet measured" in cost["sentence"]

    def test_real_timing_produces_a_real_number_and_names_its_basis(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(5, 0))
        for s in [1.0, 1.0, 2.0]:
            registry.record_egeria_call_timing("create_solution_component", "write", s)
        cost = build_catalogue_depth_offer(registry, "p")["cost"]
        assert cost["basis"] == "measured"
        assert cost["seconds"] == pytest.approx(1.0 * 5)   # median 1.0s * 5 remaining
        assert "measured" in cost["sentence"] and "median" in cost["sentence"]

    def test_zero_remaining_never_claims_a_price(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(3, 3))
        registry.record_egeria_call_timing("create_solution_component", "write", 1.0)
        cost = build_catalogue_depth_offer(registry, "p")["cost"]
        assert cost["basis"] == "unknown"   # nothing left to price is not a zero-cost claim


class TestUnknownSlug:
    def test_raises_lookup_error(self, registry):
        with pytest.raises(LookupError):
            build_catalogue_depth_offer(registry, "nope")


class TestRecordLayer2Offer:
    def test_a_second_write_is_refused(self, registry):
        rec = Curations(registry).create("repo", "p", author="peterprofile", selection={},
                                         manifest={}, steps=[])
        Curations(registry).record_layer2_offer(rec["id"], "declined", "peterprofile")
        with pytest.raises(ValueError):
            Curations(registry).record_layer2_offer(rec["id"], "accepted", "peterprofile")

    def test_an_unknown_record_raises_lookup_error(self, registry):
        with pytest.raises(LookupError):
            Curations(registry).record_layer2_offer("does-not-exist", "declined", "peterprofile")

    def test_bad_outcome_is_rejected(self, registry):
        rec = Curations(registry).create("repo", "p", author="peterprofile", selection={},
                                         manifest={}, steps=[])
        with pytest.raises(ValueError):
            Curations(registry).record_layer2_offer(rec["id"], "maybe", "peterprofile")

    def test_the_outcome_vocabulary_matches_depth_offers(self):
        assert LAYER2_OFFER_OUTCOMES == {"declined", "accepted", "chose"}


class TestTheRoutes:
    """GET .../catalogue-depth-offer and POST .../curate/commits/{cid}/layer2-offer."""

    @pytest.fixture
    def client(self, registry, monkeypatch):
        from fastapi.testclient import TestClient

        monkeypatch.setattr("resource_explorer.component_tree.totals",
                            lambda *a, **k: _fake_tree(9, 1))
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "peterprofile"})
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_get_offer(self, client):
        r = client.get("/api/projects/p/catalogue-depth-offer")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_components"] == 9 and body["remaining_components"] == 8
        assert body["cost"]["basis"] == "unknown"

    def test_unknown_slug_404s(self, client):
        assert client.get("/api/projects/nope/catalogue-depth-offer").status_code == 404

    def test_post_outcome_records_and_refuses_a_second_write(self, client, registry):
        rec = Curations(registry).create("repo", "p", author="peterprofile", selection={},
                                         manifest={}, steps=[])
        r = client.post(f"/api/projects/p/curate/commits/{rec['id']}/layer2-offer",
                        json={"outcome": "declined"})
        assert r.status_code == 200, r.text
        assert r.json()["layer2_offer"]["outcome"] == "declined"
        assert r.json()["layer2_offer"]["decided_by"] == "peterprofile"

        again = client.post(f"/api/projects/p/curate/commits/{rec['id']}/layer2-offer",
                            json={"outcome": "accepted"})
        assert again.status_code == 409

    def test_post_bad_outcome_422s(self, client, registry):
        rec = Curations(registry).create("repo", "p", author="peterprofile", selection={},
                                         manifest={}, steps=[])
        r = client.post(f"/api/projects/p/curate/commits/{rec['id']}/layer2-offer",
                        json={"outcome": "maybe"})
        assert r.status_code == 422

    def test_post_unknown_record_404s(self, client):
        r = client.post("/api/projects/p/curate/commits/nope/layer2-offer",
                        json={"outcome": "declined"})
        assert r.status_code == 404

    def test_post_anonymous_is_401(self, client, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
        rec = Curations(registry).create("repo", "p", author="peterprofile", selection={},
                                         manifest={}, steps=[])
        r = client.post(f"/api/projects/p/curate/commits/{rec['id']}/layer2-offer",
                        json={"outcome": "declined"})
        assert r.status_code == 401
