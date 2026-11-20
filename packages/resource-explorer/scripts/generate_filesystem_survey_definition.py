"""Generate RE's file-system Survey Definition Dr.Egeria docs from the file-system step registry and
`filesystem_survey_types.csv` — the file-system equivalent of `generate_database_survey_definition.py`
(item D2 of the file-system Survey Definition rework).

Two documents, one per `survey_kind`:

- **scouting** — "Filesystem Scouting Survey": the walk (`filesystem_inventory`), then the two analyses that read
  only what that walk stored (`fs_layout_signals`, `fs_change_rates`). No file's contents are opened.
- **analysis** — "Filesystem Analysis Survey": the walk, then the analyses that open files or read their stored
  facts: `file_profiles` (column profiles of the data files), `narrative_docs` (README-style files read into RE's
  search index), `file_data_class_match` (column and file-name matches against Egeria's data classes, proposals
  only), and `fs_activity`, `fs_duplicates`, `fs_permissions`.

**Resource Explorer leads; Egeria's Folder Survey is not part of these definitions.** Every step is
`executes_at: resource-explorer`. `filesystem_inventory` REFUSES (the step's error says so) when the stored
reachability state says Egeria's Folder Survey leads for the folder; that survey is run from the file system's
folder-survey card, not from a definition. `resource_reachability` and `survey_price` are stored states with no step
of their own, so they are in no definition (their questions are answered from the state, not from a run).

Steps in a definition run in the order of the CSV's `step_order`; each reads what the walk before it stored, so
`filesystem_inventory` is first in both and `file_profiles` precedes `file_data_class_match`. The `fs_*` steps come
from the installed Analytic Library (`FILESYSTEM_STEP_REGISTRY`); where one is not installed the generator refuses
rather than writing a step the executor would report as "not run: unknown step".

Also emits one "Link Element To Scope" ScopedBy block per Question each document answers — the join is each step's
analysis (`FILESYSTEM_ANALYSIS_STEP_MAP`) against `question_catalog.yaml`'s `answering.analysis_ids`, restricted to
`resource_type="filesystem"` and to the analyses this document owns (`SPEC_ANALYSIS_SCOPE`). A question the document
names that is not authored in the questions batch (`docs/dr-egeria/questions*/`) fails the generator: a ScopedBy
block naming an absent Question term succeeds in Dr.Egeria and links nothing.

Run this whenever FILESYSTEM_STEP_REGISTRY, `filesystem_survey_types.csv` or an analysis's questions change, then
execute the regenerated doc(s) against Egeria. After executing a regenerated doc against an *already-linked* process,
run `uv run python scripts/reconcile_filesystem_survey_definition_links.py`: Dr.Egeria's "Link First/Next Process
Step" commands are not idempotent (see `generate_repo_survey_definition.py` and `survey_definition_reconciler.py`).
The two reconcilers load this module's SPECS at import, so a step that is not runnable raises SurveyTypesCsvError
there too (the same as the database batch).
The overwrite guard (a sha256 sidecar, `.generated_filesystem.json`) and `RE_SURVEY_DEFS_DIR` are the database
generator's, unchanged.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from resource_explorer.surveyors.dr_egeria_survey_publisher import (
    PublishableStep,
    generate_survey_definition_markdown,
)
from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
    _ADAPTER,
    FILESYSTEM_ANALYSIS_STEP_MAP,
    FILESYSTEM_STEP_REGISTRY,
)
from resource_explorer.surveyors.question_catalog_reader import get_questions

TECHNOLOGY_TYPE = "File System Directory"
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria"
SPECS_CSV = DOCS_DIR / "filesystem_survey_types.csv"
# RE_SURVEY_DEFS_DIR redirects every write (documents and the provenance sidecar) so a test can exercise the
# generator on a copy instead of rewriting the tracked tree.
SURVEY_DEFS_DIR = Path(os.environ.get("RE_SURVEY_DEFS_DIR") or DOCS_DIR / "survey-definitions-filesystem")


@dataclass(frozen=True)
class SurveyDefSpec:
    survey_kind: str
    survey_group: str
    survey_display_name: str
    description: str
    step_keys: list[str]  # order matters — this is the chain order
    output_filename: str


class SurveyTypesCsvError(ValueError):
    """A malformed or step-registry-mismatched filesystem_survey_types.csv, or a Question the definitions name that
    the questions batch does not author — deliberately loud rather than a broken or silently unscoped definition."""


def _runnable_steps() -> set[str]:
    """Every analysis that has a runnable step (the adapter's built-in steps plus the installed library steps).
    `resource_reachability` and `survey_price` are stored states, not steps."""
    return set(_ADAPTER.re_analysis_steps) & set(FILESYSTEM_ANALYSIS_STEP_MAP)


def load_specs_from_csv(csv_path: Path = SPECS_CSV) -> list[SurveyDefSpec]:
    groups: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["survey_kind"], row["survey_group"])
            if key not in groups:
                groups[key] = {
                    "survey_display_name": row["survey_display_name"],
                    "description": row["description"],
                    "output_filename": row["output_filename"],
                    "steps": [],  # (step_order, step_key)
                }
                order.append(key)
            groups[key]["steps"].append((int(row["step_order"]), row["step_key"]))

    runnable = _runnable_steps()
    specs = []
    for survey_kind, survey_group in order:
        g = groups[(survey_kind, survey_group)]
        step_keys = [key for _, key in sorted(g["steps"])]
        unknown = [k for k in step_keys if k not in runnable]
        if unknown:
            raise SurveyTypesCsvError(
                f"{survey_group}: step_key(s) with no runnable step in the file-system adapter: {unknown} "
                f"(a typo in {csv_path.name}, a state with no step such as resource_reachability, or an "
                f"Analytic Library step that is not installed — the executor would report each as "
                f"'not run: unknown step')"
            )
        specs.append(SurveyDefSpec(
            survey_kind=survey_kind, survey_group=survey_group, survey_display_name=g["survey_display_name"],
            description=g["description"], step_keys=step_keys, output_filename=g["output_filename"]))

    referenced = {key for spec in specs for key in spec.step_keys}
    unreferenced = sorted(runnable - referenced)
    if unreferenced:
        print(f"WARNING: runnable file-system step(s) with no Survey Type reference in {csv_path.name}: {unreferenced}")
    return specs


SPECS = load_specs_from_csv()


def _step_description(key: str) -> str:
    """The step's own words: the adapter's step info for its built-in steps, the installed library step's declared
    description for the `fs_*` ones. A step with none is refused, not given an invented sentence."""
    info = _ADAPTER.re_analysis_step_info.get(key) or {}
    text = info.get("description") or getattr(FILESYSTEM_STEP_REGISTRY.get(key), "description", "")
    if not text:
        raise SurveyTypesCsvError(f"{key}: no description in the adapter's step info or the step registry")
    return text


def build_steps(step_keys: list[str]) -> list[PublishableStep]:
    return [
        PublishableStep(step_key=key, description=_step_description(key), technology_type=TECHNOLOGY_TYPE,
                        # Resource Explorer's own walk and presses; Egeria's Folder Survey is not part of these
                        # definitions (see this module's docstring).
                        executes_at="resource-explorer")
        for key in step_keys
    ]


def _build_step_key_to_questions() -> dict[str, list[str]]:
    """Invert `question_catalog.yaml`'s `answering.analysis_ids` through `FILESYSTEM_ANALYSIS_STEP_MAP` (analysis id ->
    the step key(s) that produce it) to get step_key -> [question display names]."""
    mapping: dict[str, list[str]] = {}
    for entry in get_questions(resource_type="filesystem"):
        for analysis_id in entry["answering"]["analysis_ids"]:
            for step_key in FILESYSTEM_ANALYSIS_STEP_MAP.get(analysis_id, []):
                mapping.setdefault(step_key, [])
                if entry["question"] not in mapping[step_key]:
                    mapping[step_key].append(entry["question"])
    return mapping


#: Which analysis ids each document may claim questions for. `filesystem_inventory` is chained into both documents
#: (each later step reads the walk), but its questions are claimed by the scouting document only, so the analysis
#: document does not re-claim what the walk alone answers.
SPEC_ANALYSIS_SCOPE: dict[str, set[str]] = {
    "FilesystemScoutingSurvey": {"filesystem_inventory", "fs_layout_signals", "fs_change_rates"},
    "FilesystemAnalysisSurvey": {
        "file_profiles", "narrative_docs", "file_data_class_match", "fs_activity", "fs_duplicates", "fs_permissions",
    },
}


def _answered_questions(survey_group: str, step_keys: list[str], step_key_to_questions: dict[str, list[str]]) -> list[str]:
    """Union of questions answered by any step in this definition, restricted to the analyses this survey_group owns
    (`SPEC_ANALYSIS_SCOPE`) — order-stable and de-duplicated."""
    allowed_analyses = SPEC_ANALYSIS_SCOPE.get(survey_group, set())
    allowed_questions: set[str] = set()
    for entry in get_questions(resource_type="filesystem"):
        if set(entry["answering"]["analysis_ids"]) & allowed_analyses:
            allowed_questions.add(entry["question"])
    seen: list[str] = []
    for key in step_keys:
        for question in step_key_to_questions.get(key, []):
            if question in allowed_questions and question not in seen:
                seen.append(question)
    return seen


def _authored_question_text() -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(DOCS_DIR.glob("questions*/*.md")))


def _require_questions_authored(survey_group: str, questions: list[str]) -> None:
    """A ScopedBy block naming a Question term the questions batch does not author creates no link (Dr.Egeria succeeds
    quietly), leaving a definition that looks healthy and is unscoped. Fail here instead."""
    authored = _authored_question_text()
    missing = [q for q in questions if q not in authored]
    if missing:
        raise SurveyTypesCsvError(
            f"{survey_group}: Question term(s) not authored in docs/dr-egeria/questions*/: {missing}")


#: Records the sha256 of the content this script last wrote for each
#: document — same overwrite-safety mechanism as
#: `generate_repo_survey_definition.py`'s identical sidecar; see that
#: script's docstring for the full rationale.
PROVENANCE_FILE = SURVEY_DEFS_DIR / ".generated_filesystem.json"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_provenance() -> dict:
    try:
        return json.loads(PROVENANCE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write_provenance(record: dict) -> None:
    PROVENANCE_FILE.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def _first_divergent_line(existing: str, generated: str) -> str:
    a, b = existing.splitlines(), generated.splitlines()
    for i in range(max(len(a), len(b))):
        old_line = a[i] if i < len(a) else "(end of file)"
        new_line = b[i] if i < len(b) else "(end of file)"
        if old_line != new_line:
            return f"line {i + 1}:\n      on disk:   {old_line[:100]}\n      generated: {new_line[:100]}"
    return "(no line differs — whitespace only)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite documents that have been edited since they were "
             "generated. This DISCARDS guards, request parameters and any "
             "other detail the CSV cannot express.",
    )
    args = parser.parse_args()

    SURVEY_DEFS_DIR.mkdir(parents=True, exist_ok=True)
    provenance = _load_provenance()
    skipped: list[str] = []
    step_key_to_questions = _build_step_key_to_questions()
    for spec in SPECS:
        steps = build_steps(spec.step_keys)
        answers_questions = _answered_questions(
            spec.survey_group, spec.step_keys, step_key_to_questions
        )
        _require_questions_authored(spec.survey_group, answers_questions)
        markdown = generate_survey_definition_markdown(
            survey_group=spec.survey_group,
            survey_display_name=spec.survey_display_name,
            technology_type=TECHNOLOGY_TYPE,
            description=spec.description,
            steps=steps,
            survey_kind=spec.survey_kind,
            answers_questions=answers_questions,
        )
        output_path = SURVEY_DEFS_DIR / spec.output_filename
        existing = output_path.read_text() if output_path.exists() else None

        if existing is not None and existing != markdown:
            untouched = provenance.get(spec.output_filename) == _digest(existing)
            if not untouched and not args.force:
                skipped.append(spec.output_filename)
                print(
                    f"[{spec.survey_kind}] SKIPPED {output_path.name} — it has been "
                    f"edited since it was generated.\n"
                    f"      This file is the definition; the CSV is only a "
                    f"specification of it, and cannot express guards, request "
                    f"parameters or branching.\n"
                    f"      First difference at {_first_divergent_line(existing, markdown)}\n"
                    f"      Re-run with --force to discard those edits."
                )
                continue

        if existing == markdown:
            provenance[spec.output_filename] = _digest(markdown)
            print(f"[{spec.survey_kind}] unchanged: {output_path.name}")
            continue

        output_path.write_text(markdown)
        provenance[spec.output_filename] = _digest(markdown)
        verb = "wrote" if existing is None else "regenerated"
        print(
            f"[{spec.survey_kind}] {verb} {len(steps)} step(s), "
            f"{len(answers_questions)} question link(s) to {output_path}"
        )

    _write_provenance(provenance)
    if skipped:
        print(
            f"\n{len(skipped)} document(s) left untouched: {', '.join(skipped)}\n"
            "Nothing was lost. Reconcile them by hand, or re-run with --force."
        )


if __name__ == "__main__":
    main()
