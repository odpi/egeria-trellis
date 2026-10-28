"""Outbox rows and the drain that applies them.

docs/outbox-publishing-design.md §5. What is worth pinning here is not the CRUD
but the four properties the design actually promises: dependency ordering,
exponential backoff, dead-lettering, and idempotent apply.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from resource_explorer.egeria_outbox import (
    OutboxApplyError,
    OutboxClients,
    apply_element,
    drain_outbox,
    enqueue_doc_source_publish,
    enqueue_doc_source_unpublish,
    record_drain_outcome,
)
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)

# Brief I: these tests drive the drain mechanics, as the background loop does: a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture()
def db(tmp_path):
    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path/'t.db'}")
    reg._init_schema()
    return reg


@pytest.fixture()
def project(db):
    db.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
    return "p"


def _enqueue(db, slug, kind="annotation", qn="Annotation::p::2026-01-01T00:00:00::0", **kw):
    return db.enqueue_outbox_element("repo", slug, kind, qn, {"class": "X"}, **kw)


class TestEnqueue:
    def test_it_round_trips_and_starts_pending_and_due(self, db, project):
        row_id = _enqueue(db, project)
        due = db.claim_due_outbox_elements()
        assert [r["id"] for r in due] == [row_id]
        # A claim MARKS the row; "pending" is what it was before being taken.
        # Read that with peek, which is the non-mutating view.
        assert due[0]["status"] == "running", "claiming marks the row as held"
        assert due[0]["attempts"] == 0
        assert json.loads(due[0]["payload_json"]) == {"class": "X"}

    def test_an_empty_qualified_name_is_refused(self, db, project):
        # It is the idempotency key. A row without one cannot be checked for
        # prior application, so accepting it would silently reintroduce the
        # duplicate-on-replay bug the whole table exists to prevent.
        with pytest.raises(ValueError, match="idempotency key"):
            db.enqueue_outbox_element("repo", project, "annotation", "", {})


class TestDependencyOrdering:
    def test_a_dependent_row_is_not_due_until_its_dependency_is_done(self, db, project):
        report = _enqueue(db, project, kind="report", qn="Report::p::1")
        ann = _enqueue(db, project, qn="Annotation::p::1::0", depends_on_id=report)

        assert [r["id"] for r in db.claim_due_outbox_elements()] == [report]

        db.mark_outbox_done(report, "guid-report")
        assert [r["id"] for r in db.claim_due_outbox_elements()] == [ann]

    def test_a_failed_dependency_still_blocks_its_dependent(self, db, project):
        # The point of ordering: a partial drain must leave a coherent prefix,
        # never an annotation whose report was never written.
        report = _enqueue(db, project, kind="report", qn="Report::p::1")
        ann = _enqueue(db, project, qn="Annotation::p::1::0", depends_on_id=report)
        db.mark_outbox_failed(report, "boom")

        assert ann not in [r["id"] for r in db.claim_due_outbox_elements()]


class TestBackoffAndDeadLettering:
    def test_backoff_grows_and_the_row_is_not_immediately_due(self, db, project):
        row_id = _enqueue(db, project)
        now = datetime(2026, 1, 1, 12, 0, 0)

        assert db.mark_outbox_failed(row_id, "e1", now=now, base_delay_seconds=60) == "failed"
        first = db.claim_due_outbox_elements(now=now.isoformat())
        assert first == [], "a just-failed row must wait for its backoff"

        due_later = db.claim_due_outbox_elements(now=(now + timedelta(seconds=61)).isoformat())
        assert [r["id"] for r in due_later] == [row_id]

        # Second failure waits twice as long as the first.
        db.mark_outbox_failed(row_id, "e2", now=now, base_delay_seconds=60)
        assert db.claim_due_outbox_elements(now=(now + timedelta(seconds=61)).isoformat()) == []
        assert db.claim_due_outbox_elements(now=(now + timedelta(seconds=121)).isoformat())

    def test_backoff_is_capped(self, db, project):
        row_id = _enqueue(db, project)
        now = datetime(2026, 1, 1, 12, 0, 0)
        for _ in range(6):
            db.mark_outbox_failed(row_id, "e", now=now, max_attempts=99, base_delay_seconds=60)
        row = db.claim_due_outbox_elements(now=(now + timedelta(days=2)).isoformat())
        assert row, "capped backoff must eventually come due"
        gap = datetime.fromisoformat(row[0]["next_attempt_at"]) - now
        assert gap <= timedelta(days=1)

    def test_it_dead_letters_instead_of_retrying_forever(self, db, project):
        row_id = _enqueue(db, project)
        statuses = [db.mark_outbox_failed(row_id, "nope", max_attempts=3) for _ in range(3)]
        assert statuses == ["failed", "failed", "dead"]

        # Terminal: never picked up again, however far in the future.
        assert db.claim_due_outbox_elements(now="2099-01-01T00:00:00") == []
        dead = db.list_dead_outbox_elements()
        assert [r["id"] for r in dead] == [row_id]
        assert dead[0]["last_error"] == "nope"

    def test_a_long_error_is_truncated_rather_than_rejected(self, db, project):
        row_id = _enqueue(db, project)
        db.mark_outbox_failed(row_id, "x" * 5000)
        assert len(db.list_dead_outbox_elements(limit=0) or []) == 0
        row = db.claim_due_outbox_elements(now="2099-01-01T00:00:00")[0]
        assert len(row["last_error"]) <= 2000


class TestApplyIsIdempotent:
    def test_a_recorded_guid_short_circuits_without_writing(self, db, project):
        calls = []
        discovery = type("D", (), {"create_annotation": lambda self, body: calls.append(body)})()
        row = {"id": 1, "egeria_guid": "already-there", "qualified_name": "Q",
               "element_kind": "annotation", "payload_json": "{}"}

        assert apply_element(row, OutboxClients(discovery=discovery), lambda qn: "") == "already-there"
        assert calls == [], "a row with a GUID must not write again"

    def test_it_adopts_an_existing_element_instead_of_duplicating_it(self, db, project):
        # The exact failure the design names: Egeria wrote the annotation, we
        # crashed before recording success. Replaying must converge.
        calls = []
        discovery = type("D", (), {"create_annotation": lambda self, body: calls.append(body)})()
        row = {"id": 1, "egeria_guid": "", "qualified_name": "Annotation::p::1::0",
               "element_kind": "annotation", "payload_json": "{}"}

        assert apply_element(row, OutboxClients(discovery=discovery), lambda qn: "found-guid") == "found-guid"
        assert calls == [], "a qualifiedName hit must adopt, not create"

    def test_it_creates_when_nothing_exists(self, db, project):
        discovery = type("D", (), {"create_annotation": lambda self, body: {"guid": "new"}})()
        row = {"id": 1, "egeria_guid": "", "qualified_name": "Annotation::p::1::0",
               "element_kind": "annotation", "payload_json": '{"class": "X"}'}

        assert apply_element(row, OutboxClients(discovery=discovery), lambda qn: "") == "new"

    def test_a_lookup_that_raises_creates_nothing(self, db, project):
        # Could not check is not "not there" (re/lookup-failure-never-creates; it used to fall through to
        # create). An ordinary OutboxApplyError: the drain retries the row per policy.
        made = []
        discovery = type("D", (), {"create_annotation": lambda self, body: made.append(body) or "new"})()
        row = {"id": 1, "egeria_guid": "", "qualified_name": "Q",
               "element_kind": "annotation", "payload_json": "{}"}

        def boom(qn):
            raise RuntimeError("lookup down")

        with pytest.raises(OutboxApplyError, match="look-up for 'Q' could not be made"):
            apply_element(row, OutboxClients(discovery=discovery), boom)
        assert made == []

    def test_an_unknown_element_kind_raises_rather_than_reporting_success(self, db, project):
        # A row that reports 'done' without writing anything is the exact
        # confident-wrong-answer this subsystem exists to remove.
        row = {"id": 1, "egeria_guid": "", "qualified_name": "Q",
               "element_kind": "wormhole", "payload_json": "{}"}
        with pytest.raises(OutboxApplyError, match="wormhole"):
            apply_element(row, OutboxClients(discovery=object()), lambda qn: "")


class TestDrain:
    def test_it_applies_due_rows_and_records_their_guids(self, db, project):
        row_id = _enqueue(db, project)
        discovery = type("D", (), {"create_annotation": lambda self, body: {"guid": "g1"}})()

        summary = drain_outbox(db, OutboxClients(discovery=discovery), lambda qn: "")
        assert summary["done"] == 1 and summary["failed"] == 0
        assert db.outbox_counts() == {"done": 1}

    def test_one_bad_row_does_not_stop_the_rest(self, db, project):
        bad = _enqueue(db, project, qn="Annotation::p::1::0")
        good = _enqueue(db, project, qn="Annotation::p::1::1")

        class D:
            def create_annotation(self, body):
                if body.get("fail"):
                    raise RuntimeError("egeria said no")
                return {"guid": "ok"}

        db.enqueue_outbox_element("repo", project, "annotation", "Q3", {"fail": True})
        summary = drain_outbox(db, OutboxClients(discovery=D()), lambda qn: "")
        assert summary["done"] == 2 and summary["failed"] == 1

    def test_no_client_leaves_rows_pending_rather_than_burning_an_attempt(self, db, project, monkeypatch):
        # A transient outage must not consume the retry budget — that is how
        # a good write dead-letters for reasons that had nothing to do with it.
        _enqueue(db, project)
        monkeypatch.setattr(
            "resource_explorer.egeria_outbox._default_clients",
            lambda: (_ for _ in ()).throw(RuntimeError("no platform")),
        )
        summary = drain_outbox(db)
        assert summary["skipped"] == 1 and summary["done"] == 0
        still_due = db.claim_due_outbox_elements()
        assert len(still_due) == 1 and still_due[0]["attempts"] == 0

    def test_an_empty_outbox_is_a_no_op(self, db):
        assert drain_outbox(db, OutboxClients(discovery=object()), lambda qn: "")["claimed"] == 0


class TestRetention:
    def test_it_purges_only_old_done_rows(self, db, project):
        done_old = _enqueue(db, project, qn="Q1")
        done_new = _enqueue(db, project, qn="Q2")
        dead = _enqueue(db, project, qn="Q3")
        pending = _enqueue(db, project, qn="Q4")

        db.mark_outbox_done(done_old)
        db.mark_outbox_done(done_new)
        db.mark_outbox_failed(dead, "x", max_attempts=1)
        with db._conn() as conn:
            conn.execute("UPDATE egeria_outbox SET completed_at=? WHERE id=?",
                         ("2020-01-01T00:00:00", done_old))

        assert db.purge_outbox_completed(older_than_days=14) == 1
        remaining = db.outbox_counts()
        assert remaining == {"done": 1, "dead": 1, "pending": 1}, (
            "dead rows still need a human and pending rows are live work"
        )


class TestRunScoping:
    """A publisher that enqueues then drains inline must write ITS OWN rows.

    An unscoped claim takes the oldest due rows in the table, so a publish
    could drain an unrelated resource's backlog and return believing it had
    published — the caller would see success with none of its own annotations
    written.
    """

    def test_a_scoped_drain_leaves_other_runs_alone(self, db, project):
        old = db.enqueue_outbox_element("repo", project, "annotation", "Old::0", {}, run_id="run-old")
        mine = db.enqueue_outbox_element("repo", project, "annotation", "Mine::0", {}, run_id="run-mine")

        # peek, not claim: claiming here would take the row the drain below is
        # supposed to take, and the drain would then correctly find nothing.
        visible = db.peek_due_outbox_elements(run_id="run-mine")
        assert [r["id"] for r in visible] == [mine], "the older row must not be in scope"

        discovery = type("D", (), {"create_annotation": lambda self, body: {"guid": "g"}})()
        drain_outbox(db, OutboxClients(discovery=discovery), lambda qn: "", run_id="run-mine")

        counts = db.outbox_counts()
        assert counts == {"done": 1, "pending": 1}
        assert [r["id"] for r in db.peek_due_outbox_elements()] == [old]

    def test_an_unscoped_drain_still_sees_everything(self, db, project):
        db.enqueue_outbox_element("repo", project, "annotation", "A::0", {}, run_id="r1")
        db.enqueue_outbox_element("repo", project, "annotation", "B::0", {}, run_id="r2")
        assert len(db.claim_due_outbox_elements()) == 2


class TestEnqueueAnnotations:
    def test_it_writes_one_row_per_annotation_with_indexed_qualified_names(self, db, project):
        from resource_explorer.egeria_outbox import enqueue_annotations
        from resource_explorer.surveyors.survey_report import ClassificationAnnotation

        # The real annotation class, not a stub — build_annotation_body reads
        # fields a hand-rolled fake will not have, and a fake that drifts from
        # the real shape is how the filesystem publisher's AttributeError
        # survived under test (see test_annotation_props.py's docstring).
        anns = [ClassificationAnnotation(summary="a", analysis_step="s",
                                         candidate_classifications=["python"]),
                ClassificationAnnotation(summary="b", analysis_step="s",
                                         candidate_classifications=["rust"])]
        ids = enqueue_annotations(
            db, "repo", project, anns, "report-guid",
            "Annotation::p::2026-01-01T00:00:00", run_id="r1",
        )
        assert len(ids) == 2
        rows = db.claim_due_outbox_elements(run_id="r1")
        assert [r["qualified_name"] for r in rows] == [
            "Annotation::p::2026-01-01T00:00:00::0",
            "Annotation::p::2026-01-01T00:00:00::1",
        ]
        body = json.loads(rows[0]["payload_json"])
        assert body["parentGUID"] == "report-guid"
        assert body["class"] == "NewElementRequestBody"

    def test_no_annotations_enqueues_nothing(self, db, project):
        from resource_explorer.egeria_outbox import enqueue_annotations

        assert enqueue_annotations(db, "repo", project, [], "g", "Q") == []
        assert db.outbox_counts() == {}


class TestDrainOutcomeIsVisible:
    """Logging is not a surface: nothing configures logging and uvicorn.run()
    is called without a log_config, so INFO is discarded and WARNING/ERROR
    reach only the server's stderr. Failures must reach the activity log."""

    def test_a_failure_is_written_to_the_activity_log(self, db, project):
        _enqueue(db, project)

        class D:
            def create_annotation(self, body):
                raise RuntimeError("egeria said no")

        drain_outbox(db, OutboxClients(discovery=D()), lambda qn: "")
        entries = db.list_activity(limit=10)
        assert any("Egeria publish incomplete" in e["summary"] for e in entries), (
            "a failed drain must be visible in the Activity tab, not only on stderr"
        )

    def test_a_clean_pass_writes_no_activity_entry(self, db, project):
        # An entry every quarter-hour saying nothing was wrong is how a log
        # stops being read.
        _enqueue(db, project)
        discovery = type("D", (), {"create_annotation": lambda self, body: {"guid": "g"}})()
        drain_outbox(db, OutboxClients(discovery=discovery), lambda qn: "")
        assert db.list_activity(limit=10) == []


