"""Curate UX round 3 (2026-10-06): what the owner's two real commits on coco_pharma showed.

The attach step counts what was already attached; the manifest names BOTH survey counts; a timed-out
forced refresh while the cataloguer is already REFRESHING is not a failure; RE's own survey report is
reused (never duplicated, never claimed 'published') when Egeria already holds the same run's report;
nothing on the commit path says 'removed' for an Egeria soft delete; build_plan pays for the gap pass
once. Registry is a tmp SQLite file; Egeria is the stateful fake."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_catalogue_commit import (  # noqa: E402,F401  (fixtures and helpers are shared)
    ME, SURVEY_AT, _attached, _zones, choose, derived, fake, press, registry, step, view, world,
)
from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer.catalogue_gateway import GatewayError  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import grant_all, open_access

    fake = open_access(monkeypatch)
    fake.controls.update(grant_all("zone-a", "zone-b"))   # the configured zones admit everyone (start-up zone check)
    return fake

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


def _measured(world, schemas=8):
    world["registry"].record_database_survey("db", schemas, 61, 90, {"schema_info": {"sales": {}}}, source="local",
                                             surveyed_at="2026-10-03T18:07:08")


# ── (a) the attach step says what was already attached ──────────────────────────────

def test_attach_step_separates_new_attaches_from_already_attached(world, fake, monkeypatch):
    monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)     # a zone would refuse the second Classify
    choose(world, "sales", "catalogue")
    press(world, fake)                                   # first commit attaches sales
    choose(world, "archive", "catalogue")
    _, rec = press(world, fake)                          # second: sales is already attached, archive is new
    assert step(rec, "schema_targets")["detail"] == "1 attached · 1 already attached"
    assert step(rec, "schema_targets")["state"] == "done"


def test_attach_step_with_nothing_already_attached_keeps_the_plain_words(world, fake):
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    assert step(rec, "schema_targets")["detail"] == "1 of 1 attached, each with its proof row"


# ── (c) the manifest names both counts ───────────────────────────────────────────────

def test_manifest_names_what_RE_could_read_and_what_Egeria_counts(world, fake):
    _measured(world, schemas=8)
    choose(world, "sales", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    text = next(ln["text"] for ln in p["manifest"]["lines"] if ln["id"] == "survey_report_whole")
    n_egeria = len(view(world)["schemas"])
    assert text == (f"RE's survey report is published whole; it describes the 8 schemas RE's own 10-03 survey "
                    f"could read (Egeria's survey counts {n_egeria}); elements are created for the 1 you chose.")


# ── (d) the forced refresh ─────────────────────────────────────────────────────────────

def _timeout_refresh(fake, status):
    fake.connector_state = status
    fake.fail["refresh_connector"] = ("TIMEOUT_ERROR_408 => Request timed out for endpoint "
                                      "https://localhost:9443/integration-daemon/integration-connectors/refresh")


def test_refresh_timeout_while_the_cataloguer_is_refreshing_is_skipped_not_failed(world, fake, caplog):
    choose(world, "sales", "catalogue")
    _timeout_refresh(fake, "REFRESHING")
    with caplog.at_level(logging.INFO, logger="resource_explorer.catalogue_commit"):
        _, rec = press(world, fake)
    st = step(rec, "refresh")
    assert st["state"] == "skipped"
    assert st["detail"] == "the cataloguer was already refreshing · elements arrive on its pass"
    assert not [s for s in rec["steps"] if s["state"] == "failed"], "the header's failed count stays 0"
    assert rec["state"] != "failed"
    log = " ".join(r.getMessage() for r in caplog.records)
    assert re.search(r"catalog commit \S+: asking the daemon to refresh .* \(status WAITING|REFRESHING\)", log) or "asking the daemon" in log
    assert "timed out after" in log and "REFRESHING" in log


def test_refresh_timeout_with_any_other_status_is_a_failure_with_egeria_s_word_first(world, fake):
    choose(world, "sales", "catalogue")
    _timeout_refresh(fake, "WAITING")
    _, rec = press(world, fake)
    st = step(rec, "refresh")
    assert st["state"] == "failed" and st["detail"].startswith("TIMEOUT_ERROR_408")


def test_refresh_timeout_with_an_unreadable_status_is_a_failure(world, fake):
    choose(world, "sales", "catalogue")
    _timeout_refresh(fake, "REFRESHING")
    fake.fail["connector_status"] = "500 daemon down"
    _, rec = press(world, fake)
    assert step(rec, "refresh")["state"] == "failed"


def test_a_refused_refresh_that_is_not_a_timeout_stays_a_failure_even_while_refreshing(world, fake):
    choose(world, "sales", "catalogue")
    fake.connector_state = "REFRESHING"
    fake.fail["refresh_connector"] = "403 not authorized"
    _, rec = press(world, fake)
    assert step(rec, "refresh")["state"] == "failed"


# ── (4) RE's own survey report: reuse on a 409, fail on anything else ──────────────────

DUP_409 = ("SERVER_ERROR_500 ... POST https://localhost:9443/servers/qs-view-server/api/open-metadata/asset-maker/assets "
           "caller method=_async_create_element_body_request class=GUIDResponse relatedHTTPCode=409 "
           "exceptionClassName=org.odpi.openmetadata.commonservices.ffdc.exceptions.InvalidParameterException "
           "actionDescription=createMetadataElementInStore exceptionErrorMessageId=OMAG-COMMON-409-001 "
           "not able to create an instance of type SurveyReport because parameter name qualifiedName is defined as a "
           "unique property and value SurveyReport::PostgreSQL::localhost_docker_coco_pharma::2026-10-03T18:07:08.668511 "
           "is not available for use")


class FakeAssetMaker:
    """Egeria's asset maker for ONE database: refuses a second SurveyReport with the same qualifiedName."""
    def __init__(self):
        self.reports: dict[str, str] = {}
        self.creates = 0

    def create_asset(self, body):
        qn = body["properties"]["qualifiedName"]
        if qn in self.reports:
            raise RuntimeError(DUP_409)
        self.creates += 1
        self.reports[qn] = f"00000000-0000-0000-0000-{self.creates:012d}"
        return self.reports[qn]

    def get_asset_by_guid(self, guid, body=None, output_format=None):
        return {"reportedAnnotations": [{"elementHeader": {"guid": g}, "properties": {"qualifiedName": q}}
                                        for q, g in self.annotations.items()]}


