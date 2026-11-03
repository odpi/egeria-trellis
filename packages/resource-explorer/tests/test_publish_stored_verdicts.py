"""Curate's Publish sends the stored column-profile verdicts (DESIGN-PUBLISHING-COLUMN-VERDICTS.md §1).

Until 2026-10-20 `publish_local_survey` rebuilt annotations from the kept survey blob only, so data-class and
reference-data verdicts reached Egeria only through a Survey Definition run, although #759 now stores them.
"""
from __future__ import annotations

import inspect

import pytest
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.column_profile_step import (
    column_match_from_row,
    column_match_rows,
    verdict_annotations_from_rows,
)
from resource_explorer.surveyors.survey_report import annotation_qualified_name

from tests.test_postgres_column_profile import _SCHEMA_INFO, _run

PREFIX = "Annotation::PostgreSQL::coco_ods::2026-10-20T01:00:00"
INVENTORY = {"public.customer.email", "public.customer.country", "public.customer.avatar"}


def _rows(profile=None):
    profile = profile or _run()[1]
    return column_match_rows(profile["matches"])


def _key(ann) -> tuple:
    return (ann.check_name, (ann.item_key or "").split("@", 1)[0], ann.summary, type(ann).__name__)


# ── the rebuild ─────────────────────────────────────────────────────────────────────────────────────────────


def test_a_stored_verdict_rebuilds_into_the_same_annotations_the_step_built():
    _, profile = _run()
    rebuilt = verdict_annotations_from_rows(column_match_rows(profile["matches"]), batch_surveyed_at="b1")
    # The step suppresses the proposal-fallback RFA when it could read the catalogue; this path never creates a
    # DRAFT element, so it may add one. Every annotation the step built is rebuilt, identically.
    step = {_key(a) for a in profile["annotations"]}
    again = {_key(a) for a in rebuilt["annotations"] if a.annotation_type_name != "ProposalFallback"}
    assert step == again


def test_a_row_round_trips_to_the_column_match_it_was_stored_from():
    _, profile = _run()
    for match, row in zip(profile["match_objects"], column_match_rows(profile["matches"]), strict=True):
        back = column_match_from_row(row)
        assert back.as_dict() == match.as_dict()


def test_each_verdict_carries_its_own_batch_date():
    rebuilt = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")
    assert rebuilt["annotations"]
    assert all(a.json_properties["measuredAt"] == "2026-10-19T08:00:00" for a in rebuilt["annotations"])


def test_a_newer_batch_is_a_new_annotation_never_adopted_as_the_old_one():
    """The collision: a republished kept survey reuses its report, so without the batch in the identity a newer
    verdict for the same column would get the old annotation's name and Egeria would keep the old one."""
    old = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    new = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-20T08:00:00")["annotations"]
    old_names = {annotation_qualified_name(PREFIX, i, a) for i, a in enumerate(old)}
    new_names = {annotation_qualified_name(PREFIX, i, a) for i, a in enumerate(new)}
    assert old_names.isdisjoint(new_names)
    # ...and the same batch converges on the same names, so a republish of it adds nothing.
    same = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    assert {annotation_qualified_name(PREFIX, i, a) for i, a in enumerate(same)} == old_names


def test_verdicts_for_columns_no_longer_in_the_inventory_are_dropped_and_counted():
    rebuilt = verdict_annotations_from_rows(_rows(), batch_surveyed_at="b1",
                                            inventory={"public.customer.email"})
    assert set(rebuilt["column_paths"]) == {"public.customer.email"}
    assert rebuilt["dropped_vanished"] == 2 and rebuilt["sent_columns"] == 1


# ── what publish_local_survey reads ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    reg.register_database(_entity())
    return reg


def _entity():
    return DatabaseEntity(slug="coco_ods", display_name="Coco ODS", db_type="postgresql", host="localhost",
                          egeria_host="host.docker.internal", port=5442, database_name="coco_pharma")


