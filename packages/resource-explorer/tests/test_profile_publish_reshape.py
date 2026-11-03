"""Profile publish reshape (BRIEF-PROFILE-PUBLISH-RESHAPE, ruling 2026-10-22; owner's DDL go 2026-10-23).

Egeria's own survey re-links unchanged annotations to its new report and creates only changed ones; RE now does the
same for the value profile's per-column ResourceProfileAnnotations. Unchanged = the sha256 of the measure's canonical
JSON equals the hash on RE's own proof row of the last published annotation (`native_survey_annotations.content_hash`,
keyed by `item_key`), never a re-read of Egeria.

End to end through the real Survey press (the Q2 rig: real route, queue, executor, value profile, registry on temp
SQLite, database publisher) with Egeria faked by `create_autospec(<real pyegeria class>, spec_set=True)`, whose
`attach_annotation_to_report` is multi-link like Egeria's (a second attach of a pair makes a second relationship).
The fixture database has 5 measured columns (sales.orders x4, sales.empty x1).
"""
from __future__ import annotations

import copy
import json
import sqlite3
from unittest.mock import create_autospec

import pytest

from resource_explorer.surveyors.database.quality.profile_publish import (
    PROFILE_SHAPE_VERSION,
    RECREATED_NOTE,
    canonical_measures,
    content_hash,
    plan_profile_publish,
    profile_publish_words,
    table_of,
)
from resource_explorer.surveyors.database.quality.value_profile import (
    ANALYSIS_STEP,
    measure_key,
    table_key,
)
from resource_explorer.surveyors.survey_report import (
    ResourceMeasureAnnotation,
    ResourceProfileAnnotation,
)
from tests.test_engine_host_e4 import egeria  # noqa: F401 - fixture reused
from tests.test_quality_q2_survey_press_e2e import (  # noqa: F401 - fixtures and helpers reused
    _annotation_bodies,
    _by_class,
    _no_live_database,
    _press,
    _utc_minute,
    client,
    rig,
    world,
)
from tests.test_value_profile_quality_checks import PROFILE, SLUG, TIMEOUT, registry  # noqa: F401

PROFILE_EMPTY = PROFILE[("sales", "empty")]

PROFILE_CLASS = "ResourceProfileAnnotationProperties"


def _key(path: str) -> str:
    return measure_key(*path.split("."))


def _measure(path="sales.orders.id", value_count=None, **props) -> ResourceProfileAnnotation:
    json_properties = {"generalType": "N", "profileDates": {}, "sampleRatio": None, "ranAs": "surveyor"}
    json_properties.update(props)
    return ResourceProfileAnnotation(
        summary=f"Value profile: {path}", analysis_step=ANALYSIS_STEP, annotation_type_name="Value Profile",
        check_name="value_profile", item_key=_key(path), label="measured",
        value_count={"record_ct": 1000, "value_ct": 1000, "null_value_ct": 0} if value_count is None else value_count,
        json_properties=json_properties)


# ── the hash is faithful ───────────────────────────────────────────────────────────────────────────────────

def test_two_measures_differing_in_one_value_count_entry_hash_differently():
    a = _measure()
    b = _measure(value_count={"record_ct": 1000, "value_ct": 999, "null_value_ct": 0})
    assert canonical_measures(a) != canonical_measures(b)
    assert content_hash(a) != content_hash(b)


def test_two_equal_measures_hash_equally_and_the_full_compare_agrees():
    a, b = _measure(profileDates={"min_date": "2020-01-01"}), _measure(profileDates={"min_date": "2020-01-01"})
    assert a is not b
    assert content_hash(a) == content_hash(b)
    assert canonical_measures(a) == canonical_measures(b)          # the hash matches only what the full compare does


def test_a_reordered_json_properties_hashes_equally():
    a = _measure()
    b = _measure()
    b.json_properties = dict(reversed(list(a.json_properties.items())))
    b.value_count = dict(reversed(list(a.value_count.items())))
    assert list(a.json_properties) != list(b.json_properties)
    assert content_hash(a) == content_hash(b)


def test_who_ran_it_is_not_a_measure_but_a_date_or_a_present_absent_key_is():
    assert content_hash(_measure(ranAs="someone")) == content_hash(_measure(ranAs="else"))
    assert content_hash(_measure(profileDates={"max_date": "2026-10-01"})) != content_hash(
        _measure(profileDates={"max_date": "2026-10-02"}))
    assert content_hash(_measure(value_count={"value_ct": 0})) != content_hash(_measure(value_count={}))
    # numbers exactly: an int and a float are different measures, never equal by accident
    assert content_hash(_measure(value_count={"value_ct": 5})) != content_hash(_measure(value_count={"value_ct": 5.0}))


def test_the_shape_version_is_hashed_and_the_key_is_a_json_array():
    assert canonical_measures(_measure())["shape_version"] == PROFILE_SHAPE_VERSION
    assert _key("sales.orders.id") == '["sales","orders","id"]'
    # a dot inside a name is a different column, never the same key
    assert measure_key("a.b", "c", "d") != measure_key("a", "b.c", "d")
    assert table_of(measure_key("sales", "orders", "id")) == table_key("sales", "orders") == '["sales","orders"]'
    assert table_of("sales.orders.id") == "" and table_of(table_key("sales", "orders")) == ""


# ── round 2 (architect rulings 2026-10-24) ─────────────────────────────────────────────────────────────────

class _ClockConn:
    """The value profile's fake Postgres for a STATIC table whose `placed` column holds one date half an hour after
    the first profile: `future_date_ct` is computed from the `{RUN_DATE}` literal the statement carries, as Postgres
    would, so it moves with the clock while nothing in the table does."""

    PLACED = "2026-10-20 10:30:00"

    def __init__(self):
        from tests.test_value_profile_quality_checks import FakeConn

        self.inner = FakeConn(profile=copy.deepcopy(PROFILE))

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def execute_query_isolated(self, sql, params=()):
        import re

        rows = self.inner.execute_query_isolated(sql, params)
        run_date = re.search(r"> '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'", sql)
        for alias in re.findall(r"AS (c\d+__future_date_ct)", sql):
            rows[0][alias] = int(self.PLACED > run_date.group(1))
        return rows


def _profile_at(registry, at: str) -> dict[str, ResourceProfileAnnotation]:
    from contextlib import contextmanager

    from resource_explorer.surveyors.database.quality.value_profile import run_value_profile
    from tests.test_value_profile_quality_checks import BOUNDS

    conn = _ClockConn()

    @contextmanager
    def connect():
        yield conn

    out = run_value_profile(registry.get_database(SLUG), registry, connect, bounds=BOUNDS, surveyed_at=at,
                            ran_as="surveyor", clock=lambda: 0.0)
    return {a.item_key: a for a in out["annotations"] if isinstance(a, ResourceProfileAnnotation)}


