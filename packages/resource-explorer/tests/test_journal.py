"""The journal — resource_explorer/journal.py.

Append-only prose with an author and a date; suggestion routes to a
work list per audience, never a notification. These drive the module on a
fresh registry and the route with a faked identity.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.journal import Journal, audience_slug
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.work_lists import WorkLists


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    for s in ("p", "q"):
        r.add(Project(slug=s, display_name=s.upper(), github_url=f"https://github.com/x/{s}", description=""))
    return r


class TestAppendOnly:
    def test_entries_accumulate_newest_first_and_nothing_is_overwritten(self, registry):
        j = Journal(registry)
        j.write("repo", "p", author="a", body="Picked up during scouting.")
        j.write("repo", "p", author="a", body="Dependency count moved 57 → 68.")
        entries = j.entries("repo", "p")
        assert [e["body"] for e in entries] == ["Dependency count moved 57 → 68.", "Picked up during scouting."]
        assert all(e["author"] == "a" and e["written_at"].startswith("2026") for e in entries)

    def test_an_entry_needs_an_author_and_a_body(self, registry):
        j = Journal(registry)
        with pytest.raises(ValueError):
            j.write("repo", "p", author="", body="x")
        with pytest.raises(ValueError):
            j.write("repo", "p", author="a", body="   ")

    def test_entries_are_per_resource(self, registry):
        j = Journal(registry)
        j.write("repo", "p", author="a", body="about p")
        assert j.entries("repo", "q") == []


class TestSuggestionIsAWorkListEntry:
    def test_a_suggestion_lands_in_a_list_named_for_its_audience(self, registry):
        j = Journal(registry)
        out = j.write("repo", "p", author="peterprofile",
                      body="Worth using for anyone building against the Egeria API from Python.",
                      suggest_to=["Data Expert", "App/AI Builder"])
        assert [w["work_list"] for w in out["work_lists"]] == ["suggested-to-data-expert", "suggested-to-app-ai-builder"]
        # The NAME travels back too: the UI says where it landed, and a
        # machine slug is not where it landed (review, 2026-09-12).
        assert [w["name"] for w in out["work_lists"]] == ["Suggested to Data Expert", "Suggested to App/AI Builder"]
        wl = WorkLists(registry).get("suggested-to-data-expert")
        assert wl["display_name"] == "Suggested to Data Expert"
        assert wl["derived_from"] == "journal" and wl["created_by"] == "peterprofile"
        member = next(m for m in wl["members"] if m["entity_slug"] == "p")
        assert member["rationale"].startswith("Worth using")

    def test_a_second_suggestion_to_the_same_audience_updates_not_duplicates(self, registry):
        j = Journal(registry)
        j.write("repo", "p", author="a", body="first note", suggest_to=["Consumer"])
        second = j.write("repo", "p", author="b", body="second note, better", suggest_to=["Consumer"])
        assert second["work_lists"][0]["name"] == "Suggested to Consumer"   # the existing list's name, second time round
        j.write("repo", "q", author="b", body="a different resource", suggest_to=["Consumer"])
        wl = WorkLists(registry).get("suggested-to-consumer")
        members = {m["entity_slug"]: m["rationale"] for m in wl["members"]}
        assert members == {"p": "second note, better", "q": "a different resource"}
        # and both notes are still in p's journal -- the list holds the
        # latest rationale, the journal holds every entry
        assert len(j.entries("repo", "p")) == 2
        assert j.suggested_targets("repo", "p") == ["Consumer"]

    def test_audience_slugs(self):
        assert audience_slug("Data Expert") == "suggested-to-data-expert"
        assert audience_slug("App/AI Builder") == "suggested-to-app-ai-builder"
        assert audience_slug("peterprofile") == "suggested-to-peterprofile"


class TestRoute:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
        )
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_author_is_the_signed_in_user_and_anonymous_is_refused(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.journal.get_current_user", lambda request: None)
        assert client.post("/api/journal/repo/p", json={"body": "x"}).status_code == 401
        monkeypatch.setattr("resource_explorer.web.routes.journal.get_current_user", lambda request: {"user_id": "peterprofile"})
        r = client.post("/api/journal/repo/p", json={"body": "Note the sole-contributor risk.", "suggest_to": ["Steward"]})
        assert r.status_code == 200, r.text
        assert r.json()["author"] == "peterprofile"
        assert r.json()["work_lists"] == [{"target": "Steward", "work_list": "suggested-to-steward", "name": "Suggested to Steward"}]
        listing = client.get("/api/journal/repo/p").json()
        assert len(listing["entries"]) == 1 and listing["suggested_to"] == ["Steward"]


class TestTheAudienceIsTheWholeVocabulary:
    client = TestRoute.client   # the same app, the same anonymous read

    """`list_perspectives()` is a deliberate subset -- what can narrow a
    filter row. An audience is not a filter: someone is a Data Owner whether
    or not any analysis is tagged with it today. `scope=all` returns the
    vocabulary; the default stays the subset."""

    def test_scope_all_returns_every_egeria_perspective(self, client):
        from resource_explorer.surveyors.analysis_catalog_reader import EGERIA_PERSPECTIVES, list_perspectives
        assert client.get("/api/analyses/perspectives?scope=all").json() == list(EGERIA_PERSPECTIVES)
        assert client.get("/api/analyses/perspectives").json() == list_perspectives()


class TestInvestigationJournal:
    """A chat answer saved to an investigation: the same journal, keyed `investigation`, local until promoted."""

    @pytest.fixture
    def client(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
        )
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        monkeypatch.setattr("resource_explorer.web.routes.journal.get_current_user",
                            lambda request: {"user_id": "peterprofile"})
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_an_entry_lands_in_the_investigations_journal(self, client, registry):
        inv = registry.create_investigation("Lineage study")
        r = client.post(f"/api/journal/investigation/{inv['slug']}", json={"body": "Per chat: marquez emits OpenLineage."})
        assert r.status_code == 200, r.text
        assert r.json()["author"] == "peterprofile"
        listing = client.get(f"/api/journal/investigation/{inv['slug']}").json()
        assert [e["body"] for e in listing["entries"]] == ["Per chat: marquez emits OpenLineage."]

    def test_the_compile_id_survives_save_and_read_back_and_a_missing_one_is_null(self, client, registry):
        inv = registry.create_investigation("Compile study")
        url = f"/api/journal/investigation/{inv['slug']}"
        assert client.post(url, json={"body": "with", "compile_id": "ab12cd34ef56"}).json()["compile_id"] == "ab12cd34ef56"
        assert client.post(url, json={"body": "without"}).json()["compile_id"] is None
        got = {e["body"]: e["compile_id"] for e in client.get(url).json()["entries"]}
        assert got == {"with": "ab12cd34ef56", "without": None}

    def test_an_unknown_investigation_is_a_404_not_a_new_journal(self, client):
        assert client.post("/api/journal/investigation/nope", json={"body": "x"}).status_code == 404
        assert client.get("/api/journal/investigation/nope").status_code == 404

    def test_an_investigation_entry_cannot_be_suggested_to_an_audience(self, client, registry):
        inv = registry.create_investigation("Lineage study 2")
        r = client.post(f"/api/journal/investigation/{inv['slug']}", json={"body": "x", "suggest_to": ["Steward"]})
        assert r.status_code == 422
        assert client.get(f"/api/journal/investigation/{inv['slug']}").json()["entries"] == []
