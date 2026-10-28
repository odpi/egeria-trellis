"""Append-only write path for the Question catalog CSV
(docs/dr-egeria/resource_questions.csv) — the ONLY new backend surface
SPEC-ADMIN-THE-FOUR-GAPS.md §4 asked for. Backs Admin > Question Catalog's
"add question" / "retire" actions.

**Decision (project owner, 2026-09-20):** the catalog is append-only. You
may ADD a new question and RETIRE an existing one. You may never reword an
existing question's text — editing a question changes the meaning of
answers already recorded against it (a survey result that answered "does it
have a published spec?" did not answer a reworded question), and this
module is deliberately built so an edit is impossible to express, not just
undiscoverable in the UI: there is no `update_question()` function here, and
`add_question()` refuses (rather than upserts) when the question text
already exists in the CSV, retired or not.

**Why the CSV, not a new table.** docs/dr-egeria/resource_questions.csv is
the actual source of truth for two independently generated outputs — this
package's own configdata/question_catalog.yaml (via
scripts/csv_to_question_catalog_yaml.py) AND the Dr.Egeria markdown batch
that creates real Egeria Question elements (scripts/csv_to_dr_egeria_questions.py)
— and both generators are guarded by byte-identical regeneration tests
(tests/test_question_catalog_generator_guard.py). Standing up a database
table as a second, competing source of truth would either fork the CSV's
authority or require this module to also own regenerating the Dr.Egeria
markdown and re-running it against a live platform, which is out of scope
for an Admin CRUD pane. Writing the CSV and regenerating the YAML that
already ships from it keeps exactly one source of truth and reuses the
existing generator rather than re-deriving its parsing rules.

**Concurrency.** The CSV has no row-level locking of its own, and this is a
real gap the spec asked to be honest about rather than paper over: a
FileLock (already a transitive dependency — see observability/logging_setup.py)
serializes read-modify-write across processes/threads, and both the CSV and
the regenerated YAML are written via a temp-file-then-`os.replace()` swap so
a reader (question_catalog_reader.py, or a `cat`) never observes a
half-written file. This is the minimal viable fix for a low-traffic admin
form, not a migration to a database-backed catalog — a CSV genuinely cannot
give the transactional guarantees a shared Postgres table would under real
concurrent write load, and if this pane ever sees that kind of traffic, that
migration is the right next step, not a bigger lock.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import sys
from datetime import date
from pathlib import Path

from filelock import FileLock

from resource_explorer.surveyors import question_catalog_reader

_ROOT = Path(__file__).resolve().parent.parent.parent  # packages/resource-explorer/
_CSV_PATH = _ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
_YAML_PATH = _ROOT / "resource_explorer" / "configdata" / "question_catalog.yaml"
_GENERATOR_SCRIPT = _ROOT / "scripts" / "csv_to_question_catalog_yaml.py"
_LOCK_PATH = _CSV_PATH.with_suffix(".csv.lock")

#: The CSV's own "not a reword" marker — never a plain UPDATE. Any status
#: value that is not this string is treated as active, so pre-existing rows
#: (which have no Status column populated at all) keep working unchanged.
STATUS_RETIRED = "Retired"

# Columns generate()/_parse_answering() do NOT read for validation and this
# module does not attempt to author programmatically — a new question starts
# as a declared gap (RE-internal convention already used throughout the CSV:
# "GAP: not yet answered") rather than guessing an analysis_ids binding that
# nobody has verified answers it.
_DEFAULT_ANSWERING_ANALYSIS = "GAP: not yet answered"


class QuestionCatalogWriteError(ValueError):
    """A write was refused — duplicate question, unknown Purpose/Perspective,
    or any other input the generator's own validation rejects. 400, not 500:
    caller error, not a server fault."""


class QuestionNotFoundError(LookupError):
    """retire_question() was asked to retire a question that is not in the
    CSV at all (as opposed to one that is retired already, which is a
    QuestionCatalogWriteError — that one exists, this one never did)."""


def _load_generator_module():
    """Import scripts/csv_to_question_catalog_yaml.py by path.

    `scripts/` is not a package (no `__init__.py`, not on `sys.path` by
    default — mirrors how tests/test_question_catalog_generator_guard.py
    reaches it too, there via `subprocess` instead). Importing in-process
    here avoids spawning a Python interpreter on every write while reusing
    the exact same `generate()` that produces the committed YAML, so a
    write can never emit a shape the generator's own guard test would
    reject."""
    spec = importlib.util.spec_from_file_location(
        "csv_to_question_catalog_yaml", _GENERATOR_SCRIPT,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _read_rows() -> tuple[list[str], list[dict]]:
    if not _CSV_PATH.exists():
        raise FileNotFoundError(f"question catalog CSV not found at {_CSV_PATH}")
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)  # os.replace under the hood — atomic on POSIX and Windows


def _write_csv(fieldnames: list[str], rows: list[dict]) -> None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    _atomic_write_text(_CSV_PATH, buf.getvalue())


def _regenerate_yaml(rows: list[dict]) -> None:
    """Run the real generator against `rows` and swap the YAML in atomically.

    Called with the CANDIDATE row set before anything is written to disk —
    `generate()` raises ValueError on the same problems it always has
    (an unknown check ref; an unknown Purpose only warns since 2026-10-13), which this module surfaces as
    QuestionCatalogWriteError so a bad add/retire fails before either file
    is touched, not after the CSV already changed."""
    generator = _load_generator_module()
    try:
        content = generator.generate(rows)
    except ValueError as exc:
        raise QuestionCatalogWriteError(str(exc)) from exc
    _atomic_write_text(_YAML_PATH, content)


def _is_retired(row: dict) -> bool:
    return (row.get("Status") or "").strip().lower() == STATUS_RETIRED.lower()


def add_question(question: str, **fields) -> list[str]:
    """`add_question_with_id` for a caller that wants only the warnings."""
    return add_question_with_id(question, **fields)[0]


def add_question_with_id(
    question: str,
    *,
    stage: str = "",
    perspectives: list[str] | None = None,
    purposes: list[str] | None = None,
    why_important: str = "",
    rationale: str = "",
    answering_mechanism: str = "",
) -> tuple[list[str], str]:
    """Append a new question, assigning it the next free stable id. Returns `(warnings, question_id)`.
    The warnings: each purpose outside the investigation-purposes list,
    kept as given (owner ruling 2026-10-13, suggestive everywhere — no longer a refusal). Refuses (does not upsert) if `question`
    already appears in the CSV — active or retired — because accepting a
    second call with the same text and different other fields IS an edit
    wearing an add's clothes, and append-only means that path does not
    exist here, not merely that the UI declines to offer it."""
    question = (question or "").strip()
    if not question:
        raise QuestionCatalogWriteError("question text is required")
    perspectives = perspectives or []
    purposes = purposes or []

    with FileLock(str(_LOCK_PATH), timeout=10):
        fieldnames, rows = _read_rows()
        if "Status" not in fieldnames:
            fieldnames = fieldnames + ["Status"]
        if "Question ID" not in fieldnames:
            raise QuestionCatalogWriteError(
                "the catalog CSV has no `Question ID` column; assign every existing question an id first"
            )
        if any((r.get("Question") or "").strip() == question for r in rows):
            raise QuestionCatalogWriteError(
                f"question {question!r} already exists in the catalog — "
                "append-only means it can be retired and a new question "
                "added, never reworded in place."
            )

        perspective_cols = [
            c for c in fieldnames
            if c not in {"Question ID", "Question", "Funnel Stage", "Why is this important?",
                         "Rationale/Source", "Answering Analysis",
                         "Answering Mechanism", "Purposes", "Catalog History",
                         "Status"}
        ]
        unknown_perspectives = [p for p in perspectives if p not in perspective_cols]
        if unknown_perspectives:
            raise QuestionCatalogWriteError(
                f"unknown perspective column(s) {unknown_perspectives}; valid "
                f"columns are {perspective_cols}"
            )

        new_row = {c: "" for c in fieldnames}
        # The next free id, one past the highest ever issued (retired rows count), so an id is never reused.
        question_id = _load_generator_module().next_question_id(rows)
        new_row["Question ID"] = question_id
        new_row["Question"] = question
        new_row["Funnel Stage"] = stage
        new_row["Why is this important?"] = why_important
        new_row["Rationale/Source"] = rationale
        new_row["Answering Analysis"] = _DEFAULT_ANSWERING_ANALYSIS
        new_row["Answering Mechanism"] = answering_mechanism
        new_row["Purposes"] = ";".join(purposes)
        new_row["Catalog History"] = f"Added {date.today().isoformat()} via Admin > Question Catalog."
        new_row["Status"] = ""
        for p in perspectives:
            new_row[p] = "X"

        candidate_rows = rows + [new_row]
        _regenerate_yaml(candidate_rows)  # validates first; raises before any write
        _write_csv(fieldnames, candidate_rows)

    question_catalog_reader.clear_cache()
    # The new question's own purposes only: rows already in the catalog (Remediate, Attest, pending the
    # owner's ruling) are the generator's stderr, not this caller's business.
    from resource_explorer.investigation_purposes import known_purpose_names, unknown_purpose_warnings

    return unknown_purpose_warnings(purposes, known_purpose_names()), question_id


def retire_question(question: str) -> None:
    """Mark an existing question retired. Never removes or rewrites its
    other fields — retirement is a status flip, not an edit of the
    question's own text or history, so an old survey answer's question
    still reads exactly as it was asked."""
    question = (question or "").strip()
    if not question:
        raise QuestionCatalogWriteError("question text is required")

    with FileLock(str(_LOCK_PATH), timeout=10):
        fieldnames, rows = _read_rows()
        if "Status" not in fieldnames:
            fieldnames = fieldnames + ["Status"]

        # Text or stable id: a caller holding only the id (a manifest, a stored key) can retire by it.
        match_idx = next(
            (i for i, r in enumerate(rows)
             if (r.get("Question") or "").strip() == question
             or (r.get("Question ID") or "").strip() == question),
            None,
        )
        if match_idx is None:
            raise QuestionNotFoundError(f"question {question!r} not found in the catalog")
        if _is_retired(rows[match_idx]):
            raise QuestionCatalogWriteError(f"question {question!r} is already retired")

        candidate_rows = [dict(r) for r in rows]
        row = candidate_rows[match_idx]
        row["Status"] = STATUS_RETIRED
        history = (row.get("Catalog History") or "").strip()
        note = f"Retired {date.today().isoformat()} via Admin > Question Catalog."
        row["Catalog History"] = f"{history} {note}".strip() if history else note

        _regenerate_yaml(candidate_rows)
        _write_csv(fieldnames, candidate_rows)

    question_catalog_reader.clear_cache()
