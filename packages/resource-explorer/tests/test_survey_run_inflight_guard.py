"""TR-1: one Survey Definition run per (definition, resource) at a time.

The 2026-11-14 hedwig incident: a resent console request POSTed the run route about 4/s for 96 s and queued 382
RepoAnalysisSurvey runs on one repository. The ruling (RULING-RE-RUN-INFLIGHT-GUARD-2026-11-13): a second request
while one is in flight is refused with 409 naming that run; a retry carrying the same Idempotency-Key gets the
same run; a stale claimed row does not block; more than N run POSTs a minute from one user is 429.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry

URL = "/api/survey-definitions/repo/myproj/run"
BODY = {"survey_definition_ref": "RepoFullSurvey"}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    r = ProjectRegistry(db_path=str(tmp_path / "guard.db"))
    r.add(Project(slug="myproj", display_name="My Project",
                  github_url="https://github.com/test/myproj", description="A test repo."))

    def _factory(*a, **k):
        return r

    _factory.RUN_STATES = ProjectRegistry.RUN_STATES
    _factory.RUN_KINDS = ProjectRegistry.RUN_KINDS
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", _factory)
    return r


@pytest.fixture
def client(registry):
    from resource_explorer.web.app import app

    with patch("resource_explorer.workflows.survey_definition.run_definition"):
        yield TestClient(app)


def _queued(registry):
    return [r for r in registry.list_runs(kind="survey_definition_run", limit=1000)
            if r["state"] in ("queued", "claimed", "running")]


def _running_entries(registry):
    return [e for e in registry.list_activity(limit=1000) if e.get("status") == "running"] \
        if hasattr(registry, "list_activity") else []


class TestDuplicateRefused:
    def test_a_second_post_while_one_is_queued_is_a_409_naming_it(self, client, registry):
        first = client.post(URL, json=BODY)
        assert first.status_code == 200, first.text
        second = client.post(URL, json=BODY)
        assert second.status_code == 409, second.text
        body = second.json()
        assert body["error"] == "run_in_flight"
        assert body["run"]["run_id"] == first.json()["run_id"]
        assert body["run"]["activity_id"] == first.json()["activity_id"]
        assert "already queued on myproj" in body["detail"]
        assert "Wait for it, or cancel it first." in body["detail"]
        assert len(_queued(registry)) == 1

    def test_a_refused_post_leaves_no_running_activity_entry(self, client, registry):
        """The activity row is opened only when the run is queued: a refusal must not make the TR-2 phantom."""
        first = client.post(URL, json=BODY).json()
        before = {e["id"] for e in _running_entries(registry)}
        client.post(URL, json=BODY)
        after = {e["id"] for e in _running_entries(registry)}
        assert after == before
        assert first["activity_id"] in after or not hasattr(registry, "list_activity")

    def test_different_parameters_on_the_same_key_are_still_refused(self, client, registry):
        assert client.post(URL, json=BODY).status_code == 200
        resp = client.post(URL, json={**BODY, "force_custom": True, "engine": "resource-explorer"})
        assert resp.status_code == 409

    def test_another_definition_or_resource_is_not_blocked(self, client, registry):
        registry.add(Project(slug="other", display_name="Other",
                             github_url="https://github.com/test/other", description="x"))
        assert client.post(URL, json=BODY).status_code == 200
        assert client.post(URL, json={"survey_definition_ref": "RepoAnalysisSurvey"}).status_code == 200
        assert client.post("/api/survey-definitions/repo/other/run", json=BODY).status_code == 200
        assert len(_queued(registry)) == 3

    def test_a_finished_run_does_not_block_the_next(self, client, registry):
        first = client.post(URL, json=BODY).json()
        assert registry.cancel_queued_run(first["run_id"]) is True
        assert client.post(URL, json=BODY).status_code == 200


class TestIdempotencyKey:
    def test_a_retry_with_the_same_key_gets_the_same_run(self, client, registry):
        first = client.post(URL, json=BODY, headers={"Idempotency-Key": "press-1"})
        retry = client.post(URL, json=BODY, headers={"Idempotency-Key": "press-1"})
        assert retry.status_code == 200, retry.text
        assert retry.json()["run_id"] == first.json()["run_id"]
        assert retry.json()["activity_id"] == first.json()["activity_id"]
        assert retry.json()["coalesced"] is True
        assert len(_queued(registry)) == 1

    def test_a_different_key_is_a_new_press_and_is_refused(self, client, registry):
        client.post(URL, json=BODY, headers={"Idempotency-Key": "press-1"})
        resp = client.post(URL, json=BODY, headers={"Idempotency-Key": "press-2"})
        assert resp.status_code == 409

    def test_no_key_is_refused(self, client, registry):
        client.post(URL, json=BODY, headers={"Idempotency-Key": "press-1"})
        assert client.post(URL, json=BODY).status_code == 409


class TestStale:
    def test_a_claimed_row_with_no_sign_of_life_does_not_block(self, client, registry):
        first = client.post(URL, json=BODY).json()
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        registry.claim_next_run("dead-worker:1", now=long_ago)
        assert registry.get_run(first["run_id"])["state"] == "claimed"
        resp = client.post(URL, json=BODY)
        assert resp.status_code == 200, resp.text
        assert resp.json()["run_id"] != first["run_id"]

    def test_a_claimed_row_with_a_recent_heartbeat_blocks(self, client, registry):
        client.post(URL, json=BODY)
        registry.claim_next_run("live-worker:1")
        assert client.post(URL, json=BODY).status_code == 409


class TestAtomic:
    def test_concurrent_enqueues_make_one_run(self, registry):
        """Many callers at once, the same key: exactly one row. (SQLite: the in-process lock; Postgres: the advisory
        lock, which the requires_pgvector tier exercises.)"""
        from resource_explorer.web.routes.survey_definitions import (
            RunAlreadyInFlight,
            enqueue_definition_run,
        )
        from resource_explorer.workflows.survey_definition import SurveyDefinitionRunParams

        made, refused, errors = [], [], []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            try:
                made.append(enqueue_definition_run(
                    registry, "repo", "myproj", SurveyDefinitionRunParams(survey_definition_ref="RepoFullSurvey")))
            except RunAlreadyInFlight:
                refused.append(1)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert len(made) == 1 and len(refused) == 7
        assert len(_queued(registry)) == 1


class TestRateBackstop:
    def test_more_than_n_posts_a_minute_is_a_429(self, client, registry):
        from resource_explorer.web.routes import survey_definitions as sd

        codes = [client.post(URL, json={"survey_definition_ref": f"Def{i}"}).status_code
                 for i in range(sd.RUN_POSTS_PER_MINUTE + 1)]
        assert codes[:sd.RUN_POSTS_PER_MINUTE] == [200] * sd.RUN_POSTS_PER_MINUTE
        assert codes[-1] == 429
        assert len(_queued(registry)) == sd.RUN_POSTS_PER_MINUTE

    def test_the_window_slides(self):
        from resource_explorer.web.routes import survey_definitions as sd

        for i in range(sd.RUN_POSTS_PER_MINUTE):
            assert sd._over_rate("u", now=1000.0 + i) is False
        assert sd._over_rate("u", now=1000.0 + sd.RUN_POSTS_PER_MINUTE) is True
        assert sd._over_rate("u", now=1100.0) is False


class TestQualityBatch:
    def test_an_in_flight_member_is_said_so_not_failed(self, registry, monkeypatch):
        from resource_explorer import quality_batch
        from resource_explorer.web.routes.survey_definitions import RunAlreadyInFlight

        def refuse(*a, **k):
            raise RunAlreadyInFlight({"id": "run-1", "state": "queued"}, "'Q' is already queued on db1 (…).")

        monkeypatch.setattr("resource_explorer.web.routes.survey_definitions.enqueue_definition_run", refuse)
        monkeypatch.setattr(quality_batch, "price_row", lambda *a, **k: {
            "display_name": "db1", "can_run": True, "words": "", "change": {"unchanged": False, "words": ""}})
        result = quality_batch.enqueue_quality_batch(registry, ["db1"], definition="Q")
        member = result["members"][0]
        assert member["outcome"] == quality_batch.ALREADY_RUNNING
        assert member["run_id"] == "run-1"
        assert "already running" in result["summary"]


def test_the_in_flight_words_read_as_the_ruling_wrote_them():
    from resource_explorer.web.routes.survey_definitions import in_flight_words

    words = in_flight_words({"id": "r1", "state": "queued", "requested_by": "peterprofile",
                             "enqueued_at": "2026-11-14T12:03:09+00:00",
                             "target_decoded": {"params": {"survey_definition_ref": "RepoAnalysisSurvey"}}},
                            "nobel_network")
    assert words == ("'RepoAnalysisSurvey' is already queued on nobel_network (queued 12:03Z by peterprofile; "
                     "run r1). Wait for it, or cancel it first. To cancel it: POST /api/runs/r1/cancel, or "
                     "`resource-explorer runs cancel r1`.")
    running = in_flight_words({"id": "r2", "state": "running", "claimed_at": "2026-11-14T12:05:00+00:00",
                               "target_decoded": {"params": {"survey_definition_ref": "X"}}}, "y")
    assert "To cancel it" not in running and running.endswith("Wait for it, or cancel it first.")


def test_a_failed_insert_closes_the_activity_entry_it_opened(tmp_path, monkeypatch):
    """Read-back advisory b: the entry is opened under the lock just before the INSERT; a failed INSERT must not
    leave it 'running' for ever."""
    from resource_explorer.activity_logger import log_scout

    reg = ProjectRegistry(db_path=str(tmp_path / "fail.db"))
    opened = []

    def before():
        opened.append(log_scout(reg, "repo", "a", "A", "", "running", "queued…"))
        return opened[-1]

    with pytest.raises(Exception):
        reg.enqueue_run_once("survey_definition_run", {"not": "serialisable", "x": object()}, lock_key="k",
                             same=lambda t: False, stale_before="", before_insert=before)
    assert reg.get_activity(opened[0])["status"] == "error"