def test_two_profiles_of_a_static_table_an_hour_apart_hash_equal(registry):
    """Fail-on-purpose (ruling (b)): the table does not change between 10:00 and 11:00, but its one date passes
    'now' at 10:30, so `future_date_ct` goes 1 -> 0. Hashed or published in `value_count`, every date column would
    read as changed on every press of a live database."""
    first, later = _profile_at(registry, "2026-10-20T10:00:00+00:00"), _profile_at(registry, "2026-10-20T11:00:00+00:00")
    placed = _key("sales.orders.placed")
    # RE's own rows keep the relative measure for the Future_Date check, and it did move
    assert {r["column_name"]: r["future_date_ct"] for r in registry.query_detail_rows(
        "database_value_profiles", SLUG, "2026-10-20T10:00:00+00:00") if r["table_name"] == "orders"}["placed"] == 1
    assert {r["column_name"]: r["future_date_ct"] for r in registry.query_detail_rows(
        "database_value_profiles", SLUG, "2026-10-20T11:00:00+00:00") if r["table_name"] == "orders"}["placed"] == 0
    # the annotation carries neither the count nor a clock-dependent hash; it says what the bounds were as of when
    assert "future_date_ct" not in first[placed].value_count
    assert first[placed].json_properties["profileDates"]["asOf"] == "2026-10-20T10:00:00Z"
    assert later[placed].json_properties["profileDates"]["asOf"] == "2026-10-20T11:00:00Z"
    assert set(first) == set(later)
    for key in first:
        assert content_hash(first[key]) == content_hash(later[key]), key


def test_the_shape_version_moved_so_round_1_hashes_never_match():
    """Every round-1 proof row hashed a different shape (future_date_ct in it): the first round-2 publish creates
    each measure once rather than matching an old hash by accident."""
    assert PROFILE_SHAPE_VERSION == 2


def test_a_relative_measure_on_an_old_annotation_is_not_hashed_either():
    a = _measure(value_count={"record_ct": 10, "future_date_ct": 3}, profileDates={"max_date": "2026-01-01",
                                                                                     "asOf": "2026-10-20T10:00:00Z"})
    b = _measure(value_count={"record_ct": 10}, profileDates={"max_date": "2026-01-01", "asOf": "2026-10-24T21:09:00Z"})
    assert content_hash(a) == content_hash(b)
    assert content_hash(b) != content_hash(_measure(value_count={"record_ct": 10},
                                                    profileDates={"max_date": "2026-01-02"}))


def _proposals(digest="d1", run_id="2026-10-24T21:09:00", proposed=3):
    from resource_explorer.surveyors.database.quality import propose
    from resource_explorer.surveyors.survey_report import RequestForActionAnnotation

    return RequestForActionAnnotation(
        summary=f"{proposed} quality checks proposed for coco", analysis_step=propose.ANALYSIS_STEP,
        annotation_type_name="Proposed Quality Checks", check_name=propose.CHECK_NAME, item_key=propose.ITEM_KEY,
        label="proposed", action_requested=f"review {proposed} proposed quality checks in Resource Explorer",
        action_target_name="coco", explanation=f"Resource Explorer proposed {proposed} quality checks in run {run_id}.",
        additional_properties={"runId": run_id, "proposedChecks": str(proposed), "proposedChecksDigest": digest})


def test_the_proposals_request_compares_on_its_checks_never_on_its_run():
    from resource_explorer.surveyors.database.quality.profile_publish import PROPOSALS, relink_kind

    assert relink_kind(_proposals()) == PROPOSALS
    assert content_hash(_proposals(run_id="a")) == content_hash(_proposals(run_id="b"))
    assert content_hash(_proposals(digest="d1")) != content_hash(_proposals(digest="d2"))   # same count, other checks
    assert content_hash(_proposals(proposed=3)) != content_hash(_proposals(proposed=4))


def test_the_proposals_request_is_planned_against_its_own_steps_proof_rows():
    from resource_explorer.surveyors.database.quality import propose

    req = _proposals()
    others = {propose.ANALYSIS_STEP: {"report_guid": "r1", "report_at": "2026-10-24T21:09:00",
                                      "measures": {propose.ITEM_KEY: {"annotation_guid": "g-rfa",
                                                                      "content_hash": content_hash(req)}}}}
    plan = plan_profile_publish([_measure(), req], None, others)
    assert plan.relink == {1: "g-rfa"} and plan.create == [0]
    assert plan.steps == {0: ANALYSIS_STEP, 1: propose.ANALYSIS_STEP}
    assert plan.previous_by_step == {propose.ANALYSIS_STEP: ("r1", "2026-10-24T21:09:00")}
    # a key in the value profile's proof rows is never the request's, even if equal text
    assert plan_profile_publish([req], {"report_guid": "r0", "report_at": "x", "measures": {
        propose.ITEM_KEY: {"annotation_guid": "wrong", "content_hash": content_hash(req)}}}).relink == {}


def test_a_not_profiled_table_relinks_when_its_reason_is_the_same_and_is_never_no_longer_measured():
    slow = _not_profiled("s.slow")
    previous = {"report_guid": "r1", "report_at": "2026-10-24T21:09:00",
                "measures": {table_key("s", "slow"): {"annotation_guid": "g-slow-t", "content_hash": content_hash(slow)},
                             table_key("s", "fast"): {"annotation_guid": "g-fast-t", "content_hash": "h"}}}
    plan = plan_profile_publish([slow, _not_profiled("s.other", reason="permission denied")], previous)
    assert plan.relink == {0: "g-slow-t"} and plan.create == [1]
    # s.fast was not profiled last time and is profiled now: nothing is "no longer measured" for a table key
    assert plan.gone == {} and plan.not_measured == {}
    changed = _not_profiled("s.slow", reason="permission denied")
    assert plan_profile_publish([changed], previous).relink == {}


def test_the_card_names_the_previous_report_by_date_and_time_utc():
    counts = {"rows": 2, "published": 2, "created": 1, "relinked": 1, "no_longer_measured": 0, "counts_moved": 1}
    for stored, shown in (("2026-10-24T21:09:41.123456", "2026-10-24 21:09Z"),     # RE's naive UTC
                          ("2026-10-24T23:09:00+02:00", "2026-10-24 21:09Z"),      # an offset, converted
                          ("2026-10-24T21:09:00Z", "2026-10-24 21:09Z")):
        assert profile_publish_words({**counts, "previous_report_at": stored}) == (
            f"2 measures published · 1 changed since {shown}: 1 counts moved · 0 shape changed · 1 re-used"
            " · 0 no longer measured")
    # a time that cannot be read is shown as stored, never a guessed one
    assert " · 1 changed since sometime: " in profile_publish_words({**counts, "previous_report_at": "sometime"})


