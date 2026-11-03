"""Q2 proof-path fixes after the live test (2026-10-21, coco_pharma report f533c846, run a90d01a1).

1. The proof pages a report's annotations to the END. One graph read of the report holds one page (999 live), so a
   2,161-annotation report that held every annotation read "999 of 2161 annotations in Egeria · publish failed".
2. An element Egeria does not hold yet is a neutral state, not a failure of the publish.
3. Each check's `qualityDimension` is the ODCS word of its test type; `testType` stays as an additional property.
4. A malformed RE_PUBLIC_URL stops the start, quoting the value.
5. A check's summary names the check, the target and the outcome.

Egeria is faked only at the pyegeria client boundary (`create_autospec(<real class>, spec_set=True)` behind the engine
host's FakeEgeria), and that fake keeps the live limit of one graph read.
"""
from __future__ import annotations

import pytest

from tests.test_engine_host_e4 import (  # noqa: F401 - fixtures and helpers reused
    DB_GUID,
    _row,
    _run_queue_once,
    _sweep,
    egeria,
    no_connection,
    reg,
)

BIG = 1200


@pytest.fixture
def big_report(monkeypatch):
    """The step publishes BIG extra annotations (more than one graph page, 999 live)."""
    from resource_explorer import engine_host
    from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

    def many(entity, step_outputs, registry):
        return [ResourceMeasureAnnotation(summary=f"measure {i}", analysis_step="re_db_derived",
                                          check_name="m", item_key=str(i)) for i in range(BIG)]

    monkeypatch.setattr(engine_host, "_step_output_annotations", many)


def _run(reg, egeria, name):
    egeria.add_action(name)
    assert _sweep(reg).claimed == [name]
    assert _run_queue_once(reg) is not None
    return egeria.actions[name]["completion"], _row(reg)["target"]["outcome"]


# ── 1. the paged proof ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_fake_keeps_the_live_limit_of_one_graph_read(egeria, reg, no_connection, big_report):
    """The premise: a graph read of the report holds one page of its annotations, however many it has."""
    _run(reg, egeria, "ea-premise")
    [(report_guid, _)] = egeria.reports()
    assert egeria.annotations_under(report_guid) > BIG
    graph = egeria._asset(report_guid)
    assert len(graph["reportedAnnotations"]) == egeria.GRAPH_PAGE == 999 < egeria.annotations_under(report_guid)


def test_a_report_of_more_than_one_page_is_confirmed_when_every_page_is_read(egeria, reg, no_connection, big_report):
    done, outcome = _run(reg, egeria, "ea-big")
    [(report_guid, _)] = egeria.reports()
    held = egeria.annotations_under(report_guid)
    assert held > egeria.GRAPH_PAGE
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    assert outcome["annotation_count"] == held and outcome["annotations_in_egeria"] == held
    assert "incomplete" not in done["message"]
    # the pages were really read to the end, from the report's end 1, none past the cap
    pages = [p for p in egeria.related_pages if p[0] == report_guid]
    assert sum(p[3] for p in pages) == held and len(pages) >= 3 and all(p[2] <= egeria.RELATED_PAGE_CAP for p in pages)


def test_a_reader_that_stops_paging_early_makes_the_same_run_fail(egeria, reg, no_connection, big_report, monkeypatch):
    """Fail on purpose: the run above passes only because every page is read. A reader that stops after the first
    page counts 500 of 1,200 and the run is survey-failed, never confirmed."""
    from resource_explorer import egeria_link_once

    real = egeria_link_once.related_entries

    def first_page_only(expert, relationship_type, guid, end, *, page_size=egeria_link_once.PAGE_SIZE):
        answer = expert.get_related_metadata_elements(guid, relationship_type, {"class": "ResultsRequestBody"},
                                                      starting_at_end=end, start_from=0, page_size=page_size)
        return answer["elementList"]

    monkeypatch.setattr(egeria_link_once, "related_entries", first_page_only)
    done, outcome = _run(reg, egeria, "ea-early")
    assert real is not first_page_only
    assert done["guards"] == ["survey-failed"] and "incomplete" in done["message"], done
    assert outcome["guard"] == "survey-failed"