class TestCollectionMemberships:
    """Investigation membership writes (design §6 step 5).

    Idempotency here is NOT lookup-then-create — a CollectionMembership has no
    qualifiedName to search for. It rests on the relationship being uni-link,
    measured live 2026-08-25: add_to_collection returns None and adding the
    same element twice leaves one member.
    """

    def test_it_attaches_the_member_to_the_collection(self, db, project):
        from resource_explorer.egeria_outbox import OutboxClients, enqueue_collection_members

        calls = []

        class CM:
            def add_to_collection(self, coll, member):
                calls.append((coll, member))

        enqueue_collection_members(
            db, "inv-1", "coll-guid",
            [{"entity_type": "repo", "entity_slug": "p", "member_guid": "asset-1"}],
            run_id="r1",
        )
        summary = drain_outbox(db, OutboxClients(collection_manager=CM()), lambda qn: "", run_id="r1")

        assert summary["done"] == 1
        assert calls == [("coll-guid", "asset-1")]

    def test_replaying_a_membership_calls_the_upsert_again_rather_than_skipping(self, db, project):
        # Deliberate: there is no element to find by qualifiedName, so the
        # apply step cannot short-circuit. Safety comes from the operation.
        from resource_explorer.egeria_outbox import apply_element

        calls = []

        class CM:
            def add_to_collection(self, coll, member):
                calls.append((coll, member))

        row = {"id": 1, "egeria_guid": "", "qualified_name": "CollectionMembership::c::m",
               "element_kind": "collection_membership",
               "payload_json": json.dumps({"collection_guid": "c", "member_guid": "m"})}
        apply_element(row, OutboxClients(collection_manager=CM()), lambda qn: "")
        apply_element(row, OutboxClients(collection_manager=CM()), lambda qn: "")
        assert len(calls) == 2

    def test_a_missing_client_is_a_failure_not_a_silent_success(self, db, project):
        # The drain was given a discovery client but this row needs the
        # collection manager. Reporting 'done' here would record a write that
        # never happened.
        from resource_explorer.egeria_outbox import apply_element

        row = {"id": 1, "egeria_guid": "", "qualified_name": "CollectionMembership::c::m",
               "element_kind": "collection_membership",
               "payload_json": json.dumps({"collection_guid": "c", "member_guid": "m"})}
        with pytest.raises(OutboxApplyError, match="collection_manager"):
            apply_element(row, OutboxClients(discovery=object()), lambda qn: "")

    def test_resource_list_attaches_collection_to_its_parent(self, db, project):
        from resource_explorer.egeria_outbox import apply_element

        calls = []

        class CM:
            def attach_collection(self, parent, coll):
                calls.append((parent, coll))

        row = {"id": 1, "egeria_guid": "", "qualified_name": "ResourceList::p::c",
               "element_kind": "resource_list",
               "payload_json": json.dumps({"parent_guid": "proj", "collection_guid": "coll"})}
        apply_element(row, OutboxClients(collection_manager=CM()), lambda qn: "")
        assert calls == [("proj", "coll")]

    def test_enqueue_records_the_member_for_the_publish_queue(self, db, project):
        from resource_explorer.egeria_outbox import enqueue_collection_members

        ids = enqueue_collection_members(
            db, "inv-1", "coll",
            [{"entity_type": "repo", "entity_slug": "p", "member_guid": "a1"},
             {"entity_type": "repo", "entity_slug": "q", "member_guid": "a2"}],
            run_id="r1",
        )
        assert len(ids) == 2
        rows = db.list_outbox_elements(run_id="r1")
        assert {r["entity_type"] for r in rows} == {"investigation"}
        assert {json.loads(r["payload_json"])["member_entity_slug"] for r in rows} == {"p", "q"}