def test_no_stored_batch_is_none_stored_not_zero(registry):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    got = EgeriaDatabaseSurveyor._stored_verdict_annotations(registry, "coco_ods", _SCHEMA_INFO)
    assert got["state"] == "none_stored" and got["annotations"] == []


def test_no_registry_is_not_read_never_none_stored():
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    assert EgeriaDatabaseSurveyor._stored_verdict_annotations(None, "coco_ods", _SCHEMA_INFO)["state"] == "not_read"


def test_the_latest_batch_is_read_and_checked_against_the_kept_inventory(registry):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    registry.write_detail_rows("database_column_matches", "coco_ods", "2026-10-19T08:00:00", rows=_rows())
    got = EgeriaDatabaseSurveyor._stored_verdict_annotations(registry, "coco_ods", _SCHEMA_INFO)
    assert got["state"] == "sent" and got["batch"] == "2026-10-19T08:00:00"
    assert got["sent_columns"] == 3 and got["dropped_vanished"] == 0


def test_the_band_names_the_stored_batch(registry):
    from resource_explorer.single_publish import stored_verdicts_summary

    assert stored_verdicts_summary(registry, "coco_ods")["state"] == "none_stored"
    registry.write_detail_rows("database_column_matches", "coco_ods", "2026-10-19T08:00:00", rows=_rows())
    got = stored_verdicts_summary(registry, "coco_ods")
    assert got["state"] == "stored" and got["columns"] == 3 and "measured 10-19" in got["word"]


# ── through publish_local_survey ────────────────────────────────────────────────────────────────────────────


def test_publish_local_survey_sends_the_verdicts_and_links_them_to_committed_columns(registry, monkeypatch):
    from resource_explorer import egeria_link_once
    from resource_explorer.catalogue_gateway import schema_qualified_name, server_name_for
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    registry.write_detail_rows("database_column_matches", "coco_ods", "2026-10-19T08:00:00", rows=_rows())
    entity = _entity()
    sent: dict = {}

    def fake_publish(self, body, qualified_name, annotations, slug, surveyed_at, **_):
        sent["annotations"] = list(annotations)
        return {"report_guid": "report-1", "report_error": "", "annotation_error": "", "reused": False,
                "annotations_in_egeria": len(annotations)}

    # The committed schema's columns exist in Egeria (the cataloguer's names, confirmed live 2026-10-20) except
    # `avatar`, whose column element is absent; every published annotation resolves to a GUID.
    schema_qn = schema_qualified_name(server_name_for(entity), entity.database_name, "public")
    columns = {f"{schema_qn}::customer::email": "col-email", f"{schema_qn}::customer::country": "col-country"}

    def fake_find(self, name):
        if name in columns:
            return columns[name]
        return f"ann:{name}" if name.startswith("Annotation::") else ""

    links: list = []

    def fake_link_once(expert, *, relationship_type, read_guid, read_end, far_guid, create, what, **_):
        links.append((relationship_type, read_guid, read_end, far_guid))
        return egeria_link_once.LinkOutcome(egeria_link_once.LINKED, relationship_guid="rel")

    monkeypatch.setattr(EgeriaDatabaseSurveyor, "catalog_and_survey", lambda self, **k: {"database_guid": "db-1"})
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_publish_survey_report", fake_publish)
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_find_element_guid", fake_find)
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_metadata_expert_client", lambda self: object())
    monkeypatch.setattr(egeria_link_once, "link_once", fake_link_once)

    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    out = s.publish_local_survey(entity, schema_info=_SCHEMA_INFO, schema_count=1, table_count=1, column_count=3,
                                 surveyed_at="2026-10-20T01:00:00", registry=registry, survey_after_catalog=False)

    verdict_types = {a.annotation_type_name for a in sent["annotations"]}
    assert {"DataClassMatch", "ColumnValueSample"} <= verdict_types
    assert out["verdicts"]["state"] == "sent" and out["verdicts"]["batch"] == "2026-10-19T08:00:00"
    links_by_column = {far for (_, _, _, far) in links}
    assert links_by_column == {"col-email", "col-country"}
    assert all(t == "AssociatedAnnotation" and end == 2 for (t, _, end, _) in links)
    counts = out["verdicts"]["column_links"]
    assert counts["linked"] == len(links) and counts["no_column_element"] > 0  # avatar has none


