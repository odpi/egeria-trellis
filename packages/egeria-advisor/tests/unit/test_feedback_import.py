"""Phase 2 A-F1: the one-time import of EA's JSONL feedback (scripts/import_feedback_jsonl.py).

The lines are real-shape: the fields the retired writers produced. They come from:
- `user_feedback.jsonl`: `FeedbackCollector.record_vote`'s FeedbackEntry, before A-F1.
- `feedback_extended.jsonl`: the old `POST /api/feedback`'s extended record, edited later by
  `PATCH /api/feedback/extended/{idx}`.

Both run on SQLite and on a Postgres throwaway schema.
"""
from __future__ import annotations

import json

import pytest

from advisor import feedback_store
from advisor.feedback_import import EXTENDED, USER, run_import


def _ext(ts, query, vote, *, user="peterprofile", status="new", comments="", response="An answer."):
    return {"timestamp": ts, "user_id": user, "query": query, "query_type": "explanation", "vote": vote,
            "rating": {1: "positive", 0: "neutral", -1: "negative"}[vote], "perspective": "Data Steward",
            "intent_override": "explain", "routing_agent": "doc_agent", "response_text": response,
            "triage_status": status, "analysis_comments": comments}


def _user(ts, query, rating, *, user="peterprofile", query_type="explanation"):
    return {"timestamp": ts, "query": query, "query_type": query_type, "collections_searched": [],
            "response_length": 10, "rating": rating, "feedback_text": None, "suggested_collection": None,
            "session_id": None, "user_comment": None, "star_rating": None, "category": None,
            "perspective": "Data Steward", "routing_agent": "doc_agent", "user_id": user,
            "intent_override": "explain"}


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines))


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "egeria-advisor-data"
    _write(d / "feedback" / EXTENDED, [
        _ext("2026-11-01T10:00:00.500000", "what is a glossary?", -1, status="known_issue",
             comments="answer cites the wrong collection"),               # line 1
        _ext("2026-11-01T11:00:00", "how do I add a term?", 1, comments="fine, kept as a note"),  # line 2
        "{this line was cut short by a crash",                             # line 3: corrupt
        _ext("2026-11-01T12:00:00", "old status", 0, status="wontfix"),     # line 4: unknown status
        {"timestamp": "2026-11-01T13:00:00", "vote": 1, "query": ""},      # line 5: no question
    ])
    _write(d / "feedback" / USER, [
        _user("2026-11-01T10:00:00.100000", "what is a glossary?", "negative"),  # line 1: same vote as ext 1
        _user("2026-11-02T09:00:00", "cli question", "positive", user=None, query_type="agent"),  # line 2: CLI
        "",                                                                # blank: ignored
        "not json at all",                                                 # line 4: corrupt
        _user("2026-11-01T11:00:30", "how do I add a term?", "positive"),  # line 5: 30 s off -> its own vote
    ])
    return d


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, monkeypatch):
    feedback_store.reset_stores()
    if request.param == "postgres":
        schema = request.getfixturevalue("ea_test_schema")
        monkeypatch.delenv(feedback_store.URL_ENV, raising=False)
        monkeypatch.setenv(feedback_store.URL_ENV, feedback_store.feedback_database_url())
        monkeypatch.setenv(feedback_store.SCHEMA_ENV, schema)
        request.addfinalizer(feedback_store.reset_stores)
    yield feedback_store.get_store()
    feedback_store.reset_stores()


def _reasons(report):
    return {(s["file"], s["line"]): s["reason"] for s in report.skipped}


def test_import_reads_both_files_and_says_why_for_every_line_left_out(store, data_dir):
    report = run_import(store, data_dir, dry_run=False)
    # ext 1, 2, 4 + user 2, 5
    assert report.imported == 5 and report.events == 1
    reasons = _reasons(report)
    assert reasons[(EXTENDED, 3)].startswith("not JSON")
    assert reasons[(EXTENDED, 5)] == "no question text"
    assert reasons[(USER, 1)] == f"same vote as {EXTENDED} line 1"
    assert reasons[(USER, 4)].startswith("not JSON")
    assert set(reasons) == {(EXTENDED, 3), (EXTENDED, 5), (USER, 1), (USER, 4)}
    assert report.files[EXTENDED]["read"] == 5 and report.files[USER]["read"] == 4   # the blank line is not read
    assert report.notes == [{"file": EXTENDED, "line": 4, "note": "unknown triage status 'wontfix': imported as new"}]
    assert store.count(kind="answer") == 5


def test_imported_items_keep_their_fields_time_and_status(store, data_dir):
    run_import(store, data_dir, dry_run=False)
    rows = {r["query_text"] + "|" + r["extra"]["imported_from"]: r for r in store.list(kind="answer")}
    first = rows[f"what is a glossary?|{EXTENDED}"]
    assert first["created_at"] == "2026-11-01T10:00:00.500000+00:00"
    assert (first["vote"], first["user_id"], first["intent_label"]) == (-1, "peterprofile", "explanation")
    assert first["extra"]["intent_selected"] == "explain" and "query_type" not in first["extra"]
    assert first["response_text"] == "An answer." and first["extra"]["line"] == 1
    assert first["triage"]["status"] == "known_issue"
    (event,) = store.history(first["id"])
    assert (event.actor, event.note) == ("migration", "answer cites the wrong collection")
    kept = rows[f"how do I add a term?|{EXTENDED}"]
    assert kept["triage"]["status"] == "new" and kept["extra"]["analysis_comments"] == "fine, kept as a note"
    cli = rows[f"cli question|{USER}"]
    assert (cli["vote"], cli["user_id"], cli["response_text"]) == (1, "", "")
    assert cli["intent_label"] == "agent"
    assert rows[f"old status|{EXTENDED}"]["extra"]["legacy_triage_status"] == "wontfix"


def test_import_twice_makes_no_duplicates(store, data_dir):
    first = run_import(store, data_dir, dry_run=False)
    second = run_import(store, data_dir, dry_run=False)
    assert second.imported == 0 and second.events == 0
    already = [s for s in second.skipped if s["reason"] == "already imported"]
    assert len(already) == first.imported
    assert store.count(kind="answer") == first.imported


def test_a_line_whose_status_was_edited_later_is_still_the_same_item(store, data_dir):
    run_import(store, data_dir, dry_run=False)
    path = data_dir / "feedback" / EXTENDED
    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec.update(triage_status="actioned", analysis_comments="changed after the import")
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    again = run_import(store, data_dir, dry_run=False)
    assert again.imported == 0 and _reasons(again)[(EXTENDED, 2)] == "already imported"


def test_dry_run_writes_nothing_and_predicts_the_real_run(store, data_dir):
    dry = run_import(store, data_dir, dry_run=True)
    assert store.count(kind="answer") == 0
    real = run_import(store, data_dir, dry_run=False)
    assert (dry.imported, dry.events) == (real.imported, real.events)
    assert run_import(store, data_dir, dry_run=True).imported == 0


def test_the_script_dry_run_and_missing_folder(data_dir, tmp_path, capsys, monkeypatch):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "import_feedback_jsonl.py"
    spec = importlib.util.spec_from_file_location("import_feedback_jsonl", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--data-dir", str(data_dir), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "would import: 5 item(s)" in out
    assert f"{EXTENDED} line 3: not JSON" in out
    assert feedback_store.get_store().count(kind="answer") == 0
    assert mod.main(["--data-dir", str(tmp_path / "empty")]) == 2
    assert mod.main(["--data-dir", str(data_dir)]) == 0
    assert feedback_store.get_store().count(kind="answer") == 5