def test_a_page_Egeria_never_gives_is_could_not_be_counted_not_a_smaller_number(egeria, reg, no_connection, big_report):
    """The second page fails: the proof says it could not count, and never the 500 it did read."""
    egeria.fail_nth["get_related_metadata_elements"] = (2, RuntimeError("503 from the view server"))
    done, _ = _run(reg, egeria, "ea-503")
    assert done["guards"] == ["survey-failed"]
    assert "could not be counted" in done["message"], done


def test_the_report_is_read_through_the_real_signature_from_end_1(egeria, reg, no_connection):
    done, _ = _run(reg, egeria, "ea-sig")
    assert done["guards"] == ["survey-completed"]
    [(report_guid, _)] = egeria.reports()
    expert = egeria.of("MetadataExpert")
    calls = [c for c in expert.get_related_metadata_elements.call_args_list if c.args[1] == "ReportedAnnotation"]
    assert calls and all(c.args[0] == report_guid and c.kwargs["starting_at_end"] == 1 for c in calls)


# ── 2. unlinked is neutral ─────────────────────────────────────────────────────────────────────────────────────

def test_columns_not_yet_cataloged_are_words_not_a_failure():
    from resource_explorer.surveyors.database.egeria_database_surveyor import element_link_words

    words = element_link_words({"of": 3, "checks": 3, "no_element": 3, "no_element_columns": 3})
    assert words == "3 checks on the report · 3 columns not yet cataloged in Egeria, so not linked to them"
    assert "failed" not in words and "could not" not in words


def test_a_run_whose_checks_link_to_nothing_still_says_published_and_all_landed(egeria, reg, no_connection,
                                                                               monkeypatch):
    """The state the live run was in: every annotation landed, no table cataloged. The status word must be
    'published', led by the report, with the neutral words after it."""
    from resource_explorer.surveyors.database.quality.run_checks import build_quality_annotations
    from resource_explorer.surveyors.database.quality.catalogue import load_catalogue

    rows = [_row_for("Unique", "sales", "orders", "id", 0, 66), _row_for("Future_Date", "sales", "lot", "d", 0, 66),
            _row_for("Required", "sales", "orders", "note", 0, 66)]
    checks = build_quality_annotations(rows, load_catalogue())
    from resource_explorer import engine_host
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    # Egeria holds the annotations it was given, and no table or column element for them (not cataloged yet).
    original = EgeriaDatabaseSurveyor._find_element_guid

    def find(self, name):
        if name.startswith("Annotation::"):
            return next((g for g, e in egeria.elements.items() if e["type"] == "Annotation"
                         and e["body"]["properties"].get("qualifiedName") == name), "")
        if "::orders" in name or "::lot" in name:
            return ""
        return original(self, name)

    monkeypatch.setattr(EgeriaDatabaseSurveyor, "_find_element_guid", find)
    monkeypatch.setattr(engine_host, "_step_output_annotations", lambda entity, outs, registry: checks)
    done, outcome = _run(reg, egeria, "ea-unlinked")
    assert done["guards"] == ["survey-completed"], done
    assert "3 checks on the report · 3 columns not yet cataloged in Egeria, so not linked to them" in done["message"]
    assert outcome["guard"] == "survey-completed" and outcome["annotations_in_egeria"] == outcome["annotation_count"]


# ── 3. the dimension ───────────────────────────────────────────────────────────────────────────────────────────

def _row_for(test_type, schema, table, column, measure, records, status="pass", reason="ok"):
    return {"check_key": f"{schema}.{table}.{column}::{test_type}", "schema_name": schema, "table_name": table,
            "column_name": column, "test_type": test_type, "measure": measure, "record_ct": records, "status": status,
            "reason": reason, "severity": "Fail", "comparison_operator": "mustBe", "threshold_values_json": [0],
            "params_json": None, "baseline_profile_surveyed_at": "", "rule_source": "", "rule_guid": "",
            "ran_as": "", "state": "measured"}


