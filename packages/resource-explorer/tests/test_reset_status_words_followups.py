"""Review follow-ups to the reset status words: parsed-UTC time comparison everywhere, the marker read across
node kinds, a badge-only marker that says what it rests on, publish flags corrected at read time, and the
legacy page never copying an `unbound` context onto other resources."""
from __future__ import annotations

from pathlib import Path

import pytest

from resource_explorer import catalogue_commit as cc
from resource_explorer import repo_publish
from resource_explorer.registry import DatabaseEntity
from resource_explorer.workflows import analysis as analysis_mod
from tests.test_board_summary_read_cost import _wire_one_analysis
from tests.test_egeria_reset_cleanup import RESET_ISO, applied, env, ins  # noqa: F401  (env is a fixture)

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"

# (published_at, expected state) against a reset at 2026-10-08T18:30:00 UTC
CASES = [
    ("2026-10-08T18:29:59+00:00", "published_earlier"),
    ("2026-10-08T18:30:00Z", "published"),
    ("2026-10-08 18:29:59.123", "published_earlier"),          # space separator, 3-digit fraction
    ("2026-10-08T18:30:00.500000", "published"),               # 6-digit fraction
    ("2026-10-08T14:00:00-05:00", "published"),                # 19:00 UTC; as TEXT it sorts before the reset
    ("2026-10-08T20:00:00+02:00", "published_earlier"),        # 18:00 UTC; as TEXT it sorts after the reset
    ("2026-10-08T18:29:00", "published_earlier"),              # naive: read as UTC, like every writer here
]


@pytest.mark.parametrize("published_at,expected", CASES)
def test_published_state_compares_parsed_utc_times(published_at, expected):
    assert cc.published_state(published_at, RESET_ISO) == expected


def test_the_reset_may_carry_an_offset_too_and_reset_since_read_agrees():
    assert cc.published_state("2026-10-08T18:00:00Z", "2026-10-08T20:30:00+02:00") == "published_earlier"
    assert cc.reset_since_read("2026-10-08T14:00:00-05:00", RESET_ISO) is False     # read 19:00 UTC, after
    assert cc.reset_since_read("2026-10-08T20:00:00+02:00", RESET_ISO) is True      # read 18:00 UTC, before
    assert cc.reset_since_read("2026-10-08 17:59:59.9", "2026-10-08T18:30:00Z") is True


def test_the_newest_marker_is_chosen_by_parsed_time_not_by_text(env):
    reg = env["reg"]
    for at in ("2026-10-08T19:30:00+02:00", "2026-10-08T18:00:00"):      # 17:30 UTC and 18:00 UTC
        reg.append_catalogue_commit_proof("repo2", proof="egeria_reset", node_kind="repo_report", read_at=at)
    assert reg.get_egeria_reset_at("repo2") == "2026-10-08T18:00:00"


def test_the_badge_only_marker_compares_parsed_times_and_says_it_is_a_claim(env):
    reg = env["reg"]
    with reg._conn() as c:
        for slug in ("early", "late"):
            c.execute("INSERT INTO projects (slug, display_name, github_url, created_at) VALUES (?, ?, ?, '2026-09-01')",
                      (slug, slug, f"https://x/{slug}"))
    ins(reg, "project_published_analyses", project_slug="early", analysis_id="a", egeria_report_guid="g",
        published_at="2026-10-08T20:00:00+02:00")      # 18:00 UTC: before the reset
    ins(reg, "project_published_analyses", project_slug="late", analysis_id="a", egeria_report_guid="g",
        published_at="2026-10-08T14:00:00-05:00")      # 19:00 UTC: after the reset
    applied(env)
    assert reg.get_egeria_reset_at("early") == RESET_ISO
    assert reg.get_egeria_reset_at("late") == ""
    text = [p for p in reg.list_catalogue_commit_proofs("early") if p["proof"] == "egeria_reset"][0]["detail"]["text"]
    assert "published claim before the reset (no proof rows)" in text
    # a resource WITH proofs keeps the plain text
    plain = [p for p in reg.list_catalogue_commit_proofs("repo1") if p["proof"] == "egeria_reset"][0]["detail"]["text"]
    assert "claim" not in plain


