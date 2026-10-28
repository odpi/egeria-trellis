"""ENRICHMENT-E3: the server stamps what the survey measured beside an
observation the person writes (licence, repositories only), from its own fact
layer. Judgements are never stamped; databases have no proposing analysis."""
from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry
from resource_explorer.web.routes.context import licence_value_from_fact, measured_for


def _fact(summary="MIT License — Permissive", label="permissive", state="measured", at="2026-09-30T10:00:00"):
    return types.SimpleNamespace(
        state=state, last_run_at=at, headline=summary,
        value={"findings": [{"check_name": "license_risk_tier", "label": label, "summary": summary}]},
    )


class TestLicenceValueFromFact:
    def test_the_licence_name_is_the_part_before_the_dash(self):
        assert licence_value_from_fact(_fact("Apache License 2.0 — Permissive")) == "Apache License 2.0"

    @pytest.mark.parametrize("fact", [
        _fact(label="none"), _fact(state="never_run"), _fact(summary="no dash here"),
        types.SimpleNamespace(state="measured", value={}, headline="", last_run_at=""),
    ])
    def test_nothing_classified_is_absence_not_a_value(self, fact):
        assert licence_value_from_fact(fact) == ""


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    r.register_database(DatabaseEntity(slug="d", display_name="D", db_type="postgresql", host="h", port=5432,
                                       database_name="d", db_user="u", db_password="p"))
    return r


@pytest.fixture
def measuring(monkeypatch):
    box = {"fact": _fact()}
    from resource_explorer import facts
    monkeypatch.setattr(facts.FactLayer, "fact", lambda self, slug, aid, level="resource": box["fact"])
    return box


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.web.routes.context.get_current_user", lambda request: {"user_id": "dan"})
    from resource_explorer.web.app import app
    return TestClient(app)


def _put(client, entity, slug, key, value, kind="observation", **extra):
    r = client.patch(f"/api/context/{entity}/{slug}/field", json={"key": key, "value": value, "kind": kind, **extra})
    assert r.status_code == 200, r.text
    return r.json()["field"]


def test_repo_licence_write_is_stamped_with_the_measurement(client, measuring):
    f = _put(client, "repo", "p", "licence", "Apache-2.0", source="user")
    assert f["measured_value"] == "MIT License"
    assert f["measured_at"] == "2026-09-30T10:00:00"
    assert f["value"] == "Apache-2.0"     # the person's value is untouched


def test_the_client_cannot_assert_a_measurement(client, measuring):
    f = _put(client, "repo", "p", "licence", "X", measured_value="forged", measured_at="forged")
    assert f["measured_value"] == "MIT License"


def test_a_rechoice_restamps_the_current_measurement(client, measuring):
    _put(client, "repo", "p", "licence", "Apache-2.0")
    measuring["fact"] = _fact("GPL-3.0 — Copyleft", at="2026-09-30T12:00:00")
    f = _put(client, "repo", "p", "licence", "Apache-2.0")          # "keep mine"
    assert (f["measured_value"], f["measured_at"]) == ("GPL-3.0", "2026-09-30T12:00:00")


def test_a_database_has_nothing_to_stamp(client, measuring):
    f = _put(client, "database", "d", "licence", "internal use only")
    assert f["measured_value"] == "" and f["measured_at"] == ""


def test_judgements_are_never_stamped(client, measuring):
    f = _put(client, "repo", "p", "sensitivity", "internal", kind="judgement")
    assert f["measured_value"] == ""


def test_other_observations_have_no_proposing_pair(client, measuring):
    assert _put(client, "repo", "p", "environment", "prod")["measured_value"] == ""
    assert measured_for(None, "repo", "p", "retention") == ("", "")


def test_an_unreadable_measurement_never_blocks_the_save(client, monkeypatch):
    from resource_explorer import facts
    monkeypatch.setattr(facts.FactLayer, "fact", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _put(client, "repo", "p", "licence", "MIT")["measured_value"] == ""