# ── the reuse path: by identity, not by count (live check 2026-10-20) ───────────────────────────────────────


class _FakeDiscovery:
    """Egeria's DataDiscovery at its client boundary: stores each created annotation by its qualifiedName."""

    def __init__(self, maker):
        self.maker = maker

    def create_annotation(self, body):
        qn = body["properties"]["qualifiedName"]
        guid = f"ann-{len(self.maker.annotations)}"
        self.maker.annotations[qn] = guid
        return guid


def _real_surveyor():
    """An EgeriaDatabaseSurveyor whose own publish code is real, down to `publish_annotations`; only the pyegeria
    clients are faked (FakeAssetMaker refuses a second report with the same name, as Egeria does)."""
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    from tests.test_catalogue_commit_round3 import FakeAssetMaker

    s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
    maker = FakeAssetMaker()
    maker.annotations = {}
    s._asset_maker, s._discovery = maker, _FakeDiscovery(maker)
    s._find_element_guid = lambda name: maker.reports.get(name) or maker.annotations.get(name, "")
    return s, maker


def test_a_newer_batch_with_the_same_count_reaches_a_reused_report():
    """The live failure: the report held 12 annotations, the second publish sent 12 with NEW names, and the old
    count shortcut ("held >= sent, skip") sent none of them."""
    s, maker = _real_surveyor()
    body = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::coco_ods::2026-10-20T01:00:00"}}
    qn = body["properties"]["qualifiedName"]
    first = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    second = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-20T08:00:00")["annotations"]
    assert len(first) == len(second)                                   # the same count...

    s._publish_survey_report(body, qn, first, "coco_ods", "2026-10-20T01:00:00")
    held_after_first = set(maker.annotations)
    out = s._publish_survey_report(body, qn, second, "coco_ods", "2026-10-20T01:00:00")

    assert out["reused"] is True
    added = set(maker.annotations) - held_after_first                  # ...but new names: all of them created
    assert len(added) == len(second)
    assert all("@2026-10-20T08:00:00" in name for name in added)
    assert held_after_first <= set(maker.annotations)                  # the first batch is left as it was


def test_republishing_the_same_batch_into_a_reused_report_creates_nothing():
    s, maker = _real_surveyor()
    body = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::coco_ods::2026-10-20T01:00:00"}}
    qn = body["properties"]["qualifiedName"]
    anns = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    s._publish_survey_report(body, qn, anns, "coco_ods", "2026-10-20T01:00:00")
    before = dict(maker.annotations)
    again = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    s._publish_survey_report(body, qn, again, "coco_ods", "2026-10-20T01:00:00")
    assert maker.annotations == before


def test_a_create_failing_mid_batch_on_a_reused_report_is_a_failure_not_done():
    """Review of #783 (HIGH): publish_annotations returns None for a failed create instead of raising. On a reused
    report that already holds an earlier batch, the TOTAL held count runs ahead of what this publish sent, so it
    must not decide: this publish's own GUIDs do."""
    s, maker = _real_surveyor()
    body = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::coco_ods::2026-10-20T01:00:00"}}
    qn = body["properties"]["qualifiedName"]
    first = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    s._publish_survey_report(body, qn, first, "coco_ods", "2026-10-20T01:00:00")

    calls = {"n": 0}
    real_create = s._discovery.create_annotation

    def flaky(body):
        calls["n"] += 1
        if calls["n"] in (2, 4):
            raise RuntimeError("SERVER_ERROR_500 create refused")
        return real_create(body)
    s._discovery.create_annotation = flaky

    second = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-20T08:00:00")["annotations"]
    out = s._publish_survey_report(body, qn, second, "coco_ods", "2026-10-20T01:00:00")
    assert out["reused"] is True
    assert "2 of" in out["annotation_error"]
    assert out["annotations_this_publish"] == len(second) - 2

    from resource_explorer.catalogue_commit import report_step_words
    state, _ = report_step_words({**out, "report_reused": True, "annotation_count": len(second)}, "2026-10-20")
    assert state == "failed"