class TestEnqueueBlueprintMembers:
    """docs/blueprint-materialization-plan.md Phase B. Parallel to
    enqueue_collection_members above but for a repo's own blueprint (not an
    investigation) — reuses element_kind='collection_membership' unchanged,
    since CollectionMembership doesn't distinguish which kind of Collection
    it's attaching to."""

    def test_enqueues_one_row_per_member_reusing_collection_membership(self, db, project):
        from resource_explorer.egeria_outbox import enqueue_blueprint_members

        ids = enqueue_blueprint_members(
            db, "repo", project, "bp-guid-1", ["comp-guid-a", "comp-guid-b"], run_id="r2",
        )
        assert len(ids) == 2
        rows = db.list_outbox_elements(run_id="r2")
        assert {r["element_kind"] for r in rows} == {"collection_membership"}
        assert {r["entity_type"] for r in rows} == {"repo"}
        assert {r["entity_slug"] for r in rows} == {project}
        payloads = [json.loads(r["payload_json"]) for r in rows]
        assert {p["collection_guid"] for p in payloads} == {"bp-guid-1"}
        assert {p["member_guid"] for p in payloads} == {"comp-guid-a", "comp-guid-b"}

    def test_empty_member_list_enqueues_nothing(self, db, project):
        from resource_explorer.egeria_outbox import enqueue_blueprint_members

        ids = enqueue_blueprint_members(db, "repo", project, "bp-guid-1", [])
        assert ids == []


class TestNothingWrittenIsNotOneState:
    """Three different outcomes all look like "no elements were written".

    The shape to avoid is the cii_badge bug of 2026-08-31: "matched nothing"
    and "not registered" were both (None, ""), so a real gap read as a
    non-answer. The same collapse is available here — an empty queue, an
    unreachable platform, and every write failing are indistinguishable from
    the outside if all you observe is "nothing landed in Egeria".

    They must stay distinguishable in the summary AND in what reaches a person,
    because the right response to each is different: nothing to do, wait and
    retry, and go look at the errors.
    """

    def _fail_client(self):
        class D:
            def create_annotation(self, body):
                raise RuntimeError("egeria said no")
        return OutboxClients(discovery=D())

    def test_the_three_outcomes_have_different_summaries(self, db, project):
        empty = drain_outbox(db, OutboxClients(discovery=object()), lambda qn: "")

        _enqueue(db, project, qn="Q::fail")
        failed = drain_outbox(db, self._fail_client(), lambda qn: "")

        db.mark_outbox_done(_enqueue(db, project, qn="Q::done2"))
        _enqueue(db, project, qn="Q::skip")
        import resource_explorer.egeria_outbox as mod
        original = mod._default_clients
        mod._default_clients = lambda: (_ for _ in ()).throw(RuntimeError("no platform"))
        try:
            skipped = drain_outbox(db)
        finally:
            mod._default_clients = original

        assert (empty["claimed"], empty["failed"], empty["skipped"]) == (0, 0, 0)
        assert (failed["claimed"], failed["failed"], failed["skipped"]) == (1, 1, 0)
        assert (skipped["claimed"], skipped["failed"], skipped["skipped"]) == (1, 0, 1)

    def test_only_a_failure_reaches_a_person(self, db, project):
        # An empty queue and an unreachable platform are not incidents. A
        # failure is. If all three wrote an activity entry, the entry would
        # stop meaning anything; if none did, the failure would be invisible.
        drain_outbox(db, OutboxClients(discovery=object()), lambda qn: "")
        assert db.list_activity(limit=10) == [], "an empty queue is not an incident"

        _enqueue(db, project, qn="Q::skip")
        import resource_explorer.egeria_outbox as mod
        original = mod._default_clients
        mod._default_clients = lambda: (_ for _ in ()).throw(RuntimeError("no platform"))
        try:
            drain_outbox(db)
        finally:
            mod._default_clients = original
        assert db.list_activity(limit=10) == [], "an outage is not a failed write"

        drain_outbox(db, self._fail_client(), lambda qn: "")
        entries = db.list_activity(limit=10)
        assert len(entries) == 1 and "incomplete" in entries[0]["summary"]

    def test_an_outage_leaves_the_retry_budget_untouched(self, db, project):
        # The consequence of confusing outage with failure: rows would burn
        # attempts and dead-letter for a reason that had nothing to do with them.
        row_id = _enqueue(db, project, qn="Q::skip")
        import resource_explorer.egeria_outbox as mod
        original = mod._default_clients
        mod._default_clients = lambda: (_ for _ in ()).throw(RuntimeError("no platform"))
        try:
            for _ in range(20):
                drain_outbox(db)
        finally:
            mod._default_clients = original
        rows = db.claim_due_outbox_elements()
        assert len(rows) == 1 and rows[0]["attempts"] == 0
        assert db.list_dead_outbox_elements() == []


class TestDeadLettersReachAPerson:
    """A dead-lettered element will never be retried on its own, so it is a
    request for human action rather than a status line — and it has to reach
    the RFA drawer, which is the surface people actually watch.

    The drawer is fed by GET /api/activity/rfas, which keeps only ANNOTATIONS
    whose annotation_type contains "RequestForAction" and ignores
    operation="rfa" entirely. An entry without that annotation exists in the
    activity log and is invisible where it matters — which is exactly how three
    earlier RFA callers were silently unseen.
    """

    def test_a_dead_letter_raises_an_rfa_the_drawer_can_see(self, db, project):
        # Driven to dead via mark_outbox_failed rather than repeated drains:
        # the backoff means real drains would need a clock, and what is under
        # test here is the reporting, not the retry policy.
        row_id = _enqueue(db, project, qn="Q::dead")
        for _ in range(ProjectRegistry.OUTBOX_MAX_ATTEMPTS):
            db.mark_outbox_failed(row_id, "permission denied")
        assert db.list_dead_outbox_elements()

        record_drain_outcome(db, {"claimed": 1, "done": 0, "failed": 0, "dead": 1, "skipped": 0})
        entries = db.list_activity(limit=10)
        assert len(entries) == 1, "one event must produce one entry, not two"
        entry = entries[0]
        assert entry["operation"] == "rfa"
        anns = entry.get("annotations") or []
        assert any("RequestForAction" in (a.get("annotation_type") or "") for a in anns), (
            "without this annotation the RFA drawer never shows it"
        )
        assert "stuck" in entry["summary"]

    def test_a_retrying_failure_is_an_audit_line_not_an_rfa(self, db, project):
        # The scheduler is already handling it; asking a person to act would
        # make the drawer noise, and noise is how a drawer stops being read.
        record_drain_outcome(db, {"claimed": 1, "done": 0, "failed": 1, "dead": 0, "skipped": 0})
        entries = db.list_activity(limit=10)
        assert len(entries) == 1
        assert entries[0]["operation"] == "catalog"
        assert "incomplete" in entries[0]["summary"]

    def test_the_rfa_carries_the_link_to_the_publish_queue(self, db, project):
        record_drain_outcome(db, {"claimed": 1, "dead": 1, "failed": 0, "done": 0, "skipped": 0},
                             troubled_runs={"run-x": "p"})
        entry = db.list_activity(limit=10)[0]
        links = [i for i in (entry.get("items") or []) if i.get("link")]
        assert links and links[0]["link"] == "admin-outbox"
        assert links[0]["run_id"] == "run-x", (
            "an RFA saying publishes are stuck is only actionable if it says which"
        )


class TestResourceListIsQueued:
    def test_it_attaches_the_collection_to_its_parent(self, db, project):
        from resource_explorer.egeria_outbox import enqueue_resource_list

        calls = []

        class CM:
            def attach_collection(self, parent, coll):
                calls.append((parent, coll))

        enqueue_resource_list(db, "inv-1", "proj-guid", "coll-guid", run_id="rl")
        summary = drain_outbox(db, OutboxClients(collection_manager=CM()), lambda qn: "",
                               run_id="rl")
        assert summary["done"] == 1
        assert calls == [("proj-guid", "coll-guid")]

    def test_a_failed_attach_survives_as_a_retryable_row(self, db, project):
        # Previously this failure went into PromotionResult.errors and was
        # forgotten, leaving a Project and Collection that both exist, unlinked.
        from resource_explorer.egeria_outbox import enqueue_resource_list

        class CM:
            def attach_collection(self, parent, coll):
                raise RuntimeError("no permission")

        enqueue_resource_list(db, "inv-1", "p", "c", run_id="rl")
        drain_outbox(db, OutboxClients(collection_manager=CM()), lambda qn: "", run_id="rl")
        rows = db.list_outbox_elements(run_id="rl")
        assert rows[0]["status"] == "failed"
        assert "no permission" in rows[0]["last_error"]


# ── A duplicate-name 409 means "already created", not "failed" ──────────────
#
# Live, 2026-09-01. The pre-fix secret scan enqueued ~48,500 annotations per
# run; some were written to Egeria, then retried. Egeria answered:
#
#   OMAG-COMMON-409-001 ... parameter name qualifiedName is defined as a
#   unique property and value Annotation::egeria_python_git::... is not
#   available for use
#
# which is proof the element EXISTS. It was counted as a write failure, so
# those rows retried a write that had already succeeded — an exception storm
# on the console and rows that could never complete.
#
# apply_element already looks up before creating. The 409 means that lookup
# MISSED something Egeria can see, so the fix is not to skip the lookup but
# to repeat it on rejection and adopt the GUID.
_DUPE = (
    "SERVER_ERROR_500 ... relatedHTTPCode= `409` ... OMAG-COMMON-409-001 Method "
    "createMetadataElementInStore ... parameter name qualifiedName is defined as "
    "a unique property and value Annotation::x::y::1 is not available for use"
)


