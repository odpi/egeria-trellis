"""PI-098: the Next Context tab's steward, location, backup status and notes save through the one
enrichment route and mirror to the flat keys Classic's context form reads and writes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    return r


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


@pytest.mark.parametrize("key,kind,value,flat", [
    ("steward", "judgement", "alice@example.com", "responsible_steward"),
    ("notes", "judgement", "restored from backup in 2025", "notes"),
    ("location", "observation", "eu-west-1", "geographic_location"),
    ("backup_status", "observation", "partial", "backup_status"),
])
def test_each_field_is_signed_and_mirrored_to_classics_flat_key(client, registry, key, kind, value, flat):
    r = client.patch("/api/context/repo/p/field", json={"key": key, "value": value, "kind": kind})
    assert r.status_code == 200, r.text
    assert r.json()["field"]["author"] == "dan"
    ctx = registry.get_context("repo", "p")
    assert ctx["enrichment"][key]["value"] == value
    assert ctx[flat] == value


def test_a_classic_save_after_a_next_save_keeps_the_enrichment_record(client, registry):
    client.patch("/api/context/repo/p/field", json={"key": "steward", "value": "alice", "kind": "judgement"})
    r = client.post("/api/context/repo/p", json={"responsible_steward": "bob"})
    assert r.status_code == 200
    assert registry.get_context("repo", "p")["enrichment"]["steward"]["value"] == "alice"


def test_backup_status_outside_classics_list_is_refused(client, registry):
    r = client.patch("/api/context/repo/p/field", json={"key": "backup_status", "value": "sometimes", "kind": "observation"})
    assert r.status_code == 422
    assert "backup_status" not in (registry.get_context("repo", "p") or {}).get("enrichment", {})