# ── superseded batches in a reused report are retired (§1 item 10) ──────────────────────────────────────────


class _RecordingDiscovery(_FakeDiscovery):
    """Keeps each annotation's properties and type, and applies `update_annotation` as Egeria's merge update does."""

    def __init__(self, maker, refuse=()):
        super().__init__(maker)
        self.elements: dict[str, dict] = {}
        self.updates: list[tuple[str, dict]] = []
        self.refuse = set(refuse)

    def create_annotation(self, body):
        guid = super().create_annotation(body)
        props = dict(body["properties"])
        self.elements[guid] = {"type": props["class"].removesuffix("Properties"), "properties": props}
        return guid

    def update_annotation(self, *args, **kwargs):
        # Bound to the real pyegeria 6.2.7 signature, (self, annotation_guid, body): a call that does not fit raises.
        from pyegeria import DataDiscovery
        bound = inspect.signature(DataDiscovery.update_annotation).bind(self, *args, **kwargs)
        guid, body = bound.arguments["annotation_guid"], bound.arguments["body"]
        if guid in self.refuse:
            raise RuntimeError("SERVER_ERROR_500 update refused")
        self.updates.append((guid, body))
        assert body["mergeUpdate"] is True
        props = {k: v for k, v in body["properties"].items() if k != "class"}
        assert body["properties"]["class"] == self.elements[guid]["properties"]["class"]  # the element's own bean
        self.elements[guid]["properties"].update(props)


def _retiring_surveyor(refuse=()):
    s, maker = _real_surveyor()
    s._discovery = _RecordingDiscovery(maker, refuse)

    from tests.report_pages import paged_expert

    # the report's annotations are read through the paged ReportedAnnotation list, to its end
    s._metadata_expert = paged_expert(lambda guid: [(g, e["type"], e["properties"])
                                                    for g, e in s._discovery.elements.items()])
    return s, maker


def _status_by_batch(s) -> dict[str, set]:
    import json

    out: dict[str, set] = {}
    for e in s._discovery.elements.values():
        batch = json.loads(e["properties"].get("jsonProperties") or "{}").get("measuredAt", "")
        out.setdefault(batch, set()).add(e["properties"].get("contentStatus", ""))
    return out


def test_an_older_batch_in_a_reused_report_is_retired_and_the_newer_is_not():
    s, _ = _retiring_surveyor()
    body = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::coco_ods::2026-10-20T01:00:00"}}
    qn = body["properties"]["qualifiedName"]
    first = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    s._publish_survey_report(body, qn, first, "coco_ods", "2026-10-20T01:00:00")
    second = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-20T08:00:00")["annotations"]
    out = s._publish_survey_report(body, qn, second, "coco_ods", "2026-10-20T01:00:00")

    got = s._retire_superseded_verdicts(out["report_guid"], "2026-10-20T08:00:00")
    assert got == {"state": "checked", "retired": len(first), "already_retired": 0, "failed": 0}
    status = _status_by_batch(s)
    assert status["2026-10-19T08:00:00"] == {"DEPRECATED"}
    assert "DEPRECATED" not in status["2026-10-20T08:00:00"]
    assert len(s._discovery.elements) == len(first) + len(second)                # retired, never deleted

    # A republish of the newest batch changes nothing: the old ones are already retired.
    s._discovery.updates.clear()
    again = s._retire_superseded_verdicts(out["report_guid"], "2026-10-20T08:00:00")
    assert again["retired"] == 0 and again["already_retired"] == len(first) and not s._discovery.updates