class TestDuplicateQualifiedName:
    def _row(self):
        return {"id": 1, "egeria_guid": "", "qualified_name": "Annotation::x::y::1",
                "payload_json": "{}", "element_kind": "annotation"}

    def test_a_rejected_duplicate_is_adopted_when_it_can_be_found(self, monkeypatch):
        """Known-negative: remove the 409 branch and this raises instead of
        returning the GUID — which is the retry-forever behaviour."""
        from resource_explorer import egeria_outbox as ob

        monkeypatch.setitem(ob._CREATORS, "annotation",
                            lambda clients, payload: (_ for _ in ()).throw(RuntimeError(_DUPE)))
        lookups = []

        def _find(qn):
            lookups.append(qn)
            return "" if len(lookups) == 1 else "guid-from-second-lookup"

        assert ob.apply_element(self._row(), None, _find) == "guid-from-second-lookup"
        assert len(lookups) == 2, "the lookup must be REPEATED after the rejection"

    def test_an_unfindable_duplicate_is_an_error_not_a_silent_success(self, monkeypatch):
        """Egeria will not create it and we cannot find it. Marking the row
        done would claim success with no GUID to show for it."""
        from resource_explorer import egeria_outbox as ob

        monkeypatch.setitem(ob._CREATORS, "annotation",
                            lambda clients, payload: (_ for _ in ()).throw(RuntimeError(_DUPE)))
        with pytest.raises(ob.OutboxApplyError, match="invisible to our lookup"):
            ob.apply_element(self._row(), None, lambda qn: "")

    def test_an_ordinary_failure_still_fails(self, monkeypatch):
        """The guard must not swallow real write errors — a message that is
        not the duplicate signature propagates untouched."""
        from resource_explorer import egeria_outbox as ob

        monkeypatch.setitem(ob._CREATORS, "annotation",
                            lambda clients, payload: (_ for _ in ()).throw(RuntimeError("connection refused")))
        with pytest.raises(RuntimeError, match="connection refused"):
            ob.apply_element(self._row(), None, lambda qn: "")

    def test_a_passing_mention_of_409_is_not_enough(self):
        """Both markers are required, so an unrelated error that happens to
        contain '409' is not mistaken for an already-created element."""
        from resource_explorer.egeria_outbox import _is_duplicate_qualified_name

        assert not _is_duplicate_qualified_name(RuntimeError("gateway said 409"))
        assert _is_duplicate_qualified_name(RuntimeError(_DUPE))


# ── The enqueue cap ─────────────────────────────────────────────────────────
#
# 2026-09-01: a pre-fix Committed Secrets run produced 48,583 findings and
# queued one Egeria element per finding. Two runs left 48,113 rows pending and
# 9,164 already written into the catalog before the drain was stopped by hand.
# Nothing in the enqueue path knew how large a batch was.
#
# The cap is a safety net against a rule regression or a pathological
# resource, sized from measured runs: largest legitimate 522 (data_prep_kit,
# 357 per-file SchemaAnalysisAnnotations), pathological 8,980 and 48,583.
class TestEnqueueCap:
    def _anns(self, n):
        from resource_explorer.surveyors.survey_report import ClassificationAnnotation
        return [ClassificationAnnotation(summary=f"a{i}", analysis_step="S")
                for i in range(n)]

    def test_a_normal_run_is_untouched(self, db):
        from resource_explorer import egeria_outbox as ob
        ids = ob.enqueue_annotations(db, "repo", "p", self._anns(522),
                                     "rg", "QN::p::t", run_id="r")
        assert len(ids) == 522, "a legitimate 522-annotation run must not be capped"

    def test_an_oversized_run_is_capped(self, db, monkeypatch):
        from resource_explorer import egeria_outbox as ob
        monkeypatch.setattr(ob, "_MAX_ANNOTATIONS_PER_RUN", 10)
        ids = ob.enqueue_annotations(db, "repo", "p", self._anns(50),
                                     "rg", "QN::p::t", run_id="r")
        assert len(ids) == 10

    def test_the_cap_keeps_original_indices(self, db, monkeypatch):
        """The known-negative that matters most. qualified_name is
        f"{prefix}::{i}" and that string is the retry-convergence identity.
        Renumbering after a cap would mint a different qualifiedName for the
        same annotation on a re-run, so a replay would create a SECOND element
        instead of converging on the one already written — the exact
        duplication the prefix contract exists to prevent."""
        from resource_explorer import egeria_outbox as ob
        monkeypatch.setattr(ob, "_MAX_ANNOTATIONS_PER_RUN", 3)
        ob.enqueue_annotations(db, "repo", "p", self._anns(10),
                               "rg", "QN::p::t", run_id="r")
        with db._conn() as conn:
            names = [r["qualified_name"] for r in conn.execute(
                "SELECT qualified_name FROM egeria_outbox ORDER BY id").fetchall()]
        assert names == ["QN::p::t::0", "QN::p::t::1", "QN::p::t::2"]

    def test_the_cap_keeps_the_head_so_summaries_survive(self, db, monkeypatch):
        """Surveyors emit summary-then-detail — secret_scan's ruleset-freshness
        and scan-summary annotations are indices 0 and 1, with per-match
        findings after. A head cap keeps what describes the run."""
        from resource_explorer import egeria_outbox as ob
        from resource_explorer.surveyors.survey_report import ClassificationAnnotation
        anns = [ClassificationAnnotation(summary="SCAN SUMMARY", analysis_step="S")]
        anns += [ClassificationAnnotation(summary=f"match {i}", analysis_step="S")
                 for i in range(50)]
        monkeypatch.setattr(ob, "_MAX_ANNOTATIONS_PER_RUN", 3)
        ob.enqueue_annotations(db, "repo", "p", anns, "rg", "QN::p::t", run_id="r")
        with db._conn() as conn:
            first = conn.execute(
                "SELECT payload_json FROM egeria_outbox ORDER BY id LIMIT 1"
            ).fetchone()["payload_json"]
        assert "SCAN SUMMARY" in first

    def test_capping_is_logged_loudly(self, db, monkeypatch, caplog):
        """A silently truncated publish looks exactly like a complete one."""
        import logging
        from resource_explorer import egeria_outbox as ob
        monkeypatch.setattr(ob, "_MAX_ANNOTATIONS_PER_RUN", 2)
        with caplog.at_level(logging.WARNING):
            ob.enqueue_annotations(db, "repo", "p", self._anns(9),
                                   "rg", "QN::p::t", run_id="r")
        assert any("capped" in r.message.lower() and "NOT published" in r.message
                   for r in caplog.records), caplog.text

    def test_the_cap_is_above_every_legitimate_run_observed(self):
        """Sized from data, not taste. If this is ever lowered below 522 it
        would clip a run that really happened."""
        from resource_explorer.egeria_outbox import _MAX_ANNOTATIONS_PER_RUN
        assert _MAX_ANNOTATIONS_PER_RUN > 522


class TestTheClaimActuallyClaims:
    """The defect this was written for, 2026-09-02.

    `claim_due_outbox_elements` was a plain SELECT with no locking and no
    status transition, and `drain_outbox` marked a row done only AFTER its
    create succeeded. Two drainers therefore took the same rows. Its docstring
    asserted the opposite ("the drain marks each row in flight as it takes
    it"), which is worse than silence: the next reader checks, finds the claim
    described, and concludes it is handled.

    The usual second drainer is not a person — it is scheduler.py's loop inside
    any running `resource-explorer web`, firing every 900s whether anyone is at
    the keyboard or not.
    """

    def test_two_claimers_never_get_the_same_row(self, db, project):
        ids = [db.enqueue_outbox_element("repo", project, "annotation", f"A::{i}", {})
               for i in range(6)]
        first = db.claim_due_outbox_elements()
        second = db.claim_due_outbox_elements()
        assert {r["id"] for r in first} == set(ids)
        assert second == [], "a second claimer must not re-take held rows"

    def test_a_claim_is_visible_as_running(self, db, project):
        db.enqueue_outbox_element("repo", project, "annotation", "A::0", {})
        db.claim_due_outbox_elements()
        assert db.outbox_counts() == {"running": 1}

    def test_peek_does_not_claim(self, db, project):
        """Otherwise looking at the queue empties it for the real drainer."""
        db.enqueue_outbox_element("repo", project, "annotation", "A::0", {})
        db.peek_due_outbox_elements()
        db.peek_due_outbox_elements()
        assert db.outbox_counts() == {"pending": 1}
        assert len(db.claim_due_outbox_elements()) == 1

    def test_a_killed_drainer_does_not_strand_its_rows_forever(self, db, project):
        """Exactly what happened on 2026-09-02: a batch was pkill'd mid-run.
        Without a lease those rows sit in 'running' and no retry ever sees
        them again — a quieter version of dead-lettering."""
        from datetime import datetime, timedelta
        from resource_explorer.registry import CLAIM_LEASE_SECONDS

        # Deliberately NOT derived from CLAIM_LEASE_SECONDS. A first version
        # computed this offset from the constant, so inflating the constant
        # moved both sides of the comparison equally and the test passed with
        # the lease effectively infinite. The claim here is a bounded one — a
        # stranded row is reclaimable within the hour — and it has to be
        # written down independently to mean anything.
        assert CLAIM_LEASE_SECONDS <= 3600, "the lease must stay within the hour this pins"

        db.enqueue_outbox_element("repo", project, "annotation", "A::0", {})
        db.claim_due_outbox_elements()                     # claimer then "dies"
        assert db.claim_due_outbox_elements() == [], "still held inside the lease"
        an_hour_on = (datetime.utcnow() + timedelta(hours=1, minutes=1)).isoformat()
        assert len(db.claim_due_outbox_elements(now=an_hour_on)) == 1, \
            "a row held by a dead drainer must be reclaimable within the hour"

    def test_an_outage_hands_the_claim_back_rather_than_holding_it(self, db, project, monkeypatch):
        """A claim taken and then not even attempted must not park the row for
        the whole lease — that is the slow version of burning an attempt on
        'Egeria was down', which drain_outbox explicitly refuses to do.

        _default_clients is patched to raise, exactly as the sibling test does.
        Passing (None, None) does NOT reach the no-client branch: it falls
        through to the real resolver and makes a live platform call, which is
        how the first version of this test hit Egeria for real.
        """
        db.enqueue_outbox_element("repo", project, "annotation", "A::0", {})
        monkeypatch.setattr(
            "resource_explorer.egeria_outbox._default_clients",
            lambda: (_ for _ in ()).throw(RuntimeError("no platform")),
        )
        summary = drain_outbox(db)
        assert summary["skipped"] == 1
        assert db.outbox_counts() == {"pending": 1}, "released, not left running"


