"""scripts/clear_egeria_pointers_after_reset.py: clear Egeria POINTERS, keep DECISIONS and HISTORY.

Only temp SQLite (the seeds are PRAGMA-based, and Postgres behaviour is exercised here only through fakes of the
connection and the URL). The script is never pointed at the shared registry by a test (REGISTRY_DATABASE_URL is a
tmp file in every test). The complementary check is PR/CI's exercise on a scratch Postgres schema: dry run, guards,
apply, idempotent second run and the skip flag, with the real schema left identical.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import clear_egeria_pointers_after_reset as S  # noqa: E402

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import repo_publish  # noqa: E402
from resource_explorer.registry import DatabaseEntity, ProjectRegistry  # noqa: E402

RESET = "2026-10-08T18:30Z"
RESET_ISO = "2026-10-08T18:30:00"
NOW = datetime(2026, 10, 8, 19, 5, 0)
TOKEN = "dwolfson/2026-10-08T19:00Z"
PROJECT_STATUS = "linked"


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "reset_test.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", url)
    monkeypatch.setenv("PGVECTOR_PORT", "1")
    reg = ProjectRegistry(db_path=str(db))
    seed(reg)
    return {"reg": reg, "dir": tmp_path / "plans", "name": "reset_test.db"}


def ins(reg, table, **cols):
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    with reg._conn() as c:
        c.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(cols.values()))


def seed(reg):
    G = "11111111-1111-1111-1111-111111111111"
    with reg._conn() as c:
        c.execute("INSERT INTO projects (slug, display_name, github_url, created_at, egeria_asset_guid) "
                  "VALUES ('repo1', 'R1', 'https://x/r1', '2026-09-01', ?)", (G,))
        c.execute("INSERT INTO projects (slug, display_name, github_url, created_at) "
                  "VALUES ('repo2', 'R2', 'https://x/r2', '2026-09-01')")
    reg.register_database(DatabaseEntity(slug="db1", display_name="D1", db_type="postgresql", host="localhost",
                                         port=5432, database_name="d", egeria_asset_guid=G))
    ins(reg, "sub_resources", resource_type="repo", resource_slug="repo1", locator="a/b", kind="file",
        cataloged_at="2026-10-01T00:00:00", egeria_guid=G)
    ins(reg, "entity_egeria_project_context", entity_type="repo", entity_slug="repo1", user_id="",
        status="linked", egeria_project_guid=G, egeria_project_qualified_name="Project::X::x")
    ins(reg, "entity_egeria_project_context", entity_type="repo", entity_slug="repo2", user_id="",
        status="personal")
    ins(reg, "project_egeria_surveys", project_slug="repo1", surveyed_at="2026-09-01T00:00:00",
        egeria_report_guid=G, published_at="2026-09-01T01:00:00", annotation_count=3)
    ins(reg, "project_published_annotation_types", project_slug="repo1", annotation_type="Q",
        published_at="2026-09-01T01:00:00", egeria_report_guid=G)
    ins(reg, "architecture_materialized_blueprints", id="b1", entity_type="repo", entity_slug="repo1",
        perspective="deployment", cluster_name="c", qualified_name="q", guid=G, materialized_at="2026-09-01")
    ins(reg, "architecture_materialized_components", id="c1", entity_type="repo", entity_slug="repo1",
        scope_locator="s", qualified_name="q", guid=G, materialized_at="2026-09-01")
    ins(reg, "survey_definition_cache", entity_type="repo", entity_slug="repo1", technology_type="t",
        process_guid=G, cached_at="2026-09-01")
    ins(reg, "egeria_linkage_status", entity_type="repo", entity_slug="repo1", status="stale", stale_guid=G,
        detected_at="2026-09-01")
    ins(reg, "work_lists", slug="wl", display_name="WL", created_at="2026-09-01", egeria_guid=G,
        published_at="2026-09-10")
    ins(reg, "native_survey_annotations", entity_type="database", slug="db1", engine_action_guid=G,
        report_guid=G, annotation_guid=G, annotation_type="X", read_at="2026-09-01")
    # keepers
    ins(reg, "architecture_component_verdicts", id="v1", entity_type="repo", entity_slug="repo1",
        scope_locator="s", verdict="accepted", decided_by="me", created_at="2026-09-02")
    # columns are exactly those of registry.py's CREATE TABLE notification_subscriptions
    ins(reg, "notification_subscriptions", entity_type="repo", entity_slug="repo1", analysis_id="a1",
        label="watch", active=1, created_at="2026-09-01", last_checked_at="2026-09-02", last_notified_at="",
        notification_count=3, egeria_notification_type_guid=G, egeria_notification_type_qualified_name="NT::x")
    ins(reg, "resource_journal", id="j1", entity_type="repo", entity_slug="repo1", author="me",
        written_at="2026-09-03", body="a note")
    for t in ("native_survey_annotations", "architecture_component_verdicts", "notification_subscriptions",
              "resource_journal"):
        with reg._conn() as c:
            assert c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] == 1, f"seed for {t} did not land"
    with reg._conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM notification_subscriptions "
                         "WHERE egeria_notification_type_guid <> ''").fetchone()["n"] == 1
    for k, v in (("egeria_register_claim::db1", "x"), ("egeria_server_claim::h", "x"),
                 ("egeria_server_unconfirmed::h", "x"), ("egeria.github_source_control_library_guid", G),
                 ("repo_survey_step::repo1::x", "keep-me")):
        reg.set_setting(k, v)
    for status, kind in (("done", "annotation"), ("dead", "collection_membership"), ("pending", "annotation"),
                         ("failed", "annotation"), ("cancelled", "annotation")):
        ins(reg, "egeria_outbox", entity_type="repo", entity_slug="repo1", element_kind=kind,
            qualified_name=f"qn-{status}", payload_json="{}", status=status, created_at="2026-09-01",
            egeria_guid=G if status == "done" else "")
    reg.append_catalogue_commit_proof("db1", proof="database_published", node_kind="database", element_guid=G, read_at="2026-10-06T10:00:00")
    reg.append_catalogue_commit_proof("db1", proof="elements_read_back", node_kind="schema", schema_name="s1",
                                      element_guid=G, target_guid=G, detail={"tables": ["t"]},
                                      read_at="2026-10-06T11:00:00")
    reg.append_catalogue_commit_proof("repo1", proof="report_published", node_kind="repo_report", element_guid=G,
                                      read_at="2026-10-06T12:00:00")
    ins(reg, "catalogue_scope_events", database_slug="db1", node_kind="schema", schema_name="s1", table_name="",
        choice="catalogue", action="set", source="person", proposal_rule="", proposal_choice="", reason="",
        measured_at="", measured_json="{}", author="me", changed_at="2026-10-01")
    with reg._conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM catalogue_scope_events").fetchone()["n"] == 1
    seed_every_table(reg)


def seed_generic(reg, table, **over):
    """ONE realistic row built from the table's real columns (PRAGMA table_info of the registry's own CREATE
    TABLE): every column gets a non-empty value by its declared type, so blanking ANY column is visible."""
    with reg._conn() as c:
        info = c.execute(f"PRAGMA table_info({table})").fetchall()
        for fk in c.execute(f"PRAGMA foreign_key_list({table})").fetchall():     # a foreign key points at a real parent
            parent = c.execute(f"SELECT {fk['to']} AS k FROM {fk['table']} ORDER BY 1 LIMIT 1").fetchone()
            assert parent is not None, f"no {fk['table']} row for {table}.{fk['from']} to reference"
            over.setdefault(fk["from"], parent["k"])
    assert info, f"{table} does not exist"
    cols = {}
    for col in info:
        name, typ = col["name"], (col["type"] or "").upper()
        if name in over:
            cols[name] = over[name]
        elif col["pk"] and "INT" in typ and sum(1 for x in info if x["pk"]) == 1:
            continue                                      # the autoincrement id
        elif "INT" in typ:
            cols[name] = 1
        elif "REAL" in typ or "FLOAT" in typ:
            cols[name] = 1.5
        else:
            cols[name] = f"v-{name}"
    ins(reg, table, **cols)
    with reg._conn() as c:
        assert c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] >= 1, f"seed for {table} did not land"


#: Tables the byte-identical guarantee covers beyond KEEP_TABLES: enrichment, tags, feedback, dispositions.
# database_surveys is checked by the only-cleared-columns test (its report guid is cleared; the rest is kept).
EXTRA_KEEP = ["resource_context", "resource_tags", "resource_feedback", "repo_dispositions"]
#: A KEEP table that cannot be seeded cheaply would be listed HERE by name, with the reason; asserted empty below
#: so nothing is waved through. (None today: every table is seedable from its own column list.)
UNSEEDABLE: dict[str, str] = {}


def seed_every_table(reg):
    """One realistic row in every KEEP table and in every table the script clears (columns not yet seeded by
    hand above). Each seed asserts it landed."""
    kept = [t for t in KEEP_TABLES + EXTRA_KEEP if t not in UNSEEDABLE]
    cleared = [spec[0] for spec in S.CLEAR_SPECS] + [spec[0] for spec in S.DELETE_SPECS]
    slugs = {"database_surveys": {"database_slug": "db1"}, "project_analysis_findings": {"project_slug": "repo1"},
             "project_analysis_metrics": {"project_slug": "repo1"}}
    seed_generic(reg, "file_systems", slug="fs1", egeria_asset_guid="GUID-fs")
    seed_generic(reg, "filesystem_surveys", filesystem_slug="fs1", egeria_report_guid="GUID-fsr")
    for t in dict.fromkeys(kept + cleared):
        if t in ("file_systems", "filesystem_surveys"):
            continue
        seed_generic(reg, t, **slugs.get(t, {}))        # an extra row even where one exists: realistic, all columns set


def _has_table(reg, t):
    with reg._conn() as c:
        return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone())


def dump(reg, tables=None) -> dict:
    out = {}
    with reg._conn() as c:
        names = tables or [r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        for t in names:
            out[t] = [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()]
    return json.loads(json.dumps(out, default=str, sort_keys=True))


class Cap:
    def __init__(self):
        self.lines = []

    def __call__(self, s):
        self.lines.append(str(s))

    @property
    def text(self):
        return "\n".join(self.lines)


def dry(env, *extra, now=NOW):
    cap = Cap()
    rc = S.run(["--reset-at", RESET, "--out-dir", str(env["dir"]), *extra], out=cap, now=now)
    plan_hash = next((l.split(": ", 1)[1] for l in cap.text.splitlines() if l.startswith("plan hash: ")), "")
    plan_file = next((l.split(": ", 1)[1] for l in cap.text.splitlines() if l.startswith("plan file: ")), "")
    return rc, cap, plan_hash, plan_file


def apply(env, plan_hash, plan_file, extra=(), **over):
    args = {"--database": env["name"], "--cleared-by": TOKEN, "--plan-hash": plan_hash, "--plan-file": plan_file}
    args.update(over)
    argv = ["--reset-at", RESET, "--out-dir", str(env["dir"]), "--apply", *extra]
    for k, v in args.items():
        if v is not None:
            argv += [k, v]
    cap = Cap()
    rc = S.run(argv, out=cap, now=NOW)
    return rc, cap


# ── dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_writes_nothing_and_prints_a_plan_and_a_hash(env):
    before = dump(env["reg"])
    rc, cap, h, f = dry(env)
    assert rc == 0 and len(h) == 64 and Path(f).exists()
    assert dump(env["reg"]) == before
    assert "DRY RUN (nothing written to the registry)" in cap.text and "reset_test.db" in cap.text
    plan = json.loads(Path(f).read_text())["plan"]
    assert "password" not in json.dumps(plan).lower() and "sqlite:///" not in Path(f).read_text()
    by = {a["id"]: a for a in plan["actions"]}
    assert by["clear:projects"]["count"] == 2 and by["clear:databases"]["count"] == 2   # the hand seed + the generic row
    assert by["supersede:egeria_outbox"]["count"] == 2          # pending + failed only
    assert plan["dead_outbox_untouched"] and "dead before the reset · untouched" in cap.text
    assert by["marker:catalogue_commit_proofs"]["count"] == 2   # db1 and repo1


def test_reset_at_must_be_a_time(env):
    cap = Cap()
    assert S.run(["--reset-at", "yesterday"], out=cap, now=NOW) == 2 and "REFUSED" in cap.text


# ── guards (each one fails on purpose, see the note in the report) ───────────

def test_apply_refuses_without_the_token(env):
    _, _, h, f = dry(env)
    before = dump(env["reg"])
    for bad in (None, "", "dwolfson", "dwolfson/not-a-time", "dwolfson/2026-10-01T19:00Z", "/2026-10-08T19:00Z"):
        rc, cap = apply(env, h, f, **{"--cleared-by": bad})
        assert rc == 2 and "REFUSED" in cap.text, bad
    assert dump(env["reg"]) == before


def test_apply_refuses_when_the_database_name_is_not_passed_again_or_is_wrong(env):
    _, _, h, f = dry(env)
    before = dump(env["reg"])
    for bad in (None, "", "other.db"):
        rc, cap = apply(env, h, f, **{"--database": bad})
        assert rc == 2 and "REFUSED" in cap.text
    assert dump(env["reg"]) == before


def test_apply_refuses_a_missing_or_wrong_plan_hash(env):
    _, _, h, f = dry(env)
    before = dump(env["reg"])
    assert apply(env, "", f)[0] == 2
    assert apply(env, "0" * 64, f)[0] == 2
    assert apply(env, h, "")[0] == 2
    assert dump(env["reg"]) == before


def test_apply_refuses_when_the_data_changed_since_the_dry_run(env):
    _, _, h, f = dry(env)
    env["reg"].set_setting("egeria_server_claim::new", "x")       # one more thing to clear
    before = dump(env["reg"])
    rc, cap = apply(env, h, f)
    assert rc == 2 and "changed since the dry run" in cap.text
    assert dump(env["reg"]) == before


def test_apply_refuses_a_stale_dry_run(env):
    _, _, h, f = dry(env, now=datetime(2026, 10, 8, 18, 0, 0))
    rc, cap = apply(env, h, f)
    assert rc == 2 and "older than" in cap.text


def test_apply_refuses_an_edited_plan_file(env):
    _, _, h, f = dry(env)
    data = json.loads(Path(f).read_text())
    data["plan"]["actions"][0]["count"] = 99
    Path(f).write_text(json.dumps(data))
    assert apply(env, h, f)[0] == 2


def test_apply_refuses_while_a_process_looks_active(env):
    reg = env["reg"]
    ins(reg, "activity_log", id="live", ts="2026-10-08T19:00:00", operation="survey", intent="x",
        entity_type="repo", entity_slug="repo1", status="running",
        detail=json.dumps({"_runner": __import__("resource_explorer.run_reconciler", fromlist=["x"]).process_identity()}))
    _, _, h, f = dry(env)
    before = dump(reg)
    rc, cap = apply(env, h, f)
    assert rc == 2 and "activity_log row live is running" in cap.text and dump(reg) == before
    with reg._conn() as c:
        c.execute("DELETE FROM activity_log WHERE id = 'live'")
        c.execute("UPDATE egeria_outbox SET status = 'running' WHERE qualified_name = 'qn-pending'")
    _, _, h, f = dry(env)
    rc, cap = apply(env, h, f)
    assert rc == 2 and "egeria_outbox row(s) are running" in cap.text


def test_an_owner_less_running_row_is_refused_not_assumed_dead(env):
    ins(env["reg"], "activity_log", id="mystery", ts="2026-10-08T19:00:00", operation="survey", intent="x",
        entity_type="repo", entity_slug="repo1", status="running", detail="{}")
    _, _, h, f = dry(env)
    rc, cap = apply(env, h, f)
    assert rc == 2 and "cannot be judged" in cap.text


def test_a_missing_column_fails_loudly(env):
    with env["reg"]._conn() as c:
        c.execute("ALTER TABLE doc_sources RENAME COLUMN egeria_link_relationship_guid TO renamed_away")
    rc, cap, _, f = dry(env)
    assert rc == 2 and "doc_sources.egeria_link_relationship_guid" in cap.text and not f


def test_a_missing_table_fails_loudly(env):
    with env["reg"]._conn() as c:
        c.execute("DROP TABLE survey_definition_cache")
    rc, cap, _, _ = dry(env)
    assert rc == 2 and "survey_definition_cache" in cap.text


def test_no_registry_url_is_a_refusal(monkeypatch):
    monkeypatch.delenv("REGISTRY_DATABASE_URL", raising=False)
    cap = Cap()
    assert S.run(["--reset-at", RESET], out=cap, now=NOW) == 2


# ── apply ────────────────────────────────────────────────────────────────────

KEEP_TABLES = ["architecture_component_verdicts", "catalogue_scope_events", "catalogue_scope_baselines",
               "resource_scope_events", "native_survey_annotations", "step_runs",
               "project_analysis_findings", "project_analysis_metrics", "resource_journal", "project_groups",
               "work_list_members", "resource_curator_notes"]


def test_every_kept_table_exists_so_the_byte_identical_check_cannot_be_vacuous(env):
    missing = [t for t in KEEP_TABLES + EXTRA_KEEP if not _has_table(env["reg"], t)]
    assert missing == [], missing
    assert UNSEEDABLE == {}, UNSEEDABLE
    for t in KEEP_TABLES + EXTRA_KEEP:
        with env["reg"]._conn() as c:
            assert c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] >= 1, f"{t} holds no seeded row"


def test_only_the_cleared_columns_of_the_cleared_tables_changed(env):
    applied(env)
    before, after = env["before_all"], dump(env["reg"])
    cleared_cols: dict = {}
    for table, _keys, setcols, *_ in S.CLEAR_SPECS:     # investigations has two specs (linked / other)
        cleared_cols.setdefault(table, set()).update(setcols)
    for table, setcols in cleared_cols.items():
        assert before[table], f"{table} has no seeded row"
        assert len(after[table]) == len(before[table]), table
        for b, a in zip(before[table], after[table]):
            assert {k: v for k, v in a.items() if k not in setcols} == {k: v for k, v in b.items() if k not in setcols}, table
        assert any(b != a for b, a in zip(before[table], after[table])), f"{table}: nothing was cleared"


def applied(env):
    _, _, h, f = dry(env, "--old-collection-id", "OLD", "--new-collection-id", "NEW")
    kept_before = dump(env["reg"], KEEP_TABLES + EXTRA_KEEP)
    env["before_all"] = dump(env["reg"])
    proofs_before = dump(env["reg"], ["catalogue_commit_proofs"])["catalogue_commit_proofs"]
    outbox_before = {r["qualified_name"]: r for r in dump(env["reg"], ["egeria_outbox"])["egeria_outbox"]}
    argv = ["--reset-at", RESET, "--out-dir", str(env["dir"]), "--old-collection-id", "OLD",
            "--new-collection-id", "NEW", "--apply", "--plan-file", f, "--plan-hash", h,
            "--database", env["name"], "--cleared-by", TOKEN]
    cap = Cap()
    rc = S.run(argv, out=cap, now=NOW)
    return rc, cap, h, f, kept_before, proofs_before, outbox_before


def test_apply_clears_pointers_and_keeps_decisions_history_and_proofs(env):
    rc, cap, h, f, kept_before, proofs_before, outbox_before = applied(env)
    assert rc == 0, cap.text
    reg = env["reg"]
    d = dump(reg)
    # pointers cleared, rows kept
    assert all(r["egeria_asset_guid"] in (None, "") for r in d["projects"]) and len(d["projects"]) == 3
    assert d["databases"][0]["egeria_asset_guid"] == ""
    assert d["sub_resources"][0]["egeria_guid"] == "" and d["sub_resources"][0]["cataloged_at"] == "2026-10-01T00:00:00"
    ctx = {r["entity_slug"]: r for r in d["entity_egeria_project_context"]}
    assert ctx["repo1"]["egeria_project_guid"] == "" and ctx["repo1"]["status"] == S.UNBOUND_STATUS
    assert ctx["repo1"]["egeria_project_qualified_name"] == "Project::X::x"
    assert ctx["repo2"]["status"] == "personal"
    assert d["project_egeria_surveys"][0]["egeria_report_guid"] == ""
    assert d["project_egeria_surveys"][0]["published_at"] == "2026-09-01T01:00:00"
    assert d["project_published_annotation_types"][0]["egeria_report_guid"] == ""
    assert d["work_lists"][0]["egeria_guid"] == "" and d["work_lists"][0]["published_at"] == ""
    # cache rows removed
    for t in ("architecture_materialized_blueprints", "architecture_materialized_components",
              "survey_definition_cache", "egeria_linkage_status"):
        assert d[t] == []
    keys = {r["key"] for r in d["app_settings"]}
    assert keys == {"repo_survey_step::repo1::x"}
    # decisions and history byte-identical
    assert dump(reg, KEEP_TABLES + EXTRA_KEEP) == kept_before
    # proofs: every old row identical, plus one marker per resource
    proofs = d["catalogue_commit_proofs"]
    assert proofs[:len(proofs_before)] == proofs_before
    markers = proofs[len(proofs_before):]
    assert sorted(m["database_slug"] for m in markers) == ["db1", "repo1"]
    assert all(m["proof"] == "egeria_reset" and m["read_at"] == RESET_ISO for m in markers)
    detail = json.loads(markers[0]["detail_json"])
    assert detail["text"] == "Egeria reset 2026-10-08 18:30 UTC · OLD → NEW"
    # outbox: done kept, dead left dead, cancelled left, pending and failed superseded
    ob = {r["qualified_name"]: r for r in d["egeria_outbox"]}
    assert ob["qn-done"] == outbox_before["qn-done"] and ob["qn-dead"] == outbox_before["qn-dead"]
    assert ob["qn-cancelled"] == outbox_before["qn-cancelled"]
    for q in ("qn-pending", "qn-failed"):
        assert ob[q]["status"] == "superseded" and ob[q]["last_error"] == "Egeria reset 2026-10-08 18:30 UTC"
    # audit row and snapshot
    act = [r for r in d["activity_log"] if r["operation"] == "egeria_reset_cleanup"]
    assert len(act) == 1 and act[0]["status"] == "ok" and TOKEN in act[0]["detail"]
    snap = Path(f).parent / f"egeria-reset-snapshot-reset_test.db-{h[:12]}.json"
    assert snap.exists() and oct(snap.stat().st_mode & 0o777) == "0o600"
    assert "egeria_asset_guid" in snap.read_text()


def test_second_run_clears_nothing_and_writes_no_second_marker(env):
    applied(env)
    reg = env["reg"]
    after_first = dump(reg)
    rc, cap, h, f = dry(env, "--old-collection-id", "OLD", "--new-collection-id", "NEW")
    plan = json.loads(Path(f).read_text())["plan"]
    assert rc == 0 and all(a["count"] == 0 for a in plan["actions"])
    rc, cap = apply(env, h, f, extra=("--old-collection-id", "OLD", "--new-collection-id", "NEW"))
    assert rc == 0
    after_second = dump(reg)
    after_second["activity_log"] = [r for r in after_second["activity_log"] if r["operation"] != "egeria_reset_cleanup"]
    expect = dict(after_first)
    expect["activity_log"] = [r for r in expect["activity_log"] if r["operation"] != "egeria_reset_cleanup"]
    assert after_second == expect
    assert sum(1 for p in after_second["catalogue_commit_proofs"] if p["proof"] == "egeria_reset") == 2


def test_a_failure_names_the_group_and_leaves_markers_first_and_no_traceback(env):
    _, _, h, f = dry(env)
    reg = env["reg"]
    with reg._conn() as c:
        c.execute("CREATE TRIGGER boom BEFORE UPDATE ON egeria_outbox BEGIN SELECT RAISE(ABORT, 'boom'); END")
    rc, cap = apply(env, h, f)
    assert rc == 3 and "FAILED in group supersede:egeria_outbox" in cap.text
    assert "Traceback" not in cap.text and "boom" not in cap.text
    d = dump(reg)
    ob = {r["qualified_name"]: r["status"] for r in d["egeria_outbox"]}
    assert ob["qn-pending"] == "pending" and ob["qn-failed"] == "failed"     # the failing group rolled back whole
    # the markers went first, so no screen shows cleared pointers without "Egeria was reset"
    assert sum(1 for p in d["catalogue_commit_proofs"] if p["proof"] == "egeria_reset") == 2
    assert "marker:catalogue_commit_proofs" in cap.text
    act = [r for r in d["activity_log"] if r["operation"] == "egeria_reset_cleanup"]
    assert act and act[0]["status"] == "error" and "supersede:egeria_outbox" in act[0]["summary"]


def test_the_marker_group_is_the_first_action(env):
    _, _, _, f = dry(env)
    acts = json.loads(Path(f).read_text())["plan"]["actions"]
    assert acts[0]["kind"] == "write_markers"


def test_an_unexpected_error_prints_only_its_type(env, monkeypatch):
    monkeypatch.setattr(S, "build_plan", lambda *a, **k: (_ for _ in ()).throw(KeyError("secretvalue")))
    cap = Cap()
    assert S.run(["--reset-at", RESET], out=cap, now=NOW) == 3
    assert "KeyError" in cap.text and "secretvalue" not in cap.text and "Traceback" not in cap.text


def test_plan_file_is_0600(env):
    _, _, _, f = dry(env)
    assert oct(Path(f).stat().st_mode & 0o777) == "0o600"


def test_outbox_snapshot_has_every_column_the_supersede_overwrites(env):
    _, _, _, f = dry(env)
    row = next(a for a in json.loads(Path(f).read_text())["plan"]["actions"]
               if a["id"] == "supersede:egeria_outbox")["rows"][0]
    for col in ("id", "entity_slug", "element_kind", "status", "last_error", "claimed_at", "next_attempt_at",
                "completed_at"):
        assert col in row


def test_more_claim_prefixes_and_notification_guid_are_cleared(env):
    reg = env["reg"]
    for k in ("egeria_server_guid::h", "egeria_server_cred_slug::h", "egeria_asset_guid::x"):
        reg.set_setting(k, "v")
    before = dump(reg, ["notification_subscriptions"])["notification_subscriptions"]
    assert len(before) == 2 and all(r["egeria_notification_type_guid"] != "" for r in before)
    applied(env)
    d = dump(reg)
    assert {r["key"] for r in d["app_settings"]} == {"repo_survey_step::repo1::x"}
    after = d["notification_subscriptions"]
    assert len(after) == 2 and all(r["egeria_notification_type_guid"] == "" for r in after)
    # every other column of every row is untouched (including the qualified-name mirror)
    for a, b in zip(after, before):
        assert {k: v for k, v in a.items() if k != "egeria_notification_type_guid"} == \
            {k: v for k, v in b.items() if k != "egeria_notification_type_guid"}


def test_missing_work_lists_refuses_with_the_sentence_and_the_flag_skips_and_records(env):
    with env["reg"]._conn() as c:
        c.execute("DROP TABLE work_list_runs")
        c.execute("DROP TABLE work_list_members")
        c.execute("DROP TABLE work_lists")
    rc, cap, _, _ = dry(env)
    assert rc == 2 and ("table work_lists does not exist yet (created by its own module): nothing to clear there; "
                        "re-run after creating it, or pass --skip-missing-table work_lists") in cap.text
    rc, cap, h, f = dry(env, "--skip-missing-table", "work_lists")
    assert rc == 0 and "SKIPPED (table does not exist yet): work_lists" in cap.text
    plan = json.loads(Path(f).read_text())["plan"]
    assert plan["skipped_tables"] == ["work_lists"] and all(a["table"] != "work_lists" for a in plan["actions"])
    _, _, h2, _ = dry(env, "--skip-missing-table", "work_lists")
    assert h == h2
    rc, cap = apply(env, h, f, extra=("--skip-missing-table", "work_lists"))
    assert rc == 0, cap.text
    act = [r for r in dump(env["reg"])["activity_log"] if r["operation"] == "egeria_reset_cleanup"][0]
    assert "work_lists" in act["detail"]


def test_the_skip_flag_does_not_accept_other_tables_or_skip_an_existing_one(env):
    rc, cap, _, f = dry(env, "--skip-missing-table", "databases")
    assert rc == 2 and "accepts only work_lists" in cap.text
    rc, cap, _, f = dry(env, "--skip-missing-table", "work_lists")           # exists here, so nothing is skipped
    assert rc == 0 and json.loads(Path(f).read_text())["plan"]["skipped_tables"] == []
    with env["reg"]._conn() as c:
        c.execute("DROP TABLE survey_definition_cache")
    rc, cap, _, _ = dry(env, "--skip-missing-table", "survey_definition_cache")
    assert rc == 2 and "survey_definition_cache" in cap.text


def test_marker_read_at_is_T_separated_and_derivation_compares_both_forms(env):
    reg = env["reg"]
    applied(env)
    m = [p for p in reg.list_catalogue_commit_proofs("db1") if p["proof"] == "egeria_reset"][0]
    assert m["read_at"] == RESET_ISO and "T" in m["read_at"]
    # a hand-written, space-separated proof from after the reset still counts as after it (' ' < 'T' as a string)
    reg.append_catalogue_commit_proof("db1", proof="elements_read_back", node_kind="schema", schema_name="s1",
                                      element_guid="n", detail={"tables": ["t"]}, read_at="2026-10-08 20:00:00")
    assert cc.derive_commit_state(reg, "db1", _view(reg))["schemas"]["s1"]["state"] == "catalogued"
    # and a space-separated one from before the reset does not
    with reg._conn() as c:
        c.execute("DELETE FROM catalogue_commit_proofs WHERE element_guid = 'n'")
    reg.append_catalogue_commit_proof("db1", proof="elements_read_back", node_kind="schema", schema_name="s1",
                                      element_guid="o", detail={"tables": ["t"]}, read_at="2026-10-08 10:00:00")
    assert cc.derive_commit_state(reg, "db1", _view(reg))["schemas"]["s1"]["state"] == "reset"


# ── the status derives from the marker ───────────────────────────────────────

def _view(reg, schemas=("s1",)):
    return {"schemas": [{"name": n, "effective": "catalogue", "tables": [{"name": "t", "effective": "catalogue"}]}
                        for n in schemas]}


def test_status_reads_published_earlier_and_counts_zero_in_egeria_after_the_marker(env):
    reg = env["reg"]
    before = cc.derive_commit_state(reg, "db1", _view(reg))
    assert before["schemas"]["s1"]["state"] == "catalogued" and "in Egeria" in before["header"]["text"]
    applied(env)
    after = cc.derive_commit_state(reg, "db1", _view(reg))
    st = after["schemas"]["s1"]
    assert st["state"] == "reset" and st["words"] == "published earlier · Egeria was reset · not in Egeria now"
    assert "Egeria reset 2026-10-08 18:30 UTC · OLD → NEW" in st["second"]
    assert after["tables"]["s1.t"]["words"].endswith("not in Egeria now")
    text = after["header"]["text"]
    assert "0 in Egeria" in text and "published earlier · Egeria was reset 10-08 18:30" in text
    # the proof rows are still there, unedited
    assert len(reg.list_catalogue_commit_proofs("db1")) == 3


def test_a_proof_after_the_reset_time_wins_over_the_marker(env):
    reg = env["reg"]
    applied(env)
    reg.append_catalogue_commit_proof("db1", proof="elements_read_back", node_kind="schema", schema_name="s1",
                                      element_guid="new", detail={"tables": ["t"]}, read_at="2026-10-08T20:00:00")
    after = cc.derive_commit_state(reg, "db1", _view(reg))
    assert after["schemas"]["s1"]["state"] == "catalogued"
    assert "1 in Egeria" in after["header"]["text"]


def test_a_resource_without_a_marker_derives_as_before(env):
    reg = env["reg"]
    h = cc.derive_commit_state(reg, "db1", _view(reg))["header"]["text"]
    assert "in Egeria" in h and "reset" not in h


def test_repo_publish_state_reads_the_marker_and_the_unbound_status(env):
    reg = env["reg"]
    assert repo_publish.publish_state(reg, "repo1")["row"]["word"] == "published"
    applied(env)
    st = repo_publish.publish_state(reg, "repo1")
    assert st["row"]["word"] == "reset" and st["in_egeria"] is False
    assert st["row"]["first"] == "published earlier · Egeria was reset · not in Egeria now"
    # the status is the VALUE `unbound`; the words are display text only
    assert st["project"]["status"] == "unbound"
    assert st["project"]["word"] == "unbound by reset · rebind to recreate"


# ── reset status words (architect's rulings 2026-10-08) ──────────────────────

def _investigation(reg, slug, status, guid=""):
    ins(reg, "investigations", slug=slug, display_name=slug, created_at="2026-09-01T00:00:00", egeria_project_status=status,
        egeria_project_guid=guid, egeria_project_qualified_name=f"Project::Investigation::{slug}" if guid else "")


def test_the_script_writes_the_status_value_unbound_not_the_display_string(env):
    reg = env["reg"]
    _investigation(reg, "inv-linked", "linked", "22222222-2222-2222-2222-222222222222")
    _investigation(reg, "inv-local", "local")
    _investigation(reg, "inv-odd", "deferred", "33333333-3333-3333-3333-333333333333")
    rc, cap, *_ = applied(env)
    assert rc == 0, cap.text
    assert S.UNBOUND_STATUS == "unbound"
    d = dump(reg, ["entity_egeria_project_context", "investigations"])
    ctx = {r["entity_slug"]: r for r in d["entity_egeria_project_context"]}
    assert ctx["repo1"]["status"] == "unbound" and ctx["repo1"]["status"] != "unbound by reset · rebind to recreate"
    inv = {r["slug"]: r for r in d["investigations"]}
    # a linked investigation becomes unbound with the empty GUID, never `linked` with an empty GUID
    assert inv["inv-linked"]["egeria_project_status"] == "unbound" and inv["inv-linked"]["egeria_project_guid"] == ""
    # one that was not linked keeps its own status and loses only the GUID; a local one is not touched
    assert inv["inv-odd"]["egeria_project_status"] == "deferred" and inv["inv-odd"]["egeria_project_guid"] == ""
    assert inv["inv-local"]["egeria_project_status"] == "local"
    assert not [r for r in d["investigations"] if r["egeria_project_status"] == "linked" and not r["egeria_project_guid"]]
    # the investigation reads back as unbound, the shape the screens and the bind step read
    assert reg.get_investigation("inv-linked")["egeria_context"]["status"] == "unbound"


def test_an_unbound_context_is_not_an_answer_but_a_rebind_is(env):
    reg = env["reg"]
    applied(env)
    assert reg.get_project_context("repo", "repo1")["status"] == "unbound"
    assert repo_publish.resolve_project_context(reg, "repo1") is None      # gated, as `unset` is
    assert reg.get_project_context("repo", "repo2")["status"] == "personal"
    assert repo_publish.resolve_project_context(reg, "repo2")["status"] == "personal"
    # a bind (linked + a GUID) answers it again
    reg.set_project_context("repo", "repo1", "linked", egeria_project_guid="g-new",
                            egeria_project_qualified_name="Project::X::x")
    assert repo_publish.resolve_project_context(reg, "repo1")["status"] == "linked"


def test_a_resource_with_only_badge_rows_gets_a_marker_too(env):
    reg = env["reg"]
    with reg._conn() as c:
        c.execute("INSERT INTO projects (slug, display_name, github_url, created_at) "
                  "VALUES ('repo3', 'R3', 'https://x/r3', '2026-09-01')")
    ins(reg, "project_published_analyses", project_slug="repo3", analysis_id="a1",
        published_at="2026-09-01T01:00:00", egeria_report_guid="g")
    applied(env)
    assert reg.get_egeria_reset_at("repo3") == RESET_ISO


def _analysis_id():
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    return get_analyses("repo", include_egeria_live=False)[0]["id"]


def test_the_published_badge_reads_the_marker(env):
    from resource_explorer.workflows.analysis import build_analysis_last_activity
    reg, aid = env["reg"], _analysis_id()
    ins(reg, "project_published_analyses", project_slug="repo1", analysis_id=aid,
        published_at="2026-09-01T01:00:00", egeria_report_guid="g")
    # no marker: the row says Published
    la = build_analysis_last_activity(reg, "repo", "repo1")[aid]
    assert la["last_published_at"] == "2026-09-01T01:00:00"
    assert la["published_state"] == "published" and la["egeria_reset_at"] == ""
    applied(env)
    # published BEFORE the marker: "published earlier · Egeria was reset", not Published
    la = build_analysis_last_activity(reg, "repo", "repo1")[aid]
    assert la["published_state"] == "published_earlier" and la["egeria_reset_at"] == RESET_ISO
    # published AFTER the marker: Published again
    ins(reg, "project_published_analyses", project_slug="repo1", analysis_id=aid,
        published_at="2026-10-08T20:00:00", egeria_report_guid="g2")
    la = build_analysis_last_activity(reg, "repo", "repo1")[aid]
    assert la["published_state"] == "published" and la["last_published_at"] == "2026-10-08T20:00:00"


def test_publish_state_helper_edges():
    assert cc.published_state("", RESET_ISO) == ""
    assert cc.published_state("2026-10-08T18:29:59", RESET_ISO) == "published_earlier"
    assert cc.published_state("2026-10-08T18:30:00", RESET_ISO) == "published"     # at the reset counts as live
    assert cc.published_state("2026-09-01T00:00:00", "") == "published"
    assert cc.reset_since_read("2026-10-01T00:00:00", RESET_ISO) is True
    assert cc.reset_since_read("2026-10-08T19:00:00", RESET_ISO) is False
    assert cc.reset_since_read("2026-10-01T00:00:00", "") is False


def test_survey_results_boards_read_the_marker_even_from_a_persisted_summary(env):
    # the persisted board summary was written before the reset; the marker is read at answer time
    from resource_explorer.workflows import analysis as A
    reg = env["reg"]
    ins(reg, "project_published_annotation_types", project_slug="repo1", annotation_type="ZZ",
        published_at="2026-09-01T01:00:00", egeria_report_guid="g")
    applied(env)
    fields = A._reset_fields(reg, "repo1", "2026-09-01T01:00:00")
    assert fields == {"published_state": "published_earlier", "egeria_reset_at": RESET_ISO}
    assert A._reset_fields(reg, "repo2", "2026-09-01T01:00:00")["published_state"] == "published"

# ── schema guard ─────────────────────────────────────────────────────────────

PG = "postgresql://u:secret@localhost:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer"


def test_target_schema_parses_the_registry_url_and_refuses_a_missing_one():
    assert S.target_schema(PG) == "resource_explorer"
    assert S.target_schema("sqlite:///x.db") == "main"
    with pytest.raises(S.Refused, match="names no schema"):
        S.target_schema("postgresql://u:p@localhost:5442/egeria_advisor")


def test_a_postgres_url_without_a_search_path_is_refused_in_dry_run_and_apply(monkeypatch):
    monkeypatch.setenv("REGISTRY_DATABASE_URL", "postgresql://u:secret@localhost:1/egeria_advisor")
    for extra in ([], ["--apply", "--database", "egeria_advisor", "--schema", "public", "--cleared-by", TOKEN,
                       "--plan-file", "x", "--plan-hash", "y"]):
        cap = Cap()
        assert S.run(["--reset-at", RESET, *extra], out=cap, now=NOW) == 2
        assert "names no schema" in cap.text and "secret" not in cap.text


def test_dry_run_header_prints_the_schema_only(env):
    _, cap, _, f = dry(env)
    assert "schema: main" in cap.text
    assert json.loads(Path(f).read_text())["plan"]["schema"] == "main"


def test_the_plan_hash_covers_the_schema(env):
    reg = env["reg"]
    with reg._conn() as c:
        a = S.build_plan(c, "d", RESET_ISO, "", "", "resource_explorer")
        b = S.build_plan(c, "d", RESET_ISO, "", "", "public")
    assert a["hash"] != b["hash"]


def test_sqlite_apply_accepts_no_schema_flag_or_main_but_not_another(env):
    _, _, h, f = dry(env)
    before = dump(env["reg"])
    rc, cap = apply(env, h, f, **{"--schema": "resource_explorer"})
    assert rc == 2 and "--schema" in cap.text and dump(env["reg"]) == before
    rc, cap = apply(env, h, f, **{"--schema": "main"})
    assert rc == 0, cap.text


def test_sqlite_apply_without_the_schema_flag_is_accepted(env):
    _, _, h, f = dry(env)
    rc, cap = apply(env, h, f)
    assert rc == 0, cap.text


def test_a_plan_file_made_for_another_schema_is_refused(env):
    _, _, h, f = dry(env)
    data = json.loads(Path(f).read_text())
    data["plan"]["schema"] = "public"
    data["plan"]["hash"] = S.plan_hash(data["plan"])
    Path(f).write_text(json.dumps(data))
    rc, cap = apply(env, data["plan"]["hash"], f)
    assert rc == 2 and "schema" in cap.text


# ── live-schema guard, URL parsing, hint, error handling (fakes; SQLite cannot show current_schema) ─────────────

def pg(options):
    return f"postgresql://u:secret@localhost:5442/egeria_advisor?options={options}"


class FakeConn:
    def __init__(self, live):
        self.live = live

    def execute(self, sql, params=None):
        assert "current_schema" in sql
        return self

    def close(self):
        pass

    def fetchone(self):
        return {"s": self.live}


def test_current_schema_mismatch_is_refused_and_agreement_passes():
    url = pg("-csearch_path%3Dresource_explorer")
    with pytest.raises(S.Refused, match="current_schema"):
        S.check_live_schema(FakeConn("public"), url, "resource_explorer")
    assert S.check_live_schema(FakeConn("resource_explorer"), url, "resource_explorer") == "resource_explorer"
    # the REAL schema name is not refused as such: only a mismatch is
    assert S.target_schema(url) == "resource_explorer"


@pytest.mark.parametrize("options", ["-csearch_path%3Da,b", "-csearch_path%3Da%20-csearch_path%3Db",
                                     "-csearch_path%3Da%20-c%20search_path%3Db", "-csearch_path%3D"])
def test_comma_duplicate_and_empty_search_paths_are_refused(options):
    with pytest.raises(S.Refused):
        S.target_schema(pg(options))


def test_a_comma_list_is_refused_by_its_own_guard_not_only_the_parser_cross_check():
    with pytest.raises(S.Refused, match="exactly one schema"):
        S.target_schema(pg("-csearch_path%3Da,b"))


def test_spellings_the_registry_parser_does_not_know_are_refused_not_guessed():
    for options in ("-c%20search_path%3Dx", "--search_path%3Dx"):
        with pytest.raises(S.Refused, match="parser reads"):
            S.target_schema(pg(options))


def test_schema_is_required_on_apply_for_any_postgres_url_even_main(monkeypatch):
    monkeypatch.setenv("REGISTRY_DATABASE_URL", pg("-csearch_path%3Dmain"))
    cap = Cap()
    rc = S.run(["--reset-at", RESET, "--apply", "--database", "egeria_advisor", "--cleared-by", TOKEN,
                "--plan-file", "x", "--plan-hash", "y"], out=cap, now=NOW)
    assert rc == 2 and "--schema" in cap.text and "secret" not in cap.text


def test_run_refuses_when_the_live_schema_differs(monkeypatch, env):
    monkeypatch.setenv("REGISTRY_DATABASE_URL", pg("-csearch_path%3Dresource_explorer"))
    monkeypatch.setattr(S, "connect", lambda url: FakeConn("public"))
    cap = Cap()
    assert S.run(["--reset-at", RESET], out=cap, now=NOW) == 2
    assert "current_schema() is 'public'" in cap.text and "secret" not in cap.text


def test_plan_records_and_hashes_the_live_schema_and_apply_refuses_a_different_one(env):
    reg = env["reg"]
    with reg._conn() as c:
        a = S.build_plan(c, "d", RESET_ISO, "", "", "main", [], "main")
        b = S.build_plan(c, "d", RESET_ISO, "", "", "main", [], "other")
    assert a["current_schema"] == "main" and a["hash"] != b["hash"]
    _, cap, h, f = dry(env)
    assert "current_schema: main" in cap.text
    data = json.loads(Path(f).read_text())
    data["plan"]["current_schema"] = "other"
    data["plan"]["hash"] = S.plan_hash(data["plan"])
    Path(f).write_text(json.dumps(data))
    rc, cap = apply(env, data["plan"]["hash"], f)
    assert rc == 2 and "current_schema" in cap.text
    _, _, h, f = dry(env)
    apply(env, h, f)
    snap = next(Path(f).parent.glob("egeria-reset-snapshot-*.json"))
    assert json.loads(snap.read_text())["plan"]["current_schema"] == "main"


def test_the_apply_hint_is_a_complete_copy_paste(env):
    with env["reg"]._conn() as c:
        c.execute("DROP TABLE work_list_runs")
        c.execute("DROP TABLE work_list_members")
        c.execute("DROP TABLE work_lists")
    _, cap, h, f = dry(env, "--skip-missing-table", "work_lists", "--old-collection-id", "O", "--new-collection-id", "N")
    hint = next(l for l in cap.text.splitlines() if l.startswith("to apply:"))
    for part in ("--schema main", "--skip-missing-table work_lists", "--old-collection-id O",
                 "--new-collection-id N", f"--reset-at {RESET}", f"--plan-hash {h}", f"--plan-file {f}"):
        assert part in hint


def test_missing_table_means_undefined_table_only():
    class Boom:
        class raw_conn:
            @staticmethod
            def rollback():
                pass

        def __init__(self, exc):
            self.exc = exc

        def execute(self, sql):
            raise self.exc

    class PgErr(Exception):
        pgcode = "42P01"

    assert S._missing_table(Boom(PgErr("relation does not exist")), "t") is True
    import sqlite3
    assert S._missing_table(Boom(sqlite3.OperationalError("no such table: t")), "t") is True
    with pytest.raises(S.Refused, match="could not check"):
        S._missing_table(Boom(RuntimeError("permission denied")), "t")


def test_a_failed_final_activity_update_is_not_reported_as_a_failure_before_any_change(env):
    _, _, h, f = dry(env)
    with env["reg"]._conn() as c:
        c.execute("CREATE TRIGGER nofinal BEFORE UPDATE ON activity_log WHEN NEW.status = 'ok' "
                  "BEGIN SELECT RAISE(ABORT, 'x'); END")
    rc, cap = apply(env, h, f)
    assert rc == 3 and "All groups committed" in cap.text and "before any change" not in cap.text
    d = dump(env["reg"])
    assert all(p["database_slug"] for p in d["catalogue_commit_proofs"]) and \
        any(p["proof"] == "egeria_reset" for p in d["catalogue_commit_proofs"])


def test_a_preexisting_world_readable_tmp_snapshot_ends_0600(env):
    _, _, h, f = dry(env)
    tmp = Path(f).parent / f"egeria-reset-snapshot-{env['name']}-{h[:12]}.tmp"
    tmp.write_text("old")
    os.chmod(tmp, 0o644)
    apply(env, h, f)
    snap = Path(f).parent / f"egeria-reset-snapshot-{env['name']}-{h[:12]}.json"
    assert oct(snap.stat().st_mode & 0o777) == "0o600"