def test_the_same_batch_republished_retires_nothing():
    s, _ = _retiring_surveyor()
    body = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::coco_ods::2026-10-20T01:00:00"}}
    qn = body["properties"]["qualifiedName"]
    anns = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    out = s._publish_survey_report(body, qn, anns, "coco_ods", "2026-10-20T01:00:00")
    got = s._retire_superseded_verdicts(out["report_guid"], "2026-10-19T08:00:00")
    assert got["retired"] == 0 and not s._discovery.updates


def test_survey_annotations_without_a_verdict_batch_are_never_retired():
    from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

    s, _ = _retiring_surveyor()
    body = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::coco_ods::2026-10-20T01:00:00"}}
    qn = body["properties"]["qualifiedName"]
    survey = ResourceMeasureAnnotation(annotation_type_name="TableCount", summary="3 tables",
                                       analysis_step="DatabaseSchemaSurvey", check_name="table_count")
    old = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    out = s._publish_survey_report(body, qn, [survey, *old], "coco_ods", "2026-10-20T01:00:00")
    got = s._retire_superseded_verdicts(out["report_guid"], "2026-10-20T08:00:00")
    assert got["retired"] == len(old)
    survey_status = [e["properties"].get("contentStatus", "") for e in s._discovery.elements.values()
                     if e["properties"].get("analysisStep") == "DatabaseSchemaSurvey"]
    assert survey_status == [""]


def test_a_refused_retirement_is_counted_and_an_unreadable_report_is_not_checked():
    s, _ = _retiring_surveyor(refuse={"ann-0"})
    body = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::coco_ods::2026-10-20T01:00:00"}}
    qn = body["properties"]["qualifiedName"]
    old = verdict_annotations_from_rows(_rows(), batch_surveyed_at="2026-10-19T08:00:00")["annotations"]
    out = s._publish_survey_report(body, qn, old, "coco_ods", "2026-10-20T01:00:00")
    got = s._retire_superseded_verdicts(out["report_guid"], "2026-10-20T08:00:00")
    assert got["failed"] == 1 and got["retired"] == len(old) - 1

    def unreadable(*a, **k):
        raise RuntimeError("SERVER_ERROR_500 read refused")
    s._metadata_expert.get_related_metadata_elements.side_effect = unreadable
    gone = s._retire_superseded_verdicts(out["report_guid"], "2026-10-20T08:00:00")
    assert gone["state"] == "not_checked" and gone["retired"] == 0


def test_publish_local_survey_retires_only_when_the_report_was_reused(registry, monkeypatch):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    registry.write_detail_rows("database_column_matches", "coco_ods", "2026-10-20T08:00:00", rows=_rows())
    calls: list = []
    reused = {"value": False}
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "catalog_and_survey", lambda self, **k: {"database_guid": "db-1"})
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_publish_survey_report", lambda self, *a, **k: {
        "report_guid": "report-1", "report_error": "", "annotation_error": "", "reused": reused["value"],
        "annotations_in_egeria": 0, "annotations_this_publish": 0})
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_link_verdicts_to_columns", lambda self, *a, **k: {})
    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_retire_superseded_verdicts",
                        lambda self, guid, batch: calls.append((guid, batch)) or {"state": "checked", "retired": 3})

    def publish():
        s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
        return s.publish_local_survey(_entity(), schema_info=_SCHEMA_INFO, schema_count=1, table_count=1,
                                      column_count=3, surveyed_at="2026-10-20T01:00:00", registry=registry,
                                      survey_after_catalog=False)

    assert "superseded" not in publish()["verdicts"] and not calls        # a fresh report holds no older batch
    reused["value"] = True
    out = publish()
    assert calls == [("report-1", "2026-10-20T08:00:00")]
    assert out["verdicts"]["superseded"]["retired"] == 3