class TestTheClaimSqlIsValidOnPostgres:
    """The SQLite tier cannot see this class of bug, so pin it directly.

    The first version of the atomic claim kept the dependency check as a
    LEFT JOIN and appended FOR UPDATE SKIP LOCKED. Every test in this file
    passed — they run on SQLite, which ignores both. Against the real backend
    it raised `FeatureNotSupported: FOR UPDATE cannot be applied to the
    nullable side of an outer join`, and the only reason it surfaced was three
    unrelated Postgres-backed route tests failing in the full suite.
    """

    def _claim_sql(self):
        """The claim's source with COMMENTS STRIPPED, lifted rather than
        transcribed — a hand-copied query would pass while the real one was
        broken.

        Comments are removed because the first version of this test failed on
        the word "LEFT JOIN" inside the comment explaining why there is no
        LEFT JOIN. A check that matches prose is not checking the code.
        """
        import inspect
        from resource_explorer.registry import ProjectRegistry
        src = inspect.getsource(ProjectRegistry.claim_due_outbox_elements)
        return "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))

    def test_the_claim_does_not_combine_for_update_with_an_outer_join(self):
        src = self._claim_sql()
        assert "FOR UPDATE" in src, "fixture assumption: the claim locks rows"
        assert "LEFT JOIN" not in src and "RIGHT JOIN" not in src, (
            "Postgres rejects FOR UPDATE on the nullable side of an outer join. "
            "Express the dependency check as a correlated EXISTS instead.")

    def test_the_dependency_gate_still_exists_in_some_form(self):
        """Removing the join must not quietly remove the rule it enforced:
        an annotation may not be attempted before the report it hangs off."""
        src = self._claim_sql()
        assert "depends_on_id" in src and "EXISTS" in src

    def test_a_row_whose_dependency_is_unfinished_is_not_claimed(self, db, project):
        """The behaviour itself, not just the SQL shape."""
        parent = db.enqueue_outbox_element("repo", project, "report", "R::0", {})
        child = db.enqueue_outbox_element("repo", project, "annotation", "A::0", {},
                                          depends_on_id=parent)
        claimed = {r["id"] for r in db.claim_due_outbox_elements()}
        assert parent in claimed and child not in claimed
        db.mark_outbox_done(parent, "guid-1")
        assert child in {r["id"] for r in db.claim_due_outbox_elements()}


class TestDefaultClientsIncludesCollectionManager:
    """2026-09-04, real bug found live: _default_clients() (what the
    SCHEDULED drain actually uses — drain_outbox()'s own default when no
    clients= is passed) built discovery/metadata_expert but never a
    collection_manager. Every collection_membership row it drained failed
    with "This element needs the 'collection_manager' client, which the
    drain was not given" — confirmed in the live server log, same 3 rows
    failing on every 15-minute cycle. This is exactly how a blueprint could
    materialize (a different outbox kind) while its members never actually
    got attached. Regression guard: _default_clients()'s OutboxClients must
    carry a non-None collection_manager, sourced from EgeriaPublisher's own
    connected client (not a second, independent connection)."""

    def test_default_clients_carries_a_collection_manager(self, monkeypatch):
        from unittest.mock import MagicMock

        from resource_explorer import egeria_outbox as mod

        fake_publisher = MagicMock()
        fake_publisher._collection_manager = "the-real-collection-manager-client"
        fake_publisher._connect = MagicMock()
        monkeypatch.setattr(
            "resource_explorer.surveyors.egeria_publisher.EgeriaPublisher",
            lambda **_kw: fake_publisher,   # the drain now hands it its service identity
        )

        clients, _find_guid = mod._default_clients()

        assert clients.collection_manager == "the-real-collection-manager-client"


class TestDeferredLinksWaitForTheirReferents:
    """run-in-background plan: a background publish enqueues its evidence
    links BEFORE the annotations they reference are applied, keyed by outbox
    row id rather than GUID. `apply_element` must resolve those row ids at
    APPLY time and leave the link queued — not failed, not dead — until both
    referents have landed."""

    def test_a_link_row_id_form_is_left_pending_until_referents_land(self, db, project):
        from resource_explorer.egeria_outbox import (
            OutboxClients, drain_outbox, enqueue_annotation_links,
        )

        summary_id = _enqueue(db, project, qn="Annotation::p::1::0")
        evidence_id = _enqueue(db, project, qn="Annotation::p::1::1")
        [link_id] = enqueue_annotation_links(
            db, "repo", project,
            [{"summary_row_id": summary_id, "evidence_row_id": evidence_id}],
            run_id="r::links",
        )

        # Neither annotation has been applied yet — draining just the link
        # run_id must not fail or dead-letter it.
        discovery = type("D", (), {"create_annotation": lambda *a, **k: "should-not-run"})()
        summary = drain_outbox(db, OutboxClients(discovery=discovery),
                               lambda qn: "", run_id="r::links")
        assert summary["done"] == 0
        assert summary["failed"] == 0
        assert summary["dead"] == 0
        assert summary["skipped"] == 1

        row = db.claim_due_outbox_elements(run_id="r::links")
        assert len(row) == 1, "the link must still be claimable — pending, not stuck"
        assert row[0]["status"] == "running"  # claim_due_outbox_elements marks it
        assert row[0]["attempts"] == 0, "a deferred row must not burn a retry attempt"
        assert "not yet applied" in (row[0]["last_error"] or "")

    def test_the_link_applies_once_both_referents_are_done(self, db, project):
        from unittest.mock import MagicMock

        from resource_explorer.egeria_outbox import (
            OutboxClients, drain_outbox, enqueue_annotation_links,
        )

        summary_id = _enqueue(db, project, qn="Annotation::p::1::0")
        evidence_id = _enqueue(db, project, qn="Annotation::p::1::1")
        db.mark_outbox_done(summary_id, "guid-summary")
        db.mark_outbox_done(evidence_id, "guid-evidence")
        enqueue_annotation_links(
            db, "repo", project,
            [{"summary_row_id": summary_id, "evidence_row_id": evidence_id}],
            run_id="r::links",
        )

        metadata_expert = MagicMock()
        metadata_expert.create_related_elements.return_value = "guid-link"
        summary = drain_outbox(db, OutboxClients(metadata_expert=metadata_expert),
                               lambda qn: "", run_id="r::links")
        assert summary["done"] == 1
        assert summary["skipped"] == 0
        body = metadata_expert.create_related_elements.call_args.kwargs["body"]
        assert body["metadataElement1GUID"] == "guid-summary"
        assert body["metadataElement2GUID"] == "guid-evidence"

    def test_a_link_with_no_registry_resolver_is_deferred_not_failed(self, db, project):
        """apply_element called with no resolve_row_guids (the 3-positional-arg
        form every pre-existing caller uses) must not misread a row-id-keyed
        link as a write failure — it has no way to resolve it, and that is
        exactly the "not ready" case, not a broken row."""
        from resource_explorer.egeria_outbox import (
            OutboxClients, OutboxNotReadyError, apply_element,
        )

        row = {"id": 1, "egeria_guid": "", "qualified_name": "AnnotationExtension::row1::row2",
               "element_kind": "annotation_link",
               "payload_json": json.dumps({"summary_row_id": 1, "evidence_row_id": 2})}

        with pytest.raises(OutboxNotReadyError):
            apply_element(row, OutboxClients(), lambda qn: "")


