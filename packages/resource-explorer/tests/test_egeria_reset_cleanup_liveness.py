"""The clear keeps pointers that still resolve in Egeria (owner's decision 2026-11-13).

Found by the 2026-11-13 recovery dry run: three pointers written by a publish AFTER the 11-11 reset (docling's
asset, its survey report, the GitHub SourceControlLibrary in app_settings) resolved in Egeria, and the clear would
have blanked them. Each pointer is now read by GUID first: live -> kept, gone -> cleared, unreadable -> kept and
said so (an unread pointer is never taken for a dead one). The reads here are a fake Egeria; the registry is a temp
SQLite (see test_egeria_reset_cleanup.py, whose fixture and seed this reuses).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_egeria_reset_cleanup import NOW, RESET, TOKEN, Cap, env, ins  # noqa: F401  (env is a fixture)
import clear_egeria_pointers_after_reset as S  # noqa: E402

LIVE_G = "22222222-2222-2222-2222-222222222222"
UNREAD_G = "33333333-3333-3333-3333-333333333333"
LIB_G = "44444444-4444-4444-4444-444444444444"
AFTER = "2026-10-08T20:00:00.000+00:00"           # created after RESET (2026-10-08T18:30Z)
BEFORE = "2026-10-01T09:00:00.000+00:00"


def not_found():
    from pyegeria.core._exceptions import PyegeriaNotFoundException
    return PyegeriaNotFoundException.__new__(PyegeriaNotFoundException)


def unauthorized():
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    return PyegeriaUnauthorizedException.__new__(PyegeriaUnauthorizedException)


def element(type_name: str, qn: str, created: str) -> dict:
    """A by-GUID answer in the LIVE 6.2 shape (observed 2026-11-13 on laz)."""
    return {"elementGUID": "x", "type": {"typeName": type_name},
            "versions": {"createdBy": "resourceexplorernpa", "createTime": created},
            "elementProperties": {"propertyValueMap": {"qualifiedName": {"primitiveValue": qn}}}}


class FakeEgeria:
    """`answers`: GUID -> an element dict (live), an exception (raised), or any other value (returned as is).
    Every other GUID raises pyegeria's not-found. Counts the reads."""

    def __init__(self, answers: dict | None = None):
        self.answers = dict(answers or {})
        self.reads: list[str] = []

    def read(self, guid: str):
        self.reads.append(guid)
        a = self.answers.get(guid)
        if a is None:
            raise not_found()
        if isinstance(a, BaseException):
            raise a
        return a

    def factory(self):
        return lambda: S.GuidLiveness(self.read)


def sql(env, statement, params=()):
    with env["reg"]._conn() as c:
        c.execute(statement, params)


def one(env, statement, params=()):
    with env["reg"]._conn() as c:
        row = c.execute(statement, params).fetchone()
    return dict(row) if row else None


def dry(env, egeria):
    cap = Cap()
    rc = S.run(["--reset-at", RESET, "--out-dir", str(env["dir"])], out=cap, now=NOW, reads_factory=egeria.factory())
    h = next((l.split(": ", 1)[1] for l in cap.text.splitlines() if l.startswith("plan hash: ")), "")
    f = next((l.split(": ", 1)[1] for l in cap.text.splitlines() if l.startswith("plan file: ")), "")
    return rc, cap, h, f


def apply(env, egeria, h, f):
    cap = Cap()
    rc = S.run(["--reset-at", RESET, "--out-dir", str(env["dir"]), "--apply", "--plan-file", f, "--plan-hash", h,
                "--database", env["name"], "--cleared-by", TOKEN], out=cap, now=NOW, reads_factory=egeria.factory())
    return rc, cap


def plan_of(f) -> dict:
    return json.loads(Path(f).read_text())["plan"]


def action(plan, action_id) -> dict:
    return next(a for a in plan["actions"] if a["id"] == action_id)


def markers_for(env, slug) -> int:
    return one(env, "SELECT COUNT(*) AS n FROM catalogue_commit_proofs WHERE database_slug = ? AND proof = ?",
               (slug, S.PROOF_KIND))["n"]


