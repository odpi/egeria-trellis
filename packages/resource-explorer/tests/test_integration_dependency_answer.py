"""A question that names a resource and one of ITS declared dependencies is answered from the stored dependency list.

"Is there a relationship between data-prep-kit and docling-core?", asked on data_prep_kit: docling-core is not a
registered resource, it is a dependency data_prep_kit declares (runtime, python, ==2.50.1, in requirements.txt and two
transform requirement files). The integration agent asked for a second project and the route added resource chips;
now it says which manifests declare it, the version and the dependency type, and the done event says what the
answer was read from.

Real temp-sqlite registry and the real route, ConversationAgent and IntegrationAgent. The model is never reached:
`_run_agent` and `_run_persistent` raise if called.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from resource_explorer.agents.base import BaseExplorerAgent
from resource_explorer.agents.integration_agent import IntegrationAgent
from resource_explorer.ingestion.dependency_parser import DependencyParser
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.web.routes import query as query_routes

QUESTION = "Is there a relationship between data-prep-kit and docling-core?"
FILES = ["requirements.txt", "transforms/language/doc_chunk/requirements.txt",
         "transforms/language/docling2parquet/requirements.txt"]


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    r.add(Project(slug="data_prep_kit", display_name="data-prep-kit", github_url="https://x/data-prep-kit"))
    r.add(Project(slug="egeria", display_name="Egeria", github_url="https://x/egeria"))
    # As stored before the parser dropped inline comments: the version carries the requirements.txt comment.
    r.upsert_dependencies("data_prep_kit", [
        {"dep_name": "docling-core", "dep_version": "==2.50.1    # remove potential conflicts with docling",
         "dep_type": "runtime", "ecosystem": "python", "source_file": f} for f in FILES
    ] + [{"dep_name": "pandas", "dep_version": ">=2", "dep_type": "runtime", "ecosystem": "python",
          "source_file": "requirements.txt"}])
    with patch("resource_explorer.registry.ProjectRegistry", return_value=r):
        yield r


def _no_model(*a, **k):
    raise AssertionError("the model was asked; the stored dependency list answers this")


def test_the_parser_drops_a_requirements_inline_comment(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("docling-core==2.50.1    # remove potential conflicts with docling\npandas>=2\n")
    rows = DependencyParser()._parse_requirements(req)
    assert [(d["dep_name"], d["dep_version"]) for d in rows] == [("docling-core", "==2.50.1"), ("pandas", ">=2")]


def test_a_stored_version_is_read_without_its_comment(reg):
    versions = {d["dep_version"] for d in reg.query_dependencies("data_prep_kit") if d["dep_name"] == "docling-core"}
    assert versions == {"==2.50.1"}


def test_the_integration_agent_answers_from_the_dependency_list(reg):
    agent = IntegrationAgent()
    with patch.object(IntegrationAgent, "_run_agent", _no_model):
        answer = agent.handle(QUESTION, resource_slug="data_prep_kit")
    assert answer.splitlines()[0] == (
        "Yes. data_prep_kit declares docling-core as a direct dependency, in 3 manifests (from its stored "
        "dependency list):")
    assert f"- runtime python, version ==2.50.1: {', '.join(sorted(FILES))}" in answer
    assert "remove potential conflicts" not in answer and "pandas" not in answer
    assert "transitively is not known" in answer
    assert "Please name at least two" not in answer and "not a registered resource" not in answer
    assert agent._answered_from == {"kind": "stored_dependencies", "resource_slug": "data_prep_kit",
                                    "analysis_id": "dependency_analysis", "dependencies": ["docling-core"]}


def test_a_second_name_that_is_neither_a_resource_nor_a_dependency_still_asks(reg):
    agent = IntegrationAgent()
    answer = agent.handle("Is there a relationship between data-prep-kit and kubeflow?", resource_slug="data_prep_kit")
    assert answer.startswith("Please name at least two projects")
    assert agent._answered_from is None


def test_the_route_says_what_it_was_read_from_and_offers_no_resource_chips(reg):
    app = FastAPI()
    app.include_router(query_routes.router, prefix="/api/query")
    with patch.object(IntegrationAgent, "_run_agent", _no_model), \
         patch.object(BaseExplorerAgent, "_run_persistent", _no_model, create=True):
        r = TestClient(app).post("/api/query/stream", json={"query": QUESTION, "project_slug": "data_prep_kit"})
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.split("\n\n") if line.startswith("data: ")]
    done = next(e for e in events if e["t"] == "done")
    text = "".join(e["v"] for e in events if e["t"] == "chunk")
    assert done["intent"] == "integration"
    assert "resource_question" not in done and not done.get("held")
    assert "disambiguation_chips" not in done
    assert done["answered_from"]["resource_slug"] == "data_prep_kit"
    assert done["answered_from"]["dependencies"] == ["docling-core"]
    assert "docling-core as a direct dependency" in text and "==2.50.1" in text