class TestCompletePublishRunIfDone:
    """registry.complete_publish_run_if_done — the flip from `published:
    "queued"` to `published: True` once a background publish's outbox rows
    (annotations AND their `::links` companion) have all landed AND its
    `::read_back` row is done (2026-10-14: published means read back)."""

    def _read_back(self, db, project, run_id, *, done=True):
        from resource_explorer.egeria_outbox import enqueue_publish_read_back

        row = enqueue_publish_read_back(db, "repo", project, run_id, "report-guid")
        if done:
            db.mark_outbox_done(row, "report-guid", note="read back")
        return row

    def _activity_row(self, db, publish_run_id, published="queued"):
        from resource_explorer.activity_logger import log_analysis_run

        entry_id = log_analysis_run(
            db, "repo", "p", "p", "ok", "2 annotation(s); 2 queued", "fake_analysis",
            published=published,
        )
        db.update_activity_status(
            entry_id, "ok", detail=json.dumps({"published": published}),
            publish_run_id=publish_run_id,
        )
        return entry_id

    def test_flips_to_true_once_the_run_is_read_back(self, db, project):
        """Every write done is not published: a run with no read-back gets one queued (a run enqueued before the
        read-back existed), and only that row, done, flips it."""
        run_id = "Annotation::p::2026-01-01T00:00:00"
        db.enqueue_outbox_element("repo", project, "annotation", "Annotation::p::1::0",
                                  {"class": "X", "parentGUID": "report-guid-1"}, run_id=run_id)
        row = db.list_outbox_elements(run_id=run_id)[0]["id"]
        db.mark_outbox_done(row, "guid-1")
        entry_id = self._activity_row(db, run_id)

        assert db.complete_publish_run_if_done(run_id) is False
        assert json.loads(db.get_activity(entry_id)["detail"])["published"] == "queued"
        [rb] = db.list_outbox_elements(run_id=f"{run_id}::read_back")
        assert rb["element_kind"] == "publish_read_back" and rb["status"] == "pending"
        assert json.loads(rb["payload_json"])["report_guid"] == "report-guid-1"
        assert db.get_activity(entry_id)["publish_proof"]["state"] == "queued"

        db.mark_outbox_done(rb["id"], "report-guid-1", note="read back")
        assert db.complete_publish_run_if_done(run_id) is True
        entry = db.get_activity(entry_id)
        detail = json.loads(entry["detail"])
        assert detail["published"] is True
        assert "published_at" in detail
        assert "published · read back at" in entry["summary"]
        assert entry["publish_proof"]["state"] == "read_back"

    def test_does_not_flip_while_a_row_is_still_pending(self, db, project):
        run_id = "Annotation::p::2026-01-01T00:00:01"
        _enqueue(db, project, qn="Annotation::p::2::0", run_id=run_id)  # never marked done
        entry_id = self._activity_row(db, run_id)

        assert db.complete_publish_run_if_done(run_id) is False
        detail = json.loads(db.get_activity(entry_id)["detail"])
        assert detail["published"] == "queued"

    def test_waits_for_the_links_companion_run_too(self, db, project):
        run_id = "Annotation::p::2026-01-01T00:00:02"
        row = _enqueue(db, project, qn="Annotation::p::3::0", run_id=run_id)
        db.mark_outbox_done(row, "guid-3")
        # The evidence link, enqueued under the "::links" companion run_id,
        # is still pending — the group is not done yet.
        _enqueue(db, project, kind="annotation_link",
                qn="AnnotationExtension::row1::row2", run_id=f"{run_id}::links")
        entry_id = self._activity_row(db, run_id)

        assert db.complete_publish_run_if_done(run_id) is False
        assert json.loads(db.get_activity(entry_id)["detail"])["published"] == "queued"

    def test_does_not_flip_an_activity_row_that_is_not_queued(self, db, project):
        """Idempotent, and safe to call on a run that was never deferred at
        all — an activity row whose `published` is already True/False must
        not be touched."""
        run_id = "Annotation::p::2026-01-01T00:00:03"
        row = _enqueue(db, project, qn="Annotation::p::4::0", run_id=run_id)
        db.mark_outbox_done(row, "guid-4")
        entry_id = self._activity_row(db, run_id, published=True)

        assert db.complete_publish_run_if_done(run_id) is False
        assert json.loads(db.get_activity(entry_id)["detail"])["published"] is True

    def test_no_rows_for_the_run_id_is_a_no_op(self, db):
        assert db.complete_publish_run_if_done("never-enqueued") is False

    def test_empty_run_id_is_a_no_op(self, db):
        assert db.complete_publish_run_if_done("") is False

    def test_drain_outbox_triggers_the_flip_on_its_own(self, db, project):
        """The integration point: drain_outbox must call this itself once a
        run's last row lands, not leave it to a separate caller."""
        run_id = "Annotation::p::2026-01-01T00:00:04"
        from unittest.mock import create_autospec

        from pyegeria.omvs.data_discovery import DataDiscovery

        discovery = create_autospec(DataDiscovery, instance=True, spec_set=True)
        discovery.create_annotation.return_value = "guid-5"
        # The live shape (probed 2026-10-14): a list with the GUID at elementHeader.guid, then "No elements found".
        discovery.get_new_annotations.side_effect = lambda guid, **k: (
            [{"elementHeader": {"guid": "guid-5"}, "properties": {}}] if not k.get("start_from")
            else "No elements found")
        row = db.enqueue_outbox_element(
            "repo", project, "annotation", "Annotation::p::5::0",
            {"class": "NewElementRequestBody"}, run_id=run_id,
        )
        self._read_back(db, project, run_id, done=False)
        entry_id = self._activity_row(db, run_id)

        from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

        summary = drain_outbox(db, OutboxClients(discovery=discovery), lambda qn: "")
        assert summary["done"] == 2, summary
        detail = json.loads(db.get_activity(entry_id)["detail"])
        assert detail["published"] is True

    def test_a_dead_row_reports_false_not_true(self, db, project):
        """Review fix on 84f4851: a mix of done+dead is NOT success. All
        terminal but one write permanently failed must flip `published` to
        False (the existing 'needs a human' state, which renders the ☁
        Publish retry button) — never to True, and never to a fifth state."""
        run_id = "Annotation::p::2026-01-01T00:00:05"
        good = _enqueue(db, project, qn="Annotation::p::6::0", run_id=run_id)
        db.mark_outbox_done(good, "guid-6")
        bad = _enqueue(db, project, qn="Annotation::p::6::1", run_id=run_id)
        db.mark_outbox_failed(bad, "permission denied", max_attempts=1)
        entry_id = self._activity_row(db, run_id)

        assert db.complete_publish_run_if_done(run_id) is True
        detail = json.loads(db.get_activity(entry_id)["detail"])
        assert detail["published"] is False
        assert detail["published_failed_count"] == 1
        summary = db.get_activity(entry_id)["summary"]
        assert "1 Egeria write(s) failed permanently" in summary
        assert "Publish Queue" in summary

    def test_all_done_with_no_dead_rows_still_reports_true(self, db, project):
        """The other half of the same guard — sabotaging "any dead ->
        False" into "any terminal -> False" would break this."""
        run_id = "Annotation::p::2026-01-01T00:00:06"
        row = _enqueue(db, project, qn="Annotation::p::7::0", run_id=run_id)
        db.mark_outbox_done(row, "guid-7")
        self._read_back(db, project, run_id)
        entry_id = self._activity_row(db, run_id)

        assert db.complete_publish_run_if_done(run_id) is True
        detail = json.loads(db.get_activity(entry_id)["detail"])
        assert detail["published"] is True
        assert "published_failed_count" not in detail

    def test_pending_published_record_is_applied_on_the_true_flip(self, db, project):
        """Review fix on 84f4851: the badge-table writes a deferred publish
        stashed must be made HERE, at the point they become true — not at
        enqueue time. Registry-only calls, exercised directly since
        EgeriaPublisher's own version of this is covered in test_workflows.py."""
        run_id = "Annotation::p::2026-01-01T00:00:07"
        row = _enqueue(db, project, qn="Annotation::p::8::0", run_id=run_id)
        db.mark_outbox_done(row, "guid-8")
        self._read_back(db, project, run_id)
        entry_id = self._activity_row(db, run_id)
        db.update_activity_status(
            entry_id, "ok",
            detail=json.dumps({
                "published": "queued",
                "pending_published_record": {
                    "slug": project,
                    "annotation_types": ["ResourceMeasureAnnotation"],
                    "analyses": ["security_scan"],
                    "report_guid": "report-guid-8",
                },
            }),
        )

        assert db.complete_publish_run_if_done(run_id) is True
        assert db.get_last_published_annotation_types(project)
        assert db.get_last_published_analyses(project)
        detail = json.loads(db.get_activity(entry_id)["detail"])
        assert "pending_published_record" not in detail, (
            "consumed at the flip, not left sitting in the detail forever"
        )

    def test_pending_published_record_is_not_applied_when_dead(self, db, project):
        """The badge tables must not record a publish that did not actually
        land — a dead row means the run's outbox rows did NOT all reach
        Egeria, so there is nothing true to record yet."""
        run_id = "Annotation::p::2026-01-01T00:00:08"
        bad = _enqueue(db, project, qn="Annotation::p::9::0", run_id=run_id)
        db.mark_outbox_failed(bad, "boom", max_attempts=1)
        entry_id = self._activity_row(db, run_id)
        db.update_activity_status(
            entry_id, "ok",
            detail=json.dumps({
                "published": "queued",
                "pending_published_record": {
                    "slug": project,
                    "annotation_types": ["ResourceMeasureAnnotation"],
                    "analyses": ["security_scan"],
                    "report_guid": "report-guid-9",
                },
            }),
        )

        assert db.complete_publish_run_if_done(run_id) is True
        assert db.get_last_published_annotation_types(project) == {}
        assert db.get_last_published_analyses(project) == {}