def _not_profiled(table: str, reason: str = "canceling statement due to statement timeout"
                  ) -> ResourceMeasureAnnotation:
    return ResourceMeasureAnnotation(summary=f"Value profile: {table} not profiled (not_measured)",
                                     analysis_step=ANALYSIS_STEP, annotation_type_name="Value Profile",
                                     check_name="value_profile", item_key=table_key(*table.split(".")),
                                     label="not_measured", confidence=0, explanation=reason,
                                     resource_properties={"state": "not_measured", "reason": reason})


def test_the_plan_relinks_the_unchanged_creates_the_changed_and_new_and_names_the_gone():
    same, changed, new = _measure("s.t.a"), _measure("s.t.b", value_count={"value_ct": 2}), _measure("s.t.c")
    previous = {"report_guid": "r1", "report_at": "2026-10-22T10:00:00",
                "measures": {_key("s.t.a"): {"annotation_guid": "g-a", "content_hash": content_hash(same)},
                             _key("s.t.b"): {"annotation_guid": "g-b", "content_hash": content_hash(_measure("s.t.b"))},
                             _key("s.t.gone"): {"annotation_guid": "g-gone", "content_hash": "x"},
                             _key("s.slow.x"): {"annotation_guid": "g-slow", "content_hash": "y"},
                             _key("s.t.counted"): {"annotation_guid": "g-counted", "content_hash": "z"}},
                "gone_keys": {_key("s.t.counted")}}
    other = object()                                                   # any other annotation is never planned
    plan = plan_profile_publish([other, same, changed, new, _not_profiled("s.slow")], previous)
    assert plan.relink == {1: "g-a"}
    assert plan.create == [2, 3, 4]                                    # the table not profiled is new: created
    assert plan.keys[4] == table_key("s", "slow") and plan.steps[4] == ANALYSIS_STEP
    assert plan.gone == {_key("s.t.gone"): "g-gone"}                 # counted once: s.t.counted already was
    assert plan.not_measured == {_key("s.slow.x"): "g-slow"}         # its table was not profiled: not a drop
    assert plan_profile_publish([same], None).relink == {}            # nothing published before: all created
    plan.recreate([1])
    assert plan.relink == {} and plan.recreated == {1} and plan.create == [1, 2, 3, 4]


def test_the_card_line_words():
    assert profile_publish_words({"rows": 0}) == ""
    assert profile_publish_words({"rows": 5, "published": 5, "created": 5, "relinked": 0, "no_longer_measured": 0,
                                  "previous_report_at": ""}) == "5 measures published · first profile published"
    assert profile_publish_words({"rows": 5, "published": 4, "created": 1, "relinked": 3, "no_longer_measured": 1,
                                  "counts_moved": 1, "shape_changed": 0,
                                  "previous_report_at": "2026-10-22T10:00:00"}) == (
        "4 measures published · 1 changed since 2026-10-22 10:00Z: 1 counts moved · 0 shape changed · 3 re-used"
        " · 1 no longer measured")
    assert profile_publish_words({"rows": 5, "published": 4, "created": 0, "relinked": 4, "no_longer_measured": 0,
                                  "not_measured_this_run": 1, "previous_report_at": "2026-10-22T10:00:00"}) == (
        "4 measures published · 0 changed since 2026-10-22 10:00Z · 4 re-used · 0 no longer measured"
        " · 1 not measured this run")


def test_the_card_line_names_a_recreated_measure_and_does_not_call_it_changed():
    counts = {"rows": 5, "published": 5, "created": 1, "recreated": 1, "changed": 0, "relinked": 4,
              "no_longer_measured": 0, "previous_report_at": "2026-10-22T10:00:00"}
    assert profile_publish_words(counts) == (
        "5 measures published · 0 changed since 2026-10-22 10:00Z · 4 re-used"
        " · 1 re-created (the re-used annotation was gone) · 0 no longer measured")


# ── the read-first guard ───────────────────────────────────────────────────────────────────────────────────

def _surveyor_with(held: list[dict] | None):
    from pyegeria import DataDiscovery

    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
    s._discovery = create_autospec(DataDiscovery, spec_set=True, instance=True)
    s._report_annotations = lambda report_guid: held
    return s


def test_a_report_that_already_links_the_annotation_gets_no_second_reported_annotation():
    s = _surveyor_with([{"guid": "g-a", "qualified_name": "Annotation::x"}])
    out = s._relink_unchanged("report-2", {0: "g-a", 1: "g-b", 2: "g-b"})
    assert out == {0: "already_linked", 1: "relinked", 2: "already_linked"}
    # exactly one create per pair, with the real signature (autospec, spec_set)
    calls = s._discovery.attach_annotation_to_report.call_args_list
    assert [(c.args[0], c.args[1]) for c in calls] == [("report-2", "g-b")]
    assert calls[0].kwargs["body"]["properties"]["class"] == "ReportedAnnotationProperties"


def test_a_refused_relink_is_refused_and_a_transport_failure_is_failed():
    from tests.test_engine_host_e4 import _refusal

    s = _surveyor_with([])
    s._discovery.attach_annotation_to_report.side_effect = [
        _refusal("OMAG-REPOSITORY-HANDLER-404-001", "no Annotation g-a", http=404), RuntimeError("timed out")]
    assert s._relink_unchanged("report-2", {0: "g-a", 1: "g-b"}) == {0: "refused", 1: "failed"}


def test_the_read_first_is_the_real_paged_read_of_the_report(monkeypatch):
    """`_report_annotations` through `reported_annotations` and `related_entries`, against an autospec'd
    MetadataExpert: the list is read page by page until Egeria's "No elements found"."""
    from pyegeria import DataDiscovery, MetadataExpert

    from resource_explorer.egeria_link_once import PAGE_SIZE
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    from tests.report_pages import related_entry

    expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    page = [related_entry("g-a", "Annotation", {"qualifiedName": "Annotation::a"}),
            related_entry("g-b", "Annotation", {"qualifiedName": "Annotation::b"})]
    expert.get_related_metadata_elements.side_effect = (
        lambda guid, rel, body=None, starting_at_end=0, start_from=0, page_size=0, **k:
        {"elementList": page} if start_from == 0 else "No elements found")
    s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
    s._discovery = create_autospec(DataDiscovery, spec_set=True, instance=True)
    s._metadata_expert = expert
    out = s._relink_unchanged("report-2", {0: "g-a", 1: "g-c"})
    assert out == {0: "already_linked", 1: "relinked"}
    assert [(c.args[0], c.args[1], c.kwargs["start_from"], c.kwargs["page_size"])
            for c in expert.get_related_metadata_elements.call_args_list] == [
        ("report-2", "ReportedAnnotation", 0, PAGE_SIZE), ("report-2", "ReportedAnnotation", PAGE_SIZE, PAGE_SIZE)]
    assert [c.args[1] for c in s._discovery.attach_annotation_to_report.call_args_list] == ["g-c"]