# ── live ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_live_pointer_is_kept_with_its_created_time_and_its_resource_gets_no_marker(env):
    sql(env, "UPDATE projects SET egeria_asset_guid = ? WHERE slug = 'repo1'", (LIVE_G,))
    sql(env, "UPDATE project_egeria_surveys SET egeria_report_guid = ? WHERE project_slug = 'repo1' "
             "AND egeria_report_guid = '11111111-1111-1111-1111-111111111111'", (LIVE_G,))
    egeria = FakeEgeria({LIVE_G: element("Asset", "GitHubRepository::https://x/r1", AFTER)})
    rc, cap, h, f = dry(env, egeria)
    assert rc == 0, cap.text
    plan = plan_of(f)
    projects = action(plan, "clear:projects")
    assert "repo1" not in [r["slug"] for r in projects["rows"]]           # the seed's other project is gone: cleared
    (kept,) = projects["kept"]
    assert kept["row"]["slug"] == "repo1"
    assert kept["reason"] == f"keep: live, created {AFTER}"
    assert kept["pointers"]["egeria_asset_guid"] == {"guid": LIVE_G, "state": "live", "type": "Asset",
                                                      "qualified_name": "GitHubRepository::https://x/r1",
                                                      "created": AFTER}
    assert [k["row"]["id"] for k in action(plan, "clear:project_egeria_surveys")["kept"]]
    # repo1 had a report_published proof before the reset; its publish is live, so it gets NO marker
    assert "repo1" not in [m["slug"] for m in action(plan, "marker:catalogue_commit_proofs")["rows"]]
    assert plan["markers_skipped"][0]["slug"] == "repo1"
    assert "project_egeria_surveys" in plan["markers_skipped"][0]["reason"]
    assert "KEPT projects [slug=repo1] keep: live, created" in cap.text
    assert "NO MARKER for repo1" in cap.text
    assert plan["liveness"]["rows_kept"] >= 2 and plan["liveness"]["guids_live"] == 1

    rc, cap = apply(env, egeria, h, f)
    assert rc == 0, cap.text
    assert one(env, "SELECT egeria_asset_guid FROM projects WHERE slug = 'repo1'")["egeria_asset_guid"] == LIVE_G
    assert markers_for(env, "repo1") == 0
    assert markers_for(env, "db1") == 1                                   # a resource whose pointers were gone
    assert one(env, "SELECT egeria_asset_guid FROM databases WHERE slug = 'db1'")["egeria_asset_guid"] == ""


def test_a_live_pointer_created_before_the_reset_is_kept_and_the_reason_says_check_the_reset_time(env):
    sql(env, "UPDATE databases SET egeria_asset_guid = ? WHERE slug = 'db1'", (LIVE_G,))
    rc, cap, h, f = dry(env, FakeEgeria({LIVE_G: element("RelationalDatabase", "db::d", BEFORE)}))
    (kept,) = action(plan_of(f), "clear:databases")["kept"]
    assert kept["reason"] == f"keep: live, created {BEFORE} (BEFORE the reset: check --reset-at)"


def test_a_live_answer_without_a_create_time_is_still_kept(env):
    sql(env, "UPDATE databases SET egeria_asset_guid = ? WHERE slug = 'db1'", (LIVE_G,))
    rc, cap, h, f = dry(env, FakeEgeria({LIVE_G: {"type": {"typeName": "RelationalDatabase"}}}))
    (kept,) = action(plan_of(f), "clear:databases")["kept"]
    assert kept["reason"] == "keep: live, created at an unknown time"


# ── dead ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_every_pointer_gone_clears_everything_and_reads_each_distinct_guid_once(env):
    egeria = FakeEgeria()
    rc, cap, h, f = dry(env, egeria)
    plan = plan_of(f)
    assert all(not a.get("kept") for a in plan["actions"])
    assert plan["markers_skipped"] == [] and plan["liveness"]["rows_kept"] == 0
    assert len(egeria.reads) == len(set(egeria.reads)) > 1             # one read per distinct GUID
    assert "0 rows KEPT" in cap.text
    rc, cap = apply(env, egeria, h, f)
    assert rc == 0, cap.text
    assert one(env, "SELECT egeria_asset_guid FROM projects WHERE slug = 'repo1'")["egeria_asset_guid"] is None
    assert markers_for(env, "repo1") == 1


# ── unreadable ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer, why", [
    (unauthorized(), "by-GUID read raised PyegeriaUnauthorizedException"),
    (RuntimeError("connection reset"), "by-GUID read raised RuntimeError"),
    ("Something unexpected", "by-GUID read gave an answer of an unexpected shape"),
    ({}, "by-GUID read gave an answer of an unexpected shape"),
])
def test_an_unreadable_pointer_is_kept_never_taken_for_gone(env, answer, why):
    sql(env, "UPDATE databases SET egeria_asset_guid = ? WHERE slug = 'db1'", (UNREAD_G,))
    egeria = FakeEgeria({UNREAD_G: answer})
    rc, cap, h, f = dry(env, egeria)
    assert rc == 0, cap.text
    (kept,) = action(plan_of(f), "clear:databases")["kept"]
    assert kept["reason"] == f"keep: unreadable ({why}): not treated as gone"
    rc, cap = apply(env, egeria, h, f)
    assert rc == 0, cap.text
    assert one(env, "SELECT egeria_asset_guid FROM databases WHERE slug = 'db1'")["egeria_asset_guid"] == UNREAD_G