@pytest.mark.usefixtures("allow_example_platform")   # the fake entities name egeria.example
class TestDocSourceOutbox:
    """Egeria publish-state fix (2026-09-29,
    DOC-SOURCES-DECLARE-AND-PROBE-IMPLEMENTED.md).

    `doc_source_publish`/`doc_source_unpublish` reuse THIS outbox (not a new
    retry queue) for a documentation source declared/removed on a resource
    that is ALREADY published — previously the source stayed local-only
    until the resource's own next full re-publish. Both creators resolve the
    entity and (for publish) the local `doc_sources` row fresh at apply
    time, via a `ProjectRegistry()` they construct themselves — the
    `doc_registry` fixture below monkeypatches `ProjectRegistry.__init__` to
    share one prepared registry's state, the same trick
    `tests/test_doc_sources_routes.py`'s `client` fixture already uses.
    """

    @pytest.fixture
    def doc_registry(self, tmp_path, monkeypatch):
        from resource_explorer.registry import DatabaseEntity

        reg = ProjectRegistry(db_path=str(tmp_path / "docsrc.db"))
        reg.register_database(DatabaseEntity(
            slug="adventureworks", display_name="AdventureWorks", db_type="postgresql",
            host="localhost", port=5432, database_name="adventureworks",
            egeria_url="https://egeria.example", egeria_server="view1",
            egeria_user="u", egeria_password="p", egeria_asset_guid="asset-1",
        ))
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", reg.__dict__) or None,
        )
        return reg

    def _publish_row(self, source_id: str, url: str = "https://docs.example/x") -> dict:
        return {
            "id": 1, "egeria_guid": "", "element_kind": "doc_source_publish",
            "qualified_name": f"ExternalReference::{url}",
            "payload_json": json.dumps({
                "entity_type": "database", "entity_slug": "adventureworks", "source_id": source_id,
            }),
        }

    def _unpublish_row(self, ref_guid: str = "ref-1") -> dict:
        return {
            "id": 1, "egeria_guid": "", "element_kind": "doc_source_unpublish",
            "qualified_name": f"ExternalReferenceRemoval::{ref_guid}",
            "payload_json": json.dumps({
                "entity_type": "database", "entity_slug": "adventureworks", "ref_guid": ref_guid,
            }),
        }

    def test_publish_creator_writes_the_ref_guid_back_onto_the_local_row(self, doc_registry, monkeypatch):
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: {"ok": True, "ref_guid": "ref-1", "link_guid": "link-1", "error": ""},
        )
        monkeypatch.setattr("resource_explorer.doc_source_egeria.ref_guid_exists", lambda *a, **kw: True)

        guid = apply_element(self._publish_row(row["id"]), OutboxClients(), lambda qn: "SHOULD-NOT-BE-CALLED")

        assert guid == "ref-1"
        updated = doc_registry.get_doc_source("database", "adventureworks", row["id"])
        assert updated["egeria_external_ref_guid"] == "ref-1"
        assert updated["egeria_link_relationship_guid"] == "link-1"

    def test_generic_lookup_is_never_consulted_for_doc_source_publish(self, doc_registry, monkeypatch):
        # If the outbox's generic step-2 qualifiedName search DID run for
        # this kind, it could adopt a same-qualifiedName ExternalReference
        # from an unrelated feature (the homepage reference uses the exact
        # same "ExternalReference::<url>" convention) and mark the row done
        # WITHOUT ever linking it to this asset — the bug _SELF_RESOLVING_
        # KINDS exists to prevent. A poisoned lookup proves it is skipped.
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: {"ok": True, "ref_guid": "ref-1", "link_guid": "link-1", "error": ""},
        )
        monkeypatch.setattr("resource_explorer.doc_source_egeria.ref_guid_exists", lambda *a, **kw: True)

        def poisoned(qn):
            raise AssertionError("generic find_element_guid must not be called for doc_source_publish")

        assert apply_element(self._publish_row(row["id"]), OutboxClients(), poisoned) == "ref-1"

    def test_publish_failure_raises_so_the_drain_retries(self, doc_registry, monkeypatch):
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: {"ok": False, "ref_guid": "", "link_guid": "", "error": "boom"},
        )

        with pytest.raises(OutboxApplyError, match="boom"):
            apply_element(self._publish_row(row["id"]), OutboxClients(), lambda qn: "")

    def test_publish_is_a_no_op_when_the_local_row_was_removed_before_the_drain(self, doc_registry, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: calls.append(1) or
                {"ok": True, "ref_guid": "x", "link_guid": "", "error": ""},
        )

        guid = apply_element(self._publish_row("never-existed", "https://gone.example"),
                             OutboxClients(), lambda qn: "")

        assert guid == ""
        assert calls == [], "a source removed before its publish drained must not call Egeria at all"

    def test_unpublish_creator_detaches_and_deletes_the_one_reference(self, doc_registry, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.unpublish_doc_source",
            lambda ref_guid, asset_guid, **kw: calls.append((ref_guid, asset_guid)) or {"ok": True, "error": ""},
        )

        assert apply_element(self._unpublish_row("ref-1"), OutboxClients(), lambda qn: "") == "ref-1"
        assert calls == [("ref-1", "asset-1")]

    def test_unpublish_failure_raises_so_the_drain_retries(self, doc_registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.unpublish_doc_source",
            lambda ref_guid, asset_guid, **kw: {"ok": False, "error": "network down"},
        )

        with pytest.raises(OutboxApplyError, match="network down"):
            apply_element(self._unpublish_row("ref-1"), OutboxClients(), lambda qn: "")

    # ── Adoption-race fix, round 4 (2026-09-29) ──────────────────────────────
    # A ref guid ALONE must not short-circuit the publish creator: only a
    # row with BOTH ref_guid and link_guid already set is genuinely done.

    def test_publish_short_circuits_only_when_both_guids_are_already_set(
        self, doc_registry, monkeypatch,
    ):
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")
        doc_registry.set_doc_source_egeria_ref(
            "database", "adventureworks", row["id"], "ref-already", "link-already")

        def poisoned(source, asset_guid, **kw):
            raise AssertionError("publish_doc_source must not be called when ref+link are both set")

        monkeypatch.setattr("resource_explorer.doc_source_egeria.publish_doc_source", poisoned)

        guid = apply_element(self._publish_row(row["id"]), OutboxClients(), lambda qn: "SHOULD-NOT-BE-CALLED")

        assert guid == "ref-already"

    def test_publish_still_runs_and_links_when_ref_is_set_but_link_is_not(
        self, doc_registry, monkeypatch,
    ):
        # The exact self-heal shape: a row adopted by read-back carries a ref
        # guid but no link guid. Re-queueing its publish must actually LINK
        # it, not treat the bare ref guid as "already done" (the bug that
        # would have silently left it unlinked forever).
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")
        doc_registry.set_doc_source_egeria_ref("database", "adventureworks", row["id"], "ref-only")

        calls = []
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: calls.append(kw.get("known_ref_guid")) or
                {"ok": True, "ref_guid": "ref-only", "link_guid": "link-now", "error": ""},
        )
        monkeypatch.setattr("resource_explorer.doc_source_egeria.ref_guid_exists", lambda *a, **kw: True)

        guid = apply_element(self._publish_row(row["id"]), OutboxClients(), lambda qn: "SHOULD-NOT-BE-CALLED")

        assert guid == "ref-only"
        assert calls == ["ref-only"], "the known (already-adopted) ref guid must be passed through"
        updated = doc_registry.get_doc_source("database", "adventureworks", row["id"])
        assert updated["egeria_external_ref_guid"] == "ref-only"
        assert updated["egeria_link_relationship_guid"] == "link-now"

    # ── Proof-row rule (round 5, 2026-09-29) ─────────────────────────────────
    # "done" is a claim like any other state this system renders and needs
    # the same evidence-backing discipline: a row must not reach `done`
    # unless BOTH guids were actually written back AND verified. This is the
    # live incident's own shape at the outbox layer -- outbox row 68961
    # completed `done` with `egeria_guid = b9925119...` and an empty link,
    # silently, for exactly the reason these guards now refuse.

    def test_publish_does_not_mark_done_when_the_link_guid_is_missing(self, doc_registry, monkeypatch):
        # publish_doc_source's own best-effort link step can legitimately
        # return ok=True with an empty link_guid (a caught link exception,
        # see its own docstring) -- that must not read as "done" from the
        # outbox's point of view; it must retry.
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: {
                "ok": True, "ref_guid": "b9925119-dead", "link_guid": "", "error": "",
            },
        )

        with pytest.raises(OutboxApplyError, match="link"):
            apply_element(self._publish_row(row["id"]), OutboxClients(), lambda qn: "SHOULD-NOT-BE-CALLED")

        # And the row must NOT have been written back -- next drain retries
        # from scratch rather than reading a half-written row.
        updated = doc_registry.get_doc_source("database", "adventureworks", row["id"])
        assert updated["egeria_external_ref_guid"] == ""
        assert updated["egeria_link_relationship_guid"] == ""

    def test_publish_does_not_mark_done_when_the_ref_guid_fails_verification(self, doc_registry, monkeypatch):
        # Both guids come back non-empty, but the ref guid does not actually
        # resolve when re-checked -- the exact live-incident shape if the
        # verification step is skipped: "attempted" is not "verified".
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: {
                "ok": True, "ref_guid": "b9925119-dead", "link_guid": "link-1", "error": "",
            },
        )
        monkeypatch.setattr("resource_explorer.doc_source_egeria.ref_guid_exists", lambda *a, **kw: False)

        with pytest.raises(OutboxApplyError, match="does not resolve"):
            apply_element(self._publish_row(row["id"]), OutboxClients(), lambda qn: "SHOULD-NOT-BE-CALLED")

        updated = doc_registry.get_doc_source("database", "adventureworks", row["id"])
        assert updated["egeria_external_ref_guid"] == ""
        assert updated["egeria_link_relationship_guid"] == ""

    def test_publish_passes_the_unpublish_guard_bound_to_this_entity(self, doc_registry, monkeypatch):
        row = doc_registry.add_doc_source("database", "adventureworks", "https://docs.example/x")

        captured = {}
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source",
            lambda source, asset_guid, **kw: captured.update(kw) or
                {"ok": True, "ref_guid": "ref-1", "link_guid": "link-1", "error": ""},
        )
        monkeypatch.setattr("resource_explorer.doc_source_egeria.ref_guid_exists", lambda *a, **kw: True)

        apply_element(self._publish_row(row["id"]), OutboxClients(), lambda qn: "SHOULD-NOT-BE-CALLED")

        assert callable(captured.get("is_ref_unpublishing"))
        # Bound to THIS entity — a pending unpublish enqueued for a
        # different entity/slug must not register as unpublishing here.
        from resource_explorer.egeria_outbox import enqueue_doc_source_unpublish

        enqueue_doc_source_unpublish(doc_registry, "database", "some-other-db", "ref-elsewhere")
        assert captured["is_ref_unpublishing"]("ref-elsewhere") is False
        enqueue_doc_source_unpublish(doc_registry, "database", "adventureworks", "ref-here")
        assert captured["is_ref_unpublishing"]("ref-here") is True

    # ── Self-heal reopen fix, round 6 (2026-09-29) ───────────────────────────
    # Round 5 made `_create_doc_source_publish` verify a reused ref guid
    # before trusting it, and required BOTH guids + verification before a
    # row reaches `done`. That fix never RAN for the live-incident row
    # because the self-heal enqueue step (`web/routes/doc_sources.py::
    # _compute_egeria_state`) treated an existing `done` row — even one
    # whose claim predates round 5's guarantee — as "already handled" and
    # never re-queued anything. `registry.reopen_outbox_row` is the fix:
    # reset the SAME row to `pending` (not a second row) so the next drain
    # actually exercises round 5's verify-before-trust logic.
    #
    # This test reproduces the exact data shape from the live bug end to
    # end at the outbox/drain layer: a `done` row with `egeria_guid=X`, a
    # `doc_sources` row with `ref=X`/no link, `X` missing in Egeria — reopen
    # it, drain for real, and confirm the SAME row ends up `done` with a
    # NEW ref + a NEW link, not a second row alongside the old one.

    def test_reopening_a_done_row_with_a_dead_guid_and_draining_produces_a_new_ref_and_link(
        self, doc_registry, monkeypatch,
    ):
        row = doc_registry.add_doc_source("database", "adventureworks", "https://egeria.ai")
        doc_registry.set_doc_source_egeria_ref("database", "adventureworks", row["id"], "dead-guid-b99")
        # Enqueue as the original add would have, then simulate the
        # PRE-round-5 write-back that produced the live incident: `done`,
        # `egeria_guid` set to the now-dead ref, no link ever recorded.
        element_id = enqueue_doc_source_publish(
            doc_registry, "database", "adventureworks", row["id"], row["url"])
        doc_registry.mark_outbox_done(element_id, "dead-guid-b99")
        stuck = doc_registry.get_doc_source_outbox_row("database", "adventureworks", row["id"])
        assert stuck["status"] == "done"

        # `ref_guid_exists` reports the dead guid gone (the live fact — an
        # unrelated unpublish deleted it) and any fresh guid this drain
        # mints as present, so `publish_doc_source`'s own round-5 guard
        # abandons the dead guid and creates a new reference.
        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.ref_guid_exists",
            lambda guid, **kw: guid != "dead-guid-b99",
        )
        publish_calls = []

        def fake_publish_doc_source(source, asset_guid, **kw):
            publish_calls.append(kw.get("known_ref_guid"))
            # Mirrors publish_doc_source's real round-5 behavior at exactly
            # the boundary this test cares about: a known_ref_guid that
            # fails verification is abandoned in favor of a fresh reference.
            return {"ok": True, "ref_guid": "fresh-ref-guid", "link_guid": "fresh-link-guid", "error": ""}

        monkeypatch.setattr(
            "resource_explorer.doc_source_egeria.publish_doc_source", fake_publish_doc_source)

        # Reproduces exactly what `_compute_egeria_state`'s self-heal now
        # does for a not_catalogued row backed by a `done` outbox row.
        doc_registry.reopen_outbox_row(element_id, "test: proof no longer holds")
        reopened = doc_registry.get_doc_source_outbox_row("database", "adventureworks", row["id"])
        assert reopened["id"] == element_id, "reopened the SAME row, not a fresh one"
        assert reopened["status"] == "pending"
        assert reopened["egeria_guid"] == ""

        # A non-empty OutboxClients is required so `drain_outbox` doesn't hit
        # its own "no Egeria client available" early-out (`_default_clients()`
        # constructing a REAL pyegeria client against the live platform) —
        # the creator this test exercises never reads `clients` itself
        # (`publish_doc_source`/`ref_guid_exists` are monkeypatched directly
        # above), so any non-None stub satisfies it, matching this file's own
        # established pattern for a drain that doesn't need a real client
        # (e.g. `OutboxClients(discovery=object())` elsewhere in this file).
        summary = drain_outbox(doc_registry, OutboxClients(discovery=object()), lambda qn: "")

        assert summary["done"] == 1
        assert publish_calls == ["dead-guid-b99"], (
            "the dead guid was passed through so publish_doc_source's own "
            "verify-before-reuse guard is what abandons it, not this test"
        )
        final_row = doc_registry.get_doc_source_outbox_row("database", "adventureworks", row["id"])
        assert final_row["id"] == element_id, "still the same row — not a duplicate"
        assert final_row["status"] == "done"
        assert final_row["egeria_guid"] == "fresh-ref-guid"
        updated_source = doc_registry.get_doc_source("database", "adventureworks", row["id"])
        assert updated_source["egeria_external_ref_guid"] == "fresh-ref-guid"
        assert updated_source["egeria_link_relationship_guid"] == "fresh-link-guid"
        # No second doc_source_publish row was created for this element.
        all_rows = doc_registry.list_outbox_elements(entity_slug="adventureworks")
        publish_rows = [r for r in all_rows if r["element_kind"] == "doc_source_publish"]
        assert len(publish_rows) == 1

    def test_reopen_outbox_row_clears_status_guid_and_attempts_and_records_why(self, doc_registry):
        # Direct unit coverage of `reopen_outbox_row` itself, independent of
        # the doc-sources self-heal caller — kind-agnostic, so this also
        # covers the `doc_source_unpublish` shape (no self-heal call site
        # exists for that kind yet; this proves the primitive itself is
        # ready for one without inventing a call site that doesn't
        # correspond to a real bug — see that kind's own docstring).
        element_id = enqueue_doc_source_unpublish(doc_registry, "database", "adventureworks", "ref-1")
        doc_registry.mark_outbox_done(element_id, "ref-1")
        with doc_registry._conn() as conn:
            conn.execute("UPDATE egeria_outbox SET attempts=3, last_error='old failure' WHERE id=?",
                         (element_id,))

        doc_registry.reopen_outbox_row(element_id, "test: reference still resolves, retry deletion")

        rows = doc_registry.list_outbox_elements(entity_slug="adventureworks")
        row = next(r for r in rows if r["id"] == element_id)
        assert row["status"] == "pending"
        assert row["egeria_guid"] == ""
        assert row["attempts"] == 0
        assert row["completed_at"] == ""
        assert row["last_error"] == ""
        assert row["reopened_at"]
        assert row["reopen_reason"] == "test: reference still resolves, retry deletion"
        # It is still the SAME row (same qualified_name/element_kind) — a
        # reopen never touches the element's own identity.
        assert row["element_kind"] == "doc_source_unpublish"
        assert row["qualified_name"] == "ExternalReferenceRemoval::ref-1"