def _surveyor(maker, found=None, annotations_made=None):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
    s._asset_maker = maker
    maker.annotations = {}
    s._find_element_guid = lambda name: (found or {}).get(name, maker.reports.get(name, ""))
    made = annotations_made if annotations_made is not None else []

    def create_annotations(annotations, report_guid, slug, at):
        for i, _a in enumerate(annotations):
            qn = f"Annotation::{slug}::{at}::{i}"
            if qn not in maker.annotations:            # publish_annotations looks the name up first: never twice
                maker.annotations[qn] = f"a-{len(maker.annotations)}"
                made.append(qn)
    s._create_annotations = create_annotations
    return s, made


BODY = {"properties": {"qualifiedName": "SurveyReport::PostgreSQL::db::2026-10-03T18:07:08.668511"}}
QN = BODY["properties"]["qualifiedName"]
ANNS = [object()] * 3


def test_two_commits_make_exactly_one_report_and_no_duplicate_annotations():
    maker = FakeAssetMaker()
    s, made = _surveyor(maker)
    first = s._publish_survey_report(BODY, QN, ANNS, "db", "2026-10-03T18:07:08")
    second = s._publish_survey_report(BODY, QN, ANNS, "db", "2026-10-03T18:07:08")
    assert maker.creates == 1 and len(maker.reports) == 1
    assert len(made) == 3 and len(set(made)) == 3, "zero duplicate annotations"
    assert first["reused"] is False and first["report_error"] == ""
    assert second["reused"] is True and second["report_guid"] == first["report_guid"]
    assert second["annotations_in_egeria"] == 3 and second["report_error"] == ""


