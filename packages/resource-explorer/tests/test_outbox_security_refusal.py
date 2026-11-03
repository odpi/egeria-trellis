"""test_outbox_security_refusal (2026-10-07 reliability batch). Fake clients/exceptions only; temp SQLite."""
from __future__ import annotations

import os

import pytest

import resource_explorer.egeria_outbox as outbox
import resource_explorer.run_queue as rq
from resource_explorer.activity_logger import log_survey
from resource_explorer.curate_plan import Curations
from resource_explorer.registry import Project, ProjectRegistry

# Brief I: these tests drive the drain mechanics, as the background loop does: a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "rel.db"))
    r.add(Project(slug="myproj", display_name="My Project",
                  github_url="https://github.com/test/myproj", description="A test repo."))
    return r


def _open_activity(reg, summary="working…"):
    return log_survey(reg, entity_type="repo", entity_slug="myproj", entity_name="My Project",
                      entity_location="", intent="curate", status="running", summary=summary)


def _run(reg, monkeypatch, kind, target, handler, activity_id):
    reg.enqueue_run(kind, target, result_ref=activity_id)
    row = reg.claim_next_run("host:1", {"pid": os.getpid()})
    monkeypatch.setitem(rq.HANDLERS, kind, handler)
    return rq.execute_run(row, reg)



# ── 2. security refusal is not retried ──────────────────────────────────────────

class _Coded(Exception):
    def __init__(self, text, code=None):
        super().__init__(text)
        self.related_http_code = code


SEC = ("OPEN-METADATA-SECURITY-403-007 User erinoverview is not authorized to issue operation Attach on "
       "SolutionBlueprint anchor element abc-123. Contact your administrator for more information")


class _Mgr:
    def __init__(self, exc):
        self.exc = exc

    def add_to_collection(self, *a, **k):
        raise self.exc


def _drain_with(reg, exc, kind="collection_membership"):
    reg.enqueue_outbox_element("repo", "myproj", kind, "CollectionMembership::b::m",
                               {"collection_guid": "b", "member_guid": "m"})
    clients = outbox.OutboxClients(discovery=None, metadata_expert=None, collection_manager=_Mgr(exc),
                                   acting_as="svc")
    summary = outbox.drain_outbox(reg, clients, lambda qn: "")
    return summary, reg.list_outbox_elements()[0]


class TestSecurityRefusal:
    def test_security_403_message_is_dead_on_first_failure(self, reg):
        summary, row = _drain_with(reg, _Coded(SEC))
        assert row["status"] == "dead" and row["attempts"] == 1
        assert row["last_error"] == (
            "refused by Egeria's security: OPEN-METADATA-SECURITY-403-007 User erinoverview is not authorized "
            "to issue operation Attach on SolutionBlueprint anchor element abc-123 · "
            "needs a permission or zone change, not a retry")
        assert summary["dead"] == 1 and summary["security_refused"] == 1

    def test_http_403_is_dead_on_first_failure(self, reg):
        _, row = _drain_with(reg, _Coded("User not authorized received for user - ``.", 403))
        assert row["status"] == "dead"
        assert row["last_error"].startswith("refused by Egeria's security: User not authorized")

    @pytest.mark.parametrize("exc", [
        _Coded("bare token problem", 401),
        _Coded("boom", 500),
        _Coded("OMAG-COMMON-503 unavailable", 503),
        TimeoutError("timed out"),
        ConnectionError("connection reset"),
    ])
    def test_everything_else_still_retries(self, reg, exc):
        summary, row = _drain_with(reg, exc)
        assert row["status"] == "failed" and row["attempts"] == 1
        assert row["next_attempt_at"]
        assert not summary.get("security_refused")

    def test_destructive_kinds_keep_their_own_never_retry_wording(self, reg, monkeypatch):
        def refuse(row, clients, find, **kw):
            raise _Coded(SEC)
        monkeypatch.setattr(outbox, "apply_element", refuse)
        _, row = _drain_with(reg, _Coded(SEC), kind="doc_source_unpublish")
        assert row["status"] == "dead"
        assert row["last_error"].startswith(outbox.NOT_RETRIED)

    def test_classifier(self):
        assert outbox.is_security_refusal(_Coded(SEC))
        assert outbox.is_security_refusal(_Coded("x", 403))
        assert not outbox.is_security_refusal(_Coded("x", 401))
        assert not outbox.is_security_refusal(RuntimeError("x"))

