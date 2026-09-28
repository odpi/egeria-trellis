"""
Generate a Dr.Egeria markdown "questions" document from a CSV, using the
Question/Perspective/Intent authoring pattern established in
docs/dr-egeria/foundations.md and docs/dr-egeria/scouting-questions.md.

Why this exists: hand-converting a CSV of questions x perspectives into raw
Dr.Egeria command blocks is slow and error-prone (wrong attribute names
silently produce "Not Found" validation errors, not loud failures) — this
was proven real while building the Scouting questions file by hand. This
script encodes the field-name mapping once (discovered empirically against
a real Dr.Egeria instance, not guessed) so every future stage's questions
file is generated, not hand-typed.

CSV schema (columns, in order) — updated 2026-08-14 when Asked At/Answered
At collapsed into a single Funnel Stage column (see docs/survey-question-
context-plan.md); this generator was not kept in sync with that change
until this pass (2026-08-15, restoring live Egeria state lost to a
container restart — see that same doc's "ScopedBy link" follow-up):
    Question           - required; becomes the Question's Display Name
    Funnel Stage        - required; the funnel-stage(s) this question
                          belongs to. May be a single stage ("Scouting")
                          or a slash-combined value ("Analysis/Enrichment",
                          signaling an Analysis-first/Enrichment-fallback
                          pattern — see docs/confidence-gated-validation-
                          plan.md) — one Link Element To Scope is emitted
                          per slash-separated stage token, all using Scope
                          Category "Asked At" uniformly (the Asked-At/
                          Answered-At distinction itself is retired at the
                          CSV level, but the Scope Category attribute still
                          needs *some* value — "Asked At" is reused rather
                          than inventing a new foundational term for what
                          is, in practice today, a single funnel-stage tag).
    Why is this important? - optional; becomes the term's Summary
    Rationale/Source    - optional; becomes the term's Description (falls
                          back to the question text itself if blank)
    Answering Analysis  - optional, RE-internal roadmap note; excluded
                          from the perspective-column scan (see
                          OPTIONAL_LEAD_COLUMNS)
    Answering Mechanism - optional, RE-internal roadmap note (which
                          engine answers this — Git Statistics/Code
                          Analysis/RAG/etc.); also excluded from the
                          perspective-column scan
    <perspective columns...> - one column per Perspective (must match a
                          Display Name already created in foundations.md,
                          e.g. "App/AI Builder"), any non-empty cell
                          (conventionally "X") links that perspective to
                          the question via Link Perspective to Question.
                          Every column after the optional lead columns is
                          treated as a perspective column — order doesn't
                          matter.

Model generated per row (matches foundations.md's documented pattern):
    Create Glossary Term (Glossary Name=<--questions-glossary>, Display
      Name=<question>, Description=<Rationale/Source>, Summary=<Why is
      this important?>, Usage=<derived from Funnel Stage>, classified as
      Question) -> Classify Term as Question
      -> Link Perspective to Question (one per marked perspective column)
      -> Link Element To Scope / ScopedBy, one per Funnel Stage token
         (Scope Category "Asked At" for all of them — see CSV schema note
         above).

Output is a plain Dr.Egeria markdown file — run `foundations.md` first
(creates the shared glossaries/perspectives/stage-terms this file
references by name), then VALIDATE this file before PROCESS, exactly like
every other Dr.Egeria document in this repo.

This is a generator, not a live Egeria client — it does not connect to
Egeria or execute anything. Feed the output through Dr.Egeria's own
validate/process pipeline (the `dr_egeria_run_block` MCP tool, or
whatever CLI/agent has that capability) — never process blind.

Usage:
    uv run --package resource-explorer \\
        python packages/resource-explorer/scripts/csv_to_dr_egeria_questions.py \\
        docs/dr-egeria/resource_questions.csv \\
        [--output docs/dr-egeria/scouting-questions.md] \\
        [--questions-glossary "User Questions"] \\
        [--stage-glossary "Funnel Stages"]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REQUIRED_COLUMNS = ["Question", "Funnel Stage"]
# "Answering Analysis"/"Answering Mechanism" are RE-internal roadmap
# documentation (which analysis_catalog.yaml id, if any, and which kind of
# engine answers this question today) — CSV/human-facing only, deliberately
# not sent to Egeria. Excluded here so the "everything after these is a
# perspective column" rule in generate() doesn't try to link them as a
# Perspective.
# "Purposes" is RE-internal (see csv_to_question_catalog_yaml.py) and, like the
# two Answering columns, is excluded from the Dr.Egeria output. It MUST stay in
# this list: perspective columns are identified by elimination, so any column
# missing from here silently becomes a phantom Perspective on every question.
# "Catalog History" (added to the CSV by #45, 2026-09-12) is provenance prose
# for the question's own row, never a scope. It was missing here for two days
# and the first regeneration after it (#58) emitted 17 "Link Perspective to
# Question -> Perspective::Catalog History" blocks — the phantom the sentence
# above warns about, caught before any heal executed them (the Perspective
# does not exist on the platform, so each would have failed).
# "Status" (added 2026-09-20, SPEC-ADMIN-THE-FOUR-GAPS.md §4) is the
# append-only catalog's retirement marker ("Retired"/empty) — RE-internal,
# consumed only by question_catalog_writer.py and question_catalog_reader.py,
# never sent to Egeria. Same phantom-Perspective risk as every column above,
# added here for the same reason.
#
# "Resource Types" (2026-09-20, docs/multi-resource-questions-design.md §1.1)
# carries the `;`-separated resource types a question applies to, `*` for all.
# It is NOT sent to Egeria either: the Egeria side of the question model is
# resource-type-agnostic by design (one User Questions glossary, one Funnel
# Stages glossary, twelve Perspectives — design §1.1's first paragraph), so a
# question term is the same term whichever resource type asks it. The column
# steers RE's own catalog generation only. It is listed here purely so the
# by-elimination perspective scan does not turn it into a thirteenth
# Perspective on every row.
# "Level" (2026-09-25, design §18.3) is RE-internal like Purposes: the
# granularity the answer is a single value at. Not published to Egeria yet —
# it will ride the same valid-value-set mechanism as Purposes (§10).
OPTIONAL_LEAD_COLUMNS = ["Why is this important?", "Rationale/Source", "Answering Analysis", "Answering Mechanism", "Purposes", "Catalog History", "Status", "Resource Types", "Level"]


def _block(command: str, **fields: str) -> str:
    """One Dr.Egeria command block: '## Command\\n\\n### Label\\nValue\\n\\n...'.
    Fields with empty/whitespace-only values are omitted entirely — an
    empty '### Label\\n\\n' block is not the same as the attribute being
    absent, and every field here is optional except the command name."""
    lines = [f"## {command}", ""]
    for label, value in fields.items():
        value = (value or "").strip()
        if not value:
            continue
        lines.append(f"### {label}")
        lines.append(value)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n\n___\n"


def _question_summary(why: str, rationale: str) -> str:
    """Egeria's Summary is user-facing — only 'Why is this important?'
    (the reason a user would care about this question) goes into it.
    'Rationale/Source' is deliberately excluded: it now goes into the
    term's Description instead (see _question_description), which is a
    better semantic fit and confirmed live to be a real, recognized
    Create Glossary Term attribute (not guessed). `rationale` is kept as
    a parameter (not dropped from the signature) so a future caller can
    still opt back in without re-deriving this decision."""
    return (why or "").strip()


def _question_description(question: str, rationale: str) -> str:
    """Description defaulted to a bare repeat of the question text before
    (wasted attribute). Rationale/Source — which RE table/surveyor would
    answer this, candidate tooling for real gaps — is genuine elaboration
    on the question, so it's a better fit for Description than Summary.
    Falls back to the question text itself when Rationale/Source is
    blank, so Description is never empty."""
    rationale = (rationale or "").strip()
    return rationale if rationale else question


def _question_usage(stage_tokens: list[str]) -> str:
    """Confirmed live (2026-08-12 probe) that Create Glossary Term
    recognizes a real 'Usage' attribute — put to use here as a
    human-readable restatement of the question's funnel-stage ScopedBy
    relationship(s), so someone browsing the term directly in Egeria's
    glossary doesn't have to traverse relationships to see when it
    applies. Post-2026-08-14 single-Funnel-Stage-column model — see module
    docstring; a slash-combined value produces one sentence naming all its
    stages, not the old two-sentence Asked-At/Answered-At phrasing."""
    if not stage_tokens:
        return ""
    if len(stage_tokens) == 1:
        return f"Typically asked and answerable during {stage_tokens[0]}."
    return f"Typically relevant during {' and '.join(stage_tokens)}."


def generate(
    rows: list[dict],
    *,
    questions_glossary: str,
    title: str,
) -> str:
    perspective_columns = [
        c for c in rows[0].keys()
        if c not in REQUIRED_COLUMNS and c not in OPTIONAL_LEAD_COLUMNS
    ] if rows else []

    out: list[str] = [
        f"# {title}",
        "",
        "> Generated by scripts/csv_to_dr_egeria_questions.py — do not hand-edit,",
        "> regenerate from the source CSV instead. Run docs/dr-egeria/foundations.md",
        "> first (creates the shared glossaries/perspectives/stage-terms this file",
        "> references by name). Run with VALIDATE first, then PROCESS.",
        "",
        "---",
        "",
        "# Questions",
        "",
    ]

    skipped: list[str] = []
    for row in rows:
        question = (row.get("Question") or "").strip()
        if not question:
            continue
        funnel_stage = (row.get("Funnel Stage") or "").strip()
        if not funnel_stage:
            skipped.append(f"{question!r} — missing 'Funnel Stage', skipped")
            continue
        stage_tokens = [t.strip() for t in funnel_stage.split("/") if t.strip()]

        rationale = row.get("Rationale/Source", "")
        summary = _question_summary(row.get("Why is this important?", ""), rationale)
        description = _question_description(question, rationale)
        usage = _question_usage(stage_tokens)

        out.append(_block(
            "Create Glossary Term",
            **{
                "Glossary Name": questions_glossary,
                "Display Name": question,
                "Description": description,
                "Summary": summary,
                "Usage": usage,
            },
        ))
        out.append(_block("Classify Term as Question", **{"Term Name": question}))

        for persp_col in perspective_columns:
            marked = (row.get(persp_col) or "").strip()
            if marked:
                out.append(_block(
                    "Link Perspective to Question",
                    # Qualified name, not bare display name (2026-08-15): 5 of
                    # the 12 Perspective display names collide with unrelated
                    # pre-existing elements in this Egeria instance (the stock
                    # Coco Pharmaceuticals demo data), which made Dr.Egeria's
                    # by-display-name resolver fail "Multiple elements found"
                    # for those 5 every time. foundations.md now gives every
                    # Perspective an explicit, unique "Perspective::<Name>"
                    # qualified name for exactly this reason — see its own
                    # Perspectives-section note and docs/survey-question-
                    # context-plan.md for the full incident writeup. Every
                    # perspective column here follows that same convention,
                    # not just the 5 that happened to collide, so this
                    # generator never has to special-case which ones are safe.
                    **{"Perspective Name": f"Perspective::{persp_col}", "Question Name": question},
                ))

        for stage_token in stage_tokens:
            out.append(_block(
                "Link Element To Scope",
                **{
                    "Target Element": question,
                    "Scope Reference": stage_token,
                    "Scope Category": "Asked At",
                },
            ))

    if skipped:
        print("Warnings:", file=sys.stderr)
        for s in skipped:
            print(f"  - {s}", file=sys.stderr)

    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv_path", type=Path, help="Source CSV (see module docstring for schema)")
    parser.add_argument("--output", type=Path, default=None, help="Output .md path (default: <csv stem>.md next to the CSV)")
    parser.add_argument("--questions-glossary", default="User Questions", help='Glossary Name questions are created in (default: "User Questions")')
    parser.add_argument("--title", default=None, help="H1 title for the generated file (default: derived from the CSV filename)")
    args = parser.parse_args()

    if not args.csv_path.exists():
        parser.error(f"CSV not found: {args.csv_path}")

    with open(args.csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        parser.error(f"CSV has no data rows: {args.csv_path}")

    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        parser.error(f"CSV missing required column(s): {missing}")

    output_path = args.output or args.csv_path.with_suffix(".md")
    title = args.title or f"{args.csv_path.stem.replace('-', ' ').title()} — Generated"

    content = generate(rows, questions_glossary=args.questions_glossary, title=title)
    output_path.write_text(content)
    n_questions = sum(1 for r in rows if (r.get("Question") or "").strip())
    print(f"Wrote {output_path} ({n_questions} questions from {len(rows)} CSV rows)")


if __name__ == "__main__":
    main()