def test_a_report_whose_list_cannot_be_read_gets_no_blind_attach():
    s = _surveyor_with(None)
    assert s._relink_unchanged("report-2", {0: "g-a"}) == {0: "not_checked"}
    s._discovery.attach_annotation_to_report.assert_not_called()


def test_the_relationship_body_is_one_pyegeria_accepts():
    from pyegeria.models import NewRelationshipRequestBody

    body = {"class": "NewRelationshipRequestBody", "properties": {"class": "ReportedAnnotationProperties"}}
    assert NewRelationshipRequestBody.model_validate(body).properties["class"] == "ReportedAnnotationProperties"


# ── the DDL: two nullable TEXT columns, idempotent ─────────────────────────────────────────────────────────

NEW = ("item_key", "content_hash")


def _cols(reg) -> set[str]:
    with reg._conn() as conn:
        return reg._get_table_columns(conn, "native_survey_annotations")


def test_a_fresh_registry_has_the_columns_and_a_second_init_is_a_no_op(tmp_path):
    from resource_explorer.registry import ProjectRegistry

    path = str(tmp_path / "twice.db")
    first, second = _cols(ProjectRegistry(db_path=path)), _cols(ProjectRegistry(db_path=path))
    assert set(NEW) <= first and first == second


def test_an_existing_registry_gains_the_columns_and_its_rows_stay_null(tmp_path):
    from resource_explorer.registry import ProjectRegistry

    path = tmp_path / "old.db"
    reg = ProjectRegistry(db_path=str(path))
    reg.record_native_survey_report("ea-1", entity_type="database", slug="d", process_qualified_name="p",
                                    report_guid="r", report_at="t", read_at="t",
                                    annotations=[{"guid": "a1", "annotation_type": "X"}])
    con = sqlite3.connect(path)
    for col in NEW:
        con.execute(f"ALTER TABLE native_survey_annotations DROP COLUMN {col}")
    con.commit()
    con.close()
    again = ProjectRegistry(db_path=str(path))
    assert set(NEW) <= _cols(again)
    with again._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT item_key, content_hash FROM native_survey_annotations").fetchall()]
    assert rows == [{"item_key": None, "content_hash": None}]           # a native row: never compared
    assert again.latest_profile_publish("database", "d", ANALYSIS_STEP) is None


def test_the_migration_is_idempotent_on_postgres(pg_registry, monkeypatch):
    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url             # the throwaway schema only
    before = _cols(pg_registry)
    assert set(NEW) <= before
    monkeypatch.setattr(PR, "_pg_schema_ready", set())
    assert _cols(PR(database_url=pg_registry.database_url)) == before


# ── end to end: the real press, twice, then a change, then a drop ──────────────────────────────────────────

def _profile_creates(egeria, since: set[str]) -> int:
    return sum(1 for g, e in egeria.elements.items() if g not in since and e["type"] == "Annotation"
               and (e["body"] or {}).get("properties", {}).get("class") == PROFILE_CLASS)


def _proof(registry, report_guid, *, notes: list | None = None) -> dict[str, int]:
    with registry._conn() as conn:
        every = [dict(r) for r in conn.execute(
            "SELECT detail_json, item_key, content_hash, annotation_type FROM native_survey_annotations "
            "WHERE report_guid = ?", (report_guid,)).fetchall()]
    rows = [r for r in every if r["annotation_type"] == "ResourceProfileAnnotation"]
    out: dict[str, int] = {}
    for r in rows:
        detail = json.loads(r["detail_json"])
        outcome = detail["outcome"]
        out[outcome] = out.get(outcome, 0) + 1
        assert len(json.loads(r["item_key"])) == 3                        # every proof row names its column
        assert (r["content_hash"] is None) == (outcome in ("no_longer_measured", "not_measured_this_run"))
        if notes is not None and detail.get("note"):
            notes.append((outcome, detail["note"]))
    # the rows NOT on the report are not counted as on it (the request's and any table's rows are on it too)
    on_report = sum(v for k, v in out.items() if k in ("created", "relinked")) + len(every) - len(rows)
    assert registry.count_native_survey_annotations(report_guid) == on_report
    assert len(registry.query_native_survey_annotations(report_guid)) == on_report
    return out


def _report_rows(registry, report_guid) -> list[dict]:
    with registry._conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT detail_json, annotation_type, analysis_step FROM native_survey_annotations "
            "WHERE report_guid = ?", (report_guid,)).fetchall()]


def _other_proof(registry, report_guid) -> dict[tuple[str, str], int]:
    """The proof rows of the re-linkable annotations that are not column measures: {(type, outcome): n}."""
    out: dict[tuple[str, str], int] = {}
    for r in _report_rows(registry, report_guid):
        if r["annotation_type"] != "ResourceProfileAnnotation":
            key = (r["annotation_type"], json.loads(r["detail_json"])["outcome"])
            out[key] = out.get(key, 0) + 1
    return out


def _relinked_rows(registry, report_guid) -> int:
    return sum(1 for r in _report_rows(registry, report_guid) if json.loads(r["detail_json"])["outcome"] == "relinked")


def test_only_the_gone_outcomes_have_an_item_key_and_no_content_hash(client, registry, egeria, world, rig):
    """The convention the changed/recreated query relies on: created and relinked proof rows always carry a hash."""
    _press(client, registry)
    r1 = egeria.reports()[0][0]
    egeria.archive(_profile_on(egeria, r1)[0])
    _press(client, registry)
    with registry._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT detail_json, item_key, content_hash FROM native_survey_annotations").fetchall()]
    seen = set()
    for r in rows:
        outcome = json.loads(r["detail_json"])["outcome"]
        seen.add(outcome)
        if outcome in ("created", "relinked"):
            assert r["content_hash"]
        else:
            assert outcome in ("no_longer_measured", "not_measured_this_run")
            assert r["item_key"] and r["content_hash"] is None
    assert {"created", "relinked"} <= seen


