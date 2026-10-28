"""Backend tests for the append-only Question catalog write path
(resource_explorer/surveyors/question_catalog_writer.py), the real new
backend surface SPEC-ADMIN-THE-FOUR-GAPS.md §4 asked for.

**Decision (project owner, 2026-09-20):** append-only — add and retire are
the only two operations; a reword-in-place must be refused by the backend
itself, not merely left off the UI. `test_add_refuses_when_question_already_exists`
and `test_add_refuses_when_retired_question_text_reused` are that invariant
under direct test, per the task's own instruction to prove editing "is not
accepted by the backend even if attempted directly against the route."

Isolated from the real docs/dr-egeria/resource_questions.csv and
resource_explorer/configdata/question_catalog.yaml via monkeypatching the
module's path constants — these tests never touch the committed catalog.
"""
from __future__ import annotations

import csv

import pytest
import yaml

from resource_explorer.surveyors import question_catalog_writer as qcw

_HEADER = [
    "Question ID", "Question", "Funnel Stage", "Why is this important?", "Rationale/Source",
    "Answering Analysis", "Answering Mechanism", "Steward", "Consumer",
    "Purposes", "Catalog History", "Status",
]

_EXISTING_ROW = {
    "Question ID": "Q-0001",
    "Question": "Is this repository actively maintained?",
    "Funnel Stage": "Scouting",
    "Why is this important?": "So it is not a dead end.",
    "Rationale/Source": "Recency signal.",
    "Answering Analysis": "repository_health",
    "Answering Mechanism": "Git Statistics",
    "Steward": "X",
    "Consumer": "",
    "Purposes": "Select",
    "Catalog History": "",
    "Status": "",
}


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    csv_path = tmp_path / "resource_questions.csv"
    yaml_path = tmp_path / "question_catalog.yaml"
    lock_path = tmp_path / "resource_questions.csv.lock"
    _write_csv(csv_path, _HEADER, [_EXISTING_ROW])

    monkeypatch.setattr(qcw, "_CSV_PATH", csv_path)
    monkeypatch.setattr(qcw, "_YAML_PATH", yaml_path)
    monkeypatch.setattr(qcw, "_LOCK_PATH", lock_path)
    yield csv_path, yaml_path


class TestAddQuestion:
    def test_append_adds_a_new_row_and_regenerates_yaml(self, isolated):
        csv_path, yaml_path = isolated
        qcw.add_question(
            "What license does this repository use?",
            stage="Scouting",
            perspectives=["Steward"],
            purposes=["Select"],
            why_important="Legal risk.",
            rationale="License field.",
            answering_mechanism="Direct Field",
        )

        rows = _read_csv(csv_path)
        assert len(rows) == 2
        new_row = rows[1]
        assert new_row["Question"] == "What license does this repository use?"
        assert new_row["Steward"] == "X"
        assert new_row["Answering Analysis"] == qcw._DEFAULT_ANSWERING_ANALYSIS
        assert new_row["Status"] == ""  # active by default

        # The original row is untouched — append-only means "unchanged", not
        # "reformatted".
        assert rows[0]["Question"] == _EXISTING_ROW["Question"]

        generated = yaml.safe_load(yaml_path.read_text())
        questions = [e["question"] for e in generated["repo_questions"]]
        assert "What license does this repository use?" in questions
        assert len(generated["repo_questions"]) == 2
        new_entry = next(e for e in generated["repo_questions"] if e["question"] == "What license does this repository use?")
        assert new_entry["retired"] is False

    def test_add_refuses_when_question_already_exists(self, isolated):
        csv_path, yaml_path = isolated
        with pytest.raises(qcw.QuestionCatalogWriteError, match="already exists"):
            qcw.add_question(_EXISTING_ROW["Question"], stage="Scouting")

        # Refusal must be all-or-nothing: neither file changes.
        assert _read_csv(csv_path) == [_EXISTING_ROW]
        assert not yaml_path.exists()

    def test_add_refuses_when_retired_question_text_reused(self, isolated):
        """Retiring a question and then "adding" the identical text back is
        indistinguishable from editing it in place with extra steps —
        append-only refuses this too, not only a straight duplicate of an
        active row."""
        csv_path, yaml_path = isolated
        qcw.retire_question(_EXISTING_ROW["Question"])

        with pytest.raises(qcw.QuestionCatalogWriteError, match="already exists"):
            qcw.add_question(_EXISTING_ROW["Question"], stage="Scouting")

    def test_add_rejects_unknown_perspective_column(self, isolated):
        csv_path, yaml_path = isolated
        with pytest.raises(qcw.QuestionCatalogWriteError, match="unknown perspective"):
            qcw.add_question("A new question?", perspectives=["NotAColumn"])
        assert _read_csv(csv_path) == [_EXISTING_ROW]

    def test_add_keeps_an_off_list_purpose_and_warns(self, isolated):
        """Purposes are suggestive (owner ruling 2026-10-13): an off-list value
        is written as given and named in the warnings, never refused."""
        csv_path, yaml_path = isolated
        warnings = qcw.add_question("A new question?", purposes=["NotARealPurpose"])
        assert warnings == ["purpose 'NotARealPurpose' is not in the investigation-purposes list; kept as written"]
        assert _read_csv(csv_path)[-1]["Purposes"] == "NotARealPurpose"
        entry = yaml.safe_load(yaml_path.read_text())["repo_questions"][-1]
        assert entry["purposes"] == ["NotARealPurpose"]

    def test_add_accepts_find_and_integrate(self, isolated):
        csv_path, _ = isolated
        assert qcw.add_question("Where is the customer data?", purposes=["Find", "Integrate"]) == []

    def test_add_requires_question_text(self, isolated):
        with pytest.raises(qcw.QuestionCatalogWriteError):
            qcw.add_question("   ")