#: Egeria's `qualityDimension`: QualityAnnotationProperties.java:29 (the bean field), OpenMetadataProperty.java:1129
#: (QUALITY_DIMENSION("qualityDimension", String)), OpenMetadataTypesArchive1_2.java:10660 (a QualityAnnotation
#: attribute). The word is the ODCS `DataContractQualityDimension`.
EXPECTED_DIMENSION = {"Missing_Pct": "completeness", "Required": "completeness", "Required_No_Placeholder": "completeness",
                      "Unique": "uniqueness", "Future_Date": "timeliness", "Min_Date": "accuracy",
                      "Row_Ct_Pct": "completeness"}


@pytest.mark.parametrize("test_type,dimension", sorted(EXPECTED_DIMENSION.items()))
def test_each_check_publishes_the_odcs_dimension_word_and_keeps_its_test_type(test_type, dimension):
    from resource_explorer.surveyors.annotation_props import build_annotation_props
    from resource_explorer.surveyors.database.quality.catalogue import ODCS_DIMENSIONS, load_catalogue
    from resource_explorer.surveyors.database.quality.run_checks import build_quality_annotations

    [ann] = build_quality_annotations([_row_for(test_type, "s", "t", "c", 1, 10)], load_catalogue())
    props = build_annotation_props(ann, "qn")
    assert props["class"] == "QualityAnnotationProperties"
    assert props["qualityDimension"] == dimension and dimension in ODCS_DIMENSIONS
    assert props["additionalProperties"]["testType"] == test_type            # kept, beside the dimension


# ── 4. RE_PUBLIC_URL ───────────────────────────────────────────────────────────────────────────────────────────

LIVE_BAD = "http://localhost:8813git push origin re/design-cataloguing-at-scale"


@pytest.mark.parametrize("value", ["", "   ", "http://localhost:8813", "https://box.example.org", "http://box:8810/",
                                   "  http://box:8810  "])
def test_a_usable_public_url_is_accepted(value):
    from resource_explorer.config import check_public_url

    assert check_public_url(value) == value.strip()


@pytest.mark.parametrize("value", [LIVE_BAD, "localhost:8813", "ftp://box", "http://", "http://box:99999",
                                   "http://box:80 80", "box.example.org", "http://box\n:8810"])
def test_a_malformed_public_url_is_refused_quoting_the_value(value):
    from resource_explorer.config import PublicUrlInvalid, check_public_url

    with pytest.raises(PublicUrlInvalid) as exc:
        check_public_url(value)
    assert repr(value) in str(exc.value) and "will not start" in str(exc.value)


def test_the_web_app_refuses_to_start_with_the_live_value(monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.config import PublicUrlInvalid
    from resource_explorer.web.app import app

    monkeypatch.setenv("RE_PUBLIC_URL", LIVE_BAD)
    with pytest.raises(PublicUrlInvalid) as exc:
        with TestClient(app):
            pass
    assert LIVE_BAD in str(exc.value)


def test_the_worker_refuses_to_start_with_the_live_value(monkeypatch):
    from resource_explorer import worker
    from resource_explorer.config import PublicUrlInvalid

    monkeypatch.setenv("RE_PUBLIC_URL", LIVE_BAD)
    monkeypatch.setattr(worker, "_preload_pyegeria", lambda: None)
    with pytest.raises(PublicUrlInvalid):
        worker.run_worker()


# ── 5. the summary ─────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("test_type,measure,records,status,expected", [
    ("Future_Date", 0, 66, "pass", "Future_Date on lot_status.quar_start: passed (0 future dates of 66)"),
    ("Future_Date", 3, 66, "fail", "Future_Date on lot_status.quar_start: failed (3 future dates of 66)"),
    ("Unique", 0, 1000, "pass", "Unique on lot_status.quar_start: passed (0 duplicate values of 1000)"),
    ("Required", 4, 1000, "fail", "Required on lot_status.quar_start: failed (4 missing values of 1000)"),
    ("Required_No_Placeholder", 0, 20, "pass",
     "Required_No_Placeholder on lot_status.quar_start: passed (0 missing values of 20)"),
    ("Min_Date", 0, 12, "pass", "Min_Date on lot_status.quar_start: passed (0 dates prior to limit of 12)"),
    ("Missing_Pct", 2.5, 100, "warning", "Missing_Pct on lot_status.quar_start: failed (difference measure 2.5)"),
    ("Row_Ct_Pct", 100, 200, "pass", "Row_Ct_Pct on lot_status.quar_start: passed (percent of baseline 100)"),
])
def test_a_summary_names_the_check_the_target_and_the_outcome(test_type, measure, records, status, expected):
    from resource_explorer.surveyors.database.quality.catalogue import load_catalogue
    from resource_explorer.surveyors.database.quality.run_checks import build_quality_annotations

    [ann] = build_quality_annotations(
        [_row_for(test_type, "lot_status", "lot_status", "quar_start", measure, records, status)], load_catalogue())
    assert ann.summary.replace("lot_status.lot_status.quar_start", "lot_status.quar_start") == expected
    assert "Past Dates" not in ann.summary


