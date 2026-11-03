"""The blueprint cache keyed by Egeria identity, on the real Postgres registry (pg tier, run by CI).

architecture_materialized_blueprints is a CACHE of Egeria elements, not a proof or a decision: re-recording
under the same qualifiedName replaces the stale row, and proof rows are never deleted. Each test uses its own
project slug (the pg schema is shared within a session).
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.requires_pgvector

G1 = "11111111-1111-1111-1111-111111111111"
G2 = "22222222-2222-2222-2222-222222222222"
G3 = "33333333-3333-3333-3333-333333333333"


def _qn(slug, tail="deployment"):
    return f"SolutionBlueprint::repo::{slug}::{tail}"


def test_two_blueprints_are_read_back_by_identity_and_unknown_is_none(pg_registry):
    slug = "bpid_pg_read"
    pg_registry.record_materialized_blueprint("repo", slug, "deployment", "root", _qn(slug), G1)
    pg_registry.record_materialized_blueprint("repo", slug, "deployment", "web", _qn(slug, "deployment::Servers"), G2)
    one = pg_registry.get_materialized_blueprint_by_identity("repo", slug, _qn(slug))
    two = pg_registry.get_materialized_blueprint_by_identity("repo", slug, _qn(slug, "deployment::Servers"))
    assert (one["guid"], one["cluster_name"]) == (G1, "root")
    assert (two["guid"], two["cluster_name"]) == (G2, "web")
    assert pg_registry.get_materialized_blueprint_by_identity("repo", slug, _qn(slug, "build")) is None


def test_re_recording_under_the_same_identity_rekeys_the_row_and_keeps_two(pg_registry):
    slug = "bpid_pg_rekey"
    pg_registry.record_materialized_blueprint("repo", slug, "deployment", "root", _qn(slug), G1)
    pg_registry.record_materialized_blueprint("repo", slug, "deployment", "web", _qn(slug, "deployment::Servers"), G2)
    pg_registry.record_materialized_blueprint("repo", slug, "deployment", "root-renamed", _qn(slug), G1)
    rows = pg_registry.get_materialized_blueprints("repo", slug)
    assert set(rows) == {"deployment::root-renamed", "deployment::web"}
    assert pg_registry.get_materialized_blueprint_by_identity("repo", slug, _qn(slug))["cluster_name"] == "root-renamed"


def test_another_slugs_row_under_the_same_qualified_name_is_untouched(pg_registry):
    mine, other = "bpid_pg_mine", "bpid_pg_other"
    shared = "SolutionBlueprint::repo::shared::deployment"
    pg_registry.record_materialized_blueprint("repo", other, "deployment", "c", shared, G3)
    pg_registry.record_materialized_blueprint("repo", mine, "deployment", "c", shared, G1)
    pg_registry.record_materialized_blueprint("repo", mine, "deployment", "c2", shared, G2)
    assert pg_registry.get_materialized_blueprint_by_identity("repo", other, shared)["guid"] == G3
    assert pg_registry.get_materialized_blueprint_by_identity("repo", mine, shared)["guid"] == G2


def test_re_recording_returns_the_cluster_it_displaced(pg_registry):
    slug = "bpid_pg_displaced"
    first = pg_registry.record_materialized_blueprint("repo", slug, "deployment", "web", _qn(slug), G1)
    assert first["displaced"] == []
    second = pg_registry.record_materialized_blueprint("repo", slug, "deployment", "db", _qn(slug), G1)
    assert second["displaced"] == ["web"]
    again = pg_registry.record_materialized_blueprint("repo", slug, "deployment", "db", _qn(slug), G1)
    assert again["displaced"] == []


def test_a_claim_is_taken_once_and_released_only_by_its_holder(pg_registry):
    key = "blueprint-claim::bpid_pg_claim"
    assert pg_registry.take_claim(key, "a") is True
    assert pg_registry.take_claim(key, "b") is False
    pg_registry.release_claim(key, "b")                 # not the holder: the claim stands
    assert pg_registry.take_claim(key, "c") is False
    pg_registry.release_claim(key, "a")
    assert pg_registry.take_claim(key, "c") is True
    pg_registry.release_claim(key, "c")


def test_a_split_with_one_acceptance_adopts_once_and_the_other_half_is_refused_afterwards(pg_registry):
    """The SQLite twin of this walks the same path; here the claim's ON CONFLICT rowcount is the real
    Postgres one. Recording-fake Egeria only; the pg_registry gives the real claim and proof tables."""
    from tests.test_blueprint_identity_kind_repo import DISPLAY, _created, _m
    from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintIdentifierNeeded

    slug = "bpid_pg_split"
    base = f"SolutionBlueprint::repo::{slug}::deployment"
    props = {base: {"guid": G1, "props": {"re_entity_type": "repo", "re_slug": slug, "re_kind": "deployment",
                                           "re_cluster_key": "old-root", "re_version": "1"}}}
    live = {"alpha", "beta"}              # one group re-clustered into two; the old name is gone

    def press(cluster):
        m = _m(pg_registry, found=props)
        return m, m.materialize_blueprint_element("repo", slug, "deployment", cluster, display_name=DISPLAY,
                                                  live_clusters=live)

    first, out = press("beta")            # beta alone pressed adopts
    assert out["guid"] == G1 and _created(first) == []
    later = _m(pg_registry, found=props)  # alpha pressed afterwards: beta now holds the element
    with pytest.raises(BlueprintIdentifierNeeded, match=re.escape("a Deployment Blueprint already exists for bpid_pg_split (held by beta) "
                                          "\u00b7 give this one an identifier")):
        later.materialize_blueprint_element("repo", slug, "deployment", "alpha", display_name=DISPLAY,
                                            live_clusters=live)
    assert _created(later) == []
    assert set(pg_registry.get_materialized_blueprints("repo", slug)) == {"deployment::beta"}
    proofs = [p for p in pg_registry.list_catalogue_commit_proofs(slug) if p["proof"] == "rekey"]
    assert len(proofs) == 1 and proofs[0]["element_guid"] == G1
    assert proofs[0]["detail"]["new_cluster_key"] == "beta"
    claim = f"blueprint-claim::{base}"
    assert pg_registry.take_claim(claim, "after") is True          # released by the holder in its finally
    pg_registry.release_claim(claim, "after")
