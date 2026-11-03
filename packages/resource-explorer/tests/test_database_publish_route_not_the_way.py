"""The database Publish route says it is not the way to publish (Curate slice B; Classic is gone).

BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md, the owner's gate item 6: "Classic's database Publish
is gone". The button and its modal are removed from the Classic page and replaced by a pointer
that claims no state either way; the REST route stays only for the survey-definition retry, and
its docstring says it is not the way to publish (it starts an UNSCOPED native survey).
"""
from __future__ import annotations

from pathlib import Path


def test_the_route_documents_that_it_is_not_the_way_to_publish():
    src = (Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "routes" / "databases.py").read_text()
    assert "Retired from the UI (Curate slice B" in src and "UNSCOPED" in src
