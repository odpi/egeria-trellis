"""Generate all of RE's database Survey Definition Dr.Egeria docs from
DATABASE_STEP_REGISTRY — the database equivalent of
`generate_repo_survey_definition.py` (Phase 1b coordinator brief, slice 12).

Today every authored Survey Definition in `docs/dr-egeria/survey-definitions/`
is repo-*; "PostgreSQL Database" has none, so the `/next` Survey & analyses
pane for a database resource shows only Egeria's two native processes and no
RE-authored candidate at all.

Three documents, one per `survey_kind`, matching the three
`analysis_catalog.yaml` `intent` tiers `DATABASE_STEP_REGISTRY`'s steps
actually back:

- **scouting**  — "Database Scouting Scan": `postgres_schema_and_stats`
  (schema_inventory, row_count_snapshot), `credential_capability`, and the
  scouting-tier half of `postgres_operations` (db_activity_signals,
  db_resilience).
- **analysis**  — "Database Analysis Survey": `postgres_column_profile`
  (data_class_match, reference_data_match), `postgres_nested_columns`
  (nested_column_profile), `db_derived` (schema_conventions,
  db_change_rates, schema_diff, grant_change), and the analysis-tier
  member of `postgres_operations` (db_external_dependencies).
- **assessment** — "Database Assessment Survey": the assessment-tier
  member of `postgres_operations` (privilege_audit) — currently a
  single-analysis document; more will be added here as new assessment-tier
  database analyses land (see `database_survey_types.csv`'s own row
  description).

`postgres_operations` bundles four analyses across THREE different
`intent` tiers (scouting: db_activity_signals/db_resilience; analysis:
db_external_dependencies; assessment: privilege_audit) and cannot be split
apart — the step either runs in full or not at all
(`DatabaseSurveyor._survey_operations` gates each sub-analysis
independently on `EngineCapabilities`, but the STEP itself is one
Governance Action Process Step). So `postgres_operations` is chained into
all three generated Survey Definitions, each under its own qualified name
(`GovActionProcessStep::{survey_group}::postgres_operations` — distinct
per `survey_group`, not a duplicate of the same element), so that each
document's own ScopedBy links only claim the questions its own analyses
actually answer (see `_build_step_key_to_questions()` below — the
step→question join is per (survey_group, step_key), not per bare
step_key, for exactly this reason).

Run this whenever DATABASE_STEP_REGISTRY changes (a step added/removed/
re-described, or an analysis's `intent` tier changes in
analysis_catalog.yaml), then execute the regenerated doc(s) against Egeria.
IMPORTANT: after executing a regenerated doc against an *already-linked*
process, run `uv run python scripts/reconcile_database_survey_definition_links.py`
afterward — Dr.Egeria's "Link First/Next Process Step" commands are not
idempotent (see `generate_repo_survey_definition.py`'s identical warning
and `survey_definition_reconciler.py` for the full incident history; the
same mechanism applies here unchanged).

Also emits one "Link Element To Scope" ScopedBy block per Question each
generated Survey Definition answers — the join is: each step_key's
containing analysis_catalog id (via `DATABASE_ANALYSIS_RE_STEP_MAP`)
cross-referenced against `question_catalog.yaml`'s per-question
`answering.analysis_ids`, restricted to `resource_type="database"`
questions. Run the database Question terms' own Dr.Egeria document(s)
first — the Question terms these blocks reference by name must already
exist in Egeria before this doc is executed.

Everything this script calls (`resource_explorer.surveyors.
dr_egeria_survey_publisher`) is resource-type-agnostic — this is the one
resource-type-specific piece of the mechanism, mirroring
`generate_repo_survey_definition.py` almost line for line. See that
script's own docstring for the design rationale this one inherits
unchanged (D1-D3, the CSV-as-source-of-truth pattern, the provenance/
hash-guard overwrite safety).
"""
from __future__ import annotations

import argparse
import hashlib
import json

import csv
from dataclasses import dataclass
from pathlib import Path

from resource_explorer.surveyors.dr_egeria_survey_publisher import (
    PublishableStep,
    generate_survey_definition_markdown,
)
from resource_explorer.surveyors.question_catalog_reader import get_questions
from resource_explorer.surveyors.database.survey_definition_adapter import (
    DATABASE_ANALYSIS_RE_STEP_MAP,
    DATABASE_STEP_REGISTRY,
)

TECHNOLOGY_TYPE = "PostgreSQL Database"
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria"
SPECS_CSV = DOCS_DIR / "database_survey_types.csv"
# Generated documents live in the survey-definitions *batch folder*, matching
# the repo generator's own layout choice — see that script's identical
# comment for why the CSV itself stays at the dr-egeria root rather than in
# a batch folder.
SURVEY_DEFS_DIR = DOCS_DIR / "survey-definitions-database"
ALL_STEPS_SENTINEL = "*"


@dataclass(frozen=True)
class SurveyDefSpec:
    survey_kind: str
    survey_group: str
    survey_display_name: str
    description: str
    step_keys: list[str]  # order matters — this is the chain order
    output_filename: str


