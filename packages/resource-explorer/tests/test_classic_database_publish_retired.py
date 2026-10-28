"""Classic's database Publish retires into the Curate Catalogue commit (slice B).

BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md, the owner's gate item 6: "Classic's database Publish
is gone". The button and its modal are removed from the Classic page and replaced by a pointer
that claims no state either way; the REST route stays only for the survey-definition retry, and
its docstring says it is not the way to publish (it starts an UNSCOPED native survey).
"""
from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"
HTML = (STATIC / "index.html").read_text()


def test_the_classic_publish_modal_and_button_are_gone():
    for gone in ("showPublishDbModal", "hidePublishDbModal", "submitPublishDb", "publish-db-modal",
                 "db-publish-btn", "Catalog &amp; Survey in Egeria", "Re-survey in Egeria"):
        assert gone not in HTML, gone


def test_classic_points_at_curate_and_claims_no_state():
    assert "Publish: Curate → Catalog in /next" in HTML
    # the survey-definition retry still has its own route; only the database Publish UI retired
    assert "catalogAndRetrySurveyDefinition" in HTML


def test_the_route_documents_that_it_is_not_the_way_to_publish():
    src = (Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "routes" / "databases.py").read_text()
    assert "Retired from the UI (Curate slice B" in src and "UNSCOPED" in src
