"""The log viewer, and the three empties it must not conflate.

An empty log list has at least three causes — nothing logged, the buffer emptied
by a restart, a filter that excluded everything — and rendering them alike is the
absence-as-answer failure this codebase keeps finding (see `null_pct: 0.0`, and
`cii_badge`'s unreachable-vs-not-registered). The buffer being in-memory makes
the second cause routine rather than theoretical, so it is asserted here.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from resource_explorer.observability import logging_setup as ls
from resource_explorer.web.app import app


@pytest.fixture
def client():
    ls.configure_logging(force=True)
    ls.ring_handler.clear()
    return TestClient(app)


def test_records_reach_the_route(client):
    logging.getLogger("resource_explorer.viewer_test").warning("visible in the viewer")
    body = client.get("/api/logs/").json()
    msgs = [r["message"] for r in body["records"]]
    assert "visible in the viewer" in msgs, msgs[:5]


def test_the_response_says_enough_to_read_an_empty_list_correctly(client):
    """Records alone cannot distinguish the three empties. The metadata can."""
    body = client.get("/api/logs/").json()
    assert body["records"] == []
    buf = body["buffer"]
    assert buf["held"] == 0
    assert buf["durable"] is False, (
        "a viewer that does not know the buffer is volatile will report "
        "'no logs' after every restart as though it were a finding"
    )
    assert buf["capacity"] == ls.RING_CAPACITY
    assert body["filtered"] is False


def test_filtered_empty_is_distinguishable_from_genuinely_empty(client):
    logging.getLogger("resource_explorer.viewer_test").info("something happened")

    filtered = client.get("/api/logs/?level=CRITICAL").json()
    assert filtered["records"] == []
    assert filtered["filtered"] is True
    assert filtered["buffer"]["held"] > 0, (
        "held must report the whole buffer, not the filtered view — otherwise "
        "a filter that matches nothing looks like an empty buffer"
    )


def test_levels_are_not_derived_from_what_happens_to_be_buffered(client):
    """Offering only the levels present would hide ERROR from the filter exactly
    when no error has occurred — which is when someone goes looking for it."""
    body = client.get("/api/logs/").json()
    assert body["levels"] == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def test_an_unknown_level_is_rejected_rather_than_silently_matching_nothing(client):
    res = client.get("/api/logs/?level=WARN")  # a plausible typo for WARNING
    assert res.status_code == 400, (
        "an unrecognised level returned 200 with an empty list, which reads as "
        "'no warnings' rather than 'that is not a level'"
    )


def test_limit_cannot_exceed_the_buffer(client):
    for i in range(20):
        logging.getLogger("resource_explorer.viewer_test").info("line %d", i)
    body = client.get(f"/api/logs/?limit={ls.RING_CAPACITY * 10}").json()
    assert len(body["records"]) <= ls.RING_CAPACITY


def test_newest_first(client):
    log = logging.getLogger("resource_explorer.viewer_test")
    log.info("older")
    log.info("newer")
    msgs = [r["message"] for r in client.get("/api/logs/").json()["records"]]
    assert msgs.index("newer") < msgs.index("older")


def test_a_traceback_survives_into_the_record(client):
    """The thing a log viewer exists for. A one-line "it failed" is what the
    activity log already gives you."""
    try:
        raise ValueError("the actual cause")
    except ValueError:
        logging.getLogger("resource_explorer.viewer_test").exception("step failed")

    rec = client.get("/api/logs/").json()["records"][0]
    assert rec["exc"], "no traceback captured — the record is just the message"
    assert "Traceback" in rec["exc"]
    assert "the actual cause" in rec["exc"], (
        "the traceback was captured but not the exception that caused it"
    )
    assert len(rec["exc"].splitlines()) > 1, "traceback collapsed to one line"


def test_stack_info_is_captured_as_well_as_exc_info(client):
    """Different fields, and only exc_info was read until 2026-08-31.

    `log.warning(..., stack_info=True)` printed a stack to the console and
    reached the viewer as a bare one-line message — the opposite of why anyone
    passes stack_info.
    """
    logging.getLogger("resource_explorer.viewer_test").warning("where am I", stack_info=True)
    rec = client.get("/api/logs/").json()["records"][0]
    assert rec["exc"], "stack_info was dropped on the way into the buffer"
    assert "Stack" in rec["exc"]