class TestElementScopedDrain:
    """Egeria publish-state fix round 3 (2026-09-29): `add_doc_source`/the
    DELETE handler must attempt the row they just enqueued immediately, not
    only rely on the scheduler's next 15-minute pass. `element_id` is the
    mechanism `drain_outbox`/`drain_outbox_row` use to do that — pinned here
    at the outbox layer (route-level behavior is
    `tests/test_doc_sources_routes.py::TestImmediateOutboxAttempt`).
    """

    def test_claim_due_outbox_elements_with_element_id_takes_only_that_row(self, db, project):
        first = _enqueue(db, project, qn="Annotation::p::2026-01-01T00:00:00::0")
        second = _enqueue(db, project, qn="Annotation::p::2026-01-01T00:00:00::1")

        claimed = db.claim_due_outbox_elements(element_id=second)

        assert [r["id"] for r in claimed] == [second]
        # The other row is untouched — still pending, still claimable on its
        # own, exactly as an unscoped drain would still find it.
        assert [r["id"] for r in db.peek_due_outbox_elements()] == [first]

    def test_element_id_scoping_respects_the_ordinary_due_gate(self, db, project):
        # A row not yet due (backoff not elapsed) must stay off-limits even
        # when named explicitly by id — element_id narrows WHICH rows are
        # eligible, it does not bypass the ordinary claim rules.
        row_id = _enqueue(db, project)
        db.mark_outbox_failed(row_id, "transient")  # sets a future next_attempt_at

        claimed = db.claim_due_outbox_elements(element_id=row_id)

        assert claimed == []

    def test_drain_outbox_with_element_id_applies_only_that_row(self, db, project):
        untouched = _enqueue(db, project, qn="Annotation::p::2026-01-01T00:00:00::0")
        target = _enqueue(db, project, qn="Annotation::p::2026-01-01T00:00:00::1")

        discovery = type("D", (), {"create_annotation": lambda self, body: {"guid": "g"}})()
        summary = drain_outbox(db, OutboxClients(discovery=discovery), lambda qn: "",
                               element_id=target)

        assert summary["claimed"] == 1
        assert summary["done"] == 1
        counts = db.outbox_counts()
        assert counts == {"done": 1, "pending": 1}
        assert [r["id"] for r in db.peek_due_outbox_elements()] == [untouched]

    def test_drain_outbox_row_is_the_same_mechanism_as_drain_outbox(self, db, project):
        # drain_outbox_row is a thin, named wrapper — not a second
        # implementation — so a failure there is reported and retried
        # exactly like any other outbox row, via the SAME mark_outbox_failed
        # bookkeeping the scheduler's own unscoped drain uses.
        from resource_explorer.egeria_outbox import drain_outbox_row

        row_id = _enqueue(db, project)

        class _RaisingDiscovery:
            def create_annotation(self, body):
                raise RuntimeError("Egeria unreachable")

        summary = drain_outbox_row(db, row_id, OutboxClients(discovery=_RaisingDiscovery()),
                                   lambda qn: "")

        assert summary["claimed"] == 1
        assert summary["failed"] == 1
        row = db.peek_due_outbox_elements(now=(datetime.utcnow() + timedelta(hours=1)).isoformat())
        assert row and row[0]["id"] == row_id
        assert row[0]["status"] == "failed"
        # Left exactly where the normal 15-minute scheduler drain would find
        # and retry it — not dead-lettered on a single failed attempt.
        assert row[0]["attempts"] == 1