def test_a_409_whose_report_cannot_be_looked_up_fails_with_the_named_reason():
    maker = FakeAssetMaker()
    maker.reports[QN] = "x"
    s, _ = _surveyor(maker)
    s._find_element_guid = lambda name: ""
    out = s._publish_survey_report(BODY, QN, ANNS, "db", "2026-10-03T18:07:08")
    assert out["report_guid"] == "" and "refused the report as a duplicate but no report with that name could be read" in out["report_error"]


def test_a_different_create_failure_is_a_failure_not_a_reuse():
    class Boom(FakeAssetMaker):
        def create_asset(self, body):
            raise RuntimeError("SERVER_ERROR_500 something else broke")
    s, made = _surveyor(Boom())
    out = s._publish_survey_report(BODY, QN, ANNS, "db", "2026-10-03T18:07:08")
    assert out["report_guid"] == "" and "something else broke" in out["report_error"] and made == []


def test_a_reused_report_with_fewer_annotations_gets_only_the_missing_ones():
    maker = FakeAssetMaker()
    s, made = _surveyor(maker)
    s._publish_survey_report(BODY, QN, ANNS, "db", "2026-10-03T18:07:08")
    first_one = next(iter(maker.annotations))
    maker.annotations = {first_one: maker.annotations[first_one]}          # Egeria holds only 1 of 3
    made.clear()
    out = s._publish_survey_report(BODY, QN, ANNS, "db", "2026-10-03T18:07:08")
    assert len(made) == 2 and first_one not in made and out["annotations_in_egeria"] == 3


def _commit_with(world, fake, monkeypatch, res):
    _measured(world)
    monkeypatch.setattr(fake, "publish_local_report", lambda *a, **k: res)
    choose(world, "sales", "catalogue")
    return press(world, fake)[1]


def test_reused_report_step_prints_the_count_egeria_holds_and_the_local_count_only_when_it_differs(world, fake, monkeypatch):
    g = "11111111-2222-3333-4444-555555555555"
    rec = _commit_with(world, fake, monkeypatch, {"report_element_guid": g, "report_guid": g, "annotation_count": 76,
                                                  "report_reused": True, "annotations_in_egeria": 76})
    st = step(rec, "survey_report")
    assert st["state"] == "done" and st["detail"] == "already in Egeria · report 11111111 · from the 10-03 survey · 76 annotations in Egeria"
    proof = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_REPORT][-1]
    assert proof["element_guid"] == g and proof["detail"]["note"] == "reused existing report (same survey run 2026-10-03T18:07:08)"


def test_reused_report_step_with_a_different_local_count_says_so(world, fake, monkeypatch):
    g = "11111111-2222-3333-4444-555555555555"
    rec = _commit_with(world, fake, monkeypatch, {"report_element_guid": g, "annotation_count": 80,
                                                  "report_reused": True, "annotations_in_egeria": 76})
    assert step(rec, "survey_report")["detail"].endswith("76 annotations in Egeria · built locally: 80")


def test_failed_publish_step_reads_not_published_with_the_cause_and_records_the_failure(world, fake, monkeypatch):
    rec = _commit_with(world, fake, monkeypatch, {"report_guid": "", "annotation_count": 76,
                                                  "report_error": "SERVER_ERROR_500 something else broke"})
    st = step(rec, "survey_report")
    assert st["state"] == "failed"
    assert st["detail"].startswith("report not published · SERVER_ERROR_500") and "76 annotations built, none published" in st["detail"]
    # Rewritten 2026-10-07 (proof rows by GUID): a failed publish is NOT a report_published row; it is a read_failed one.
    proofs = world["registry"].list_catalogue_commit_proofs("db")
    assert [p for p in proofs if p["proof"] == cc.P_REPORT] == []
    failed = [p for p in proofs if p["proof"] == cc.P_READ_FAILED and p["detail"].get("what") == "RE's own survey report"]
    assert len(failed) == 1 and failed[0]["detail"]["error"] == "SERVER_ERROR_500 something else broke"


