"""Generate all of RE's repo Survey Definition Dr.Egeria docs from repo's own
STEP_REGISTRY — reproducible artifacts that publish repo analysis steps as
real GovernanceActionProcessStep elements and chain them into runnable
Survey Definitions, one per UI surface (survey_kind).

Analysis-step Egeria registration plan, D1-D3
(docs/analysis-step-egeria-registration-plan.md), extended per
docs/discovery-automate-project-context-plan.md Part 1 to generate three
separate Survey Definitions instead of one, each tagged with a survey_kind
Additional Property so Discovery's candidate list doesn't show the
Automate-tier "run everything" bundle (and vice versa):

- **scouting**  — "Repo Coarse Scout": the fast, GitHub-API-only pair
  (repo_health, repo_language) — re-authored here because the original
  live Egeria element was lost to a database reset and never recreated
  (confirmed missing via a live probe before this script was written).
- **discovery** — "Repo Discovery Survey": early-headlights signals from
  data already collected, no new fetch. Currently just
  repo_license_classification (a real, currently-available "should we
  pursue this" signal) — deliberately a minimal, honest interim set;
  grows once Part 2's new Discovery-tier analyses (maturity, security
  policy heuristic, deployment/Docker evidence, etc.) are built.
- **automate_full** — "Repo Full Survey": every current STEP_REGISTRY
  step, chained — the comprehensive bundle for an already-tracked
  resource, not Discovery's launch target. Re-tags the exact same
  qualified_name as before (upsert=true on Create Governance Action
  Process means re-running this in Egeria updates the existing element
  in place, not a duplicate) — only its Additional Properties gain the
  new survey_kind tag.

Run this whenever STEP_REGISTRY changes (a step added/removed/re-described),
then execute the regenerated doc(s) against Egeria to keep Discovery/
Scouting/Automate's candidate lists in sync. IMPORTANT: after executing a
regenerated doc against an *already-linked* process (i.e. every time except
the very first authoring), run
`uv run python scripts/reconcile_survey_definition_links.py` afterward —
Dr.Egeria's "Link First/Next Process Step" commands are not idempotent and
will duplicate or leave stale the step-to-step links, which makes
SurveyDefinitionReader see "branching" and refuse to run the Survey
Definition at all (a real live incident, 2026-08-13 — see that script's and
survey_definition_reconciler.py's docstrings for the full story).

Also emits one "Link Element To Scope" ScopedBy block per Question each
generated Survey Definition answers (docs/survey-question-context-plan.md
D1) — the join is: each step_key's containing analysis_catalog id (via
REPO_ANALYSIS_SOURCE_STEPS) cross-referenced against question_catalog.yaml's
per-question answering.analysis_ids. Run docs/dr-egeria/foundations.md and
docs/dr-egeria/scouting-questions.md (or their generated equivalents)
first — the Question terms these blocks reference by name must already
exist in Egeria before this doc is executed.

This is the one resource-type-specific piece of the mechanism — everything
it calls (resource_explorer.surveyors.dr_egeria_survey_publisher) is
resource-type-agnostic, so a future database/filesystem equivalent of this
script just needs its own PublishableStep list(s) built from whatever
registry that resource type uses (neither has a STEP_REGISTRY-shaped
registry today — see the plan doc's D1 for why repo went first).

D3 (docs/repo-survey-catalog-completion-plan.md): SPECS is now parsed from
docs/dr-egeria/repo_survey_types.csv instead of being a Python literal —
the primary Survey Type authoring mechanism, matching the precedent
docs/dr-egeria/resource_questions.csv already set for Questions. One row
per (survey, step) pair, grouped by (survey_kind, survey_group) and
ordered by step_order. A single row with step_key "*" means "every
current STEP_REGISTRY step, in STEP_REGISTRY's own order" — used for
"Repo Full Survey" specifically so it stays automatically complete by
construction (this is NOT just cosmetic: before this change, "Full
Survey"'s step_keys was already `list(STEP_REGISTRY.keys())` in code, but
the generated *doc on disk* had gone stale and silently dropped
repo_symbol_extraction after it was added — regenerating always would
have caught this immediately; converting "*" into an explicit hand-listed
CSV row-list would have reintroduced exactly that failure mode for the
next step added later, so it stays a sentinel instead).
"""
from __future__ import annotations

import argparse
import os
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
from resource_explorer.surveyors.repo_survey_definition_adapter import (
    REPO_ANALYSIS_SOURCE_STEPS,
    STEP_REGISTRY,
)