def _reported_pairs(egeria, report_guid) -> list[str]:
    return [r["end2"] for r in egeria.relationships if r["type"] == "ReportedAnnotation" and r["end1"] == report_guid]


def test_two_identical_presses_then_a_change_then_a_drop(client, registry, egeria, world, rig):
    # 1. first run: all created, 0 re-linked
    first = _press(client, registry)
    [(r1, _)] = egeria.reports()
    assert len(_by_class(_annotation_bodies(egeria, r1))[PROFILE_CLASS]) == 5
    assert _proof(registry, r1) == {"created": 5}
    assert first["publish_state"].endswith("· 5 measures published · first profile published")
    assert first["published"] is True
    first_date = _utc_minute(egeria.reports()[0][1]["properties"]["qualifiedName"].rsplit("::", 1)[1])
    profile_on_r1 = {g for g in _reported_pairs(egeria, r1)
                     if egeria.elements[g]["body"]["properties"]["class"] == PROFILE_CLASS}

    # 2. second run, nothing changed: 0 created, all re-linked, and the fake received no profile create
    seen = set(egeria.elements)
    attaches = len(egeria.method_calls("attach_annotation_to_report"))
    second = _press(client, registry)
    r2 = egeria.reports()[1][0]
    assert _profile_creates(egeria, seen) == 0
    assert _proof(registry, r2) == {"relinked": 5}
    # round 2: the four tables not profiled and the proposals request are unchanged too, so re-linked as well
    assert _other_proof(registry, r2) == {("ResourceMeasureAnnotation", "relinked"): 4,
                                          ("RequestForAction", "relinked"): 1}
    assert len(egeria.method_calls("attach_annotation_to_report")) - attaches == _relinked_rows(registry, r2) == 10
    on_r2 = _reported_pairs(egeria, r2)
    assert {g for g in on_r2 if egeria.elements[g]["body"]["properties"]["class"] == PROFILE_CLASS} == profile_on_r1
    assert len(on_r2) == len(set(on_r2))                                  # one ReportedAnnotation per pair
    # the read-back counts what the report holds, re-linked ones included: M + K = 0 + 5
    # the request is re-used too, still naming the run that first published it: the card says since when it is
    # unchanged (the first report's time, from the proof rows), so it is not read as new work
    assert second["publish_state"].endswith(
        f"· 5 measures published · 0 changed since {first_date} · 5 re-used · 0 no longer measured"
        f" · proposals unchanged since {first_date}")
    assert second["published"] is True and second["status"] != "error"
    # the re-linked annotations are the first report's, anchored there, now reported by both
    for g in profile_on_r1:
        assert sorted(r["end1"] for r in egeria.relationships
                      if r["type"] == "ReportedAnnotation" and r["end2"] == g) == sorted([r1, r2])

    # 3. one column changed: exactly that one created, the rest re-linked (on a copy: PROFILE is shared)
    world.profile = copy.deepcopy(world.profile)
    world.profile[("sales", "orders")]["columns"][3]["value_ct"] = 11
    seen = set(egeria.elements)
    third = _press(client, registry)
    r3 = egeria.reports()[2][0]
    assert _profile_creates(egeria, seen) == 1
    assert _proof(registry, r3) == {"created": 1, "relinked": 4}
    # ruling 5: the one change was a count (value_ct), not the column's shape; and it moved on one of the two tables
    # measured, which is past the default share (0.25), so the card says the database is written live
    assert " · 5 measures published · 1 changed since " in third["publish_state"]
    assert ": 1 counts moved · 0 shape changed · 4 re-used · 0 no longer measured" in third["publish_state"]
    assert third["publish_state"].endswith(" · this database is written live · see change rates")
    assert third["written_live"] == {"analysis_id": "db_change_rates", "question_id": "Q-0086"}
    assert "written_live" not in second and "written live" not in second["publish_state"]

    # 4. a table not profiled this time (it timed out): its column is NOT MEASURED THIS RUN, never "no longer
    # measured"; nothing written to Egeria for it
    world.profile[("sales", "empty")] = TIMEOUT
    seen, attaches = set(egeria.elements), len(egeria.method_calls("attach_annotation_to_report"))
    fourth = _press(client, registry)
    r4 = egeria.reports()[3][0]
    assert _profile_creates(egeria, seen) == 0
    assert len(egeria.method_calls("attach_annotation_to_report")) - attaches == _relinked_rows(registry, r4)
    assert _proof(registry, r4) == {"relinked": 4, "not_measured_this_run": 1}
    # the newly not-profiled table's annotation is created; the four that were already not profiled are re-linked
    assert {k: v for k, v in _other_proof(registry, r4).items() if k[0] == "ResourceMeasureAnnotation"} == {
        ("ResourceMeasureAnnotation", "created"): 1, ("ResourceMeasureAnnotation", "relinked"): 4}
    assert " · 4 measures published · 0 changed since " in fourth["publish_state"]
    assert "· 4 re-used · 0 no longer measured · 1 not measured this run" in fourth["publish_state"]
    assert registry.profile_publish_counts(r4, ANALYSIS_STEP)["published"] == 4

    # 5. it is profiled again, unchanged: re-linked from the last run that measured it, not created
    world.profile[("sales", "empty")] = PROFILE_EMPTY
    seen = set(egeria.elements)
    fifth = _press(client, registry)
    r5 = egeria.reports()[4][0]
    assert _profile_creates(egeria, seen) == 0
    assert _proof(registry, r5) == {"relinked": 5}
    assert "· 5 re-used · 0 no longer measured" in fifth["publish_state"]


def _drop_table(registry, schema: str, table: str, at="2026-10-20T09:30:00+00:00") -> None:
    """A later inventory batch without the table and its columns: the database no longer has it."""
    tables = [r for r in registry.query_detail_rows("database_tables", SLUG)
              if (r["schema_name"], r["table_name"]) != (schema, table)]
    cols = [r for r in registry.query_detail_rows("database_columns", SLUG)
            if (r["schema_name"], r["table_name"]) != (schema, table)]
    registry.write_detail_rows("database_tables", SLUG, at, rows=tables)
    registry.write_detail_rows("database_columns", SLUG, at, rows=cols)


def test_a_dropped_table_is_no_longer_measured_once(client, registry, egeria, world, rig):
    _press(client, registry)
    _drop_table(registry, "sales", "empty")
    second = _press(client, registry)
    r2 = egeria.reports()[1][0]
    assert _proof(registry, r2) == {"relinked": 4, "no_longer_measured": 1}
    assert "· 4 re-used · 1 no longer measured" in second["publish_state"]
    # the next run does not count the same drop again
    third = _press(client, registry)
    assert _proof(registry, egeria.reports()[2][0]) == {"relinked": 4}
    assert "· 4 re-used · 0 no longer measured" in third["publish_state"]


