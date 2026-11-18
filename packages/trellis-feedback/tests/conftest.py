"""Every store test runs on SQLite, and on Postgres too when TRELLIS_FEEDBACK_TEST_PG_URL is set.

The Postgres run creates a schema with a random name and drops it afterwards, so it never touches
an app's real tables. Example (the shared dev instance):

    TRELLIS_FEEDBACK_TEST_PG_URL=postgresql://egeria_advisor:advisor@localhost:5442/egeria_advisor
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from trellis_feedback import FeedbackStore

PG_URL = os.environ.get("TRELLIS_FEEDBACK_TEST_PG_URL", "")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


@pytest.fixture(params=BACKENDS)
def make_store(request, tmp_path):
    """Returns a factory: make_store(app="re", external_sources=()) -> FeedbackStore, all on one
    database, so two apps' stores can be checked for isolation."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'feedback.db'}"
        schema = None
        cleanup = None
    else:
        url = PG_URL
        schema = f"tf_test_{uuid.uuid4().hex[:12]}"

        def cleanup():
            eng = create_engine(url)
            with eng.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            eng.dispose()

    made: list[FeedbackStore] = []

    def factory(app: str = "resource-explorer", external_sources=()) -> FeedbackStore:
        s = FeedbackStore(url, app, schema=schema, external_sources=external_sources)
        made.append(s)
        return s

    yield factory
    for s in made:
        s.engine.dispose()
    if cleanup:
        cleanup()


@pytest.fixture
def store(make_store):
    return make_store(external_sources=("resource_feedback",))