def test_a_relationship_guid_alone_is_not_read_as_an_element_and_is_kept(env):
    sql(env, "UPDATE doc_sources SET egeria_external_ref_guid = '', egeria_link_relationship_guid = ?", (LIVE_G,))
    egeria = FakeEgeria()
    rc, cap, h, f = dry(env, egeria)
    kept = action(plan_of(f), "clear:doc_sources")["kept"]
    assert kept and kept[0]["pointers"]["egeria_link_relationship_guid"]["state"] == "unreadable"
    assert LIVE_G not in egeria.reads


def test_egeria_out_of_reach_refuses_and_writes_nothing(env):
    def broken():
        raise ConnectionError("no route")
    cap = Cap()
    rc = S.run(["--reset-at", RESET, "--out-dir", str(env["dir"])], out=cap, now=NOW, reads_factory=broken)
    assert rc == 2
    assert ("REFUSED: cannot read Egeria to check which pointers still resolve (ConnectionError); nothing is "
            "planned, because the clear never treats an unread pointer as gone") in cap.text
    assert not env["dir"].exists() or not list(env["dir"].iterdir())


# ── app_settings and the caches ─────────────────────────────────────────────────────────────────────────────

def test_a_live_library_guid_in_app_settings_is_kept_and_the_claims_are_still_removed(env):
    env["reg"].set_setting("egeria.github_source_control_library_guid", LIB_G)
    egeria = FakeEgeria({LIB_G: element("SourceControlLibrary", "SourceControlLibrary::github.com", AFTER)})
    rc, cap, h, f = dry(env, egeria)
    a = action(plan_of(f), "delete:app_settings")
    assert "egeria.github_source_control_library_guid" not in [r["key"] for r in a["rows"]]
    (kept,) = a["kept"]
    assert kept["row"]["key"] == "egeria.github_source_control_library_guid"
    assert kept["pointers"]["value"]["qualified_name"] == "SourceControlLibrary::github.com"
    assert "egeria_register_claim::db1" in [r["key"] for r in a["rows"]]  # a claim, not a GUID: removed as before
    rc, cap = apply(env, egeria, h, f)
    assert rc == 0, cap.text
    assert env["reg"].get_setting("egeria.github_source_control_library_guid") == LIB_G
    assert not env["reg"].get_setting("egeria_register_claim::db1")


def test_a_dead_library_guid_is_removed(env):
    env["reg"].set_setting("egeria.github_source_control_library_guid", LIB_G)
    rc, cap, h, f = dry(env, FakeEgeria())
    a = action(plan_of(f), "delete:app_settings")
    assert "egeria.github_source_control_library_guid" in [r["key"] for r in a["rows"]] and not a["kept"]


def test_a_live_cache_row_is_kept_but_linkage_status_is_not_a_pointer(env):
    sql(env, "UPDATE survey_definition_cache SET process_guid = ? WHERE entity_slug = 'repo1'", (LIVE_G,))
    sql(env, "UPDATE egeria_linkage_status SET stale_guid = ? WHERE entity_slug = 'repo1'", (LIVE_G,))
    egeria = FakeEgeria({LIVE_G: element("GovernanceActionProcess", "GovActionProcess::RepoRefreshSurvey", AFTER)})
    rc, cap, h, f = dry(env, egeria)
    plan = plan_of(f)
    assert [k["row"]["entity_slug"] for k in action(plan, "delete:survey_definition_cache")["kept"]] == ["repo1"]
    linkage = action(plan, "delete:egeria_linkage_status")
    assert "repo1" in [r["entity_slug"] for r in linkage["rows"]] and not linkage["kept"]
    # a kept cache row is not a publish pointer: repo1 still gets its marker
    assert "repo1" in [m["slug"] for m in action(plan, "marker:catalogue_commit_proofs")["rows"]]


# ── end to end: dry run -> plan -> apply, and the hash covers what Egeria answered ─────────────────────────