def _profile_on(egeria, report_guid) -> list[str]:
    return [g for g in _reported_pairs(egeria, report_guid)
            if egeria.elements[g]["body"]["properties"]["class"] == PROFILE_CLASS]


def test_a_relink_egeria_refuses_is_created_in_the_same_run(client, registry, egeria, world, rig):
    _press(client, registry)
    r1 = egeria.reports()[0][0]
    gone = _profile_on(egeria, r1)[0]
    egeria.archive(gone)                                                  # archived in Egeria since the last run
    seen = set(egeria.elements)
    second = _press(client, registry)
    r2 = egeria.reports()[1][0]
    assert _profile_creates(egeria, seen) == 1                            # created again, in this run
    notes: list = []
    assert _proof(registry, r2, notes=notes) == {"created": 1, "relinked": 4}
    assert notes == [("created", RECREATED_NOTE)]
    assert gone not in _reported_pairs(egeria, r2) and len(_profile_on(egeria, r2)) == 5
    assert second["published"] is True
    counts = registry.profile_publish_counts(r2, ANALYSIS_STEP)
    assert counts["created"] == 1 and counts["recreated"] == 1 and counts["changed"] == 0
    assert counts["published"] == 5
    assert " · 5 measures published · 0 changed since " in second["publish_state"]
    assert " · 4 re-used · 1 re-created (the re-used annotation was gone) · 0 no longer measured" in second["publish_state"]


def test_a_partial_relink_is_relinked_on_the_next_run_not_changed(client, registry, egeria, world, rig):
    _press(client, registry)
    # the second run: Egeria fails one re-link for a reason that is not "gone" (could not tell)
    attaches = len(egeria.method_calls("attach_annotation_to_report"))
    egeria.fail_nth["attach_annotation_to_report"] = (attaches + 2, RuntimeError("Egeria timed out"))
    seen = set(egeria.elements)
    second = _press(client, registry)
    r2 = egeria.reports()[1][0]
    assert _profile_creates(egeria, seen) == 0                            # not created: it may well be there
    assert _proof(registry, r2) == {"relinked": 4}                         # the read by GUID did not find it
    assert " · 4 measures published · 0 changed since " in second["publish_state"]
    del egeria.fail_nth["attach_annotation_to_report"]
    # the third run: its hash is unchanged, compared with run 1's row: re-linked, not "changed"
    seen = set(egeria.elements)
    third = _press(client, registry)
    r3 = egeria.reports()[2][0]
    assert _profile_creates(egeria, seen) == 0
    assert _proof(registry, r3) == {"relinked": 5}
    assert " · 5 measures published · 0 changed since " in third["publish_state"]
    assert "· 5 re-used · 0 no longer measured" in third["publish_state"]


def test_a_report_that_already_holds_a_pair_is_not_attached_again_end_to_end(client, registry, egeria, world, rig,
                                                                             monkeypatch):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    _press(client, registry)
    held = _profile_on(egeria, egeria.reports()[0][0])[0]
    real = EgeriaDatabaseSurveyor._relink_unchanged

    def already_there(self, report_guid, pairs):
        # Egeria already holds one pair on the new report (a retried request landed): the read-first must see it
        egeria.relationships.append({"type": "ReportedAnnotation", "end1": report_guid, "end2": held,
                                     "guid": "pre-existing"})
        return real(self, report_guid, pairs)

    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_relink_unchanged", already_there)
    attaches = len(egeria.method_calls("attach_annotation_to_report"))
    second = _press(client, registry)
    r2 = egeria.reports()[1][0]
    # every planned re-link but the one the report already held (4 measures + 4 tables + 1 request)
    assert len(egeria.method_calls("attach_annotation_to_report")) - attaches == _relinked_rows(registry, r2) - 1 == 9
    on_r2 = _reported_pairs(egeria, r2)
    assert len(on_r2) == len(set(on_r2))                                  # one ReportedAnnotation per pair
    assert _proof(registry, r2) == {"relinked": 5}
    assert "· 5 re-used · 0 no longer measured" in second["publish_state"]


# ── ruling 5 (architect, 2026-10-24): which kind of change, and the written-live words ───────────────────────

def _moved(before: ResourceProfileAnnotation | None, after: ResourceProfileAnnotation) -> list[str]:
    from resource_explorer.surveyors.database.quality.profile_publish import (
        measure_groups,
        moved_groups,
    )

    return moved_groups(measure_groups(after), {"groups": measure_groups(before)} if before else None)


def test_a_count_that_moved_inside_its_shape_is_counts_moved_only():
    base = _measure(value_count={"record_ct": 1000, "value_ct": 900, "null_value_ct": 100, "distinct_value_ct": 900})
    grown = _measure(value_count={"record_ct": 1010, "value_ct": 909, "null_value_ct": 101, "distinct_value_ct": 909})
    assert _moved(base, grown) == ["counts"]


def test_general_type_null_pattern_distinct_band_and_min_max_are_shape():
    counts = {"record_ct": 1000, "value_ct": 1000, "null_value_ct": 0, "distinct_value_ct": 1000}
    base = _measure(value_count=counts, profileDates={"min_date": "2020-01-01"})
    assert _moved(base, _measure(value_count=counts, generalType="A", profileDates={"min_date": "2020-01-01"})) == [
        "shape"]
    assert _moved(base, _measure(value_count=counts, profileDates={"min_date": "2019-12-31"})) == ["shape"]
    # nulls appear (none -> some): the counts moved AND the shape changed; counted in both
    assert _moved(base, _measure(value_count={**counts, "value_ct": 990, "null_value_ct": 10, "distinct_value_ct": 990},
                                 profileDates={"min_date": "2020-01-01"})) == ["counts", "shape"]
    # unique -> medium distinct ratio
    assert _moved(base, _measure(value_count={**counts, "distinct_value_ct": 100},
                                 profileDates={"min_date": "2020-01-01"})) == ["counts", "shape"]


def test_the_bands_and_patterns_are_what_the_shape_names():
    from resource_explorer.surveyors.database.quality.profile_publish import (
        distinct_band,
        null_pattern,
    )

    assert [null_pattern({"record_ct": 5, "null_value_ct": n}) for n in (0, 2, 5)] == ["none", "some", "all"]
    assert null_pattern({"record_ct": 5}) is None                         # not measured, never "none"
    assert [distinct_band({"value_ct": 100, "distinct_value_ct": d}) for d in (100, 50, 5, 1)] == [
        "unique", "high", "medium", "low"]
    assert distinct_band({"value_ct": 0, "distinct_value_ct": 0}) is None