TECHNOLOGY_TYPE = "Git Repository"
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "dr-egeria"
SPECS_CSV = DOCS_DIR / "repo_survey_types.csv"
# Generated documents live in the survey-definitions *batch folder*, not at the
# dr-egeria root: docs/dr-egeria/ is now organised as bootstrap batches (one
# folder per ordered execution group, each with a _batch.json — see
# docs/dr-egeria/_folder_order.json and resource_explorer/bootstrap.py). The CSV
# above deliberately stays at the root: it is a source of truth this script
# reads, not a Dr.Egeria command document to be executed, and only folders are
# treated as batches.
# RE_SURVEY_DEFS_DIR redirects every write (documents and the provenance sidecar) so a test
# can exercise the generator on a copy instead of rewriting the tracked tree.
SURVEY_DEFS_DIR = Path(os.environ.get("RE_SURVEY_DEFS_DIR") or DOCS_DIR / "survey-definitions")
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
    """Raised on a malformed or STEP_REGISTRY-mismatched repo_survey_types.csv
    — deliberately loud rather than silently producing a broken or
    incomplete Survey Definition (see D3's validation-guard rationale:
    this is exactly the class of gap that let repo_symbol_extraction
    silently fall out of Repo Full Survey last time)."""


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
            step_keys = list(STEP_REGISTRY.keys())
        else:
            unknown = [k for k in step_keys if k not in STEP_REGISTRY]
            if unknown:
                raise SurveyTypesCsvError(
                    f"{survey_group}: step_key(s) not found in STEP_REGISTRY: {unknown} "
                    f"(typo in {csv_path.name}, or a step was removed from STEP_REGISTRY "
                    f"without updating the CSV)"
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

    # Coverage guard: every STEP_REGISTRY step should appear in at least
    # one CSV-authored survey (directly, or via the "*" sentinel) — a step
    # with zero references is invisible to every generated Survey
    # Definition, silently. Warn rather than hard-fail: a step legitimately
    # excluded from every Survey Type (rare, but not impossible) shouldn't
    # block generation of everything else.
    referenced = {key for spec in specs for key in spec.step_keys}
    unreferenced = [key for key in STEP_REGISTRY if key not in referenced]
    if unreferenced:
        print(
            f"WARNING: STEP_REGISTRY step(s) with no Survey Type reference "
            f"in {csv_path.name}: {unreferenced}"
        )
    return specs


SPECS = load_specs_from_csv()


# Steps deliberately opted into Prefect (executes_at: prefect) rather than
# plain local execution — real flow-run observability/cancellation is worth
# the per-step overhead specifically for long-running or thrash-prone steps,
# not a blanket switch. See docs/re-ea-consolidation-audit.md and the Prefect
# entry in docs/Backlog.md. repo_arch_coupling does a real `git log` history
# clone (git_clone_root, unlike repo_arch_detect's zipball-only read) and is
# the step this was written for — long enough, and network/IO-dependent
# enough, that "is it still running or did it hang" is a real question for it
# in a way it usually isn't for the cheaper steps.
#
# Widened 2026-09-19 (project owner decision, PLAN-PREFECT-OR-ALTERNATIVE.md
# §5 phase 5) after phase 4's measurement put Prefect's fixed dispatch
# overhead at ~8-10s (Backlog.md, "Measured 2026-09-18 — Prefect per-step
# dispatch overhead") — small relative to the runtime of the other
# compute_cost="high" steps in repo_survey_definition_adapter.py.
# repo_secret_scan (277.3s measured on egeria_git) and repo_rag_ingestion
# (embeds the repository) both qualify on the same basis as
# repo_arch_coupling; every other step stays local.
PREFECT_ROUTED_STEPS = frozenset({
    "repo_arch_coupling",
    "repo_secret_scan",
    "repo_rag_ingestion",
})


def build_steps(step_keys: list[str]) -> list[PublishableStep]:
    return [
        PublishableStep(
            step_key=key, description=STEP_REGISTRY[key].description, technology_type=TECHNOLOGY_TYPE,
            executes_at="prefect" if key in PREFECT_ROUTED_STEPS else "resource-explorer",
        )
        for key in step_keys
    ]


def _build_step_key_to_questions() -> dict[str, list[str]]:
    """Invert question_catalog.yaml's answering.analysis_ids (analysis_catalog
    id, e.g. "security_scan") through REPO_ANALYSIS_SOURCE_STEPS (analysis id ->
    the STEP_REGISTRY step_key(s) that produce its data) to get
    step_key -> [question display names] — the join
    docs/survey-question-context-plan.md's D1 depends on. A question with no
    analysis_ids (kind="human"/"gap"/etc.) contributes nothing here; that's
    expected, not every question is answered by a survey step at all.

    SOURCE steps, not owned ones. The question here is "running this step, which
    questions become answerable" — executability, not ownership. Read off
    REPO_ANALYSIS_STEP_MAP after ownership and executability were split
    (2026-09-08), "How do its components relate to each other?" — answered by
    `architecture_diagram` alone, which deliberately owns no steps — mapped to
    no step at all and would have been dropped from every generated Survey
    Definition. Nothing would have failed: the document simply loses a
    ScopedBy link, and the next resync un-authors it from Egeria.
    """
    mapping: dict[str, list[str]] = {}
    for entry in get_questions(resource_type="repo"):
        for analysis_id in entry["answering"]["analysis_ids"]:
            for step_key in REPO_ANALYSIS_SOURCE_STEPS.get(analysis_id, []):
                mapping.setdefault(step_key, [])
                if entry["question"] not in mapping[step_key]:
                    mapping[step_key].append(entry["question"])
    return mapping


def _answered_questions(step_keys: list[str], step_key_to_questions: dict[str, list[str]]) -> list[str]:
    """Union of questions answered by any step in this Survey Definition,
    order-stable and de-duplicated (a question spanning multiple steps in
    the same spec, e.g. language_file_classification's three-step bundle,
    gets exactly one ScopedBy link, not one per contributing step)."""
    seen: list[str] = []
    for key in step_keys:
        for question in step_key_to_questions.get(key, []):
            if question not in seen:
                seen.append(question)
    return seen


# D2 (docs/survey-tab-unification-plan.md): a Survey Definition with zero
# ScopedBy links never appears in ANY phase-scoped candidate panel
# (find_candidate_process_guids_by_questions requires at least one) — and
# the automatic cross-reference above is a genuine dead end for
# RepoCoarseProfile: no authored Question's answering.analysis_ids
# currently names language_file_classification or data_file_profiling
# (confirmed by grep against question_catalog.yaml before adding this).
# Rather than hand-editing the Question catalog just to manufacture a
# cross-reference, this is an explicit, small manual override — extra
# Question display names to scope a survey_group to, on top of whatever
# the automatic join already finds. Empty/absent for every spec whose own
# steps' analysis_ids already produce real links.
MANUAL_EXTRA_SCOPE_QUESTIONS: dict[str, list[str]] = {
    "RepoCoarseProfile": [
        "Is this repository actively maintained?",
        "What is this resource, and what is it for?",
    ],
}


#: Records the sha256 of the content this script last wrote for each document.
#:
#: It exists to tell two indistinguishable states apart. When a generated file
#: no longer matches what the CSV would produce, that is either "the CSV
#: changed" (regenerating is correct) or "someone hand-authored into it"
#: (regenerating destroys their work). The file alone cannot say which. With a
#: recorded hash it can: content that still matches what we last wrote has not
#: been touched by hand, whatever the CSV now says.
#:
#: Deliberately a sidecar rather than a marker inside the document. These files
#: are parsed by Dr.Egeria, and adding content to them to protect them from
#: corruption would be its own risk.
PROVENANCE_FILE = SURVEY_DEFS_DIR / ".generated.json"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_provenance() -> dict:
    try:
        return json.loads(PROVENANCE_FILE.read_text())
    except (OSError, ValueError):
        # No record, or an unreadable one. Both mean we cannot prove any file
        # is untouched, so every existing file is treated as hand-authored and
        # left alone. Failing towards "refuse to overwrite" is the whole point.
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
        answers_questions = _answered_questions(spec.step_keys, step_key_to_questions)
        for extra in MANUAL_EXTRA_SCOPE_QUESTIONS.get(spec.survey_group, []):
            if extra not in answers_questions:
                answers_questions.append(extra)
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
            # The document differs from what the CSV now produces. Overwrite
            # ONLY if we can prove it is untouched generator output.
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
            # Byte-identical. Recording provenance is the whole value of this
            # pass — it is what lets a LATER legitimate CSV change regenerate
            # silently instead of being refused as possibly hand-authored.
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