def test_success_path_still_says_published(world, fake, monkeypatch):
    rec = _commit_with(world, fake, monkeypatch, {"report_element_guid": "abcdef12-0000", "report_guid": "abcdef12-0000", "annotation_count": 76,
                                                  "annotations_in_egeria": 76})
    assert step(rec, "survey_report")["detail"] == "report abcdef12 · 76 annotations published · from the 10-03 survey"


# ── (2) no 'removed' for an Egeria soft delete on the commit path ────────────────────────

def test_no_user_facing_string_on_the_commit_path_says_removed_for_a_soft_delete(world, fake):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["button"] == "Publish · 0 schemas · deletes 1 from Egeria" and "will delete" in p["leave_out"][0]["text"]
    _, rec = press(world, fake)
    d = derived(world)
    texts = [ln["text"] for ln in p["manifest"]["lines"]] + [r["text"] for r in p["leave_out"]]
    texts += [s["detail"] for s in rec["steps"]] + [d["header"]["text"]]
    for st in list(d["schemas"].values()) + list(d["tables"].values()):
        texts += [st.get("words", ""), st.get("second", "")]
    assert d["schemas"]["sales"]["state"] == "deleted" and d["schemas"]["sales"]["words"].startswith("deleted in Egeria")
    assert not [t for t in texts if re.search(r"\bremoved\b|deleted from Egeria", t or "")], texts


# ── (5) the plan pays for the gap pass once ─────────────────────────────────────────────

def test_build_plan_runs_the_gap_pass_once(tmp_path, monkeypatch):
    from resource_explorer import gaps
    from resource_explorer.curate_plan import build_plan
    from resource_explorer.registry import Project, ProjectRegistry
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    calls = []
    real = gaps.record_gaps_for
    monkeypatch.setattr(gaps, "record_gaps_for", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    import resource_explorer.facts as facts_mod
    monkeypatch.setattr(facts_mod, "record_gaps_for", gaps.record_gaps_for, raising=False)
    build_plan(r, "p")
    assert len(calls) == 1, f"{len(calls)} gap passes in one plan"


def test_a_reused_report_whose_missing_annotations_fail_to_publish_fails_the_step(world, fake, monkeypatch):
    g = "11111111-2222-3333-4444-555555555555"
    rec = _commit_with(world, fake, monkeypatch, {"report_element_guid": g, "annotation_count": 76, "report_reused": True,
                                                  "annotations_in_egeria": 40, "annotation_error": "500 broke"})
    st = step(rec, "survey_report")
    assert st["state"] == "failed" and "missing annotations were not published: 500 broke" in st["detail"]


def test_a_fresh_report_short_of_its_annotations_is_failed_with_a_failure_row(world, fake, monkeypatch):
    """re/lookup-failure-never-creates round 3: 0 of 76 landed is not "done", and no success row is written."""
    g = "11111111-2222-3333-4444-555555555555"
    rec = _commit_with(world, fake, monkeypatch, {"report_element_guid": g, "report_guid": g, "annotation_count": 76,
                                                  "annotations_in_egeria": 0})
    st = step(rec, "survey_report")
    assert st["state"] == "failed" and "0 of 76 annotations published" in st["detail"]
    proofs = world["registry"].list_catalogue_commit_proofs("db")
    assert [p for p in proofs if p["proof"] == cc.P_REPORT] == []
    failed = [p for p in proofs if p["proof"] == cc.P_READ_FAILED and p["detail"].get("what") == "RE's own survey report"]
    assert len(failed) == 1 and "0 of 76 annotations published" in failed[0]["detail"]["error"]