def test_a_new_column_is_new_and_an_earlier_row_without_groups_is_not_compared():
    from resource_explorer.surveyors.database.quality.profile_publish import (
        measure_groups,
        moved_groups,
    )

    assert _moved(None, _measure()) == ["new"]
    assert moved_groups(measure_groups(_measure()), {"annotation_guid": "g", "content_hash": "h"}) == ["not_compared"]


def test_the_plan_records_groups_for_every_measure_and_moved_only_for_the_created():
    same, grown = _measure("s.t.a"), _measure("s.t.b", value_count={"record_ct": 1001, "value_ct": 1001,
                                                                     "null_value_ct": 0})
    from resource_explorer.surveyors.database.quality.profile_publish import measure_groups

    previous = {"report_guid": "r1", "report_at": "2026-10-24T21:09:00",
                "measures": {_key("s.t.a"): {"annotation_guid": "g-a", "content_hash": content_hash(same),
                                             "groups": measure_groups(same)},
                             _key("s.t.b"): {"annotation_guid": "g-b", "content_hash": content_hash(_measure("s.t.b")),
                                             "groups": measure_groups(_measure("s.t.b"))}}}
    plan = plan_profile_publish([same, grown, _measure("s.t.c")], previous)
    assert plan.relink == {0: "g-a"} and set(plan.groups) == {0, 1, 2}
    assert plan.moved == {1: ["counts"], 2: ["new"]}


def test_the_card_breaks_changed_down_and_names_the_rest_only_when_there_are_any():
    base = {"rows": 80, "published": 80, "created": 70, "relinked": 10, "no_longer_measured": 0,
            "previous_report_at": "2026-10-24T21:09:00"}
    assert profile_publish_words({**base, "counts_moved": 70, "shape_changed": 0}) == (
        "80 measures published · 70 changed since 2026-10-24 21:09Z: 70 counts moved · 0 shape changed"
        " · 10 re-used · 0 no longer measured")
    assert ": 66 counts moved · 3 shape changed · 2 new · 1 not compared · 10 re-used" in profile_publish_words(
        {**base, "counts_moved": 66, "shape_changed": 3, "new": 2, "not_compared": 1})
    # nothing changed: no breakdown
    assert profile_publish_words({**base, "created": 0, "relinked": 80}).endswith(
        "0 changed since 2026-10-24 21:09Z · 80 re-used · 0 no longer measured")


def test_written_live_is_more_than_the_declared_share_of_measured_tables():
    from resource_explorer.surveyors.database.quality.profile_publish import (
        WRITTEN_LIVE_WORDS,
        written_live,
    )

    counts = {"rows": 10, "published": 10, "created": 3, "relinked": 7, "counts_moved": 3,
              "previous_report_at": "2026-10-24T21:09:00", "tables_measured": 8, "tables_counts_moved": 2}
    assert not written_live(counts, 0.25)                                 # 2 of 8 is the share, not more
    assert written_live({**counts, "tables_counts_moved": 3}, 0.25)
    assert not written_live({**counts, "tables_counts_moved": 3}, None)   # no share declared: never said
    assert not written_live({**counts, "previous_report_at": ""}, 0.0)    # a first publish moved nothing
    assert not written_live({**counts, "tables_measured": 0}, 0.0)
    assert profile_publish_words({**counts, "tables_counts_moved": 3}, 0.25).endswith(f" · {WRITTEN_LIVE_WORDS}")
    assert WRITTEN_LIVE_WORDS not in profile_publish_words(counts, 0.25)


def test_the_share_is_a_config_value(monkeypatch):
    from resource_explorer.config import ValueProfileSettings

    assert ValueProfileSettings().live_write_table_share == 0.25
    monkeypatch.setenv("VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE", "0.6")
    assert ValueProfileSettings().live_write_table_share == 0.6
    monkeypatch.setenv("VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE", "1.5")
    with pytest.raises(ValueError):
        ValueProfileSettings()


def test_the_counts_come_from_the_rows_moved_and_an_old_row_is_not_compared(tmp_path):
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "counts.db"))
    row = {"annotation_type": "ResourceProfileAnnotation", "previous_report_guid": "r0",
           "previous_report_at": "2026-10-24T21:09:00"}
    reg.record_profile_publish("database", "d", report_guid="r1", report_at="2026-10-24T22:00:00", read_at="t",
                               analysis_step=ANALYSIS_STEP, rows=[
        {**row, "annotation_guid": "a", "item_key": _key("s.t.a"), "content_hash": "1", "outcome": "created",
         "moved": ["counts"]},
        {**row, "annotation_guid": "b", "item_key": _key("s.t.b"), "content_hash": "2", "outcome": "created",
         "moved": ["counts", "shape"]},
        {**row, "annotation_guid": "c", "item_key": _key("s.u.c"), "content_hash": "3", "outcome": "created"},
        {**row, "annotation_guid": "d", "item_key": _key("s.v.d"), "content_hash": "4", "outcome": "relinked"},
        # a not-profiled table's row on the same step is not a measure and not a table measured
        {**row, "annotation_type": "ResourceMeasureAnnotation", "annotation_guid": "t", "item_key": table_key("s", "w"),
         "content_hash": "5", "outcome": "created"}])
    counts = reg.profile_publish_counts("r1", ANALYSIS_STEP)
    assert (counts["changed"], counts["counts_moved"], counts["shape_changed"], counts["new"],
            counts["not_compared"]) == (3, 2, 1, 0, 1)
    assert (counts["tables_measured"], counts["tables_counts_moved"]) == (3, 1)
    # the groups are read back as "previous" for the next publish
    reg.record_profile_publish("database", "d", report_guid="r2", report_at="2026-10-24T23:00:00", read_at="t",
                               analysis_step=ANALYSIS_STEP, rows=[
        {**row, "annotation_guid": "e", "item_key": _key("s.t.e"), "content_hash": "6", "outcome": "created",
         "groups": {"counts": "c1", "shape": "s1"}, "moved": ["new"]}])
    latest = reg.latest_profile_publish("database", "d", ANALYSIS_STEP)
    assert latest["measures"][_key("s.t.e")]["groups"] == {"counts": "c1", "shape": "s1"}
    assert "groups" not in latest["measures"][_key("s.t.a")]