def test_end_to_end_the_plan_hash_covers_the_answers_and_a_changed_answer_refuses_the_apply(env):
    sql(env, "UPDATE projects SET egeria_asset_guid = ? WHERE slug = 'repo1'", (LIVE_G,))
    live = FakeEgeria({LIVE_G: element("Asset", "GitHubRepository::https://x/r1", AFTER)})
    rc, cap, h, f = dry(env, live)
    assert rc == 0
    assert plan_of(f)["hash"] == h
    # the same registry, but Egeria now says the asset is gone: a different plan, so the apply refuses
    rc, cap = apply(env, FakeEgeria(), h, f)
    assert rc == 2
    assert "the registry changed since the dry run" in cap.text
    assert "and so must what Egeria answered for each pointer" in cap.text
    assert one(env, "SELECT egeria_asset_guid FROM projects WHERE slug = 'repo1'")["egeria_asset_guid"] == LIVE_G
    # with the same answers the apply goes through, and a second dry run finds only the kept rows
    rc, cap = apply(env, live, h, f)
    assert rc == 0, cap.text
    rc, cap, h2, f2 = dry(env, live)
    plan = plan_of(f2)
    assert sum(a["count"] for a in plan["actions"] if a["kind"] == "clear_columns") == 0
    assert [k["row"]["slug"] for k in action(plan, "clear:projects")["kept"]] == ["repo1"]
    act = one(env, "SELECT detail FROM activity_log WHERE operation = 'egeria_reset_cleanup'")
    detail = json.loads(act["detail"])
    assert detail["kept_live"] >= 1 and detail["markers_skipped"] == ["repo1"]


def test_a_plan_with_nothing_to_check_never_contacts_egeria(env):
    with env["reg"]._conn() as c:                     # blank every pointer the clear would check
        for table, _keys, setcols, *_ in S.CLEAR_SPECS:
            for col in (c_ for c_ in setcols if "guid" in c_):
                c.execute(f"UPDATE {table} SET {col} = {'NULL' if table == 'projects' else chr(39) * 2}")
        for t in S.DELETE_POINTERS:
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM app_settings")
    called = []

    def factory():
        called.append(1)
        return lambda guid: {"state": S.GONE}
    cap = Cap()
    assert S.run(["--reset-at", RESET, "--out-dir", str(env["dir"])], out=cap, now=NOW, reads_factory=factory) == 0
    assert called == []


# ── the guard gap: a publish after the reset that left no proof row ─────────────────────────────────────────

def test_a_publish_row_after_the_reset_is_reported_even_without_a_proof_row(env):
    """2026-11-12 18:02Z: docling's scheduled RepoRefreshSurvey published through the survey-run publisher, which
    records project_egeria_surveys (with published_at) but writes no catalogue_commit_proofs row, so the
    proofs-only guard (slugs_with_proofs_after_reset) did not see it."""
    ins(env["reg"], "project_egeria_surveys", project_slug="repo1", surveyed_at="2026-10-08T19:59:00",
        egeria_report_guid=LIVE_G, published_at="2026-10-08T20:00:00", annotation_count=1)
    egeria = FakeEgeria({LIVE_G: element("SurveyReport", "SurveyReport::GitHubRepo::repo1::x", AFTER)})
    rc, cap, h, f = dry(env, egeria)
    plan = plan_of(f)
    assert plan["slugs_with_proofs_after_reset"] == []                    # the old guard alone misses it
    assert {"slug": "repo1", "table": "project_egeria_surveys",
            "published_at": "2026-10-08T20:00:00"} in plan["slugs_published_after_reset"]
    assert "WARNING publish rows newer than the reset time exist" in cap.text
    assert "repo1 (project_egeria_surveys 2026-10-08T20:00:00)" in cap.text
    # its live report is kept, and so the resource gets no marker
    assert LIVE_G in [k["row"]["egeria_report_guid"] for k in action(plan, "clear:project_egeria_surveys")["kept"]]
    assert "repo1" in [m["slug"] for m in plan["markers_skipped"]]


def test_publish_rows_before_the_reset_are_not_reported(env):
    rc, cap, h, f = dry(env, FakeEgeria())
    assert plan_of(f)["slugs_published_after_reset"] == []
    assert "publish rows newer than the reset time" not in cap.text


def test_the_survey_run_publisher_still_writes_no_proof_row_so_the_guard_must_read_publish_rows():
    """Pins the cause: if the survey-run publisher starts writing proof rows, this guard's reason changes."""
    src = (Path(S.__file__).resolve().parents[1] / "resource_explorer" / "surveyors" / "egeria_publisher.py").read_text()
    assert "record_egeria_survey(" in src and "append_catalogue_commit_proof" not in src