class SurveyTypesCsvError(ValueError):
    """Raised on a malformed or DATABASE_STEP_REGISTRY-mismatched
    database_survey_types.csv — deliberately loud rather than silently
    producing a broken or incomplete Survey Definition, same reasoning as
    `generate_repo_survey_definition.py`'s identical guard."""


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

    specs = []
    for survey_kind, survey_group in order:
        g = groups[(survey_kind, survey_group)]
        step_keys = [key for _, key in sorted(g["steps"])]
        if step_keys == [ALL_STEPS_SENTINEL]:
            step_keys = list(DATABASE_STEP_REGISTRY.keys())
        else:
            unknown = [k for k in step_keys if k not in DATABASE_STEP_REGISTRY]
            if unknown:
                raise SurveyTypesCsvError(
                    f"{survey_group}: step_key(s) not found in DATABASE_STEP_REGISTRY: "
                    f"{unknown} (typo in {csv_path.name}, or a step was removed from "
                    f"DATABASE_STEP_REGISTRY without updating the CSV)"
                )
        specs.append(
            SurveyDefSpec(
                survey_kind=survey_kind,
                survey_group=survey_group,
                survey_display_name=g["survey_display_name"],
                description=g["description"],
                step_keys=step_keys,
                output_filename=g["output_filename"],
            )
        )

    # Coverage guard: every DATABASE_STEP_REGISTRY step should appear in at
    # least one CSV-authored survey — a step with zero references is
    # invisible to every generated Survey Definition, silently. Warn rather
    # than hard-fail, same reasoning as the repo generator's identical
    # guard.
    referenced = {key for spec in specs for key in spec.step_keys}
    unreferenced = [key for key in DATABASE_STEP_REGISTRY if key not in referenced]
    if unreferenced:
        print(
            f"WARNING: DATABASE_STEP_REGISTRY step(s) with no Survey Type "
            f"reference in {csv_path.name}: {unreferenced}"
        )
    return specs


SPECS = load_specs_from_csv()


def build_steps(step_keys: list[str]) -> list[PublishableStep]:
    return [
        PublishableStep(
            step_key=key, description=DATABASE_STEP_REGISTRY[key].description,
            technology_type=TECHNOLOGY_TYPE,
            # No database step is currently Prefect-routed (none is long-
            # running/thrash-prone the way repo_arch_coupling/repo_secret_
            # scan/repo_rag_ingestion are) — plain local execution for all.
            executes_at="resource-explorer",
        )
        for key in step_keys
    ]


def _build_step_key_to_questions() -> dict[tuple[str, str], list[str]]:
    """Invert `question_catalog.yaml`'s `answering.analysis_ids` (analysis
    id, e.g. "privilege_audit") through `DATABASE_ANALYSIS_RE_STEP_MAP`
    (analysis id -> the DATABASE_STEP_REGISTRY step_key(s) that produce its
    data) to get (survey_group, step_key) -> [question display names].

    Keyed by (survey_group, step_key), not bare step_key, because
    `postgres_operations` is chained into three different Survey
    Definitions (see this module's own docstring) and each one must only
    claim the questions ITS OWN sub-analyses answer — `DatabaseScoutingSurvey`
    should not claim `privilege_audit`'s questions just because
    `postgres_operations` also happens to answer them for
    `DatabaseAssessmentSurvey`. The caller restricts each spec's lookup to
    analysis_ids actually reachable from THAT spec's own step set (see
    `_answered_questions_for_spec` below) — this function itself still
    returns every question a step could ever answer, unfiltered; the
    per-spec filtering happens where it's applied.
    """
    mapping: dict[str, list[str]] = {}
    for entry in get_questions(resource_type="database"):
        for analysis_id in entry["answering"]["analysis_ids"]:
            for step_key in DATABASE_ANALYSIS_RE_STEP_MAP.get(analysis_id, []):
                mapping.setdefault(step_key, [])
                if entry["question"] not in mapping[step_key]:
                    mapping[step_key].append(entry["question"])
    return mapping


#: Per `intent` tier in `analysis_catalog.yaml` — which analysis_ids each
#: generated Survey Definition is allowed to claim questions for, even when
#: a shared step (`postgres_operations`) would otherwise pull in a
#: sibling analysis's questions too. See this module's own docstring for
#: why `postgres_operations` is chained into all three documents but must
#: not let one document over-claim another's questions.
SPEC_ANALYSIS_SCOPE: dict[str, set[str]] = {
    "DatabaseScoutingSurvey": {
        "schema_inventory", "row_count_snapshot", "credential_capability",
        "db_activity_signals", "db_resilience",
    },
    "DatabaseAnalysisSurvey": {
        "data_class_match", "reference_data_match", "nested_column_profile",
        "schema_conventions", "db_change_rates", "schema_diff", "grant_change",
        "db_external_dependencies",
    },
    "DatabaseAssessmentSurvey": {"privilege_audit"},
}


def _answered_questions(
    survey_group: str, step_keys: list[str], step_key_to_questions: dict[str, list[str]],
) -> list[str]:
    """Union of questions answered by any step in this Survey Definition,
    restricted to the analyses this survey_group actually owns
    (`SPEC_ANALYSIS_SCOPE`) — order-stable and de-duplicated."""
    allowed_analyses = SPEC_ANALYSIS_SCOPE.get(survey_group, set())
    allowed_questions: set[str] = set()
    for entry in get_questions(resource_type="database"):
        if set(entry["answering"]["analysis_ids"]) & allowed_analyses:
            allowed_questions.add(entry["question"])

    seen: list[str] = []
    for key in step_keys:
        for question in step_key_to_questions.get(key, []):
            if question in allowed_questions and question not in seen:
                seen.append(question)
    return seen


#: Records the sha256 of the content this script last wrote for each
#: document — same overwrite-safety mechanism as
#: `generate_repo_survey_definition.py`'s identical sidecar; see that
#: script's docstring for the full rationale.
PROVENANCE_FILE = SURVEY_DEFS_DIR / ".generated_database.json"


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