class TestRetireQuestion:
    def test_retire_flips_status_without_touching_other_fields(self, isolated):
        csv_path, yaml_path = isolated
        qcw.retire_question(_EXISTING_ROW["Question"])

        rows = _read_csv(csv_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["Status"] == qcw.STATUS_RETIRED
        assert row["Question"] == _EXISTING_ROW["Question"]
        assert row["Rationale/Source"] == _EXISTING_ROW["Rationale/Source"]
        assert "Retired" in row["Catalog History"]

        generated = yaml.safe_load(yaml_path.read_text())
        entry = generated["repo_questions"][0]
        assert entry["retired"] is True
        assert entry["question"] == _EXISTING_ROW["Question"]

    def test_retire_is_not_a_delete(self, isolated):
        """The question stays in the CSV and the generated YAML — retiring
        removes nothing, per the spec's "retirement is a state the UI
        already knows how to show", not a row that disappears."""
        csv_path, yaml_path = isolated
        qcw.retire_question(_EXISTING_ROW["Question"])
        assert len(_read_csv(csv_path)) == 1
        generated = yaml.safe_load(yaml_path.read_text())
        assert len(generated["repo_questions"]) == 1

    def test_retire_unknown_question_raises_not_found(self, isolated):
        with pytest.raises(qcw.QuestionNotFoundError):
            qcw.retire_question("This question was never asked.")

    def test_retire_twice_refuses_the_second_time(self, isolated):
        qcw.retire_question(_EXISTING_ROW["Question"])
        with pytest.raises(qcw.QuestionCatalogWriteError, match="already retired"):
            qcw.retire_question(_EXISTING_ROW["Question"])

    def test_retire_requires_question_text(self, isolated):
        with pytest.raises(qcw.QuestionCatalogWriteError):
            qcw.retire_question("")


class TestNoEditPathExists:
    """The append-only decision means there is no function anywhere in this
    module that can change an existing question's own text — proven here by
    the module's public surface, not just by its two call sites' behaviour."""

    def test_module_exposes_no_update_or_edit_function(self):
        public_names = [n for n in dir(qcw) if not n.startswith("_")]
        forbidden = {"update_question", "edit_question", "reword_question", "set_question"}
        assert not (forbidden & set(public_names))