def test_a_repo_and_a_database_sharing_a_slug_both_read_the_one_marker(env):
    reg = env["reg"]
    with reg._conn() as c:
        c.execute("INSERT INTO projects (slug, display_name, github_url, created_at) "
                  "VALUES ('shared', 'S', 'https://x/s', '2026-09-01')")
    reg.register_database(DatabaseEntity(slug="shared", display_name="S", db_type="postgresql", host="localhost",
                                         port=5432, database_name="s"))
    reg.append_catalogue_commit_proof("shared", proof="database_published", node_kind="database",
                                      element_guid="g", read_at="2026-10-06T10:00:00")
    reg.append_catalogue_commit_proof("shared", proof="report_published", node_kind="repo_report",
                                      element_guid="g", read_at="2026-10-06T12:00:00")
    applied(env)
    markers = [p for p in reg.list_catalogue_commit_proofs("shared") if p["proof"] == "egeria_reset"]
    assert len(markers) == 1                                    # one per slug, keyed by node kind of the first
    assert repo_publish.publish_state(reg, "shared")["row"]["word"] == "reset"      # the repo reads it
    header = cc.derive_commit_state(reg, "shared", {"schemas": []})["header"]["text"]
    assert "Egeria was reset" in header                          # and so does the database


def test_a_persisted_board_gets_its_publish_flags_corrected_at_read_time(registry, monkeypatch):
    mock_reader, _ = _wire_one_analysis(monkeypatch)
    monkeypatch.setattr(
        "resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
        lambda entity_type, **kw: [{"id": "db_classification", "name": "C", "description": "", "intent": "discovery",
                                    "annotation_types": ["ZZ"]}])
    registry.record_published_annotation_types("mydb", {"ZZ"})
    first = analysis_mod.build_survey_results(registry, "database", "mydb", include_empty=True, board_id="db_classification")
    assert first["dashboards"][0]["publish_stale"] is False
    registry.mark_egeria_linkage_stale("database_publish", "mydb", "guid-1")
    second = analysis_mod.build_survey_results(registry, "database", "mydb", include_empty=True, board_id="db_classification")
    assert mock_reader.call_count == 1                           # still the persisted fast path
    assert second["dashboards"][0]["publish_stale"] is True


@pytest.fixture
def registry(tmp_path):
    from resource_explorer.registry import ProjectRegistry
    r = ProjectRegistry(db_path=str(tmp_path / "b.db"))
    r.register_database(DatabaseEntity(slug="mydb", display_name="My DB", db_type="postgresql",
                                       host="localhost", port=5432, database_name="mydb"))
    return r


def test_the_legacy_page_never_copies_an_unbound_context_onto_other_resources():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    # the investigation label must not make an `unbound` investigation the session default ...
    assert ("inv.egeria_context.status !== 'unset' && inv.egeria_context.status !== 'unbound'") in html
    # ... and a session default that is `unbound` must not be POSTed to a resource
    assert "_sessionEgeriaProjectContext.status === 'unset' || _sessionEgeriaProjectContext.status === 'unbound'" in html


# ── the owner's invocation, the version refusal, unknown times, one tie rule ─────────────────────────────

import json  # noqa: E402

from tests.test_egeria_reset_cleanup import RESET, TOKEN, apply, dry  # noqa: E402
import clear_egeria_pointers_after_reset as S  # noqa: E402


def test_the_to_apply_line_starts_with_the_command_that_works(env):
    rc, cap, h, f = dry(env)
    line = next(l for l in cap.text.splitlines() if l.startswith("to apply: "))
    assert line.startswith("to apply: uv run python scripts/clear_egeria_pointers_after_reset.py --apply ")
    doc = S.__doc__
    assert "uv run python scripts/clear_egeria_pointers_after_reset.py" in doc and "ModuleNotFoundError" in doc
    assert "unknown → unknown" in doc


def test_a_plan_from_another_script_version_is_refused_with_its_own_sentence(env):
    rc, cap, h, f = dry(env)
    saved = json.loads(Path(f).read_text())
    saved["plan"]["script_version"] = "old-1"
    Path(f).write_text(json.dumps(saved))
    rc, cap = apply(env, h, f)
    assert rc == 2
    assert f"different version of the script (old-1, this is {S.SCRIPT_VERSION}); run the dry run again" in cap.text
    assert "registry changed" not in cap.text
    # a plan with no version recorded at all (an older script) says so
    del saved["plan"]["script_version"]
    Path(f).write_text(json.dumps(saved))
    assert "none recorded" in apply(env, h, f)[1].text