def test_every_test_type_in_the_catalogue_summarises_with_its_key_not_its_display_name():
    from resource_explorer.surveyors.database.quality.catalogue import load_catalogue
    from resource_explorer.surveyors.database.quality.run_checks import build_quality_annotations

    catalogue = load_catalogue()
    for key, test_type in catalogue.items():
        [ann] = build_quality_annotations([_row_for(key, "s", "t", "c", 1, 10)], catalogue)
        assert ann.summary.startswith(f"{key} on s.t.c: passed (")
        # The key is stripped first: a display name that is a piece of its own key (Schema in Schema_Drift) is
        # not a leak. Anything the summary says beyond the key must not carry the display name.
        assert test_type.name_short not in ann.summary.replace(key, "", 1) or test_type.name_short == key


def test_a_check_that_reached_no_measure_gives_its_reason_shortened():
    from resource_explorer.surveyors.database.quality.catalogue import load_catalogue
    from resource_explorer.surveyors.database.quality.run_checks import build_quality_annotations

    long_reason = "permission denied for table orders " + "x" * 400
    [ann] = build_quality_annotations(
        [_row_for("Unique", "s", "t", "c", None, None, status="not_permitted", reason=long_reason)], load_catalogue())
    assert ann.summary.startswith("Unique on s.t.c: not_permitted (permission denied for table orders")
    assert len(ann.summary) < 220 and ann.summary.endswith("…)")


# ── 1b. the annotations routes read the same paged list (databases, filesystems, egeria) ───────────────────────

def _many(n=BIG):
    return [(f"a{i}", "Annotation", {"qualifiedName": f"qn-{i}", "summary": f"s{i}"}) for i in range(n)]


def test_the_database_annotations_route_reader_pages_past_one_graph_page():
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    from tests.report_pages import paged_expert

    s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
    s.connect = lambda: None
    s._metadata_expert = paged_expert(lambda guid: _many())
    assert len(s.get_annotations_by_report_guid("r")) == BIG


def test_the_filesystem_annotations_route_reader_pages_past_one_graph_page():
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
    from tests.report_pages import paged_expert

    s = EgeriaFileSystemSurveyor.__new__(EgeriaFileSystemSurveyor)
    s.connect = lambda: None
    s._metadata_expert = paged_expert(lambda guid: _many())
    assert len(s.get_annotations_by_report_guid("r")) == BIG


def test_the_egeria_annotations_route_reader_pages_past_one_graph_page():
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
    from tests.report_pages import paged_expert

    p = EgeriaPublisher.__new__(EgeriaPublisher)
    p._connect = lambda: None
    p._metadata_expert = paged_expert(lambda guid: _many())
    assert len(p.get_annotations_by_report_guid("r")) == BIG


def test_the_native_survey_read_back_reads_every_annotation(egeria, reg, no_connection, monkeypatch):
    """`native_survey_run.read_report` takes its header from the graph read and its annotations from the paged list."""
    from resource_explorer import native_survey_run
    from tests.report_pages import paged_expert

    port = native_survey_run.PyegeriaSurveyPort.__new__(native_survey_run.PyegeriaSurveyPort)
    port._enter = lambda: None
    port._get_expert = lambda: paged_expert(lambda guid: _many())
    assets = type("A", (), {"get_asset_by_guid": staticmethod(lambda *a, **k: {
        "elementHeader": {"guid": "r", "versions": {}}, "properties": {"createdTime": "2026-10-21T00:00:00"}})})()
    port._get_assets = lambda: assets
    read = port.read_report("r")
    assert len(read.annotations) == BIG
