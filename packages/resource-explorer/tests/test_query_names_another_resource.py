"""A question asked ON a resource that names a DIFFERENT one is held and the person is asked.

Owner ruling 2026-10-13: "it should be possible for a user to ask any question on any resource - so if we
aren't sure we should ask". Before this, the alias suggestion only fired for an unscoped question, and
the Next chat always sends a scope, so "did you mean ...?" could never appear and a question naming
repo B was silently answered about repo A.

Real temp-sqlite registry (two repos, one database, one file system); only the LLM boundary is faked
(the conversation agent), so a held turn can be shown to spend NO answer.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, FileSystemEntity, Project, ProjectRegistry
from resource_explorer.web.routes import query as query_routes


class FakeAgent:
    def __init__(self):
        self.calls: list[str] = []
        self.session_id = "s"

    def handle(self, q, **kw):
        self.calls.append(q)
        return "an answer about the scoped resource"


@pytest.fixture
def world(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    reg.add(Project(slug="alpha_repo", display_name="Alpha Repo", github_url="https://x/alpha"))
    reg.add(Project(slug="bravo_service", display_name="Bravo Service", github_url="https://x/bravo"))
    reg.register_database(DatabaseEntity(slug="orders_warehouse", display_name="Orders Warehouse",
                                          db_type="postgresql", host="h", port=1, database_name="o"))
    reg.register_filesystem(FileSystemEntity(slug="shared_drive", display_name="Shared Drive",
                                             local_mount_point="/tmp/shared_drive"))
    agent = FakeAgent()
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api/query")
    with patch("resource_explorer.registry.ProjectRegistry", return_value=reg), \
         patch.object(query_routes, "_get_or_create_session", return_value=agent), \
         patch.object(query_routes, "_persist_turn", lambda *a, **k: None), \
         patch.object(query_routes, "_compiled_payload", lambda a: None):
        yield TestClient(app), agent


def _ask(client, q, scope="alpha_repo", **extra):
    body = {"query": q, "project_slug": scope, "session_id": "s", **extra}
    r = client.post("/api/query/stream", json=body)
    assert r.status_code == 200
    events = [json.loads(l[6:]) for l in r.text.split("\n\n") if l.startswith("data: ")]
    return [e for e in events if e["t"] == "chunk"], [e for e in events if e["t"] == "done"][0]


def test_exact_other_repo_is_other_resource_and_held(world):
    client, agent = world
    chunks, done = _ask(client, "how does bravo service compare on test coverage")
    assert done["resource_question"] == {
        "kind": "other_resource", "named_slug": "bravo_service", "named_name": "Bravo Service",
        "named_type": "repo", "scoped_slug": "alpha_repo"}
    assert "alias_suggestion" not in done
    assert done["held"] is True
    assert agent.calls == [], "held: no LLM answer was spent on the wrong resource"
    assert "Bravo Service" in "".join(c["v"] for c in chunks)


def test_misspelled_other_repo_is_alias_suggestion(world):
    client, agent = world
    _, done = _ask(client, "what is the status of bravo_servise right now")
    assert done["alias_suggestion"]["candidate_slug"] == "bravo_service"
    assert done["alias_suggestion"]["candidate_type"] == "repo"
    assert done["alias_suggestion"]["scoped_slug"] == "alpha_repo"
    assert "resource_question" not in done
    assert agent.calls == []


def test_naming_the_database_is_other_resource_with_its_type(world):
    client, _ = world
    _, done = _ask(client, "which tables does orders warehouse have")
    rq = done["resource_question"]
    assert (rq["named_slug"], rq["named_type"]) == ("orders_warehouse", "database")


def test_naming_the_file_system_is_other_resource_with_its_type(world):
    client, _ = world
    _, done = _ask(client, "how big is the shared drive")
    assert done["resource_question"]["named_type"] == "filesystem"


def test_naming_the_scoped_resource_or_nothing_asks_nothing_and_answers(world):
    client, agent = world
    for q in ("what does alpha repo do", "is it maintained", "what is the status of alpha_repo"):
        _, done = _ask(client, q)
        assert "resource_question" not in done and "alias_suggestion" not in done
        assert not done.get("held")
    assert len(agent.calls) == 3


def test_a_misspelling_of_the_scoped_resource_does_not_suggest_itself(world):
    client, agent = world
    _, done = _ask(client, "what is the status of alpha_rpeo")
    assert "alias_suggestion" not in done and "resource_question" not in done
    assert len(agent.calls) == 1


def test_keep_scoped_skips_the_check_and_answers(world):
    client, agent = world
    _, done = _ask(client, "how does bravo service compare", skip_resource_check=True)
    assert "resource_question" not in done and not done.get("held")
    assert len(agent.calls) == 1


def test_unscoped_keeps_the_old_behaviour(world):
    client, agent = world
    # No scope: no hold, no resource_question; the old unscoped alias hint still comes with the answer.
    with patch.object(query_routes, "_get_or_create_session", return_value=agent):
        _, done = _ask(client, "what is the status of bravo_servise", scope=None)
    assert "resource_question" not in done and not done.get("held")
    assert done["alias_suggestion"]["candidate_slug"] == "bravo_service"
    assert "scoped_slug" not in done["alias_suggestion"]


def test_a_failed_check_is_reported_not_read_as_names_nothing(world, caplog):
    client, agent = world
    with patch.object(ProjectRegistry, "fuzzy_candidate",
               side_effect=RuntimeError("registry down")):
        _, done = _ask(client, "tell me about zzzzzz qqqqqq")
    assert "RuntimeError: registry down" in done["resource_check_error"]
    assert "resource-name check failed" in caplog.text
    assert len(agent.calls) == 1, "an unknowable check does not hold the answer"