def test_the_registry_changed_refusal_has_its_own_sentence(env):
    rc, cap, h, f = dry(env)
    ins(env["reg"], "project_published_analyses", project_slug="repo1", analysis_id="zz",
        published_at="2026-09-01T00:00:00", egeria_report_guid="g")
    rc, cap = apply(env, h, f)
    assert rc == 2 and "the registry changed since the dry run; run the dry run again" in cap.text
    assert "different version" not in cap.text


def test_an_unparseable_time_is_unknown_and_never_makes_a_claim(env):
    assert cc._ts("yesterday") == "" and not cc._known("yesterday")
    assert cc.published_state("yesterday", RESET_ISO) == "published"       # no "earlier" claim from garbage
    assert cc.published_state("2026-09-01T00:00:00", "tomorrow") == "published"
    assert cc.reset_since_read("yesterday", RESET_ISO) is False
    assert cc.reset_since_read("2026-09-01T00:00:00", "tomorrow") is False
    reg = env["reg"]
    with reg._conn() as c:
        c.execute("INSERT INTO projects (slug, display_name, github_url, created_at) VALUES ('junk','j','https://x/j','2026-09-01')")
    ins(reg, "project_published_analyses", project_slug="junk", analysis_id="a", egeria_report_guid="g", published_at="yesterday")
    reg.append_catalogue_commit_proof("junk", proof="report_published", node_kind="repo_report", element_guid="g",
                                      read_at="not a time")
    applied(env)
    assert reg.get_egeria_reset_at("junk") == ""                              # the script made no marker claim


def test_one_tie_rule_a_proof_read_at_the_reset_time_is_live_on_both_screens(env):
    reg = env["reg"]
    # both written BEFORE the marker, read exactly at the reset time
    reg.append_catalogue_commit_proof("repo1", proof="report_published", node_kind="repo_report",
                                      element_guid="g2", read_at=RESET_ISO)
    reg.append_catalogue_commit_proof("db1", proof="elements_read_back", node_kind="schema", schema_name="s1",
                                      element_guid="g2", target_guid="g2", detail={"tables": ["t"]}, read_at=RESET_ISO)
    applied(env)
    assert repo_publish.publish_state(reg, "repo1")["row"]["word"] == "published"
    st = cc.derive_commit_state(reg, "db1", {"schemas": [{"name": "s1", "effective": "catalogue", "tables": []}]})
    assert st["schemas"]["s1"]["state"] == "catalogued"


def test_project_state_for_unbound_with_and_without_an_inheriting_investigation(env, monkeypatch):
    reg = env["reg"]
    applied(env)
    assert repo_publish.project_state(reg, "repo1")["status"] == "unbound"
    assert repo_publish.project_state(reg, "repo1")["word"] == "unbound by reset · rebind to recreate"
    monkeypatch.setattr(reg, "inherited_egeria_project_context", lambda *a, **k: {
        "egeria_project_qualified_name": "Project::Investigation::inv::x", "_inherited_from_name": "Inv"})
    st = repo_publish.project_state(reg, "repo1")
    assert st["status"] == "inherited" and st["detail"] == "from investigation 'Inv'"


def test_next_steps_names_the_unbound_investigation(env, monkeypatch):
    import asyncio
    from resource_explorer.web.routes import investigations as R
    reg = env["reg"]
    ins(reg, "investigations", slug="inv-u", display_name="U", created_at="2026-09-01T00:00:00",
        egeria_project_status="unbound", egeria_project_guid="")
    monkeypatch.setattr(R, "_registry", lambda: reg)
    steps = asyncio.run(R.next_steps("inv-u"))["steps"]
    bind = [s for s in steps if s["id"] == "bind_egeria"][0]
    assert bind["title"] == "unbound by reset · rebind to recreate" and "recreate it under the same name" in bind["detail"]


def test_the_investigation_list_shows_unbound_not_local():
    js = (STATIC / "next" / "stages" / "investigation.js").read_text(encoding="utf-8")
    body = js[js.index("function bindingGlyph("):js.index("async function renderList(")]
    assert "inv.egeria_context.status === 'unbound'" in body and "unbound by reset" in body
    assert body.index("unbound") < body.index("🏠 local")
