"""Every catalog question carries a stable id (`Q-0001`) that outlives edits to its wording.

Why (owner's extensibility rulings, 2026-10-07): a user step's manifest names the questions it answers by
id. Before this, a question was keyed by its display text, so rewording a question orphaned anything that
named it. These tests pin the three halves: the id is stored (CSV) and carried (generator, YAML, reader);
a missing or duplicate one is refused; and the places RE stores a question key still resolve after a
reword.
"""
from __future__ import annotations

import copy
import csv
import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from resource_explorer.surveyors import question_catalog_reader as qcr
from resource_explorer.surveyors import question_catalog_writer as qcw

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
ID_RE = re.compile(r"^Q-\d{4,}$")


@pytest.fixture(scope="module")
def generator():
    return qcw._load_generator_module()


@pytest.fixture(scope="module")
def csv_rows():
    with open(CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(autouse=True)
def _clear_reader_cache():
    qcr.clear_cache()
    yield
    qcr.clear_cache()


# --- the catalog itself ---------------------------------------------------------------------------------

class TestTheCatalogCarriesIds:
    def test_every_catalog_entry_has_a_well_formed_id(self):
        missing = [(rt, e.question) for rt, es in qcr._load().items() for e in es if not ID_RE.match(e.id)]
        assert missing == []

    def test_one_id_is_one_question_across_every_type(self):
        """A shared (`*` / several-type) question carries ONE id in every section it appears in, and no two
        different questions share an id."""
        by_id: dict[str, set[str]] = {}
        by_text: dict[str, set[str]] = {}
        for entries in qcr._load().values():
            for e in entries:
                by_id.setdefault(e.id, set()).add(e.question)
                by_text.setdefault(e.question, set()).add(e.id)
        assert {i: t for i, t in by_id.items() if len(t) > 1} == {}
        assert {t: i for t, i in by_text.items() if len(i) > 1} == {}

    def test_the_shipped_yaml_is_the_csv_ids(self, csv_rows):
        assert qcr.all_question_ids() == {r["Question ID"] for r in csv_rows}

    def test_get_questions_exposes_the_id(self):
        entry = qcr.get_questions("repo")[0]
        assert ID_RE.match(entry["id"])

    def test_lookup_by_id_and_by_text_both_resolve(self):
        entry = qcr.get_questions("repo")[0]
        assert qcr.find_question(entry["id"])["question"] == entry["question"]
        assert qcr.find_question(entry["question"])["id"] == entry["id"]
        assert qcr.find_question_by_id(entry["id"], "repo")["question"] == entry["question"]
        assert qcr.find_question("no such question") is None
        assert qcr.find_question_by_id("Q-9999") is None

    def test_a_text_that_looks_like_an_id_is_not_an_id(self):
        assert qcr.find_question_by_id("Is this repository actively maintained?") is None


# --- the generator --------------------------------------------------------------------------------------

class TestTheGeneratorRefusesBadIds:
    def test_a_missing_id_is_refused_naming_the_question(self, generator, csv_rows):
        rows = copy.deepcopy(csv_rows)
        rows[3]["Question ID"] = ""
        with pytest.raises(ValueError, match="missing Question ID") as exc:
            generator.generate(rows)
        assert rows[3]["Question"] in str(exc.value)

    def test_a_duplicate_id_is_refused_naming_both_questions(self, generator, csv_rows):
        rows = copy.deepcopy(csv_rows)
        rows[5]["Question ID"] = rows[4]["Question ID"]
        with pytest.raises(ValueError, match="duplicate Question ID") as exc:
            generator.generate(rows)
        assert rows[4]["Question"] in str(exc.value) and rows[5]["Question"] in str(exc.value)

    def test_a_malformed_id_is_refused(self, generator, csv_rows):
        rows = copy.deepcopy(csv_rows)
        rows[2]["Question ID"] = "what-is-this"
        with pytest.raises(ValueError, match="malformed Question ID"):
            generator.generate(rows)

    def test_every_problem_is_reported_in_one_run(self, generator, csv_rows):
        rows = copy.deepcopy(csv_rows)
        rows[0]["Question ID"] = ""
        rows[1]["Question ID"] = "nope"
        with pytest.raises(ValueError) as exc:
            generator.generate(rows)
        assert "missing" in str(exc.value) and "malformed" in str(exc.value)

    def test_the_id_column_is_never_read_as_a_perspective(self, generator, csv_rows):
        assert "Question ID" in generator.NON_PERSPECTIVE_COLUMNS
        text = generator.generate(csv_rows)
        perspectives = {p for es in yaml.safe_load(text).values() for e in es for p in e["perspectives"]}
        assert "Question ID" not in perspectives

    def test_the_id_is_stored_not_derived_from_the_text(self, generator, csv_rows):
        rows = copy.deepcopy(csv_rows)
        rows[0]["Question"] = rows[0]["Question"] + " (reworded)"
        loaded = yaml.safe_load(generator.generate(rows))
        ids = [e["id"] for es in loaded.values() for e in es if e["question"].endswith("(reworded)")]
        assert ids and set(ids) == {csv_rows[0]["Question ID"]}

    def test_next_id_is_past_the_highest_ever_issued_retired_included(self, generator, csv_rows):
        rows = copy.deepcopy(csv_rows)
        top = max(int(r["Question ID"][2:]) for r in rows)
        assert generator.next_question_id(rows) == f"Q-{top + 1:04d}"
        rows[-1]["Status"] = "Retired"
        assert generator.next_question_id(rows) == f"Q-{top + 1:04d}"      # a retired id is not reused


# --- reword keeps the id --------------------------------------------------------------------------------

def test_rewording_a_question_keeps_its_id_and_lookups_still_resolve(generator, csv_rows, monkeypatch):
    rows = copy.deepcopy(csv_rows)
    old_text = rows[0]["Question"]
    qid = rows[0]["Question ID"]
    rows[0]["Question"] = "Has this repository been maintained recently?"
    catalog = yaml.safe_load(generator.generate(rows))
    loaded = {k[: -len("_questions")]: [qcr._entry_from_yaml(e) for e in v] for k, v in catalog.items()}
    def fake_load(*a, **k):
        return loaded

    fake_load.cache_clear = lambda: None          # the autouse fixture clears the cache around every test
    monkeypatch.setattr(qcr, "_load", fake_load)

    assert qcr.find_question(qid)["question"] == "Has this repository been maintained recently?"
    assert qcr.find_question("Has this repository been maintained recently?")["id"] == qid
    assert qcr.find_question(old_text) is None            # the old wording is gone; the id is what survived
    assert qid in qcr.all_question_ids()


# --- add_question ---------------------------------------------------------------------------------------

_HEADER = [
    "Question ID", "Question", "Funnel Stage", "Why is this important?", "Rationale/Source",
    "Answering Analysis", "Answering Mechanism", "Resource Types", "Steward", "Purposes", "Catalog History",
    "Status",
]


def _seed(path, ids_and_status):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_HEADER)
        w.writeheader()
        for i, (qid, status) in enumerate(ids_and_status):
            w.writerow({"Question ID": qid, "Question": f"Seed question {i}?", "Funnel Stage": "Scouting",
                        "Answering Analysis": "repository_health", "Answering Mechanism": "Git Statistics",
                        "Resource Types": "repo", "Steward": "X", "Status": status})


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    csv_path = tmp_path / "resource_questions.csv"
    monkeypatch.setattr(qcw, "_CSV_PATH", csv_path)
    monkeypatch.setattr(qcw, "_YAML_PATH", tmp_path / "question_catalog.yaml")
    monkeypatch.setattr(qcw, "_LOCK_PATH", tmp_path / "resource_questions.csv.lock")
    return csv_path


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TestAddQuestionAssignsTheNextFreeId:
    def test_a_new_question_gets_a_new_unique_id(self, isolated):
        _seed(isolated, [("Q-0001", ""), ("Q-0002", "")])
        qcw.add_question("A brand new question?", stage="Scouting")
        rows = _read(isolated)
        assert [r["Question ID"] for r in rows] == ["Q-0001", "Q-0002", "Q-0003"]

    def test_the_id_reaches_the_regenerated_yaml(self, isolated):
        _seed(isolated, [("Q-0001", "")])
        qcw.add_question("A brand new question?", stage="Scouting")
        written = yaml.safe_load(qcw._YAML_PATH.read_text(encoding="utf-8"))
        ids = {e["question"]: e["id"] for es in written.values() for e in es}
        assert ids["A brand new question?"] == "Q-0002"

    def test_a_retired_questions_id_is_not_reused(self, isolated):
        _seed(isolated, [("Q-0001", ""), ("Q-0007", "Retired")])
        qcw.add_question("Another new question?", stage="Scouting")
        assert _read(isolated)[-1]["Question ID"] == "Q-0008"

    def test_two_adds_never_collide(self, isolated):
        _seed(isolated, [("Q-0001", "")])
        qcw.add_question("First new?", stage="Scouting")
        qcw.add_question("Second new?", stage="Scouting")
        ids = [r["Question ID"] for r in _read(isolated)]
        assert len(ids) == len(set(ids)) == 3

    def test_a_csv_with_no_id_column_is_refused_not_guessed_at(self, isolated):
        with open(isolated, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Question", "Funnel Stage", "Resource Types"])
            w.writerow(["Old question?", "Scouting", "repo"])
        with pytest.raises(qcw.QuestionCatalogWriteError, match="Question ID"):
            qcw.add_question("New?", stage="Scouting")

    def test_retire_accepts_the_id(self, isolated):
        _seed(isolated, [("Q-0001", ""), ("Q-0002", "")])
        qcw.retire_question("Q-0002")
        rows = _read(isolated)
        assert rows[1]["Status"] == "Retired" and rows[0]["Status"] == ""


# --- stored keys ----------------------------------------------------------------------------------------

class TestStoredQuestionKeysSurviveAReword:
    def test_an_answer_saved_with_an_id_is_found_under_the_new_wording(self, monkeypatch):
        from resource_explorer.web.routes import context as ctx

        old = "Who looks after this?"
        entry = qcr.get_questions("repo")[0]
        stored = {ctx.question_key(old): {"question": old, "question_id": entry["id"], "answer": "the platform team"}}
        out = ctx.realias_answers(stored, "repo")
        assert out[ctx.question_key(entry["question"])]["answer"] == "the platform team"
        assert ctx.question_key(old) in out                  # nothing is moved or lost, only offered twice

    def test_an_answer_saved_before_ids_existed_still_resolves_by_its_text_key(self):
        from resource_explorer.web.routes import context as ctx

        entry = qcr.get_questions("repo")[0]
        key = ctx.question_key(entry["question"])
        stored = {key: {"question": entry["question"], "answer": "old answer"}}      # no question_id
        assert ctx.realias_answers(stored, "repo") == stored

    def test_an_id_the_catalog_no_longer_has_leaves_the_answer_where_it_was(self):
        from resource_explorer.web.routes import context as ctx

        stored = {"some-key": {"question": "x", "question_id": "Q-9999", "answer": "a"}}
        assert ctx.realias_answers(stored, "repo") == stored

    def test_sending_the_aliased_document_back_does_not_persist_a_second_answer(self):
        from resource_explorer.web.routes import context as ctx

        stored = {
            "old-slug": {"question": "old", "question_id": "Q-0001", "answer": "a", "answered_at": "2026-10-01"},
            "new-slug": {"question": "new", "question_id": "Q-0001", "answer": "a", "answered_at": "2026-10-01"},
            "plain": {"question": "p", "answer": "b"},
        }
        collapsed = ctx.collapse_aliased_answers(stored)
        assert sorted(collapsed) == ["new-slug", "plain"]

    def test_save_answer_stamps_the_catalog_id_and_replaces_an_earlier_wording(self, monkeypatch):
        from resource_explorer.web.routes import context as ctx

        entry = qcr.get_questions("repo")[0]
        saved = {}
        existing = {"question_answers": {
            "an-earlier-wording": {"question": "earlier", "question_id": entry["id"], "answer": "first"}}}
        fake = SimpleNamespace(get_context=lambda *a: existing, save_context=lambda et, s, c: saved.update(c))
        monkeypatch.setattr(ctx, "ProjectRegistry", lambda: fake)
        monkeypatch.setattr(ctx, "get_current_user", lambda request: {"user_id": "dan"})
        result = ctx.save_answer("repo", "x", ctx.AnswerWrite(question_id=entry["id"], answer="second"), object())
        key = ctx.question_key(entry["question"])
        assert result["key"] == key
        assert saved["question_answers"][key]["question_id"] == entry["id"]
        assert list(saved["question_answers"]) == [key]            # the earlier-wording entry was replaced

    def test_save_answer_by_text_alone_still_works_and_gains_the_id(self, monkeypatch):
        from resource_explorer.web.routes import context as ctx

        entry = qcr.get_questions("repo")[0]
        saved = {}
        fake = SimpleNamespace(get_context=lambda *a: {}, save_context=lambda et, s, c: saved.update(c))
        monkeypatch.setattr(ctx, "ProjectRegistry", lambda: fake)
        monkeypatch.setattr(ctx, "get_current_user", lambda request: {"user_id": "dan"})
        ctx.save_answer("repo", "x", ctx.AnswerWrite(question=entry["question"], answer="a"), object())
        assert saved["question_answers"][ctx.question_key(entry["question"])]["question_id"] == entry["id"]


class _FakeGapRegistry:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.upserts = []

    def list_gaps(self, slug):
        return self.rows

    def upsert_gap(self, slug, analysis_id, gap_kind, check_name, sentence, evidence=None):
        self.upserts.append((analysis_id, gap_kind, check_name, evidence))


class TestGapsKeyedByQuestion:
    def test_a_new_disagreement_is_keyed_by_the_id_and_records_it(self):
        from resource_explorer.gaps import DISAGREEMENT, record_disagreement

        reg = _FakeGapRegistry()
        gap = record_disagreement(reg, "s", "Is it alive?", "community_support", question_id="Q-0001")
        assert gap["check_name"] == "question:Q-0001"
        assert reg.upserts[0][2] == "question:Q-0001" and reg.upserts[0][3]["question_id"] == "Q-0001"
        assert gap["gap_kind"] == DISAGREEMENT

    def test_a_row_written_under_the_text_is_refreshed_in_place_not_duplicated(self):
        from resource_explorer.gaps import DISAGREEMENT, record_disagreement

        legacy = {"check_name": "question:Is it alive?", "gap_kind": DISAGREEMENT,
                  "analysis_id": "community_support", "evidence": {}}
        reg = _FakeGapRegistry([legacy])
        gap = record_disagreement(reg, "s", "Is it alive?", "community_support", question_id="Q-0001")
        assert gap["check_name"] == "question:Is it alive?"
        assert gap["evidence"]["question_id"] == "Q-0001"          # stamped, so a later reword can still find it

    def test_a_gap_is_matched_by_id_or_by_legacy_text(self):
        from resource_explorer.gaps import gap_is_about_question

        by_id = {"check_name": "question:Q-0001", "evidence": {}}
        legacy = {"check_name": "question:Is it alive?", "evidence": {}}
        stamped = {"check_name": "question:Is it alive?", "evidence": {"question_id": "Q-0001"}}
        assert gap_is_about_question(by_id, "Reworded?", "Q-0001")
        assert gap_is_about_question(legacy, "Is it alive?", "Q-0001")
        assert gap_is_about_question(stamped, "Reworded?", "Q-0001")
        assert not gap_is_about_question(legacy, "Reworded?", "Q-0001")     # only the id could have found it
        assert not gap_is_about_question({"check_name": "elephant_factor"}, "Is it alive?", "Q-0001")


class TestRoutesAcceptTheId:
    def test_answer_route_resolves_by_id_and_by_text(self):
        from fastapi import HTTPException

        from resource_explorer.web.routes import analyses

        entry = qcr.get_questions("repo")[0]
        by_id = analyses.answer_question("some-slug", question="", entity_type="repo", question_id=entry["id"])
        by_text = analyses.answer_question("some-slug", question=entry["question"], entity_type="repo",
                                           question_id="")
        assert by_id["question"] == by_text["question"] == entry["question"]
        assert by_id["question_id"] == entry["id"]
        with pytest.raises(HTTPException) as exc:
            analyses.answer_question("some-slug", question="", entity_type="repo", question_id="Q-9999")
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            analyses.answer_question("some-slug", question="", entity_type="repo", question_id="")
        assert exc.value.status_code == 422


def test_csv_round_trips_with_the_id_column_first():
    header = next(csv.reader(io.StringIO(CSV.read_text(encoding="utf-8"))))
    assert header[0] == "Question ID"
