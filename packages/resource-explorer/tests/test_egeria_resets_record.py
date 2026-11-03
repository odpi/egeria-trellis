"""The record of Egeria resets (`egeria_resets`) and the pre-reset status rule (the 2026-10-23 Egeria reset plan (architect rulings b and c); the project owner approved the table 2026-10-23).

A proof row older than the latest recorded reset never produces ✓ / "published" / "in Egeria" on any surface the
rule changed; rows after it behave as before; the profile publish's "previous" ignores rows before it; the CLI refuses
a future or out-of-order reset; with no reset recorded every derivation is unchanged. Temp SQLite only; no Egeria.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

import pytest
from typer.testing import CliRunner

from resource_explorer import catalogue_commit as cc
from resource_explorer import repo_publish
from resource_explorer.registry import EGERIA_RESETS_DDL, DatabaseEntity, PostgresCursorWrapper, ProjectRegistry
from resource_explorer.surveyors.database.quality.profile_publish import profile_publish_words

RESET = "2026-10-24T09:00:00Z"
BEFORE = "2026-10-20T10:00:00"
AFTER = "2026-10-24T12:00:00"
NOW = datetime(2026, 10, 25, 0, 0, 0)
WORDS = "published before the 2026-10-24 reset · not in Egeria now"
STEP = "value_profile"


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "resets.db"))
    r.register_database(DatabaseEntity(slug="db1", display_name="D1", db_type="postgresql", host="localhost",
                                       port=5432, database_name="d"))
    with r._conn() as c:
        c.execute("INSERT INTO projects (slug, display_name, github_url, created_at) "
                  "VALUES ('repo1', 'R1', 'https://x/r1', '2026-09-01')")
    return r


def _reset(reg, at=RESET):
    return reg.record_egeria_reset(reset_at=at, old_platform_digest="a8bcff5c", new_platform_digest="newdig",
                                   old_commit="c6dbf952", new_commit="newc", recorded_by="coordinator", now=NOW)


def _catalogued(reg, at, guid="g"):
    reg.append_catalogue_commit_proof("db1", proof=cc.P_DATABASE, node_kind="database", element_guid=f"{guid}-db",
                                      detail={"server_guid": f"{guid}-srv"}, read_at=at)
    reg.append_catalogue_commit_proof("db1", proof=cc.P_TARGET, node_kind="schema", schema_name="s1",
                                      element_guid=f"{guid}-s1", read_at=at)
    reg.append_catalogue_commit_proof("db1", proof=cc.P_ELEMENTS, node_kind="schema", schema_name="s1",
                                      element_guid=f"{guid}-s1", detail={"tables": ["t"]}, read_at=at)


def _view():
    return {"schemas": [{"name": "s1", "effective": "catalogue", "tables": [{"name": "t", "effective": "catalogue"}]}]}


def _chips(state):
    return {c["key"]: c for c in state["read_back"]["chips"]}


# ── the DDL: the one approved table, additive and idempotent ─────────────────────────────────────────────────────

def test_the_ddl_is_one_create_table_if_not_exists_that_survives_the_postgres_translator():
    assert len(EGERIA_RESETS_DDL) == 1
    stmt = EGERIA_RESETS_DDL[0]
    assert stmt.lstrip().upper().startswith("CREATE TABLE IF NOT EXISTS EGERIA_RESETS")
    assert not re.search(r"\b(ALTER|DROP|INSERT|UPDATE|DELETE|INDEX)\b", stmt.upper())
    assert "?" not in stmt and not re.search(r"(?<!:):[A-Za-z_]", stmt)
    out = PostgresCursorWrapper(None)._translate_sql(stmt)
    assert "SERIAL PRIMARY KEY" in out and "AUTOINCREMENT" not in out and "%" not in out


def test_the_table_is_created_and_a_second_run_is_clean(tmp_path):
    path = str(tmp_path / "ddl.db")
    ProjectRegistry(db_path=path)
    ProjectRegistry(db_path=path)
    con = sqlite3.connect(path)
    for stmt in EGERIA_RESETS_DDL:              # and the statement itself, run again by hand
        con.execute(stmt)
    cols = [r[1] for r in con.execute("PRAGMA table_info(egeria_resets)")]
    assert cols == ["id", "reset_at", "old_platform_digest", "new_platform_digest", "old_commit", "new_commit",
                    "recorded_by", "note"]


# ── record / latest ──────────────────────────────────────────────────────────────────────────────────────────────

def test_no_reset_is_none_and_a_recorded_one_is_the_latest(reg):
    assert reg.latest_egeria_reset_at() is None
    _reset(reg)
    assert reg.latest_egeria_reset_at() == "2026-10-24T09:00:00Z"
    _reset(reg, "2026-10-24T21:00+00:00")
    assert reg.latest_egeria_reset_at() == "2026-10-24T21:00:00Z"
    assert reg.get_egeria_reset_at("anything") == "2026-10-24T21:00:00Z"   # applies to every resource


@pytest.mark.parametrize("at", ["2026-10-26T00:00:00Z", "not a time", "2026-10-23T00:00:00Z", RESET])
def test_record_refuses_future_unparseable_older_or_repeated(reg, at):
    _reset(reg)
    with pytest.raises(ValueError):
        _reset(reg, at)
    with reg._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM egeria_resets").fetchone()[0] == 1


def _cli(monkeypatch, tmp_path, *args):
    import resource_explorer.config as config
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    config._config = None
    from resource_explorer.cli.main import app
    return CliRunner().invoke(app, ["registry", "record-egeria-reset", *args])


def test_the_cli_records_once_and_refuses_future_and_out_of_order(monkeypatch, tmp_path):
    ok = _cli(monkeypatch, tmp_path, "--reset-at", "2026-10-01T09:00Z", "--old-digest", "a8bcff5c",
              "--new-digest", "nd", "--old-commit", "c6dbf952", "--new-commit", "nc", "--recorded-by", "coord")
    assert ok.exit_code == 0, ok.output
    future = _cli(monkeypatch, tmp_path, "--reset-at", "2999-01-01T00:00Z")
    assert future.exit_code == 2 and "future" in future.output
    older = _cli(monkeypatch, tmp_path, "--reset-at", "2026-09-01T00:00Z")
    assert older.exit_code == 2 and "not newer" in older.output
    rows = sqlite3.connect(f"{tmp_path}/cli.db").execute(
        "SELECT reset_at, old_platform_digest, new_platform_digest, old_commit, new_commit, recorded_by "
        "FROM egeria_resets").fetchall()
    assert rows == [("2026-10-01T09:00:00Z", "a8bcff5c", "nd", "c6dbf952", "nc", "coord")]


# ── derivation 1: the catalogue commit state (derive_commit_state, header, table rows) ──────────────────────────

def test_no_reset_recorded_the_catalogue_state_is_unchanged(reg):
    _catalogued(reg, BEFORE)
    st = cc.derive_commit_state(reg, "db1", _view(), NOW)
    assert st["schemas"]["s1"]["state"] == "catalogued"
    assert "reset" not in st["header"]["text"]
    assert all(c["cue"] == "read_back" for c in st["read_back"]["chips"])


def test_a_pre_reset_proof_never_reads_catalogued(reg):
    _catalogued(reg, BEFORE)
    _reset(reg)
    st = cc.derive_commit_state(reg, "db1", _view(), NOW)
    s1 = st["schemas"]["s1"]
    assert s1["state"] == "reset" and s1["words"] == WORDS
    assert st["tables"]["s1.t"]["words"] == f"as its schema: {WORDS}"
    assert WORDS in st["header"]["text"] and "0 in Egeria" not in st["header"]["text"]
    assert st["database"] is None
    assert "✓" not in cc.read_back_words(st["read_back"])


def test_a_post_reset_proof_behaves_as_today(reg):
    _catalogued(reg, BEFORE)
    _reset(reg)
    _catalogued(reg, AFTER, guid="n")
    st = cc.derive_commit_state(reg, "db1", _view(), NOW)
    assert st["schemas"]["s1"]["state"] == "catalogued" and st["database"]["guid"] == "n-db"
    assert all(c["cue"] == "read_back" for c in st["read_back"]["chips"])


def test_a_resource_with_nothing_before_the_reset_reads_as_before(reg):
    _reset(reg)
    _catalogued(reg, AFTER, guid="n")
    st = cc.derive_commit_state(reg, "db1", _view(), NOW)
    assert "reset" not in st["header"]["text"] and "in Egeria" not in st["header"]["text"].split("·")[-1]


# ── derivation 2: the read-back chips (read_back_chips / read_back_chips_for) ───────────────────────────────────

def test_the_chips_count_post_reset_proof_only_and_are_pending_not_checked(reg):
    _catalogued(reg, BEFORE)
    _reset(reg)
    chips = _chips(cc.derive_commit_state(reg, "db1", _view(), NOW))
    for key in ("published", "attached", "catalogued"):
        assert chips[key]["count"] == 0 and chips[key]["cue"] == "pending", key
        assert chips[key]["pre_reset_at"] == "2026-10-24T09:00:00Z" and WORDS in chips[key]["hover"]
    words = cc.read_back_words(cc.read_back_chips_for(reg, "db1", ["s1"], NOW))
    assert words == "published by RE 0 ◌ · attached 0 schemas ◌ · cataloged by Egeria 0 schemas ◌"


def test_cataloged_by_egeria_stays_pending_until_the_cataloguer_rebuilds(reg):
    _catalogued(reg, BEFORE)
    _reset(reg)
    reg.append_catalogue_commit_proof("db1", proof=cc.P_DATABASE, node_kind="database", element_guid="n-db",
                                      detail={"server_guid": "n-srv"}, read_at=AFTER)
    reg.append_catalogue_commit_proof("db1", proof=cc.P_TARGET, node_kind="schema", schema_name="s1",
                                      element_guid="n-s1", read_at=AFTER)
    chips = _chips(cc.derive_commit_state(reg, "db1", _view(), NOW))
    assert chips["published"]["cue"] == "read_back" and chips["attached"]["cue"] == "read_back"
    assert chips["catalogued"]["count"] == 0 and chips["catalogued"]["cue"] == "pending"


# ── derivation 3: the repository's publish row (repo_publish.publish_state) ─────────────────────────────────────

def _report(reg, at, guid="r"):
    reg.append_catalogue_commit_proof("repo1", proof=cc.P_REPORT, node_kind=repo_publish.NODE_REPORT,
                                      element_guid=guid, detail={"annotation_count": 3}, read_at=at)


def test_repo_publish_row_before_the_reset_is_not_published(reg):
    _report(reg, BEFORE)
    assert repo_publish.publish_state(reg, "repo1")["row"]["word"] == "published"   # no reset: unchanged
    _reset(reg)
    row = repo_publish.publish_state(reg, "repo1")["row"]
    assert row["word"] == "reset" and row["first"] == WORDS
    _report(reg, AFTER, guid="r2")
    assert repo_publish.publish_state(reg, "repo1")["row"]["word"] == "published"


def test_a_never_published_repo_says_nothing_about_the_reset(reg):
    _reset(reg)
    assert repo_publish.publish_state(reg, "repo1")["row"]["word"] == "none"


# ── derivation 4: the commit table's proof summary (repo_publish.commit_proof_summary) ──────────────────────────

def test_commit_summary_before_the_reset_is_pre_reset_not_read_back(reg):
    _report(reg, BEFORE)
    reg.append_catalogue_commit_proof("repo1", proof=repo_publish.P_SUB_RESOURCE, node_kind=repo_publish.NODE_SUB_RESOURCE,
                                      curation_id="c1", element_guid="sr", detail={"kind": "file", "role": "chosen"},
                                      read_at=BEFORE)
    cur = {"id": "c1", "requested_at": "2026-10-20T09:00:00"}
    plain = repo_publish.commit_proof_summary(reg, "repo1", cur)
    assert plain["report"]["state"] == "published" and plain["sub_resources"]["read_back"] == 1
    _reset(reg)
    out = repo_publish.commit_proof_summary(reg, "repo1", cur)
    assert out["report"]["state"] == "pre_reset" and out["report"]["words"] == WORDS
    assert out["sub_resources"]["read_back"] == 0 and out["sub_resources"]["by_row"]["files"]["pre_reset"] == 1
    assert out["pre_reset_words"] == WORDS


# ── derivation 5: published-as / publish proofs on run cards (registry._attach_publish_proofs) ──────────────────

def _activity(reg, ts):
    detail = json.dumps({"publish_state": "published to Egeria", "egeria_report_guid": "g", "published_as": "peter"})
    with reg._conn() as c:
        c.execute("INSERT INTO activity_log (id, ts, operation, intent, entity_type, entity_slug, status, detail) "
                  "VALUES (?, ?, 'survey', 'discovery', 'database', 'db1', 'ok', ?)", (f"a-{ts}", ts, detail))


def test_a_run_logged_before_the_reset_carries_pre_reset_and_its_read_back_proof_is_not_published(reg):
    _activity(reg, BEFORE)
    assert "pre_reset" not in reg.list_activity(entity_slug="db1")[0]                 # no reset: unchanged
    proof = {"state": "read_back", "word": "published · read back", "at": BEFORE, "words": ""}
    assert reg._pre_reset_publish_proof(proof, "db1") is proof
    _reset(reg)
    _activity(reg, AFTER)
    by_ts = {e["ts"]: e for e in reg.list_activity(entity_slug="db1")}
    assert by_ts[BEFORE]["pre_reset"] == {"at": "2026-10-24T09:00:00Z", "words": WORDS}
    assert "pre_reset" not in by_ts[AFTER]
    pre = reg._pre_reset_publish_proof(proof, "db1")
    assert pre["state"] == "pre_reset" and pre["word"] == WORDS
    assert reg._pre_reset_publish_proof({**proof, "at": AFTER}, "db1")["state"] == "read_back"


# ── derivation 6 + ruling (c): the profile publish ("previous" and the card's counts/words) ─────────────────────

def _profile(reg, guid, at, h="h1"):
    reg.record_profile_publish("database", "db1", report_guid=guid, report_at=at, read_at=at, analysis_step=STEP,
                               rows=[{"annotation_guid": f"{guid}-a", "item_key": "s1.t.c", "content_hash": h,
                                      "outcome": "created", "summary": "c"}])


def test_profile_previous_ignores_rows_before_the_reset(reg):
    _profile(reg, "rep-old", BEFORE)
    assert reg.latest_profile_publish("database", "db1", STEP)["report_guid"] == "rep-old"   # no reset: unchanged
    _reset(reg)
    assert reg.latest_profile_publish("database", "db1", STEP) is None   # the first post-reset run creates all
    _profile(reg, "rep-new", AFTER)
    prev = reg.latest_profile_publish("database", "db1", STEP)
    assert prev["report_guid"] == "rep-new" and set(prev["measures"]) == {"s1.t.c"}


def test_profile_card_of_a_pre_reset_publish_never_says_published(reg):
    _profile(reg, "rep-old", BEFORE)
    plain = reg.profile_publish_counts("rep-old", STEP)
    assert plain["pre_reset_at"] == "" and profile_publish_words(plain).startswith("1 measures published ·")
    _reset(reg)
    counts = reg.profile_publish_counts("rep-old", STEP)
    assert counts["pre_reset_at"] == "2026-10-24T09:00:00Z"
    assert profile_publish_words(counts) == f"1 measures {WORDS}"
    _profile(reg, "rep-new", AFTER)
    assert reg.profile_publish_counts("rep-new", STEP)["pre_reset_at"] == ""


# ── the per-resource reset readers pick the recorded reset up (publish_fields, stored copies, root folder) ─────

def test_publish_fields_and_reset_since_read_use_the_recorded_reset(reg):
    assert cc.publish_fields(reg, "repo1", BEFORE)["published_state"] == "published"
    _reset(reg)
    assert cc.publish_fields(reg, "repo1", BEFORE)["published_state"] == "published_earlier"
    assert cc.publish_fields(reg, "repo1", AFTER)["published_state"] == "published"
    assert cc.reset_since_read(BEFORE, cc.egeria_reset_at(reg, "repo1")) is True


# ── fix round 1 (review NEEDS-FIX, 2026-10-23) ──────────────────────────────────────────────────────────────────

READ_BACK = {"state": "read_back", "word": "published · read back", "at": BEFORE, "words": ""}


def _fake_proofs(reg, monkeypatch, proof):
    real = ProjectRegistry.publish_proofs

    def proofs(publish_run_ids):          # the real method's signature, bound
        real.__get__(reg)                 # noqa: B018 - only to pin that the real one exists
        return {r: dict(proof) for r in publish_run_ids}

    monkeypatch.setattr(reg, "publish_proofs", proofs)


def _survey_run(reg, ts, key, run_id="pub-1"):
    detail = json.dumps({"steps": [{"re_analysis_step": key, "status": "ok"}], "surveyed_at": ts})
    with reg._conn() as c:
        c.execute("INSERT INTO activity_log (id, ts, operation, intent, entity_type, entity_slug, status, detail, "
                  "publish_run_id) VALUES (?, ?, 'survey', 'discovery', 'database', 'db1', 'ok', ?, ?)",
                  (f"s-{ts}", ts, detail, run_id))


def test_item1_a_survey_run_read_back_before_the_reset_never_reads_published_read_back(reg, monkeypatch):
    key, owners = next(iter(reg._step_key_to_analysis_ids("database").items()))
    _fake_proofs(reg, monkeypatch, READ_BACK)
    _survey_run(reg, BEFORE, key)
    plain = reg.get_analysis_last_run("database", "db1")[owners[0]]
    assert plain["last_publish_proof"]["state"] == "read_back"                 # no reset: unchanged
    _reset(reg)
    out = reg.get_analysis_last_run("database", "db1")[owners[0]]
    assert out["last_run_via"] == "survey"
    assert out["last_publish_proof"]["state"] == "pre_reset" and out["last_publish_proof"]["word"] == WORDS
    assert "published · read back" not in json.dumps(out, ensure_ascii=False)


def test_item11_one_reset_read_per_call(reg, monkeypatch):
    key, _ = next(iter(reg._step_key_to_analysis_ids("database").items()))
    _fake_proofs(reg, monkeypatch, READ_BACK)
    _reset(reg)
    _survey_run(reg, BEFORE, key, "p1")
    _survey_run(reg, "2026-10-21T10:00:00", key, "p2")
    n = {"c": 0}
    real = reg.get_egeria_reset_at

    def counted(slug):
        n["c"] += 1
        return real(slug)

    monkeypatch.setattr(reg, "get_egeria_reset_at", counted)
    reg.get_analysis_last_run("database", "db1")
    assert n["c"] == 1


# item 3: the CLI's --reset-at needs Z or an explicit offset; the success line echoes the parsed UTC time

@pytest.mark.parametrize("at", ["2026-10-24", "2026-10-24T09:00", "2026-10-24T09:00:00", "2026-10-24 09:00"])
def test_item3_a_bare_date_or_a_naive_time_is_refused(reg, at):
    with pytest.raises(ValueError, match="Z or an explicit offset"):
        _reset(reg, at)
    assert reg.latest_egeria_reset_at() is None


def test_item3_an_offset_is_stored_as_utc_and_the_cli_echoes_it(reg, monkeypatch, tmp_path):
    _reset(reg, "2026-10-24T05:00-04:00")
    assert reg.latest_egeria_reset_at() == "2026-10-24T09:00:00Z"
    naive = _cli(monkeypatch, tmp_path, "--reset-at", "2026-10-01T09:00")
    assert naive.exit_code == 2 and "explicit offset" in " ".join(naive.output.split())
    ok = _cli(monkeypatch, tmp_path, "--reset-at", "2026-10-01T05:00-04:00")
    assert ok.exit_code == 0, ok.output
    assert "2026-10-01T09:00:00Z UTC" in " ".join(ok.output.split())


# item 4: --supersedes corrects a mistyped reset, either direction; the latest NON-superseded row wins

def test_item4_a_reset_typed_too_late_is_corrected_earlier(reg):
    wrong = _reset(reg, "2026-10-24T21:00:00Z")
    with pytest.raises(ValueError, match="not newer"):
        _reset(reg, RESET)                                   # without --supersedes: refused as out of order
    fixed = reg.record_egeria_reset(reset_at=RESET, supersedes=wrong, recorded_by="coordinator", now=NOW)
    assert fixed != wrong and reg.latest_egeria_reset_at() == "2026-10-24T09:00:00Z"
    with reg._conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id, reset_at, note FROM egeria_resets ORDER BY id").fetchall()]
    assert rows[0]["reset_at"] == "2026-10-24T21:00:00Z"                      # the old row stays on record
    assert rows[1]["note"].startswith(f"supersedes #{wrong}")
    _report(reg, "2026-10-24T12:00:00")                      # after the true reset, before the mistyped one
    assert repo_publish.publish_state(reg, "repo1")["row"]["word"] == "published"


def test_item4_a_reset_typed_too_early_is_corrected_later_and_cannot_be_superseded_twice(reg):
    wrong = _reset(reg, "2026-10-20T00:00:00Z")
    _report(reg, "2026-10-22T00:00:00")                       # really before the reset
    assert repo_publish.publish_state(reg, "repo1")["row"]["word"] == "published"
    reg.record_egeria_reset(reset_at=RESET, supersedes=wrong, note="typo", now=NOW)
    assert reg.latest_egeria_reset_at() == "2026-10-24T09:00:00Z"
    assert repo_publish.publish_state(reg, "repo1")["row"]["first"] == WORDS
    with pytest.raises(ValueError, match="still counts"):
        reg.record_egeria_reset(reset_at="2026-10-24T10:00:00Z", supersedes=wrong, now=NOW)
    with pytest.raises(ValueError, match="only by --supersedes"):
        reg.record_egeria_reset(reset_at="2026-10-24T10:00:00Z", note="supersedes #1", now=NOW)


def test_item4_the_cli_supersedes(monkeypatch, tmp_path):
    assert _cli(monkeypatch, tmp_path, "--reset-at", "2026-10-02T09:00Z").exit_code == 0
    out = _cli(monkeypatch, tmp_path, "--reset-at", "2026-10-01T09:00Z", "--supersedes", "1")
    assert out.exit_code == 0, out.output
    said = " ".join(out.output.split())
    assert "supersedes row 1" in said and "Latest reset now 2026-10-01T09:00:00Z" in said


# item 5: sent and never read back before the reset is not "published before the reset"

def test_item5_a_sent_row_before_the_reset_is_sent_never_confirmed_with_its_own_count(reg):
    reg.append_catalogue_commit_proof("repo1", proof=repo_publish.P_SENT, node_kind=repo_publish.NODE_REPORT,
                                      element_guid="r", read_at=BEFORE)
    for g, proof in (("a", repo_publish.P_SENT), ("b", repo_publish.P_SUB_RESOURCE)):
        reg.append_catalogue_commit_proof("repo1", proof=proof, node_kind=repo_publish.NODE_SUB_RESOURCE,
                                          curation_id="c1", element_guid=g, detail={"kind": "file", "role": "chosen"},
                                          read_at=BEFORE)
    cur = {"id": "c1", "requested_at": "2026-10-20T09:00:00"}
    assert repo_publish.commit_proof_summary(reg, "repo1", cur)["report"]["state"] == "sent"   # no reset: unchanged
    _reset(reg)
    out = repo_publish.commit_proof_summary(reg, "repo1", cur)
    sent_words = "sent before the 2026-10-24 reset · never confirmed"
    assert out["report"]["state"] == "pre_reset_sent" and out["report"]["words"] == sent_words
    files = out["sub_resources"]["by_row"]["files"]
    assert files["pre_reset"] == 1 and files["pre_reset_sent"] == 1 and files["sent"] == 0
    assert out["pre_reset_sent_words"] == sent_words and "published" not in out["report"]["words"]


# item 6: a failed read of the record raises, never a silent "no reset"

def test_item6_a_failed_read_of_the_record_raises(reg, monkeypatch):
    def broken():
        raise RuntimeError("registry down")

    monkeypatch.setattr(reg, "latest_egeria_reset_at", broken)
    with pytest.raises(RuntimeError):
        cc.latest_recorded_reset(reg)


# item 8: the words for a publish older than the reset come from the server

def test_item8_publish_fields_send_the_rulings_words(reg):
    assert cc.publish_fields(reg, "repo1", BEFORE)["published_earlier_words"] == ""
    _reset(reg)
    assert cc.publish_fields(reg, "repo1", BEFORE)["published_earlier_words"] == WORDS
    assert cc.publish_fields(reg, "repo1", AFTER)["published_earlier_words"] == ""


# item 10: the new row's id comes back from the insert itself

def test_item10_record_returns_the_inserted_rows_id(reg):
    a = _reset(reg, "2026-10-20T00:00:00Z")
    b = _reset(reg)
    with reg._conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM egeria_resets ORDER BY id").fetchall()]
    assert [a, b] == ids


# item 2: the file-system publish proofs (folder survey, walk measures, data-file profiles)

def _fs(reg, tmp_path):
    from resource_explorer.registry import FileSystemEntity

    root = tmp_path / "lake"
    root.mkdir()
    (root / "a.csv").write_text("id,name\n1,ann\n2,bob\n")
    reg.register_filesystem(FileSystemEntity(slug="lake", display_name="Lake", local_mount_point=str(root),
                                             canonical_mount_point="/deployments/lake", egeria_asset_guid="fs-guid"))
    return root


def _fs_published(reg, sd, proof_at):
    proof = {"report_guid": "rep-fs", "annotation_count": 4, "annotations_in_egeria": 4, "report_read_back": True,
             "read_back_why": "", "at": proof_at}
    reg.add_filesystem_survey(fs_slug="lake", surveyed_at=sd["surveyed_at"], survey_data={**sd, "published_to_egeria": proof},
                              egeria_report_guid="rep-fs", source="egeria-published")


def test_item2_the_folder_survey_and_walk_measures_published_before_the_reset_are_pre_reset(reg, tmp_path):
    from resource_explorer.folder_survey_view import _re_candidate, extensions_view
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    _fs(reg, tmp_path)
    fs = reg.get_filesystem("lake")
    sd = LocalFileSystemSurveyor(fs, reg).run(profile_contents=False, walk_extensions=True)
    sd["surveyed_at"] = BEFORE
    we.attach_extensions(fs, reg, sd, led_by="re")
    sd = {**sd, "surveyed_by": {"surveyor": "Resource Explorer", "reach": {}}}
    reg.add_filesystem_survey(fs_slug="lake", surveyed_at=BEFORE, survey_data=sd, source="survey-definition")
    _fs_published(reg, sd, BEFORE)
    assert _re_candidate(reg, "lake")["publish"]["published"] is True                  # no reset: unchanged
    assert extensions_view(reg, "lake")["published"] is True
    _reset(reg)
    walk, ext = _re_candidate(reg, "lake"), extensions_view(reg, "lake")
    for v in (walk, ext):
        assert v["published"] is False and v["publish"]["published"] is False
        assert v["publish"]["pre_reset"] == {"at": "2026-10-24T09:00:00Z", "words": WORDS}


def test_item2_the_file_profiles_publish_before_the_reset_is_pre_reset(reg, tmp_path):
    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    _fs(reg, tmp_path)
    fs = reg.get_filesystem("lake")
    sd = LocalFileSystemSurveyor(fs, reg).run(profile_contents=False, walk_extensions=True)
    we.attach_extensions(fs, reg, sd, led_by="re")
    run = fp.run_file_profiles(fs, reg, limit=40_000)["run_id"]
    reg.record_step_run("lake", fp.PUBLISH_STEP_KEY, run, entity_type="filesystem", source="egeria-published",
                        executor="local", metrics={"report_guid": "r", "complete": True, "annotations_in_egeria": 2,
                                                   "annotation_count": 2, "at": BEFORE})
    assert "pre_reset" not in fp.profiles_view(reg, "lake")["run"]["publish"]          # no reset: unchanged
    _reset(reg)
    assert fp.profiles_view(reg, "lake")["run"]["publish"]["pre_reset"]["words"] == WORDS


# ── fix round 2: an unreadable reset read raises; it is never "no reset" and never ✓ ─────────────────────────────

def _break_reset_reads(reg, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("registry down")

    monkeypatch.setattr(reg, "get_egeria_reset_at", broken)


def test_round2_pre_reset_of_and_egeria_reset_at_raise_on_a_failed_read(reg, monkeypatch):
    _reset(reg)
    _break_reset_reads(reg, monkeypatch)
    with pytest.raises(RuntimeError):
        reg._pre_reset_of("db1", BEFORE)
    with pytest.raises(RuntimeError):
        cc.egeria_reset_at(reg, "db1")
    assert cc.egeria_reset_at_or_none(reg, "db1") is None


def test_round2_an_unreadable_reset_never_gives_a_read_back_proof_or_a_published_entry(reg, monkeypatch):
    _activity(reg, BEFORE)
    _break_reset_reads(reg, monkeypatch)
    proof = {"state": "read_back", "word": "published · read back", "at": BEFORE, "words": ""}
    got = reg._pre_reset_publish_proof(proof, "db1")
    assert got["state"] == "unknown" and got["word"] == "publish not established"
    assert "could not be read" in got["words"]
    entry = reg.list_activity(entity_slug="db1")[0]                      # does not raise: a pre_reset ◌ line
    assert "could not be read" in entry["pre_reset"]["words"]


def test_round2_publish_fields_say_could_not_check_never_published(reg, monkeypatch):
    _break_reset_reads(reg, monkeypatch)
    f = cc.publish_fields(reg, "repo1", BEFORE)
    assert f["published_state"] == "published_earlier" and "could not be read" in f["published_earlier_words"]
    assert cc.publish_fields(reg, "repo1", "")["published_state"] == ""      # nothing published: nothing to doubt


def test_round2_the_analysis_and_definition_payloads_survive_an_unreadable_reset(reg, monkeypatch):
    from resource_explorer.workflows import analysis

    _break_reset_reads(reg, monkeypatch)
    assert cc.egeria_reset_at_or_none(reg, "repo1") is None
    assert analysis._reset_fields(reg, "repo1", BEFORE)["published_state"] == "published_earlier"


def test_round2_the_publish_route_says_could_not_check_when_the_reset_read_fails(reg, monkeypatch, tmp_path):
    from fastapi import HTTPException

    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.web.routes import filesystems

    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    _fs(reg, tmp_path)
    fs = reg.get_filesystem("lake")
    sd = LocalFileSystemSurveyor(fs, reg).run(profile_contents=False, walk_extensions=True)
    we.attach_extensions(fs, reg, sd, led_by="re")
    fp.run_file_profiles(fs, reg, limit=40_000)
    _break_reset_reads(reg, monkeypatch)
    with pytest.raises(HTTPException) as exc:
        filesystems._publish_file_profiles_claimed(reg, reg.get_filesystem("lake"), "lake")
    assert exc.value.status_code == 503 and "could not be read" in exc.value.detail


def test_round2_file_profiles_after_a_reset_publish_everything_again(reg, tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    _fs(reg, tmp_path)
    fs = reg.get_filesystem("lake")
    sd = LocalFileSystemSurveyor(fs, reg).run(profile_contents=False, walk_extensions=True)
    we.attach_extensions(fs, reg, sd, led_by="re")
    monkeypatch.setattr(fp, "_now", lambda: "2026-10-20T10:00:00+00:00")
    first = fp.run_file_profiles(fs, reg, limit=40_000)["run_id"]
    reg.record_step_run("lake", fp.PUBLISH_STEP_KEY, first, entity_type="filesystem", source="egeria-published",
                        executor="local", metrics={"report_guid": "r", "complete": True})
    monkeypatch.setattr(fp, "_now", lambda: "2026-10-21T10:00:00+00:00")
    second = fp.run_file_profiles(fs, reg, limit=40_000)["run_id"]
    assert fp.changed_rows(reg, "lake", second) == []                       # no reset: the unchanged run sends nothing
    assert fp.last_published_run(reg, "lake", before=second) == first
    _reset(reg)                                                             # Egeria was reset after that publish
    monkeypatch.setattr(fp, "_now", lambda: "2026-10-24T12:00:00+00:00")
    third = fp.run_file_profiles(fs, reg, limit=40_000)["run_id"]
    assert fp.last_published_run(reg, "lake", before=third) is None         # the old publish is no baseline
    assert fp.last_published_run(reg, "lake", before=third, since_reset=False) == first
    rows = fp.changed_rows(reg, "lake", third)
    assert rows and {r["column_name"] for r in rows} == {"id", "name"}      # every row publishes to the new Egeria
    reg.record_step_run("lake", fp.PUBLISH_STEP_KEY, third, entity_type="filesystem", source="egeria-published",
                        executor="local", metrics={"report_guid": "r2", "complete": True})
    monkeypatch.setattr(fp, "_now", lambda: "2026-10-24T13:00:00+00:00")
    fourth = fp.run_file_profiles(fs, reg, limit=40_000)["run_id"]
    assert fp.changed_rows(reg, "lake", fourth) == []                       # a publish after the reset is a baseline


def test_round2_file_profiles_baseline_raises_when_the_reset_read_fails(reg, tmp_path, monkeypatch):
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    _break_reset_reads(reg, monkeypatch)
    with pytest.raises(RuntimeError):
        fp.last_published_run(reg, "lake")
    assert fp.last_published_run(reg, "lake", since_reset=False) is None   # retention does not need the reset