def test_a_shape_change_through_the_press_says_shape_changed_and_not_written_live(client, registry, egeria, world,
                                                                                  rig):
    _press(client, registry)
    world.profile = copy.deepcopy(world.profile)
    world.profile[("sales", "orders")]["columns"][2]["min_date"] = "2019-01-01"     # the date column's lower bound
    second = _press(client, registry)
    r2 = egeria.reports()[1][0]
    assert _proof(registry, r2) == {"created": 1, "relinked": 4}
    with registry._conn() as conn:
        [moved] = [json.loads(r["detail_json"])["moved"] for r in conn.execute(
            "SELECT detail_json FROM native_survey_annotations WHERE report_guid = ? AND item_key = ?",
            (r2, _key("sales.orders.placed"))).fetchall()]
    assert moved == ["shape"]
    assert ": 0 counts moved · 1 shape changed · 4 re-used · 0 no longer measured" in second["publish_state"]
    assert "written live" not in second["publish_state"] and "written_live" not in second


# ── fix round (architect, 2026-10-24): a held band, and a re-used request says since when ─────────────────────

def _ratio_counts(distinct: int, values: int = 100) -> dict:
    return {"value_ct": values, "distinct_value_ct": distinct}


def test_the_band_is_held_until_the_ratio_passes_an_edge_by_more_than_the_hysteresis():
    from resource_explorer.surveyors.database.quality.profile_publish import (
        BAND_HYSTERESIS,
        distinct_band,
    )

    assert BAND_HYSTERESIS == 0.02
    # no previous band: the ratio's own band
    assert distinct_band(_ratio_counts(51)) == "high" and distinct_band(_ratio_counts(49)) == "medium"
    # held on both sides of the 0.5 edge, up to 0.02 past it
    assert distinct_band(_ratio_counts(51), "medium") == "medium"
    assert distinct_band(_ratio_counts(52), "medium") == "medium"            # 0.02 past: not MORE than
    assert distinct_band(_ratio_counts(53), "medium") == "high"
    assert distinct_band(_ratio_counts(48), "high") == "high"
    assert distinct_band(_ratio_counts(47), "high") == "medium"
    # the 0.05 edge the same way, and a jump over several bands goes straight to the ratio's own
    assert distinct_band(_ratio_counts(4), "medium") == "medium" and distinct_band(_ratio_counts(2), "medium") == "low"
    assert distinct_band(_ratio_counts(99), "low") == "high"
    # unique: entered when the ratio reaches 1.0 (no ratio can pass its upper edge), left only below 0.98
    assert distinct_band(_ratio_counts(100), "high") == "unique"
    assert distinct_band(_ratio_counts(99), "unique") == "unique" and distinct_band(_ratio_counts(97), "unique") == "high"
    # a previous name that is not a band is ignored, never trusted
    assert distinct_band(_ratio_counts(51), "middling") == "high"


def _set_note_distinct(world, distinct: int) -> None:
    world.profile = copy.deepcopy(world.profile)
    world.profile[("sales", "orders")]["columns"][1]["distinct_value_ct"] = distinct     # value_ct 900


def _band_of(registry, report_guid, path: str) -> str:
    with registry._conn() as conn:
        [detail] = [json.loads(r["detail_json"]) for r in conn.execute(
            "SELECT detail_json FROM native_survey_annotations WHERE report_guid = ? AND item_key = ?",
            (report_guid, _key(path))).fetchall()]
    return detail["groups"]["band"]


def test_a_ratio_oscillating_across_an_edge_stays_in_its_band_and_reads_no_shape_change(client, registry, egeria,
                                                                                         world, rig):
    """Fail-on-purpose (architect's amendment to ruling 5): sales.orders.note's distinct ratio goes 0.49 -> 0.51 ->
    0.49 over three runs. Compared on the ratio, or on a band with no hysteresis, run 2 reads "1 shape changed" and run
    3 again; held, the band stays "medium" and only the counts moved."""
    _set_note_distinct(world, 441)                                          # 441 / 900 = 0.49
    _press(client, registry)
    r1 = egeria.reports()[0][0]
    assert _band_of(registry, r1, "sales.orders.note") == "medium"
    for n, distinct in ((2, 459), (3, 441)):                                # 0.51, then 0.49 again
        _set_note_distinct(world, distinct)
        run = _press(client, registry)
        report = egeria.reports()[n - 1][0]
        assert _proof(registry, report) == {"created": 1, "relinked": 4}, n
        assert _band_of(registry, report, "sales.orders.note") == "medium", n
        assert ": 1 counts moved · 0 shape changed · 4 re-used" in run["publish_state"], (n, run["publish_state"])


def test_a_relinked_request_names_the_first_run_on_the_card():
    base = {"rows": 5, "published": 5, "created": 0, "relinked": 5, "no_longer_measured": 0,
            "previous_report_at": "2026-10-24T22:00:00"}
    assert profile_publish_words({**base, "proposals_unchanged_since": "2026-10-24T21:09:00"}) == (
        "5 measures published · 0 changed since 2026-10-24 22:00Z · 5 re-used · 0 no longer measured"
        " · proposals unchanged since 2026-10-24 21:09Z")
    # a request re-used on a publish with no profile measures still says so; none re-used says nothing
    assert profile_publish_words({"rows": 0, "proposals_unchanged_since": "2026-10-24T21:09:00"}) == (
        "proposals unchanged since 2026-10-24 21:09Z")
    assert "proposals" not in profile_publish_words(base)


def test_relinked_since_is_the_first_report_of_the_reused_request(tmp_path):
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.database.quality import propose

    reg = ProjectRegistry(db_path=str(tmp_path / "since.db"))
    row = {"annotation_type": "RequestForAction", "item_key": propose.ITEM_KEY, "content_hash": "h"}
    for report, at, outcome in (("r1", "2026-10-24T21:09:00", "created"), ("r2", "2026-10-24T22:00:00", "relinked"),
                                ("r3", "2026-10-24T23:00:00", "relinked")):
        reg.record_profile_publish("database", "d", report_guid=report, report_at=at, read_at="t",
                                   analysis_step=propose.ANALYSIS_STEP,
                                   rows=[{**row, "annotation_guid": "g-rfa", "outcome": outcome}])
    reg.record_profile_publish("database", "d", report_guid="r4", report_at="2026-10-25T00:00:00", read_at="t",
                               analysis_step=propose.ANALYSIS_STEP,
                               rows=[{**row, "annotation_guid": "g-rfa-2", "content_hash": "h2", "outcome": "created"}])
    assert reg.relinked_since("r3", propose.ANALYSIS_STEP) == "2026-10-24T21:09:00"
    assert reg.relinked_since("r1", propose.ANALYSIS_STEP) == ""            # created there: nothing re-used
    assert reg.relinked_since("r4", propose.ANALYSIS_STEP) == ""            # a new request: new work
