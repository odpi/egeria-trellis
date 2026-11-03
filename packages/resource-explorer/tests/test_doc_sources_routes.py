"""Route-level tests for /api/doc-sources — BRIEF-DATABASE-DOCUMENTATION-
SOURCES.md slice 1. Uses the same registry-sharing fixture pattern as
tests/test_databases_egeria_surveys.py (monkeypatch ProjectRegistry.__init__
to share one test registry's __dict__ across every ProjectRegistry() call
the routes make), and monkeypatches the probe/egeria functions so these run
with no network and no real Egeria platform.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from resource_explorer.doc_source_probe import ProbeResult
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.web.routes.doc_sources import derive_doc_source_egeria_state


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="zone2-tester")


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="adventureworks", display_name="AdventureWorks", db_type="postgresql",
        host="localhost", port=5432, database_name="adventureworks",
        egeria_url="https://egeria.example", egeria_server="view1",
        egeria_user="u", egeria_password="p",
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def _fake_probe(state="reachable", status_code=200):
    return ProbeResult(state=state, status_code=status_code, elapsed_ms=42,
                        title="A Page", byte_count=1234)


class TestAddAndProbe:
    def test_add_probes_immediately_and_returns_the_state(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe("reachable", 200))

        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "https://docs.example/dict", "label": "Data dict",
                                  "source_type": "data_dictionary"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["probe_state"] == "reachable"
        assert body["probe_status_code"] == 200
        assert body["probe_ms"] == 42
        assert body["label"] == "Data dict"

    def test_add_stamps_the_signed_in_user_as_added_by(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.get_current_user",
                             lambda request: {"user_id": "erinoverview"})
        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "https://docs.example/dict"})
        assert resp.status_code == 200
        assert resp.json()["added_by"] == "erinoverview"

    def test_add_without_identity_stays_unsigned(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.get_current_user",
                             lambda request: None)
        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "https://docs.example/dict"})
        assert resp.status_code == 200
        assert resp.json()["added_by"] == ""

    def test_add_rejects_a_non_http_url(self, client):
        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "not-a-url"})
        assert resp.status_code == 400

    def test_add_against_unknown_database_is_404(self, client):
        resp = client.post("/api/doc-sources/database/nope", json={"url": "https://x"})
        assert resp.status_code == 404

    @pytest.mark.parametrize("state,status", [
        ("reachable", 200), ("needs_sign_in", 401), ("not_found", 404), ("blocked", 503),
        ("timed_out", None), ("unreachable", None),
    ])
    def test_all_probe_states_pass_through(self, client, monkeypatch, state, status):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe(state, status))
        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": f"https://docs.example/{state}"})
        assert resp.json()["probe_state"] == state
        assert resp.json()["probe_status_code"] == status


class TestListAndPublishState:
    def test_local_only_when_not_published(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        client.post("/api/doc-sources/database/adventureworks", json={"url": "https://x"})

        resp = client.get("/api/doc-sources/database/adventureworks")

        assert resp.status_code == 200
        body = resp.json()
        assert body["published"] is False
        assert len(body["sources"]) == 1

    def test_published_true_once_asset_guid_is_set(self, client, monkeypatch, registry):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")

        resp = client.get("/api/doc-sources/database/adventureworks")

        assert resp.json()["published"] is True

    def test_read_back_folds_in_a_source_declared_only_in_egeria(self, client, monkeypatch, registry,
                                                                  allow_example_platform):
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.read_back_doc_sources",
            lambda *a, **kw: [{"ref_guid": "guid-x", "url": "https://declared-in-egeria.example",
                                "label": "From Egeria"}],
        )
        from resource_explorer.auth import create_access_token

        # Brief I: the read-back runs as the signed-in reader (their Egeria token), so sign in.
        token = create_access_token(user_id="dan", egeria_token="tok-dan")
        resp = client.get("/api/doc-sources/database/adventureworks",
                          headers={"Authorization": f"Bearer {token}"})

        body = resp.json()
        assert body["published"] is True
        urls = [s["url"] for s in body["sources"]]
        assert "https://declared-in-egeria.example" in urls
        matched = next(s for s in body["sources"] if s["url"] == "https://declared-in-egeria.example")
        assert matched["origin"] == "egeria"


class TestRecheck:
    def test_recheck_reruns_the_probe(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe("blocked", 500))
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()

        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe("reachable", 200))
        resp = client.post(f"/api/doc-sources/database/adventureworks/{added['id']}/recheck")

        assert resp.json()["probe_state"] == "reachable"

    def test_recheck_unknown_source_is_404(self, client):
        resp = client.post("/api/doc-sources/database/adventureworks/nope/recheck")
        assert resp.status_code == 404


class TestRemoval:
    def test_remove_deletes_the_local_row(self, client, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()

        resp = client.delete(f"/api/doc-sources/database/adventureworks/{added['id']}")

        assert resp.status_code == 200
        assert resp.json()["removed"] is True
        assert client.get("/api/doc-sources/database/adventureworks").json()["sources"] == []

    def test_remove_of_a_catalogued_source_queues_an_outbox_unpublish(
        self, client, monkeypatch, registry,
    ):
        # Egeria publish-state fix (2026-09-29): removal no longer calls
        # unpublish_doc_source synchronously (a one-shot best-effort attempt
        # with no retry if Egeria happened to be unreachable at that exact
        # moment) — it queues a doc_source_unpublish row through the SAME
        # outbox the publish side uses, so a transient failure gets retried.
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()
        registry.set_doc_source_egeria_ref("database", "adventureworks", added["id"], "ref-guid-1")

        resp = client.delete(f"/api/doc-sources/database/adventureworks/{added['id']}")

        assert resp.status_code == 200
        assert resp.json()["egeria_unpublish"] == "queued"
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        unpublish_rows = [r for r in rows if r["element_kind"] == "doc_source_unpublish"]
        assert len(unpublish_rows) == 1
        payload = json.loads(unpublish_rows[0]["payload_json"])
        assert payload["ref_guid"] == "ref-guid-1"
        assert payload["entity_type"] == "database"
        assert payload["entity_slug"] == "adventureworks"

    def test_remove_of_a_local_only_source_queues_nothing(self, client, monkeypatch, registry):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()

        resp = client.delete(f"/api/doc-sources/database/adventureworks/{added['id']}")

        assert resp.status_code == 200
        assert resp.json()["egeria_unpublish"] == "not_applicable"
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        assert [r for r in rows if r["element_kind"] == "doc_source_unpublish"] == []

    def test_remove_unknown_source_is_404(self, client):
        resp = client.delete("/api/doc-sources/database/adventureworks/nope")
        assert resp.status_code == 404


class TestEgeriaPublishStateFix:
    """Egeria publish-state fix (2026-09-29) — the bug found live on 8813:
    `add_doc_source` only ever saved locally, so a source added to an
    ALREADY-published resource stayed local-only until the next full
    re-publish, with nothing on the row saying so. Covers both required
    route behaviors plus the four-state vocabulary this fix introduces."""

    def test_add_on_an_already_published_resource_queues_an_outbox_publish(
        self, client, monkeypatch, registry,
    ):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])

        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "https://docs.example/dict"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["egeria_state"] == "publishing"
        assert body["egeria_external_ref_guid"] == ""
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        publish_rows = [r for r in rows if r["element_kind"] == "doc_source_publish"]
        assert len(publish_rows) == 1
        payload = json.loads(publish_rows[0]["payload_json"])
        assert payload["source_id"] == body["id"]
        assert publish_rows[0]["qualified_name"] == "ExternalReference::https://docs.example/dict"

    def test_add_on_an_unpublished_resource_stays_local_and_queues_nothing(
        self, client, monkeypatch, registry,
    ):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())

        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "https://docs.example/dict"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["egeria_state"] == "local_only"
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        assert [r for r in rows if r["element_kind"] == "doc_source_publish"] == []

    def test_list_reports_catalogued_when_the_ref_guid_is_set(self, client, monkeypatch, registry):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()
        # Adoption-race fix (round 4, 2026-09-29): "catalogued" requires BOTH
        # the ref guid AND the link guid — a ref guid alone is exactly the
        # shape of the incident this fix closes (see
        # test_list_reports_ref_without_link_and_no_outbox_as_not_catalogued
        # below for that case specifically).
        registry.set_doc_source_egeria_ref("database", "adventureworks", added["id"],
                                            "ref-guid-9", "link-guid-9")

        resp = client.get("/api/doc-sources/database/adventureworks")

        body = resp.json()
        row = next(s for s in body["sources"] if s["id"] == added["id"])
        assert row["egeria_state"] == "catalogued"
        assert row["egeria_state_detail"] == "ref-guid-9"
        assert body["in_egeria_count"] == 1
        assert body["local_count"] == 0

    def test_derive_reports_ref_without_link_as_not_catalogued_pure_function(self):
        # The state half of the adoption-race fix (round 4, 2026-09-29): a
        # row with a ref guid but NO link guid must never read "catalogued"
        # with nothing backing that claim — `derive_doc_source_egeria_state`
        # (the pure function, no outbox self-heal side effect) reports
        # `not_catalogued` regardless of what an outbox row says, since a
        # `done` row's claim is exactly what round 6 (below) established
        # cannot be trusted unconditionally. See `TestDeriveEgeriaStateTable`
        # for the full input table this pins; this one keeps the ORIGINAL
        # regression's own naming/shape as a direct pointer to the incident.
        state, detail = derive_doc_source_egeria_state(
            ref_guid="ref-guid-9", link_guid="", is_published=True,
            outbox_row={"status": "done", "id": 1},
        )
        assert state == "not_catalogued"
        assert state != "catalogued"

    def test_list_self_heals_by_reopening_a_done_outbox_row_rather_than_leaving_it_stuck(
        self, client, monkeypatch, registry,
    ):
        # Round 6 (2026-09-29): the fix for the case the test THIS ONE
        # REPLACES used to assert as correct — a `done` doc_source_publish
        # outbox row for a `not_catalogued` element used to be read as
        # "already handled" and self-heal never fired, leaving the row stuck
        # `not_catalogued` forever (the exact live incident on database
        # 8813, row 4711d538). A `done` row is no longer a reason to skip
        # self-heal; it gets REOPENED (same row id, not a second row) so the
        # next drain runs round 5's verify-before-trust logic for real.
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.threading.Thread", _SyncThread)
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()
        registry.set_doc_source_egeria_ref("database", "adventureworks", added["id"], "ref-guid-9")
        # A doc_source_publish row already exists from the add above; mark it
        # 'done' with a stale egeria_guid, exactly the pre-round-5 write-back
        # shape (done with a ref guid, never linked).
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        publish_row_id = next(r["id"] for r in rows if r["element_kind"] == "doc_source_publish")
        with registry._conn() as conn:
            conn.execute("UPDATE egeria_outbox SET status='done', egeria_guid='ref-guid-9' "
                         "WHERE id=?", (publish_row_id,))

        drained_ids = []

        def fake_drain_outbox_row(reg, element_id, clients=None, find_element_guid=None):
            drained_ids.append(element_id)
            return {"claimed": 1, "done": 1, "failed": 0, "dead": 0, "skipped": 0}

        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.drain_outbox_row", fake_drain_outbox_row)

        resp = client.get("/api/doc-sources/database/adventureworks")

        row = next(s for s in resp.json()["sources"] if s["id"] == added["id"])
        assert row["egeria_state"] == "publishing"
        # The SAME row was reopened — no second doc_source_publish row exists
        # for this element, and the immediate attempt drained exactly that
        # row (proving "reopen", not "duplicate alongside the old one").
        rows_after = registry.list_outbox_elements(entity_slug="adventureworks")
        publish_rows_after = [r for r in rows_after if r["element_kind"] == "doc_source_publish"]
        assert len(publish_rows_after) == 1
        assert publish_rows_after[0]["id"] == publish_row_id
        assert publish_rows_after[0]["status"] == "pending"
        assert publish_rows_after[0]["egeria_guid"] == ""
        assert publish_rows_after[0]["reopened_at"]
        assert drained_ids == [publish_row_id]

    def test_list_reports_publish_failed_with_the_real_reason(self, client, monkeypatch, registry):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()
        outbox_rows = registry.list_outbox_elements(entity_slug="adventureworks")
        row_id = next(r["id"] for r in outbox_rows if r["element_kind"] == "doc_source_publish")
        registry.mark_outbox_failed(row_id, "Egeria unreachable: connection refused")

        resp = client.get("/api/doc-sources/database/adventureworks")

        row = next(s for s in resp.json()["sources"] if s["id"] == added["id"])
        assert row["egeria_state"] == "publish_failed"
        assert "connection refused" in row["egeria_state_detail"]

    def test_list_self_heals_a_source_stranded_by_the_pre_fix_bug(self, client, monkeypatch, registry):
        # The exact bug found live on 8813: a doc_sources row added while the
        # resource was already published, with no egeria_external_ref_guid
        # AND no outbox row ever queued for it (this fix didn't exist yet).
        # A read must not render it as an unexplained gap forever — it
        # queues the missing publish right here.
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        stranded = registry.add_doc_source("database", "adventureworks", "https://stranded.example")

        resp = client.get("/api/doc-sources/database/adventureworks")

        row = next(s for s in resp.json()["sources"] if s["id"] == stranded["id"])
        assert row["egeria_state"] == "publishing"
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        publish_rows = [r for r in rows if r["element_kind"] == "doc_source_publish"]
        assert len(publish_rows) == 1


class TestPublishHook:
    def test_publish_local_doc_sources_skips_already_published_rows(self, monkeypatch, registry, signed_in_caller,
                                                                    allow_example_platform):
        from resource_explorer.web.routes.doc_sources import publish_local_doc_sources

        registry.add_doc_source("database", "adventureworks", "https://x")
        row2 = registry.add_doc_source("database", "adventureworks", "https://y")
        registry.set_doc_source_egeria_ref("database", "adventureworks", row2["id"], "already-there")

        calls = []
        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.publish_doc_source",
            lambda row, asset_guid, **kw: calls.append(row["url"]) or {"ok": True, "ref_guid": "new-guid", "link_guid": ""},
        )

        results = publish_local_doc_sources("database", "adventureworks", "asset-guid-1", registry=registry)

        assert calls == ["https://x"]  # only the unpublished one was published
        skipped = next(r for r in results if r["id"] == row2["id"])
        assert skipped.get("skipped") == "already published"


class _SyncThread:
    """Stand-in for `threading.Thread` that runs its target synchronously on
    `.start()`, so a test can observe the immediate-drain attempt
    deterministically instead of racing a real background thread."""

    def __init__(self, target=None, name=None, daemon=None):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


class TestImmediateOutboxAttempt:
    """Egeria publish-state fix round 3 (2026-09-29): design's spec is
    "attempt the publish at once, falling back to the existing 15-minute
    retry loop only if that immediate attempt fails" — round 2 only ever
    enqueued and left the row for the scheduler's next pass, up to 15
    minutes later. These pin that add/remove now trigger a SCOPED drain of
    just the row they enqueued, off the request thread, and that a failed
    immediate attempt does not fail the add/remove request itself."""

    def test_add_on_an_already_published_resource_attempts_a_scoped_drain_of_exactly_that_row(
        self, client, monkeypatch, registry,
    ):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.threading.Thread", _SyncThread)
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")

        drained_ids = []

        def fake_drain_outbox_row(reg, element_id, clients=None, find_element_guid=None):
            drained_ids.append(element_id)
            return {"claimed": 1, "done": 1, "failed": 0, "dead": 0, "skipped": 0}

        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.drain_outbox_row", fake_drain_outbox_row)

        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "https://docs.example/dict"})

        assert resp.status_code == 200
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        publish_rows = [r for r in rows if r["element_kind"] == "doc_source_publish"]
        assert len(publish_rows) == 1
        # The scoped drain ran (synchronously, via the thread stand-in) for
        # exactly the row this request enqueued — not a full, unscoped drain
        # of the whole outbox, and not left for the 15-minute scheduler loop.
        assert drained_ids == [publish_rows[0]["id"]]

    def test_remove_of_a_catalogued_source_attempts_a_scoped_drain_of_the_unpublish_row(
        self, client, monkeypatch, registry,
    ):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.threading.Thread", _SyncThread)
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        added = client.post("/api/doc-sources/database/adventureworks",
                             json={"url": "https://x"}).json()
        registry.set_doc_source_egeria_ref("database", "adventureworks", added["id"], "ref-guid-9")

        drained_ids = []

        def fake_drain_outbox_row(reg, element_id, clients=None, find_element_guid=None):
            drained_ids.append(element_id)
            return {"claimed": 1, "done": 1, "failed": 0, "dead": 0, "skipped": 0}

        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.drain_outbox_row", fake_drain_outbox_row)

        resp = client.delete(f"/api/doc-sources/database/adventureworks/{added['id']}")

        assert resp.status_code == 200
        assert resp.json()["egeria_unpublish"] == "queued"
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        unpublish_rows = [r for r in rows if r["element_kind"] == "doc_source_unpublish"]
        assert len(unpublish_rows) == 1
        assert drained_ids == [unpublish_rows[0]["id"]]

    def test_a_failed_immediate_attempt_does_not_fail_the_add_request_and_leaves_the_row_for_retry(
        self, client, monkeypatch, registry,
    ):
        # The fallback contract: if the immediate, off-thread attempt fails
        # for any reason, the add request must still succeed (the row was
        # already enqueued before the attempt ran), and the row must be left
        # exactly where the normal 15-minute scheduler drain would find and
        # retry it — not surfaced as an error on this request.
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.threading.Thread", _SyncThread)
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.read_back_doc_sources",
                             lambda *a, **kw: [])
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")

        def failing_drain_outbox_row(reg, element_id, clients=None, find_element_guid=None):
            raise RuntimeError("Egeria unreachable: connection refused")

        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.drain_outbox_row", failing_drain_outbox_row)

        resp = client.post("/api/doc-sources/database/adventureworks",
                            json={"url": "https://docs.example/dict"})

        # The add itself is unaffected by the immediate attempt's failure.
        assert resp.status_code == 200
        assert resp.json()["egeria_state"] == "publishing"
        # The row is still there, untouched by drain_outbox_row's own
        # failure bookkeeping (this test's fake never calls
        # mark_outbox_failed) — exactly the row the normal 15-minute
        # scheduler drain will pick up and actually attempt next.
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        publish_rows = [r for r in rows if r["element_kind"] == "doc_source_publish"]
        assert len(publish_rows) == 1
        assert publish_rows[0]["status"] in ("pending", "running")


class TestDeriveEgeriaStateTable:
    """`derive_doc_source_egeria_state` — design session, 2026-09-29, round 4:
    a small PURE function (row facts in -> state word out, no side effects,
    no branching on how the row got there), table-tested over every
    meaningful input combination so the vocabulary can't drift out of sync
    with reality again the way `ref_guid` alone drifted into meaning
    "catalogued" even when nothing was ever linked."""

    def _derive(self, **kw):
        from resource_explorer.web.routes.doc_sources import derive_doc_source_egeria_state
        defaults = dict(ref_guid="", link_guid="", is_published=True, outbox_row=None)
        defaults.update(kw)
        return derive_doc_source_egeria_state(**defaults)

    def test_unpublished_resource_with_no_real_link_is_local_only(self):
        assert self._derive(is_published=False) == ("local_only", "")
        assert self._derive(is_published=False, outbox_row={"status": "failed"}) == ("local_only", "")

    def test_a_genuinely_complete_ref_and_link_is_catalogued_even_if_is_published_reads_false(self):
        # `catalogued` is checked first, same precedence the pre-fix code
        # already had (a real ref+link is stronger evidence than the
        # resource-level publish flag, which is a separate, independently
        # tracked fact -- e.g. it could go stale without this source's own
        # link changing). Only the ABSENCE of a complete ref+link falls
        # through to the is_published gate.
        assert self._derive(is_published=False, ref_guid="r", link_guid="l") == ("catalogued", "r")

    def test_ref_and_link_both_present_is_catalogued(self):
        assert self._derive(ref_guid="ref-1", link_guid="link-1") == ("catalogued", "ref-1")

    def test_ref_present_link_present_still_catalogued_even_with_a_stale_done_outbox_row(self):
        # Once truly linked, a leftover 'done' outbox row changes nothing.
        assert self._derive(
            ref_guid="ref-1", link_guid="link-1", outbox_row={"status": "done"},
        ) == ("catalogued", "ref-1")

    def test_ref_only_no_link_no_outbox_row_is_not_catalogued(self):
        # The adoption-race shape exactly: a ref guid with nothing linking
        # it and nothing in flight must not read "publishing" OR
        # "catalogued".
        assert self._derive(ref_guid="ref-1", link_guid="") == ("not_catalogued", "")

    def test_neither_guid_no_outbox_row_is_not_catalogued(self):
        assert self._derive(ref_guid="", link_guid="") == ("not_catalogued", "")

    def test_ref_only_with_pending_outbox_row_is_publishing(self):
        assert self._derive(
            ref_guid="ref-1", link_guid="", outbox_row={"status": "pending"},
        ) == ("publishing", "")

    def test_neither_guid_with_running_outbox_row_is_publishing(self):
        assert self._derive(outbox_row={"status": "running"}) == ("publishing", "")

    def test_ref_only_with_failed_outbox_row_is_publish_failed_with_the_real_reason(self):
        assert self._derive(
            ref_guid="ref-1", outbox_row={"status": "failed", "last_error": "connection refused"},
        ) == ("publish_failed", "connection refused")

    def test_dead_outbox_row_is_publish_failed_worded_the_same_as_failed(self):
        assert self._derive(
            outbox_row={"status": "dead", "last_error": "retries exhausted"},
        ) == ("publish_failed", "retries exhausted")

    def test_failed_outbox_row_with_no_last_error_still_gets_a_non_generic_fallback(self):
        state, detail = self._derive(outbox_row={"status": "failed", "last_error": ""})
        assert state == "publish_failed"
        assert detail  # non-empty -- never silently blank

    def test_ref_and_link_present_beats_a_pending_outbox_row(self):
        # A pending row for a DIFFERENT reason (e.g. a stale queued retry
        # from before the ref got linked by another path) must not mask a
        # genuinely catalogued state.
        assert self._derive(
            ref_guid="ref-1", link_guid="link-1", outbox_row={"status": "pending"},
        ) == ("catalogued", "ref-1")

    def test_link_without_ref_is_not_catalogued(self):
        # Not a real shape this codebase produces, but the pure function
        # must not special-case it into "catalogued" either -- BOTH guids
        # are required, not "either one".
        assert self._derive(ref_guid="", link_guid="link-only") == ("not_catalogued", "")


class TestAdoptionRaceReadBack:
    """The actual root cause, reproduced at the route/outbox level: found
    live 2026-09-29 on database 8813. Timeline —

      15:21:41 — a source at `https://egeria.ai` is removed. Its
                 ExternalReference (`b9925119…`) is detached+deleted through
                 a `doc_source_unpublish` outbox row, enqueued but not yet
                 applied.
      15:21:42 — a DIFFERENT source, same URL, different label/type
                 ("not-adventureworks", installation_guide), is declared.

    A `GET` landing between those two moments calls `_sync_egeria_read_back`,
    which asks Egeria for every `ExternalReferenceLink`-connected reference
    on the asset. Because the unpublish's detach+delete hadn't landed yet,
    Egeria still reported the reference — matched here by its URL
    (`ExternalReference::<url>`, confirmed as the adoption key) — and, before
    this fix, `upsert_doc_source_from_egeria`'s `url` fallback would adopt it
    onto whatever local row matched that URL, without ever creating a
    `doc_source_publish` outbox row of its own.
    """

    def test_a_get_landing_mid_unpublish_does_not_adopt_the_reference_being_deleted(
        self, client, monkeypatch, registry,
    ):
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.threading.Thread", _NeverStartsThread)
        monkeypatch.setattr("resource_explorer.web.routes.doc_sources.run_probe",
                             lambda url: _fake_probe())
        registry.set_database_egeria_guid("adventureworks", "asset-guid-1")
        url = "https://egeria.ai"

        # 1. A fully-catalogued source exists at this URL (ref AND link set
        #    -- a real, previously-published reference).
        old = registry.add_doc_source("database", "adventureworks", url, label="Egeria homepage")
        registry.set_doc_source_egeria_ref(
            "database", "adventureworks", old["id"], "b9925119-old-ref", "link-old")

        # 2. It's removed -- local row gone, doc_source_unpublish enqueued.
        #    _NeverStartsThread means the immediate-attempt thread never
        #    actually runs, so the row stays 'pending' -- "still in flight",
        #    exactly the live timeline's 15:21:41-44 window.
        del_resp = client.delete(f"/api/doc-sources/database/adventureworks/{old['id']}")
        assert del_resp.status_code == 200
        assert del_resp.json()["egeria_unpublish"] == "queued"
        assert registry.list_doc_sources("database", "adventureworks") == []

        # 3. A GET lands in that window. Egeria's own read-back API (stubbed
        #    here) still reports the reference -- the detach+delete hasn't
        #    landed. No local row exists for this URL at all right now (the
        #    old one is gone, the new one hasn't been declared yet), so
        #    without the guard, upsert_doc_source_from_egeria's url fallback
        #    would have nothing to match and would CREATE a new local row
        #    carrying the being-deleted ref guid.
        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.read_back_doc_sources",
            lambda *a, **kw: [{"ref_guid": "b9925119-old-ref", "url": url, "label": "Egeria homepage"}],
        )
        mid_resp = client.get("/api/doc-sources/database/adventureworks")
        assert mid_resp.status_code == 200
        # The guard must have skipped adoption entirely.
        assert registry.list_doc_sources("database", "adventureworks") == [], (
            "read-back must not adopt a reference with a pending/running unpublish"
        )

        # 4. The new, differently-labeled source is declared at the SAME URL.
        add_resp = client.post(
            "/api/doc-sources/database/adventureworks",
            json={"url": url, "label": "not-adventureworks", "source_type": "installation_guide"},
        )
        assert add_resp.status_code == 200
        new_row = add_resp.json()
        # Must NOT have adopted the reference being deleted.
        assert new_row["egeria_external_ref_guid"] != "b9925119-old-ref"
        assert new_row["egeria_external_ref_guid"] == ""
        # It must have queued its OWN fresh publish -- never silently
        # skipped because "a ref guid was already there".
        rows = registry.list_outbox_elements(entity_slug="adventureworks")
        publish_rows = [r for r in rows if r["element_kind"] == "doc_source_publish"]
        assert len(publish_rows) == 1
        payload = json.loads(publish_rows[0]["payload_json"])
        assert payload["source_id"] == new_row["id"]
        assert new_row["egeria_state"] == "publishing"

        # 5. Once the unpublish actually completes (simulated: mark it done,
        #    and Egeria's read-back now correctly reports the ref is gone),
        #    a further GET must not resurrect anything either.
        unpublish_rows = [r for r in rows if r["element_kind"] == "doc_source_unpublish"]
        assert len(unpublish_rows) == 1
        registry.mark_outbox_done(unpublish_rows[0]["id"], "b9925119-old-ref")
        monkeypatch.setattr(
            "resource_explorer.web.routes.doc_sources.read_back_doc_sources", lambda *a, **kw: [])
        final_resp = client.get("/api/doc-sources/database/adventureworks")
        assert len(final_resp.json()["sources"]) == 1
        assert final_resp.json()["sources"][0]["id"] == new_row["id"]


class _NeverStartsThread:
    """Stand-in for `threading.Thread` whose `.start()` does nothing at all
    -- used where a test needs an enqueued outbox row to deterministically
    STAY `pending` (simulating "the immediate-attempt background thread
    hasn't run yet"), as opposed to `_SyncThread` above, which runs it
    synchronously to completion."""

    def __init__(self, target=None, name=None, daemon=None):
        pass

    def start(self):
        pass
